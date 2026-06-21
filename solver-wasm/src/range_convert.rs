//! Convert backend hand-class weight maps into `postflop_solver::Range`.
//!
//! Backend convention
//! ------------------
//! The Python builder emits per-hand-class weights in PIO-style canonical
//! form:
//!
//! * **Pairs**: `"AA"`, `"TT"`, `"22"` (2 characters, identical ranks).
//! * **Suited**: `"AKs"`, `"JTs"` (high rank, low rank, `'s'`).
//! * **Offsuit**: `"AKo"`, `"JTo"` (high rank, low rank, `'o'`).
//!
//! These are exactly the singleton chunks `postflop_solver`'s `Range`
//! parser accepts, so we build a comma-separated range string with
//! `class[:weight]` per entry and hand it to `parse::<Range>()`.
//!
//! Weights outside `(0, 1]` are clamped or skipped: zero/negative entries are
//! omitted (so the combo stays at 0.0) and weights above 1.0 are clipped to
//! 1.0 since the engine rejects out-of-range weights at parse time.
//!
//! The conversion is "compatible with the public `Range` API only" — we do
//! NOT touch `Range::data` directly. If postflop-solver's parser changes
//! semantics we get a clean error, not silently wrong probabilities.

use postflop_solver::Range;

use crate::envelope::HandClassWeights;

/// Weight filter threshold: entries with weight ≤ this are silently dropped.
/// Shared with Python `_MIN_RANGE_WEIGHT_THRESHOLD` (0.001).  Keep in sync.
const MIN_WEIGHT_FILTER: f32 = 0.001;

/// Convert a hand-class weight map into a [`Range`].
///
/// Returns `Ok(empty_range)` for an empty input (caller may want to error on
/// this; we do, in `init_game`, to surface envelope bugs early).
pub fn range_from_hand_classes(weights: &HandClassWeights) -> Result<Range, String> {
    if weights.is_empty() {
        // An empty range crashes the solver later with a less helpful message.
        return Err("range is empty — no hand classes with positive weight".to_string());
    }

    let mut chunks: Vec<String> = Vec::with_capacity(weights.len());
    for (hand_class, weight) in weights {
        if !is_valid_hand_class(hand_class) {
            return Err(format!("invalid hand class: {hand_class:?}"));
        }
        let mut w = *weight;
        if !w.is_finite() || w <= MIN_WEIGHT_FILTER {
            continue;
        }
        if w > 1.0 {
            w = 1.0;
        }
        if (w - 1.0).abs() < 1e-6 {
            chunks.push(hand_class.clone());
        } else {
            // postflop-solver's weight regex accepts the canonical decimal
            // `[01](\.\d*)?` form; format!("{w:.6}") always produces it.
            chunks.push(format!("{hand_class}:{w:.6}"));
        }
    }

    if chunks.is_empty() {
        return Err("range had only zero-weight entries".to_string());
    }

    let range_string = chunks.join(",");
    range_string
        .parse::<Range>()
        .map_err(|e| format!("postflop-solver rejected range string: {e}"))
}

fn is_valid_hand_class(s: &str) -> bool {
    let bytes = s.as_bytes();
    match bytes.len() {
        2 => is_rank(bytes[0]) && bytes[0] == bytes[1],
        3 => {
            is_rank(bytes[0])
                && is_rank(bytes[1])
                && bytes[0] != bytes[1]
                && (bytes[2] == b's' || bytes[2] == b'o')
                && rank_idx(bytes[0]) > rank_idx(bytes[1])
        }
        _ => false,
    }
}

fn is_rank(c: u8) -> bool {
    matches!(c, b'A' | b'K' | b'Q' | b'J' | b'T' | b'2'..=b'9')
}

fn rank_idx(c: u8) -> u8 {
    match c {
        b'A' => 12,
        b'K' => 11,
        b'Q' => 10,
        b'J' => 9,
        b'T' => 8,
        b'2'..=b'9' => c - b'2',
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn weights(pairs: &[(&str, f32)]) -> HandClassWeights {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), *v))
            .collect::<BTreeMap<_, _>>()
    }

    #[test]
    fn parses_pair_full_weight() {
        let r = range_from_hand_classes(&weights(&[("AA", 1.0)])).unwrap();
        // 12 = ace rank; pair_weight averages 6 combos.
        assert!((r.get_weight_pair(12) - 1.0).abs() < 1e-6);
    }

    #[test]
    fn parses_suited_partial_weight() {
        let r = range_from_hand_classes(&weights(&[("AKs", 0.5)])).unwrap();
        assert!((r.get_weight_suited(12, 11) - 0.5).abs() < 1e-6);
        // Offsuit should still be zero — suitedness must matter.
        assert!(r.get_weight_offsuit(12, 11) < 1e-6);
    }

    #[test]
    fn parses_offsuit_partial_weight() {
        let r = range_from_hand_classes(&weights(&[("AKo", 0.25)])).unwrap();
        assert!((r.get_weight_offsuit(12, 11) - 0.25).abs() < 1e-6);
        assert!(r.get_weight_suited(12, 11) < 1e-6);
    }

    #[test]
    fn skips_zero_weight_entries() {
        let r = range_from_hand_classes(&weights(&[("AA", 1.0), ("KK", 0.0)])).unwrap();
        assert!((r.get_weight_pair(12) - 1.0).abs() < 1e-6);
        assert!(r.get_weight_pair(11) < 1e-6);
    }

    #[test]
    fn rejects_invalid_class() {
        // "Ak" lowercase rank rejected (the canonical form uses uppercase).
        assert!(range_from_hand_classes(&weights(&[("Ak", 1.0)])).is_err());
        assert!(range_from_hand_classes(&weights(&[("AKx", 1.0)])).is_err());
        assert!(range_from_hand_classes(&weights(&[("AA1", 1.0)])).is_err());
        assert!(range_from_hand_classes(&weights(&[("KAs", 1.0)])).is_err()); // wrong order
    }

    #[test]
    fn rejects_empty_range() {
        assert!(range_from_hand_classes(&weights(&[])).is_err());
    }

    #[test]
    fn clamps_oversized_weight() {
        let r = range_from_hand_classes(&weights(&[("QQ", 5.0)])).unwrap();
        assert!((r.get_weight_pair(10) - 1.0).abs() < 1e-6);
    }
}
