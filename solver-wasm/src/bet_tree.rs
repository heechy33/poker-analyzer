//! Convert the backend `bet_tree` block into `postflop_solver::BetSizeOptions`.
//!
//! Backend contract (from `app/scenario/builder.py`):
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
//! Raise sizing
//! ------------
//! The backend envelope does not enumerate raise sizes (this matches the
//! wasm-postflop reference: a single `2.5x` PrevBetRelative covers ~all
//! realistic spots). We use `"2.5x"` for raises uniformly. This is the same
//! default the upstream `basic.rs` example uses.

use postflop_solver::{BetSizeOptions, DonkSizeOptions};

use crate::envelope::BetTreeConfig;

const DEFAULT_RAISE_SIZES: &str = "2.5x";

#[derive(Debug, Clone)]
pub struct StreetSizes {
    pub flop: BetSizeOptions,
    pub turn: BetSizeOptions,
    pub river: BetSizeOptions,
}

pub fn build_street_sizes(cfg: &BetTreeConfig) -> Result<StreetSizes, String> {
    Ok(StreetSizes {
        flop: build_one(&cfg.flop, cfg.allin_always, "flop")?,
        turn: build_one(&cfg.turn, cfg.allin_always, "turn")?,
        river: build_one(&cfg.river, cfg.allin_always, "river")?,
    })
}

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
}
