//! WASM glue around the [`postflop_solver`] crate.
//!
//! This crate is a thin *adapter*: it does NOT fork or vendor the upstream
//! engine. It only translates the backend `ScenarioEnvelope` JSON into the
//! engine's `CardConfig` + `TreeConfig`, runs chunked Discounted-CFR
//! iterations, and serialises results into the cache-document shape the
//! Python backend persists in `solver_runs.output_jsonb`.
//!
//! # JS API (stable surface)
//!
//! | Export | Semantics |
//! |---|---|
//! | `init_game(scenario_json) -> u32` | Parse envelope, build [`PostFlopGame`], allocate memory. Returns a handle (0 = error; see [`last_error`]). |
//! | `solve_step(handle, max_iters) -> String` | Run chunked CFR for up to `max_iters` iterations, returning a JSON `SolveProgress` doc. |
//! | `get_exploitability(handle) -> f32` | Most recent exploitability in bb. |
//! | `export_strategy(handle, history_json) -> String` | Apply a history path then return a [`strategy_export::StrategyExport`] JSON doc. |
//! | `free_game(handle)` | Drop the game and free its arena. |
//! | `last_error() -> String` | Last error message stashed by any of the above. |
//!
//! # Thread safety
//! All state lives behind a single global `Mutex<HashMap<u32, GameState>>`.
//! In wasm we expect *one active game per worker*; spawn multiple workers if
//! you want to solve in parallel. On native, the mutex is uncontended in
//! tests because each test creates its own handle.
//!
//! [`PostFlopGame`]: postflop_solver::PostFlopGame

#![doc(html_no_source)]

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

use postflop_solver::{
    card_from_str, compute_exploitability, finalize, flop_from_str, solve_step as cfr_step,
    ActionTree, BoardState, CardConfig, PostFlopGame, TreeConfig, NOT_DEALT,
};
use serde::{Deserialize, Serialize};
use wasm_bindgen::prelude::*;

pub mod bet_tree;
pub mod envelope;
pub mod range_convert;
pub mod strategy_export;

use bet_tree::build_street_sizes;
use envelope::ScenarioEnvelope;
use range_convert::range_from_hand_classes;
use strategy_export::{build_export, ExportInput};

// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------

/// Conversion factor: pot/stacks in the envelope are in big blinds, the
/// engine wants integer chips. 100 chips per bb gives 2-decimal precision,
/// which matches the backend's `_BB_QUANT = Decimal("0.01")`.
pub const CHIPS_PER_BB: i32 = 100;

/// Default early-exit threshold (PLAN.md §6: "200 iters or 0.5 bb").
/// Exposed as a constant so tests can compare apples-to-apples.
pub const DEFAULT_TARGET_EXPLOITABILITY_BB: f32 = 0.5;

/// Default cap on CFR iterations per game. Matches PLAN.md §6 ("200 iters
/// or 0.5 bb exploitability, whichever first").
pub const DEFAULT_MAX_ITERATIONS: u32 = 200;

struct GameState {
    game: PostFlopGame,
    iterations: u32,
    max_iterations: u32,
    /// In **chips** (not bb), to match `compute_exploitability` directly.
    target_exploitability_chips: f32,
    last_exploitability_chips: f32,
    chips_per_bb: f32,
    finalized: bool,
}

static GAMES: LazyLock<Mutex<HashMap<u32, GameState>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
static NEXT_HANDLE: LazyLock<Mutex<u32>> = LazyLock::new(|| Mutex::new(1));
static LAST_ERROR: LazyLock<Mutex<String>> = LazyLock::new(|| Mutex::new(String::new()));

// ---------------------------------------------------------------------------
// Error helpers
// ---------------------------------------------------------------------------

fn set_error(msg: impl Into<String>) {
    if let Ok(mut slot) = LAST_ERROR.lock() {
        *slot = msg.into();
    }
}

fn clear_error() {
    if let Ok(mut slot) = LAST_ERROR.lock() {
        slot.clear();
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Initialise a new game from an envelope JSON string.
///
/// Returns the handle on success (always > 0). On failure returns `0` and
/// stashes the reason in [`last_error`].
#[wasm_bindgen]
pub fn init_game(scenario_json: &str) -> u32 {
    clear_error();
    match init_game_inner(scenario_json) {
        Ok(handle) => handle,
        Err(e) => {
            set_error(format!("init_game: {e}"));
            0
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
struct InitOverrides {
    #[serde(default)]
    max_iterations: Option<u32>,
    #[serde(default)]
    target_exploitability_bb: Option<f32>,
}

pub fn init_game_inner(scenario_json: &str) -> Result<u32, String> {
    preflight_inner(scenario_json)?;

    let envelope: ScenarioEnvelope = serde_json::from_str(scenario_json)
        .map_err(|e| format!("envelope JSON parse error: {e}"))?;

    // Optional knobs that travel alongside the envelope.
    let overrides: InitOverrides = serde_json::from_str(scenario_json).unwrap_or_default();

    let max_iterations = overrides
        .max_iterations
        .unwrap_or(DEFAULT_MAX_ITERATIONS)
        .max(1);
    let target_bb = overrides
        .target_exploitability_bb
        .unwrap_or(DEFAULT_TARGET_EXPLOITABILITY_BB)
        .max(0.0);

    let game = build_game(&envelope)?;
    let chips_per_bb = CHIPS_PER_BB as f32;
    let target_chips = target_bb * chips_per_bb;
    // Defer the first exploitability scan until the first solve_step milestone.
    // `compute_exploitability` on a freshly built tree is as expensive as several
    // CFR iterations and dominates init latency on wide-range envelopes.
    let initial_expl = f32::INFINITY;

    let state = GameState {
        game,
        iterations: 0,
        max_iterations,
        target_exploitability_chips: target_chips,
        last_exploitability_chips: initial_expl,
        chips_per_bb,
        finalized: false,
    };

    let handle = {
        let mut next = NEXT_HANDLE.lock().map_err(|_| "handle mutex poisoned")?;
        let h = *next;
        *next = next.checked_add(1).ok_or("handle counter overflow")?;
        h
    };

    GAMES
        .lock()
        .map_err(|_| "games mutex poisoned")?
        .insert(handle, state);

    Ok(handle)
}

/// JSON document returned by [`solve_step`].
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolveProgress {
    pub handle: u32,
    pub iterations_done: u32,
    pub max_iterations: u32,
    pub exploitability_bb: f32,
    pub target_exploitability_bb: f32,
    pub finished: bool,
}

/// Run up to `max_iters_this_step` CFR iterations on `handle`, then return
/// progress as JSON.
#[wasm_bindgen]
pub fn solve_step(handle: u32, max_iters_this_step: u32) -> String {
    clear_error();
    match solve_step_inner(handle, max_iters_this_step) {
        Ok(json) => json,
        Err(e) => {
            set_error(format!("solve_step({handle}): {e}"));
            "{}".to_string()
        }
    }
}

pub fn solve_step_inner(handle: u32, max_iters_this_step: u32) -> Result<String, String> {
    let mut games = GAMES.lock().map_err(|_| "games mutex poisoned")?;
    let state = games
        .get_mut(&handle)
        .ok_or_else(|| format!("unknown handle: {handle}"))?;

    if !state.finalized {
        for _ in 0..max_iters_this_step {
            if state.iterations >= state.max_iterations {
                break;
            }
            if state.iterations > 0
                && state.last_exploitability_chips <= state.target_exploitability_chips
            {
                break;
            }
            cfr_step(&state.game, state.iterations);
            state.iterations += 1;
            // Recompute exploitability every 10 iterations (matches the
            // upstream `solve()` cadence and amortises cost).
            if state.iterations % 10 == 0 || state.iterations == state.max_iterations {
                state.last_exploitability_chips = compute_exploitability(&state.game);
            }
        }

        let reached_cap = state.iterations >= state.max_iterations;
        let converged = state.iterations > 0
            && state.last_exploitability_chips <= state.target_exploitability_chips;
        if reached_cap || converged {
            // Refresh once more so the final report is the post-finalize value.
            state.last_exploitability_chips = compute_exploitability(&state.game);
            finalize(&mut state.game);
            state.finalized = true;
        }
    }

    let progress = SolveProgress {
        handle,
        iterations_done: state.iterations,
        max_iterations: state.max_iterations,
        exploitability_bb: state.last_exploitability_chips / state.chips_per_bb,
        target_exploitability_bb: state.target_exploitability_chips / state.chips_per_bb,
        finished: state.finalized,
    };
    serde_json::to_string(&progress).map_err(|e| format!("serialise SolveProgress: {e}"))
}

/// Current exploitability for `handle`, in big blinds.
pub fn get_exploitability_inner(handle: u32) -> f32 {
    match GAMES.lock() {
        Ok(games) => match games.get(&handle) {
            Some(state) => state.last_exploitability_chips / state.chips_per_bb,
            None => f32::NAN,
        },
        Err(_) => f32::NAN,
    }
}

/// Current exploitability for `handle`, in big blinds.
#[wasm_bindgen]
pub fn get_exploitability(handle: u32) -> f32 {
    clear_error();
    let expl = get_exploitability_inner(handle);
    if expl.is_nan() {
        set_error(format!("get_exploitability: unknown handle {handle}"));
    }
    expl
}

/// Apply a history path (as `[usize, ...]` JSON) and return the
/// strategy/EV JSON doc for the resulting node.
#[wasm_bindgen]
pub fn export_strategy(handle: u32, history_path_json: &str) -> String {
    clear_error();
    match export_strategy_inner(handle, history_path_json) {
        Ok(json) => json,
        Err(e) => {
            set_error(format!("export_strategy({handle}): {e}"));
            "{}".to_string()
        }
    }
}

pub fn export_strategy_inner(handle: u32, history_path_json: &str) -> Result<String, String> {
    let history: Vec<usize> = if history_path_json.trim().is_empty() {
        Vec::new()
    } else {
        serde_json::from_str(history_path_json).map_err(|e| format!("history JSON parse: {e}"))?
    };

    let mut games = GAMES.lock().map_err(|_| "games mutex poisoned")?;
    let state = games
        .get_mut(&handle)
        .ok_or_else(|| format!("unknown handle: {handle}"))?;

    // `apply_history` re-walks from the root, so we can pass any history
    // shape (including the empty vec to request the root strategy).
    state.game.apply_history(&history);

    let export = build_export(ExportInput {
        game: &mut state.game,
        iterations: state.iterations,
        exploitability_chips: state.last_exploitability_chips,
        chips_per_bb: state.chips_per_bb,
        finalized: state.finalized,
    })?;

    serde_json::to_string(&export).map_err(|e| format!("serialise StrategyExport: {e}"))
}

/// Validate an envelope without allocating solver memory.
///
/// Rules shared with `backend/app/scenario/builder.py::validate_scenario_envelope`.
pub fn preflight_inner(envelope_json: &str) -> Result<(), String> {
    let envelope: ScenarioEnvelope = serde_json::from_str(envelope_json)
        .map_err(|e| format!("envelope JSON parse error: {e}"))?;

    if !envelope.pot_bb.is_finite() || envelope.pot_bb <= 0.0 {
        return Err(format!(
            "scenario invalid: pot_bb ({}) must be positive",
            envelope.pot_bb
        ));
    }
    if !envelope.effective_stack_bb.is_finite() || envelope.effective_stack_bb <= 0.0 {
        return Err(format!(
            "scenario invalid: effective_stack_bb ({}) must be positive",
            envelope.effective_stack_bb
        ));
    }

    const MIN_SPR: f64 = 0.5;
    let spr = envelope.effective_stack_bb / envelope.pot_bb;
    if spr < MIN_SPR {
        return Err(format!(
            "scenario invalid: SPR ({spr:.2}) is below minimum ({MIN_SPR:.1}); \
             solver may produce degenerate trees"
        ));
    }

    envelope.resolve_ranges()?;
    build_street_sizes(&envelope.bet_tree)?;
    bet_tree::validate_bet_tree_degeneracy(
        &envelope.bet_tree,
        envelope.pot_bb,
        envelope.effective_stack_bb,
    )?;

    Ok(())
}

/// Drop the game and release its arena. No-op if `handle` is unknown.
#[wasm_bindgen]
pub fn free_game(handle: u32) {
    if let Ok(mut games) = GAMES.lock() {
        games.remove(&handle);
    }
}

/// Returns the most recent error string from any of the above functions.
///
/// Every public function clears this slot before running, so a successful
/// call leaves it empty. The string persists across reads (callers can poll
/// it without losing the message).
#[wasm_bindgen]
pub fn last_error() -> String {
    LAST_ERROR
        .lock()
        .map(|s| s.clone())
        .unwrap_or_else(|_| "<error mutex poisoned>".into())
}

// ---------------------------------------------------------------------------
// Internals: envelope → PostFlopGame
// ---------------------------------------------------------------------------

fn build_game(envelope: &ScenarioEnvelope) -> Result<PostFlopGame, String> {
    let card_config = build_card_config(envelope)?;
    let tree_config = build_tree_config(envelope)?;
    let action_tree =
        ActionTree::new(tree_config).map_err(|e| format!("ActionTree::new: {e}"))?;
    let mut game = PostFlopGame::with_config(card_config, action_tree)
        .map_err(|e| format!("PostFlopGame::with_config: {e}"))?;
    // 32-bit float storage; compression saves memory but loses precision and
    // is overkill at v1 problem sizes.
    game.allocate_memory(false);
    Ok(game)
}

fn build_card_config(envelope: &ScenarioEnvelope) -> Result<CardConfig, String> {
    if envelope.board.len() < 3 || envelope.board.len() > 5 {
        return Err(format!(
            "board must have 3, 4, or 5 cards (got {})",
            envelope.board.len()
        ));
    }

    let flop_str: String = envelope.board[..3].join("");
    let flop = flop_from_str(&flop_str).map_err(|e| format!("flop parse {flop_str:?}: {e}"))?;

    let turn = if envelope.board.len() >= 4 {
        card_from_str(&envelope.board[3])
            .map_err(|e| format!("turn parse {:?}: {e}", envelope.board[3]))?
    } else {
        NOT_DEALT
    };

    let river = if envelope.board.len() == 5 {
        card_from_str(&envelope.board[4])
            .map_err(|e| format!("river parse {:?}: {e}", envelope.board[4]))?
    } else {
        NOT_DEALT
    };

    let ranges = envelope.resolve_ranges()?;
    let oop_range = range_from_hand_classes(&ranges.oop)
        .map_err(|e| format!("oop range: {e}"))?;
    let ip_range = range_from_hand_classes(&ranges.ip)
        .map_err(|e| format!("ip range: {e}"))?;

    Ok(CardConfig {
        range: [oop_range, ip_range],
        flop,
        turn,
        river,
    })
}

fn build_tree_config(envelope: &ScenarioEnvelope) -> Result<TreeConfig, String> {
    let starting_pot = bb_to_chips(envelope.pot_bb)?;
    let effective_stack = bb_to_chips(envelope.effective_stack_bb)?;
    if starting_pot < 1 {
        return Err(format!(
            "starting pot {} bb rounds to <1 chip; cannot solve",
            envelope.pot_bb
        ));
    }
    if effective_stack < 1 {
        return Err(format!(
            "effective stack {} bb rounds to <1 chip; cannot solve",
            envelope.effective_stack_bb
        ));
    }

    let initial_state = match envelope.board.len() {
        3 => BoardState::Flop,
        4 => BoardState::Turn,
        5 => BoardState::River,
        n => return Err(format!("unexpected board length {n}")),
    };

    let sizes = build_street_sizes(&envelope.bet_tree)?;
    Ok(TreeConfig {
        initial_state,
        starting_pot,
        effective_stack,
        rake_rate: 0.0,
        rake_cap: 0.0,
        flop_bet_sizes: [sizes.flop.clone(), sizes.flop],
        turn_bet_sizes: [sizes.turn.clone(), sizes.turn],
        river_bet_sizes: [sizes.river.clone(), sizes.river],
        turn_donk_sizes: bet_tree::default_donk_sizes(),
        river_donk_sizes: bet_tree::default_donk_sizes(),
        // `allin_always` is already encoded as an explicit `AllIn` bet in
        // `flop/turn/river_bet_sizes`, so we don't need the auxiliary
        // add_allin_threshold heuristic. Leave it disabled (0.0).
        add_allin_threshold: 0.0,
        // Force all-in when effectively-no-more-money — sensible default,
        // matches the upstream `basic.rs` example.
        force_allin_threshold: 0.15,
        // Don't merge close pot fractions — keep 33% and 75% distinct.
        merging_threshold: 0.0,
    })
}

fn bb_to_chips(bb: f64) -> Result<i32, String> {
    if !bb.is_finite() || bb < 0.0 {
        return Err(format!("invalid bb value: {bb}"));
    }
    let chips = (bb * CHIPS_PER_BB as f64).round();
    if chips > i32::MAX as f64 {
        return Err(format!("{bb} bb exceeds i32 chip range"));
    }
    Ok(chips as i32)
}

// ---------------------------------------------------------------------------
// Re-exports for native consumers (tests, benches, future Rust callers).
// ---------------------------------------------------------------------------

pub use envelope::{BetTreeConfig, ScenarioEnvelope as Envelope};
pub use strategy_export::{solver_version, StrategyExport};

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // Serialize tests that touch the global LAST_ERROR or GAMES map, so the
    // shared mutable state isn't observed mid-mutation by a parallel test.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn min_envelope_json() -> &'static str {
        r#"{
            "board": ["Kd","9c","Td"],
            "pot_bb": 12.5,
            "effective_stack_bb": 87.3,
            "oop_player": "BB",
            "ip_player": "BTN",
            "hero_position": "BTN",
            "hero_range": {"AKs": 1.0, "QQ": 1.0, "AKo": 0.5},
            "villain_range": {"22": 1.0, "AA": 1.0, "JTs": 1.0},
            "bet_tree": {
                "flop": ["33%","75%"],
                "turn": ["50%","100%"],
                "river": ["33%","75%","150%"],
                "allin_always": true
            }
        }"#
    }

    #[test]
    fn bb_to_chips_rounding() {
        assert_eq!(bb_to_chips(12.5).unwrap(), 1250);
        assert_eq!(bb_to_chips(87.3).unwrap(), 8730);
        assert_eq!(bb_to_chips(0.005).unwrap(), 1);
        assert!(bb_to_chips(-1.0).is_err());
    }

    #[test]
    fn init_returns_nonzero_handle_for_valid_envelope() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let h = init_game(min_envelope_json());
        let err = last_error();
        assert!(h != 0, "expected nonzero handle, got error: {err}");
        free_game(h);
    }

    #[test]
    fn init_returns_zero_on_bad_envelope() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let h = init_game("{\"board\": []}");
        assert_eq!(h, 0);
        // Read the error WHILE we still hold the test lock — otherwise a
        // parallel test could clear_error() between our call and the assert.
        assert!(!last_error().is_empty());
    }

    #[test]
    fn solver_version_includes_pin() {
        let v = solver_version();
        assert!(v.starts_with("postflop-solver@"));
    }
}
