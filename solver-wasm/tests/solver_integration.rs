//! End-to-end native test: parse the checked-in envelope, run a chunked
//! solve to convergence, and assert the export-JSON contract.
//!
//! Runs as a plain `cargo test` (native target) — see `solver-wasm/README.md`.
//! These checks are NOT a substitute for browser smoke-testing the wasm
//! bundle, but they catch the "subtle indexing / mapping is wrong" class of
//! bugs that the user-facing UI would otherwise paper over.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::Deserialize;
use solver_wasm::{
    export_strategy, free_game, get_exploitability, init_game, last_error, solve_step,
};

/// Serializes tests that read `last_error()` or hold a handle long enough
/// for another test to observe stale global state. Each test acquires this
/// lock at the top.
static TEST_LOCK: Mutex<()> = Mutex::new(());

const STEP_SIZE: u32 = 10;
const MAX_STEPS: u32 = 30;
const EXPECTED_MAX_ITERATIONS: u32 = 60;

#[derive(Debug, Deserialize)]
struct ProgressDoc {
    handle: u32,
    iterations_done: u32,
    max_iterations: u32,
    exploitability_bb: f32,
    target_exploitability_bb: f32,
    finished: bool,
}

#[derive(Debug, Deserialize)]
struct StrategyDoc {
    solver_version: String,
    iterations: u32,
    exploitability_bb: f32,
    finalized: bool,
    current_player: u8,
    actions: Vec<String>,
    combo_strategy: BTreeMap<String, BTreeMap<String, f32>>,
    combo_ev: BTreeMap<String, BTreeMap<String, f32>>,
    aggregate_frequencies: BTreeMap<String, f32>,
}

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("scenario_min.json")
}

fn load_envelope() -> String {
    fs::read_to_string(fixture_path()).expect("scenario_min.json must exist")
}

#[test]
fn init_solve_export_roundtrip() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let envelope = load_envelope();
    let handle = init_game(&envelope);
    assert!(
        handle != 0,
        "init_game failed: {err}",
        err = last_error()
    );

    // Run chunked steps, recording the exploitability trajectory.
    let mut history: Vec<f32> = Vec::with_capacity(MAX_STEPS as usize);
    let mut last_progress: Option<ProgressDoc> = None;
    for _ in 0..MAX_STEPS {
        let progress_json = solve_step(handle, STEP_SIZE);
        let progress: ProgressDoc = serde_json::from_str(&progress_json).unwrap_or_else(|e| {
            panic!(
                "progress JSON parse failed: {e} (raw={progress_json}, last_error={})",
                last_error()
            )
        });
        assert_eq!(progress.handle, handle);
        assert!(progress.max_iterations == EXPECTED_MAX_ITERATIONS);
        assert!(progress.exploitability_bb.is_finite());
        history.push(progress.exploitability_bb);
        if progress.finished {
            last_progress = Some(progress);
            break;
        }
        last_progress = Some(progress);
    }

    let progress = last_progress.expect("at least one solve step should have run");
    assert!(
        progress.finished,
        "solver did not finalize within {MAX_STEPS} chunks; \
         iters_done={done}, last_expl_bb={expl}",
        done = progress.iterations_done,
        expl = progress.exploitability_bb,
    );
    assert!(
        progress.iterations_done <= progress.max_iterations,
        "iterations exceeded cap"
    );

    // Convergence trend: the exploitability should not be monotonically
    // increasing. We require *some* sample below the first one — strict
    // monotonicity is too brittle (CFR exploitability oscillates).
    assert!(history.len() >= 2, "need at least 2 progress samples");
    let first = history[0];
    let later_min = history.iter().skip(1).copied().fold(f32::INFINITY, f32::min);
    assert!(
        later_min <= first,
        "exploitability should drop below the initial value at least once; \
         first={first} later_min={later_min} history={history:?}"
    );

    // ────────────────────────────────────────────────────────────────────
    // export_strategy: empty history => root strategy doc
    // ────────────────────────────────────────────────────────────────────
    let strat_json = export_strategy(handle, "");
    let doc: StrategyDoc = serde_json::from_str(&strat_json).unwrap_or_else(|e| {
        panic!(
            "strategy JSON parse failed: {e} (raw={strat_json}, last_error={})",
            last_error()
        )
    });

    assert!(
        doc.solver_version.starts_with("postflop-solver@"),
        "solver_version should reference the engine pin, got {:?}",
        doc.solver_version
    );
    assert!(doc.finalized, "export should mark as finalized");
    assert_eq!(doc.iterations, progress.iterations_done);
    assert!(doc.exploitability_bb.is_finite());
    assert!(
        doc.current_player == 0 || doc.current_player == 1,
        "current_player must be OOP(0) or IP(1)"
    );
    assert!(!doc.actions.is_empty(), "root node should have actions");
    assert!(
        !doc.combo_strategy.is_empty(),
        "combo_strategy must list at least one private hand"
    );

    // Per-combo strategy sanity: probabilities ≈ 1, all actions present.
    for (combo, row) in &doc.combo_strategy {
        assert_eq!(
            row.len(),
            doc.actions.len(),
            "{combo}: row keys count != actions count"
        );
        let total: f32 = row.values().copied().sum();
        assert!(
            (total - 1.0).abs() < 1e-3,
            "{combo}: probabilities sum to {total}, expected ~1.0",
        );
        for (name, p) in row {
            assert!(
                (0.0..=1.0001).contains(p),
                "{combo}.{name}: prob {p} outside [0, 1]"
            );
        }
    }

    // EVs: when finalized, every combo should have a full ev row.
    assert_eq!(
        doc.combo_ev.len(),
        doc.combo_strategy.len(),
        "combo_ev should mirror combo_strategy when finalized"
    );

    // Aggregate frequencies: well-formed and bounded.
    assert_eq!(
        doc.aggregate_frequencies.len(),
        doc.actions.len(),
        "aggregate_frequencies must list every action"
    );
    let agg_total: f32 = doc.aggregate_frequencies.values().copied().sum();
    assert!(
        (agg_total - 1.0).abs() < 1e-2,
        "aggregate_frequencies sum to {agg_total}, expected ~1.0"
    );

    // get_exploitability mirrors the doc number.
    let expl = get_exploitability(handle);
    assert!((expl - doc.exploitability_bb).abs() < 1e-3);

    free_game(handle);
}

#[test]
fn free_game_is_idempotent() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let envelope = load_envelope();
    let h = init_game(&envelope);
    assert!(h != 0);
    free_game(h);
    free_game(h);

    // After free, solve_step should report an unknown-handle error.
    let progress_json = solve_step(h, 1);
    assert_eq!(progress_json, "{}");
    assert!(last_error().contains("unknown handle"));
}

#[test]
fn reinit_after_free_does_not_leak_old_state() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let envelope = load_envelope();
    let h1 = init_game(&envelope);
    assert!(h1 != 0);
    free_game(h1);

    let h2 = init_game(&envelope);
    assert!(h2 != 0);
    assert_ne!(h1, h2, "handles must be unique");
    free_game(h2);
}

#[test]
fn unknown_handle_surfaces_error() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let progress_json = solve_step(99_999, 1);
    assert_eq!(progress_json, "{}");
    assert!(last_error().contains("unknown handle"));
}
