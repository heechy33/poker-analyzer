"""PIO-style range string parsing and combo weight utilities.

A *hand class* is the textual canonical form of a starting-hand combination
(``"AA"``, ``"AKs"``, ``"AKo"``). A *combo* is a specific 2-card hand
(``("As", "Kd")``).

The PIO range string syntax accepted here is the common subset:

* Pairs: ``TT``, ``TT+``, ``AA-22``
* Suited: ``AKs``, ``AKs+``, ``A5s-A2s``
* Offsuit: ``AKo``, ``AKo+``, ``A5o-A2o``
* Both suited and offsuit: ``AK`` (rare, but tolerated)
* Per-chunk weights: ``AKs:0.5``

The output is always a dict keyed by hand class with float weights in
``[0, 1]``.
"""

from __future__ import annotations

from collections.abc import Iterable

RANKS = "23456789TJQKA"
RANK_INDEX: dict[str, int] = {rank: i for i, rank in enumerate(RANKS)}
SUITS = "cdhs"

# Used when range_library has no row for (table_size, position, action_sequence).
# Matches the BB default_call fallback in migrations/003_seed_ranges.sql.
DEFAULT_FALLBACK_RANGE_STRING = (
    "22-99,A2s-AJs,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,A9o-AJo,KTo+,QTo+,JTo"
)


class RangeParseError(ValueError):
    """Raised when a PIO range string cannot be parsed."""


def tighten_range_for_multiway(weights: dict[str, float]) -> dict[str, float]:
    """Code-side multiway tightening: remove bottom ~20% of combos by weight.

    This is a fallback when no dedicated multiway range_library row exists.
    It sorts hand classes by descending weight, keeps top classes accounting
    for ~80% of total weight (cumulative), and drops the rest.

    This is NOT exact GTO multiway — it is an HU-engine approximation that
    narrows ranges to reflect typical multiway caution.
    """
    if not weights:
        return {}

    sorted_classes = sorted(weights.items(), key=lambda x: -x[1])
    total = sum(v for _, v in sorted_classes)
    if total <= 0:
        return dict(weights)

    cumulative = 0.0
    threshold = total * 0.80
    kept: dict[str, float] = {}

    for hand_class, weight in sorted_classes:
        if cumulative < threshold:
            kept[hand_class] = weight
            cumulative += weight
        else:
            break

    return kept if kept else dict(weights)


def parse_range_string(range_str: str) -> dict[str, float]:
    """Parse a PIO-style range string into hand-class weights."""
    out: dict[str, float] = {}
    if not range_str:
        return out
    for raw in range_str.split(","):
        chunk = raw.strip()
        if not chunk:
            continue
        weight = 1.0
        if ":" in chunk:
            chunk, weight_str = chunk.split(":", 1)
            try:
                weight = float(weight_str)
            except ValueError as exc:
                raise RangeParseError(f"invalid weight in '{raw}'") from exc
            chunk = chunk.strip()
        for hand_class in _expand_chunk(chunk):
            # Last write wins; explicit later chunks override broader earlier ones.
            out[hand_class] = weight
    return out


def apply_combo_weights(
    base: dict[str, float],
    overrides: dict[str, float] | None,
) -> dict[str, float]:
    """Merge a sparse override dict onto a base hand-class weight dict.

    Override weights replace base weights. Zero-or-negative weights drop the
    class entirely.
    """
    merged = dict(base)
    if not overrides:
        return merged
    for hand_class, value in overrides.items():
        try:
            weight = float(value)
        except (TypeError, ValueError):
            continue
        if weight <= 0.0:
            merged.pop(hand_class, None)
        else:
            merged[hand_class] = weight
    return merged


def remove_combo_from_range(
    weights: dict[str, float],
    combo: Iterable[str],
    board: Iterable[str] = (),
) -> dict[str, float]:
    """Remove a single 2-card combo from a hand-class weight dict.

    The hand-class entry is reduced proportionally based on how many combos
    in that class remain after blocking by the board cards. Returns a new
    dict.
    """
    cards = list(combo)
    if len(cards) != 2:
        return dict(weights)
    hand_class = combo_to_class(cards[0], cards[1])
    if hand_class is None:
        return dict(weights)

    out = dict(weights)
    if hand_class not in out:
        return out

    board_set = set(board)
    # Combos still possible in the class given the board's card removal.
    possible_after_board = combos_in_class(hand_class, blocked=board_set)
    if not possible_after_board:
        out.pop(hand_class, None)
        return out

    # Only subtract if the specific combo isn't already blocked by the board.
    target = frozenset({cards[0], cards[1]})
    still_present = any(frozenset(p) == target for p in possible_after_board)
    if not still_present:
        return out

    factor = (len(possible_after_board) - 1) / len(possible_after_board)
    new_weight = out[hand_class] * factor
    if new_weight <= 1e-9:
        out.pop(hand_class, None)
    else:
        out[hand_class] = round(new_weight, 6)
    return out


def combo_to_class(card1: str, card2: str) -> str | None:
    """Return the canonical hand class for a 2-card combo, or None."""
    if not _valid_card(card1) or not _valid_card(card2):
        return None
    r1, s1 = card1[0], card1[1]
    r2, s2 = card2[0], card2[1]
    if RANK_INDEX[r1] < RANK_INDEX[r2]:
        r1, r2 = r2, r1
        s1, s2 = s2, s1
    if r1 == r2:
        if s1 == s2:
            return None  # identical card duplicated
        return r1 + r2
    suit = "s" if s1 == s2 else "o"
    return f"{r1}{r2}{suit}"


def combos_in_class(
    hand_class: str, blocked: set[str] | None = None
) -> list[tuple[str, str]]:
    """Enumerate the specific 2-card combos that match a hand class."""
    blocked_set = blocked or set()
    out: list[tuple[str, str]] = []
    if len(hand_class) == 2:  # pair
        rank = hand_class[0]
        for i in range(4):
            for j in range(i + 1, 4):
                c1 = f"{rank}{SUITS[i]}"
                c2 = f"{rank}{SUITS[j]}"
                if c1 in blocked_set or c2 in blocked_set:
                    continue
                out.append((c1, c2))
        return out

    if len(hand_class) == 3:
        high, low, qualifier = hand_class[0], hand_class[1], hand_class[2]
        for s1 in SUITS:
            for s2 in SUITS:
                if qualifier == "s" and s1 != s2:
                    continue
                if qualifier == "o" and s1 == s2:
                    continue
                c1 = f"{high}{s1}"
                c2 = f"{low}{s2}"
                if c1 in blocked_set or c2 in blocked_set:
                    continue
                out.append((c1, c2))
        return out

    return out


def _valid_card(card: str) -> bool:
    return (
        isinstance(card, str)
        and len(card) == 2
        and card[0] in RANK_INDEX
        and card[1] in SUITS
    )


def _expand_chunk(chunk: str) -> list[str]:
    chunk = chunk.replace(" ", "")
    if not chunk:
        return []
    if len(chunk) >= 2 and chunk[0] == chunk[1] and chunk[0] in RANK_INDEX:
        return _expand_pair(chunk)
    return _expand_nonpair(chunk)


def _expand_pair(chunk: str) -> list[str]:
    pair = chunk[:2]
    suffix = chunk[2:]
    if suffix == "":
        return [pair]
    if suffix == "+":
        idx = RANK_INDEX[pair[0]]
        return [r + r for r in RANKS[idx:]]
    if suffix.startswith("-"):
        target = suffix[1:]
        if len(target) != 2 or target[0] != target[1] or target[0] not in RANK_INDEX:
            raise RangeParseError(f"invalid pair range: {chunk}")
        lo, hi = sorted((RANK_INDEX[target[0]], RANK_INDEX[pair[0]]))
        return [r + r for r in RANKS[lo : hi + 1]]
    raise RangeParseError(f"unsupported pair chunk: {chunk}")


def _expand_nonpair(chunk: str) -> list[str]:
    if "-" in chunk:
        left, right = chunk.split("-", 1)
        return _expand_dash(left, right)

    plus = chunk.endswith("+")
    if plus:
        chunk = chunk[:-1]
    if len(chunk) < 2:
        raise RangeParseError(f"invalid chunk: {chunk}")
    high, low = chunk[0], chunk[1]
    if high not in RANK_INDEX or low not in RANK_INDEX:
        raise RangeParseError(f"invalid ranks in chunk: {chunk}")
    if RANK_INDEX[high] <= RANK_INDEX[low]:
        raise RangeParseError(f"high rank must exceed low rank: {chunk}")

    suit = chunk[2] if len(chunk) > 2 else None
    if suit not in (None, "s", "o"):
        raise RangeParseError(f"invalid suit in chunk: {chunk}")

    if suit is None:
        return _expand_one(high, low, "s", plus) + _expand_one(high, low, "o", plus)
    return _expand_one(high, low, suit, plus)


def _expand_one(high: str, low: str, suit: str, plus: bool) -> list[str]:
    if not plus:
        return [f"{high}{low}{suit}"]
    h_idx = RANK_INDEX[high]
    lo_idx = RANK_INDEX[low]
    return [f"{high}{RANKS[k]}{suit}" for k in range(lo_idx, h_idx)]


def _expand_dash(left: str, right: str) -> list[str]:
    if len(left) < 3 or len(right) < 3:
        raise RangeParseError(f"dash range requires suit: {left}-{right}")
    if left[0] != right[0] or left[2] != right[2]:
        raise RangeParseError(f"dash range mismatch: {left}-{right}")
    high = left[0]
    suit = left[2]
    if suit not in ("s", "o"):
        raise RangeParseError(f"invalid suit: {left}-{right}")
    if (
        high not in RANK_INDEX
        or left[1] not in RANK_INDEX
        or right[1] not in RANK_INDEX
    ):
        raise RangeParseError(f"invalid ranks: {left}-{right}")
    h_idx = RANK_INDEX[high]
    lo, hi = sorted((RANK_INDEX[left[1]], RANK_INDEX[right[1]]))
    if hi >= h_idx:
        raise RangeParseError(f"dash range exceeds high rank: {left}-{right}")
    return [f"{high}{RANKS[k]}{suit}" for k in range(lo, hi + 1)]
