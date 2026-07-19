//! Convert the quarantined legacy `bet_tree` block into
//! `postflop_solver::BetSizeOptions`.
//!
//! Deleted legacy contract:
//!
//! ```text
//! bet_tree = {
//!     "flop":  ["33%", "75%"],
//!     "turn":  ["50%", "100%"],
//!     "river": ["33%", "75%", "150%"],
//!     "allin_always": true,
//! }
//! ```
//!
//! Each percent string is consumed *as-is* by postflop-solver's
//! `BetSize::try_from`, which understands `"75%"` → `BetSize::PotRelative(0.75)`.
//! If `allin_always = true`, we append the engine's `"a"` token so AllIn is
//! a legal action at every decision node, matching the v1 PLAN.md spec.
//!
//! # P2.1 — Dedup by effective chip amount
//!
//! The upstream solver deduplicates by `BetSize` variant (e.g. two `"33%"` →
//! one `PotRelative(0.33)`), but does *not* deduplicate by the actual chip
//! amounts after applying pot scaling.  At shallow SPR, `"33%"` and `"75%"`
//! may both compute to the same chip value.  We detect this here and collapse
//! to a single size + all-in if the caller allows degenerate-to-allin
//! simplification.
//!
//! Raise sizing
//! ------------
//! The backend envelope does not enumerate raise sizes (this matches the
//! wasm-postflop reference: a single `2.5x` PrevBetRelative covers ~all
//! realistic spots). We use `"2.5x"` for raises uniformly. This is the same
//! default the upstream `basic.rs` example uses.

use std::collections::BTreeSet;

use postflop_solver::{BetSizeOptions, DonkSizeOptions};

use crate::envelope::BetTreeConfig;

const DEFAULT_RAISE_SIZES: &str = "2.5x";

/// Chips-per-big-blind constant (must match `CHIPS_PER_BB` in `lib.rs`).
const CHIPS_PER_BB: i64 = 100;

/// Force-allin threshold: if a bet uses ≥ this fraction of effective stack,
/// the solver forces it to all-in.  Must match `force_allin_threshold` in
/// `lib.rs` `build_tree_config`.  Keep in sync.
const FORCE_ALLIN_THRESHOLD: f64 = 0.15;

#[derive(Debug, Clone)]
pub struct StreetSizes {
    pub flop: BetSizeOptions,
    pub turn: BetSizeOptions,
    pub river: BetSizeOptions,
}

/// Build betting options for all three streets.
///
/// `pot_chips` and `eff_chips` are used for chip-level dedup (P2.1).
pub fn build_street_sizes(cfg: &BetTreeConfig) -> Result<StreetSizes, String> {
    Ok(StreetSizes {
        flop: build_one(&cfg.flop, cfg.allin_always, "flop")?,
        turn: build_one(&cfg.turn, cfg.allin_always, "turn")?,
        river: build_one(&cfg.river, cfg.allin_always, "river")?,
    })
}

/// Build betting options for one street.
fn build_one(
    sizes: &[String],
    allin_always: bool,
    street: &str,
) -> Result<BetSizeOptions, String> {
    let mut bet_tokens: Vec<String> = sizes.iter().map(|s| s.trim().to_string()).collect();
    bet_tokens.retain(|s| !s.is_empty());

    if allin_always {
        // The "a" token deduplicates implicitly via BetSizeOptions::try_from
        // sort+dedup, but the upstream parser preserves AllIn alongside other
        // sizes — append unconditionally.
        if !bet_tokens.iter().any(|s| s.eq_ignore_ascii_case("a")) {
            bet_tokens.push("a".to_string());
        }
    }

    if bet_tokens.is_empty() {
        return Err(format!(
            "bet_tree.{street} is empty and allin_always is false; \
             postflop-solver requires at least one bet option"
        ));
    }

    let bet_str = bet_tokens.join(",");
    BetSizeOptions::try_from((bet_str.as_str(), DEFAULT_RAISE_SIZES))
        .map_err(|e| format!("postflop-solver rejected {street} sizes {bet_str:?}: {e}"))
}

/// Validate that no street collapses to a single effective bet size when
/// `allin_always` is true. This preserves a low-level invariant from the
/// deleted legacy scenario builder; it is not the rebuilt product contract.
pub fn validate_bet_tree_degeneracy(
    cfg: &BetTreeConfig,
    pot_bb: f64,
    eff_bb: f64,
) -> Result<(), String> {
    let pot_chips = bb_to_chips_i32(pot_bb)?;
    let eff_chips = bb_to_chips_i32(eff_bb)?;
    let threshold_chips = (eff_chips as f64 * FORCE_ALLIN_THRESHOLD).round() as i32;

    for (street_name, sizes) in [("flop", &cfg.flop), ("turn", &cfg.turn), ("river", &cfg.river)]
    {
        let mut distinct_effective: BTreeSet<i32> = BTreeSet::new();
        for token in sizes {
            let Some(mut chips) = raw_bet_token_to_chips(token, pot_chips, eff_chips) else {
                continue;
            };
            if chips >= threshold_chips {
                chips = eff_chips;
            }
            distinct_effective.insert(chips);
        }

        if distinct_effective.is_empty() {
            if !cfg.allin_always {
                return Err(format!(
                    "degenerate bet tree: {street_name} has no bet sizes and allin_always is false"
                ));
            }
            continue;
        }

        if distinct_effective.len() <= 1 && cfg.allin_always {
            let spr = eff_bb / pot_bb;
            return Err(format!(
                "degenerate bet tree: all {street_name} bet sizes collapse to the same \
                 effective chip amount ({distinct_effective:?}) with allin_always; \
                 SPR is too shallow for this tree (pot={pot_bb:.1}bb eff={eff_bb:.1}bb SPR={spr:.2})"
            ));
        }
    }

    Ok(())
}

fn bb_to_chips_i32(bb: f64) -> Result<i32, String> {
    if !bb.is_finite() || bb < 0.0 {
        return Err(format!("invalid bb value: {bb}"));
    }
    let chips = (bb * CHIPS_PER_BB as f64).round();
    if chips > i32::MAX as f64 {
        return Err(format!("{bb} bb exceeds i32 chip range"));
    }
    Ok(chips as i32)
}

/// Raw pot-fraction → chips without applying `force_allin_threshold`.
fn raw_bet_token_to_chips(token: &str, pot_chips: i32, eff_chips: i32) -> Option<i32> {
    let s = token.trim();
    if s.eq_ignore_ascii_case("a") {
        return Some(eff_chips);
    }
    if let Some(pct_str) = s.strip_suffix('%') {
        let pct: f64 = pct_str.parse().ok()?;
        if !pct.is_finite() || pct <= 0.0 {
            return None;
        }
        return Some((pot_chips as f64 * pct / 100.0).round() as i32);
    }
    None
}

/// Compute the effective chip amount for a bet-size token at a given pot/stack.
///
/// Returns `None` if the token cannot be parsed.
fn bet_token_to_effective_chips(token: &str, pot_chips: i32, eff_chips: i32) -> Option<i32> {
    let s = token.trim();
    if s.eq_ignore_ascii_case("a") {
        return Some(eff_chips);
    }
    if let Some(pct_str) = s.strip_suffix('%') {
        let pct: f64 = pct_str.parse().ok()?;
        if !pct.is_finite() || pct <= 0.0 {
            return None;
        }
        let chips = (pot_chips as f64 * pct / 100.0).round() as i32;
        // Apply force_allin_threshold: if the bet uses ≥ threshold of
        // effective stack, it becomes an effective all-in.
        if chips as f64 >= eff_chips as f64 * FORCE_ALLIN_THRESHOLD {
            return Some(eff_chips);
        }
        return Some(chips);
    }
    None
}

/// Compute the number of distinct effective chip amounts among `tokens`.
///
/// Returns 0 if none parse.
pub fn distinct_effective_sizes(tokens: &[String]) -> usize {
    let mut chips_set: BTreeSet<i32> = BTreeSet::new();
    // Use placeholder pot/stack of 1000/1000 — the count of distinct values
    // is what matters, not the absolute values.  Two different pot-% strings
    // produce different ratios at any pot, so we just need a non-zero pot.
    let pot = 1000i32;
    let eff = 1000i32;
    for t in tokens {
        if let Some(chips) = bet_token_to_effective_chips(t, pot, eff) {
            chips_set.insert(chips);
        }
    }
    chips_set.len()
}

/// We never use donk sizes (v1 keeps the tree simple); both turn/river donk
/// stay at engine defaults (`None`).
pub fn default_donk_sizes() -> Option<DonkSizeOptions> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use postflop_solver::BetSize;

    fn cfg() -> BetTreeConfig {
        BetTreeConfig {
            flop: vec!["33%".into(), "75%".into()],
            turn: vec!["50%".into(), "100%".into()],
            river: vec!["33%".into(), "75%".into(), "150%".into()],
            allin_always: true,
        }
    }

    #[test]
    fn flop_includes_allin_and_pot_relatives() {
        let sizes = build_street_sizes(&cfg()).unwrap();
        let has_allin = sizes.flop.bet.iter().any(|b| matches!(b, BetSize::AllIn));
        assert!(has_allin, "allin_always should append AllIn");

        let pot_relatives: Vec<f64> = sizes
            .flop
            .bet
            .iter()
            .filter_map(|b| match b {
                BetSize::PotRelative(f) => Some(*f),
                _ => None,
            })
            .collect();
        assert!(pot_relatives.contains(&0.33));
        assert!(pot_relatives.contains(&0.75));
    }

    #[test]
    fn river_handles_overbet() {
        let sizes = build_street_sizes(&cfg()).unwrap();
        let pot_relatives: Vec<f64> = sizes
            .river
            .bet
            .iter()
            .filter_map(|b| match b {
                BetSize::PotRelative(f) => Some(*f),
                _ => None,
            })
            .collect();
        assert!(pot_relatives.contains(&1.5));
    }

    #[test]
    fn allin_always_false_does_not_append() {
        let mut c = cfg();
        c.allin_always = false;
        let sizes = build_street_sizes(&c).unwrap();
        let has_allin = sizes.flop.bet.iter().any(|b| matches!(b, BetSize::AllIn));
        assert!(!has_allin);
    }

    #[test]
    fn empty_street_without_allin_errors() {
        let c = BetTreeConfig {
            flop: vec![],
            turn: vec!["50%".into()],
            river: vec!["75%".into()],
            allin_always: false,
        };
        assert!(build_street_sizes(&c).is_err());
    }

    #[test]
    fn empty_street_with_allin_is_ok() {
        let c = BetTreeConfig {
            flop: vec![],
            turn: vec!["50%".into()],
            river: vec!["75%".into()],
            allin_always: true,
        };
        let s = build_street_sizes(&c).unwrap();
        let has_allin = s.flop.bet.iter().any(|b| matches!(b, BetSize::AllIn));
        assert!(has_allin);
    }

    // -----------------------------------------------------------------------
    // P2.1 — dedup by effective chip amount
    // -----------------------------------------------------------------------

    #[test]
    fn distinct_effective_sizes_counts_correctly() {
        // "5%" of 1000 = 50 chips → < 150 threshold → stays 50.
        // "12%" of 1000 = 120 chips → < 150 threshold → stays 120.
        // These are two distinct effective sizes.
        let tokens: Vec<String> = vec!["5%".into(), "12%".into()];
        assert_eq!(distinct_effective_sizes(&tokens), 2);
    }

    #[test]
    fn distinct_effective_sizes_collapses_identical() {
        // Two identical "33%" entries → 1 distinct size.
        let tokens: Vec<String> = vec!["33%".into(), "33%".into()];
        assert_eq!(distinct_effective_sizes(&tokens), 1);
    }

    #[test]
    fn distinct_effective_sizes_allin_already_collapsed() {
        // With pot=1000, eff=1000, 15% = 150 threshold.
        // 33% → 330, 50% → 500, 75% → 750, 100% → 1000
        // All ≥ 150 → all map to eff_chips (1000).
        // So only 1 distinct effective size.
        let tokens: Vec<String> = vec![
            "33%".into(),
            "50%".into(),
            "75%".into(),
            "100%".into(),
        ];
        assert_eq!(distinct_effective_sizes(&tokens), 1);
    }

    #[test]
    fn distinct_effective_sizes_one_above_one_below_threshold() {
        // pot=1000, eff=1000, threshold=150.
        // 5% → 50 chips (< 150, stays at 50)
        // 75% → 750 chips (≥ 150, forced to 1000 = eff_chips)
        // 50 vs 1000 → 2 distinct.
        let tokens: Vec<String> = vec!["5%".into(), "75%".into()];
        assert_eq!(distinct_effective_sizes(&tokens), 2);
    }
}
