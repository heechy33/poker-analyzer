//! End-to-end native test: parse the checked-in envelope, run a chunked
//! solve to convergence, and assert the export-JSON contract.
//!
//! Runs as a plain `cargo test` (native target) — see `solver-wasm/README.md`.
//! These checks are NOT a substitute for browser smoke-testing the wasm
//! bundle, but they catch the "subtle indexing / mapping is wrong" class of
//! bugs that the user-facing UI would otherwise paper over.
//!
//! Uses the inner (non-wasm_bindgen) functions directly so tests can run
//! on native targets without wasm-bindgen.

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::Deserialize;
use solver_wasm::{
    export_strategy_inner, free_game, get_exploitability_inner, init_game_inner, last_error,
    preflight_inner, solve_step_inner, StrategyExport,
};

/// Serializes tests that read `last_error()` or hold a handle long enough
/// for another test to observe stale global state. Each test acquires this
/// lock at the top.
static TEST_LOCK: Mutex<()> = Mutex::new(());

const STEP_SIZE: u32 = 10;
const MAX_STEPS: u32 = 30;
const EXPECTED_MAX_ITERATIONS: u32 = 60;
const REGRESSION_STEP_SIZE: u32 = 10;
const REGRESSION_MAX_ITERATIONS: u32 = 10;
const REGRESSION_MAX_SOLVE_CHUNKS: u32 = 2;

#[derive(Debug, Deserialize)]
struct ProgressDoc {
    handle: u32,
    iterations_done: u32,
    max_iterations: u32,
    exploitability_bb: f32,
    target_exploitability_bb: f32,
    finished: bool,
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
    let handle = init_game_inner(&envelope).expect("init_game should succeed");

    // Run chunked steps, recording the exploitability trajectory.
    let mut history: Vec<f32> = Vec::with_capacity(MAX_STEPS as usize);
    let mut last_progress: Option<ProgressDoc> = None;
    for _ in 0..MAX_STEPS {
        let progress_json = solve_step_inner(handle, STEP_SIZE).expect("solve_step should succeed");
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
    let later_min = history
        .iter()
        .skip(1)
        .copied()
        .fold(f32::INFINITY, f32::min);
    assert!(
        later_min <= first,
        "exploitability should drop below the initial value at least once; \
         first={first} later_min={later_min} history={history:?}"
    );

    // ────────────────────────────────────────────────────────────────────
    // export_strategy: empty history => root strategy doc
    // ────────────────────────────────────────────────────────────────────
    let strat_json = export_strategy_inner(handle, "").expect("export_strategy should succeed");
    let doc: StrategyExport = serde_json::from_str(&strat_json).unwrap_or_else(|e| {
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
    let expl = get_exploitability_inner(handle);
    assert!((expl - doc.exploitability_bb).abs() < 1e-3);

    free_game(handle);
}

#[test]
fn free_game_is_idempotent() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let envelope = load_envelope();
    let h = init_game_inner(&envelope).expect("init_game should succeed");
    free_game(h);
    free_game(h);

    // After free, solve_step should report an error.
    let result = solve_step_inner(h, 1);
    assert!(result.is_err(), "expected error after free, got {result:?}");
    assert!(
        result.unwrap_err().contains("unknown handle"),
        "expected 'unknown handle' in error message"
    );
}

#[test]
fn reinit_after_free_does_not_leak_old_state() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let envelope = load_envelope();
    let h1 = init_game_inner(&envelope).expect("init should succeed");
    free_game(h1);

    let h2 = init_game_inner(&envelope).expect("reinit should succeed");
    assert_ne!(h1, h2, "handles must be unique");
    free_game(h2);
}

#[test]
fn unknown_handle_surfaces_error() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let result = solve_step_inner(99_999, 1);
    assert!(
        result.is_err(),
        "expected error for unknown handle, got {result:?}"
    );
    assert!(
        result.unwrap_err().contains("unknown handle"),
        "expected 'unknown handle' in error message"
    );
}

// ---------------------------------------------------------------------------
// Regression fixtures — P2.3
// ---------------------------------------------------------------------------
// Each test loads a checked-in JSON fixture, runs preflight, then
// init_game, then (if expected to succeed) a capped 10-iteration smoke solve + export.
// ---------------------------------------------------------------------------

fn regression_fixture(name: &str) -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("regression")
        .join(name);
    fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "regression fixture {name} not found at {}: {e}",
            path.display()
        )
    })
}

fn regression_smoke_solve(envelope: &str, label: &str) -> u32 {
    preflight_inner(envelope).unwrap_or_else(|e| panic!("{label} preflight: {e}"));
    let handle = init_game_inner(envelope).unwrap_or_else(|e| panic!("{label} init: {e}"));

    let mut last_progress = None;
    for _ in 0..REGRESSION_MAX_SOLVE_CHUNKS {
        let progress_json = solve_step_inner(handle, REGRESSION_STEP_SIZE)
            .unwrap_or_else(|e| panic!("{label} solve_step: {e}"));
        let progress: ProgressDoc = serde_json::from_str(&progress_json)
            .unwrap_or_else(|e| panic!("{label} progress JSON parse failed: {e}"));
        assert_eq!(progress.handle, handle, "{label}");
        assert_eq!(
            progress.max_iterations, REGRESSION_MAX_ITERATIONS,
            "{label}"
        );
        assert_eq!(
            progress.target_exploitability_bb, 999.0,
            "{label} target_exploitability_bb"
        );
        assert!(progress.exploitability_bb.is_finite(), "{label}");
        let finished = progress.finished;
        last_progress = Some(progress);
        if finished {
            break;
        }
    }

    let progress = last_progress.unwrap_or_else(|| panic!("{label} solve_step did not run"));
    assert!(
        progress.finished || progress.iterations_done >= REGRESSION_MAX_ITERATIONS,
        "{label}: expected capped 10-iteration smoke solve, got {progress:?}"
    );

    let strat_json =
        export_strategy_inner(handle, "").unwrap_or_else(|e| panic!("{label} export: {e}"));
    let doc: StrategyExport = serde_json::from_str(&strat_json)
        .unwrap_or_else(|e| panic!("{label} strategy JSON parse failed: {e}"));
    assert!(!doc.actions.is_empty(), "{label}");

    handle
}

/// Cause 1 + 5: Degenerate all-in tree (SPR 1.1, allin_always=true, 3 bet sizes).
/// All river bet sizes + explicit all-in collapse to the same effective action.
/// Preflight MUST reject this with a degeneracy error.
#[test]
fn regression_degenerate_allin_tree_is_rejected() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());
    let envelope = regression_fixture("degenerate_allin_tree.json");

    // Preflight should catch the degeneracy.
    let result = preflight_inner(&envelope);
    assert!(
        result.is_err(),
        "degen allin tree should fail preflight, got {result:?}"
    );
    let err = result.unwrap_err();
    assert!(
        err.contains("degenerate") || err.contains("collapsed"),
        "expected degeneracy error, got: {err}"
    );

    // init_game should also reject it.
    let result = init_game_inner(&envelope);
    assert!(
        result.is_err(),
        "degen allin tree should fail init_game, got handle {result:?}"
    );
}

/// Load all *.json in tests/fixtures/regression/ and run:
/// preflight -> init_game -> 10-iter solve_step -> export_strategy without panic.
#[test]
fn regression_all_fixtures_dynamically() {
    let _g = TEST_LOCK.lock().unwrap_or_else(|p| p.into_inner());

    let regression_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("regression");

    let entries = fs::read_dir(&regression_dir)
        .unwrap_or_else(|e| panic!("failed to read regression fixtures dir: {e}"));

    let mut fixture_count = 0;
    let mut accepted_count = 0;
    for entry in entries {
        let entry = entry.unwrap();
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) == Some("json") {
            let filename = path.file_name().unwrap().to_str().unwrap();
            let envelope = fs::read_to_string(&path)
                .unwrap_or_else(|e| panic!("failed to read {filename}: {e}"));
            fixture_count += 1;

            println!("Testing regression fixture: {filename}");

            // Run preflight
            let preflight_res = preflight_inner(&envelope);

            // Handle expected failures
            if filename.contains("degenerate_allin_tree") {
                assert!(preflight_res.is_err(), "{filename} should fail preflight");
                assert!(
                    init_game_inner(&envelope).is_err(),
                    "{filename} should fail init_game"
                );
                accepted_count += 1;
                continue;
            }

            if filename.contains("empty_range_after_removal") {
                match preflight_res {
                    Ok(_) => match init_game_inner(&envelope) {
                        Ok(handle) => {
                            free_game(handle);
                            let handle = regression_smoke_solve(&envelope, filename);
                            free_game(handle);
                        }
                        Err(e) => {
                            assert!(
                                e.contains("range") || e.contains("empty") || e.contains("invalid"),
                                "expected range/invalid error for {filename}, got: {e}"
                            );
                        }
                    },
                    Err(e) => {
                        assert!(
                            e.contains("range") || e.contains("empty") || e.contains("invalid"),
                            "expected range/invalid preflight error for {filename}, got: {e}"
                        );
                    }
                }
                accepted_count += 1;
                continue;
            }

            preflight_res.unwrap_or_else(|e| panic!("{filename} failed preflight: {e}"));
            let handle = regression_smoke_solve(&envelope, filename);
            free_game(handle);
            accepted_count += 1;
        }
    }

    assert!(
        fixture_count >= 20,
        "expected at least 20 regression fixtures, found {fixture_count}"
    );
    assert!(
        accepted_count >= 20,
        "expected at least 20 accepted regression fixtures, found {accepted_count}"
    );
}
