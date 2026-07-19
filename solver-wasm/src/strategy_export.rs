//! Build the legacy strategy-export JSON document.
//!
//! The backend cache endpoint and product range-grid caller were removed in
//! Phase 0. This serializer remains quarantined for low-level contract tests.
//!
//! # Mapping doc — how postflop-solver lays out its arrays (READ THIS!)
//!
//! At any non-terminal, non-chance node `N`:
//!
//! 1. **`PostFlopGame::available_actions()`** returns a `Vec<Action>` of length
//!    `A = N.num_actions()`. The action at index `i` is the action you would
//!    pass to `play(i)` to descend to its child. The order is FIXED for the
//!    lifetime of the game (it's the order the action tree was built in).
//!
//! 2. **`PostFlopGame::strategy()`** returns a flat `Vec<f32>` of length
//!    `A * H` where `H = num_private_hands(current_player)`. The layout is
//!    **row-major over actions**:
//!
//!    ```text
//!        strategy[i * H + j] = P(action i | hand j)
//!    ```
//!
//!    For each fixed hand `j`, `sum_i strategy[i * H + j] ≈ 1.0` (the only
//!    exceptions are hands that overlap the board, which return undefined
//!    values per the upstream docs).
//!
//! 3. **`PostFlopGame::expected_values_detail(player)`** returns a flat
//!    `Vec<f32>` of length `A * H` (when `player == current_player`), with
//!    the same row-major layout: `ev[i * H + j] = E[chips | action i, hand j]`.
//!    EV is in chips, biased by the upstream solver so the zero-point is the
//!    start-of-street stack neutral; we convert to bb by dividing by
//!    chips-per-bb.
//!
//! 4. **`PostFlopGame::private_cards(player)`** returns `&[(Card, Card)]` of
//!    length `H`, parallel-indexed with the columns of `strategy()`. Cards
//!    are `u8` encoded as `4 * rank + suit` where rank: `2..=A → 0..=12`,
//!    suit: `c=0, d=1, h=2, s=3`. We convert each pair to the canonical
//!    `"AsKh"`-style string via `postflop_solver::hole_to_string` (cards in
//!    descending id, suit lowercase).
//!
//! Action naming
//! -------------
//! We surface the postflop-solver `Action` variants under stable JSON keys:
//!
//! * `Action::Fold`     → `"fold"`
//! * `Action::Check`    → `"check"`
//! * `Action::Call`     → `"call"`
//! * `Action::Bet(c)`   → `"bet_<pct>"`   (pct = round(c / pot_at_node * 100))
//! * `Action::Raise(c)` → `"raise_<pct>"`
//! * `Action::AllIn(c)` → `"allin"`
//!
//! `pot_at_node` is `starting_pot + total_bet_amount[0] + total_bet_amount[1]`,
//! computed from the live `PostFlopGame` (`tree_config().starting_pot` and
//! `total_bet_amount()`). This matches the bet-tree percent the user
//! originally requested in the envelope (e.g. `"bet_33"` for a 33%-pot bet).

use std::collections::BTreeMap;

use postflop_solver::{hole_to_string, Action, BetSize, PostFlopGame};
use serde::{Deserialize, Serialize};

/// JSON document returned by `export_strategy`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyExport {
    /// Identifies the solver engine + commit pinned in `Cargo.toml`.
    pub solver_version: String,

    /// Total CFR iterations performed so far on this game.
    pub iterations: u32,

    /// Current exploitability in big blinds (lower = closer to GTO).
    pub exploitability_bb: f32,

    /// Whether `finalize()` has been called and EVs are valid.
    pub finalized: bool,

    /// 0 = OOP, 1 = IP. The player whose strategy is being returned.
    pub current_player: u8,

    /// Stable action names indexed in postflop-solver order
    /// (`available_actions()`). All combo strategy / EV objects use these
    /// keys.
    pub actions: Vec<String>,

    /// `combo_strategy[combo][action] = probability ∈ [0, 1]`. Per combo,
    /// probabilities sum to ≈1 (within float epsilon).
    pub combo_strategy: BTreeMap<String, BTreeMap<String, f32>>,

    /// `combo_ev[combo][action] = EV in big blinds`. Empty if game not yet
    /// finalized (EVs are only valid post-`finalize()`).
    pub combo_ev: BTreeMap<String, BTreeMap<String, f32>>,

    /// Range-aggregated action frequencies, weighted by `normalized_weights`.
    /// `aggregate_frequencies[action] = combo-weighted-mean P(action)`.
    pub aggregate_frequencies: BTreeMap<String, f32>,
}

pub fn solver_version() -> String {
    // The build script (`build.rs`) reads `git rev-parse HEAD` in
    // `../postflop-solver` and sets `SOLVER_WASM_ENGINE_SHA`. Pinned in
    // PLAN.md §0.2 (April-2026 community-fix commit). Falls back to
    // `"unknown"` if the build script couldn't resolve the sha.
    let sha = option_env!("SOLVER_WASM_ENGINE_SHA").unwrap_or("unknown");
    format!("postflop-solver@{sha}")
}

pub struct ExportInput<'a> {
    pub game: &'a mut PostFlopGame,
    pub iterations: u32,
    pub exploitability_chips: f32,
    pub chips_per_bb: f32,
    pub finalized: bool,
}

pub fn build_export(input: ExportInput<'_>) -> Result<StrategyExport, String> {
    let ExportInput {
        game,
        iterations,
        exploitability_chips,
        chips_per_bb,
        finalized,
    } = input;

    if game.is_terminal_node() {
        return Err("cannot export strategy at a terminal node".to_string());
    }
    if game.is_chance_node() {
        return Err("cannot export strategy at a chance node (apply a card history first)".into());
    }

    // Always recompute normalized weights — the current node may have moved
    // since the last call.
    game.cache_normalized_weights();

    let current_player = game.current_player();
    let combos = game.private_cards(current_player).to_vec();
    let h = combos.len();
    if h == 0 {
        return Err("current player has zero private hands at this node".into());
    }

    let actions = game.available_actions();
    let a = actions.len();
    if a == 0 {
        return Err("node has zero available actions".into());
    }

    let (action_names, _) = actions_at_current_node(game);

    let strategy = game.strategy();
    if strategy.len() != a * h {
        return Err(format!(
            "unexpected strategy buffer length: got {}, expected {} * {}",
            strategy.len(),
            a,
            h
        ));
    }

    // EVs only exist for solved games (otherwise expected_values_detail
    // panics). Pre-finalize we leave combo_ev empty.
    let evs_chips = if finalized {
        let detail = game.expected_values_detail(current_player);
        if detail.len() == a * h {
            Some(detail)
        } else if detail.len() == h {
            // We sit at a node where the current player isn't player; the
            // engine returns just the marginal EVs per hand. Skip per-action.
            None
        } else {
            None
        }
    } else {
        None
    };

    let normalized = game.normalized_weights(current_player).to_vec();
    let mut total_weight: f64 = 0.0;
    let mut action_weight: Vec<f64> = vec![0.0; a];

    let mut combo_strategy: BTreeMap<String, BTreeMap<String, f32>> = BTreeMap::new();
    let mut combo_ev: BTreeMap<String, BTreeMap<String, f32>> = BTreeMap::new();

    for (j, &(c1, c2)) in combos.iter().enumerate() {
        let combo_str = hole_to_string((c1, c2)).map_err(|e| format!("hole_to_string: {e}"))?;
        let mut combo_row: BTreeMap<String, f32> = BTreeMap::new();
        let mut ev_row: BTreeMap<String, f32> = BTreeMap::new();

        let w_norm = normalized.get(j).copied().unwrap_or(0.0) as f64;
        total_weight += w_norm.max(0.0);

        for (i, name) in action_names.iter().enumerate() {
            let p = strategy[i * h + j];
            combo_row.insert(name.clone(), p);
            action_weight[i] += w_norm.max(0.0) * p as f64;

            if let Some(evs) = &evs_chips {
                let ev_chips = evs[i * h + j];
                ev_row.insert(name.clone(), ev_chips / chips_per_bb);
            }
        }

        combo_strategy.insert(combo_str.clone(), combo_row);
        if evs_chips.is_some() {
            combo_ev.insert(combo_str, ev_row);
        }
    }

    let mut aggregate_frequencies: BTreeMap<String, f32> = BTreeMap::new();
    if total_weight > 0.0 {
        for (i, name) in action_names.iter().enumerate() {
            aggregate_frequencies.insert(name.clone(), (action_weight[i] / total_weight) as f32);
        }
    } else {
        for name in &action_names {
            aggregate_frequencies.insert(name.clone(), 0.0);
        }
    }

    Ok(StrategyExport {
        solver_version: solver_version(),
        iterations,
        exploitability_bb: exploitability_chips / chips_per_bb,
        finalized,
        current_player: current_player as u8,
        actions: action_names,
        combo_strategy,
        combo_ev,
        aggregate_frequencies,
    })
}

/// `starting_pot + sum(total_bet_amount)` reported by the engine at the
/// current node. Used to back out pot-percent labels for `Bet`/`Raise`.
pub(crate) fn actions_at_current_node(game: &PostFlopGame) -> (Vec<String>, i32) {
    let pot_at_node = pot_at_current_node(game);
    let action_names = game
        .available_actions()
        .iter()
        .map(|action| action_label(action, pot_at_node))
        .collect();
    (action_names, pot_at_node)
}

fn pot_at_current_node(game: &PostFlopGame) -> i32 {
    let starting_pot = game.tree_config().starting_pot;
    let total = game.total_bet_amount();
    starting_pot + total[0] + total[1]
}

fn action_label(action: &Action, pot_chips: i32) -> String {
    match action {
        Action::Fold => "fold".to_string(),
        Action::Check => "check".to_string(),
        Action::Call => "call".to_string(),
        Action::AllIn(_) => "allin".to_string(),
        Action::Bet(amount) => format!("bet_{}", pct_label(*amount, pot_chips)),
        Action::Raise(amount) => format!("raise_{}", pct_label(*amount, pot_chips)),
        Action::Chance(card) => format!("chance_{card}"),
        Action::None => "none".to_string(),
    }
}

fn pct_label(amount: i32, pot: i32) -> String {
    if pot <= 0 {
        // Fallback to the raw chip amount when pot is degenerate.
        return format!("{amount}c");
    }
    let pct = ((amount as f64) / (pot as f64) * 100.0).round() as i64;
    pct.to_string()
}

/// Helper exported for the action_tree size lookup (used by tests).
#[allow(dead_code)]
pub fn label_bet(size: &BetSize) -> &'static str {
    match size {
        BetSize::PotRelative(_) => "pot_relative",
        BetSize::PrevBetRelative(_) => "prev_bet_relative",
        BetSize::Additive(_, _) => "additive",
        BetSize::Geometric(_, _) => "geometric",
        BetSize::AllIn => "allin",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_pct_rounds() {
        assert_eq!(pct_label(33, 100), "33");
        assert_eq!(pct_label(50, 200), "25");
        assert_eq!(pct_label(150, 100), "150");
    }

    #[test]
    fn label_pct_degenerate_pot_falls_back_to_chips() {
        assert_eq!(pct_label(100, 0), "100c");
    }

    #[test]
    fn action_labels_are_stable() {
        assert_eq!(action_label(&Action::Fold, 100), "fold");
        assert_eq!(action_label(&Action::Check, 100), "check");
        assert_eq!(action_label(&Action::Call, 100), "call");
        assert_eq!(action_label(&Action::AllIn(900), 200), "allin");
        assert_eq!(action_label(&Action::Bet(33), 100), "bet_33");
        assert_eq!(action_label(&Action::Bet(75), 100), "bet_75");
        assert_eq!(action_label(&Action::Raise(250), 100), "raise_250");
    }
}
