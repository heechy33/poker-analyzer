"""Build the postflop scenario JSON envelope consumed by the WASM solver.

Public entry point: :func:`build_scenario`.

The builder reconstructs the state at the *start* of a postflop street
(before the first action on that street) by replaying preflop actions with
``Decimal`` precision, looks up GTO baseline ranges from
``range_library``, removes hero's known combo and any villain showdown
information, and emits a deterministic JSON envelope plus metadata.

No solver execution happens here — the envelope is the input the browser
WASM bundle consumes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from app.models import Hand, HandAction, RangeLibrary
from app.scenario.ranges import (
    DEFAULT_FALLBACK_RANGE_STRING,
    apply_combo_weights,
    parse_range_string,
    remove_combo_from_range,
)

# Fixed v1 bet tree per PLAN.md §6.
BET_TREE: dict[str, Any] = {
    "flop": ["33%", "75%"],
    "turn": ["50%", "100%"],
    "river": ["33%", "75%", "150%"],
    "allin_always": True,
}

_STREET_ORDER: tuple[str, ...] = ("preflop", "flop", "turn", "river")
_POSTFLOP_STREETS = {"flop", "turn", "river"}

# Higher index = later to act on flop/turn/river → in position (IP).
# Heads-up postflop: BB acts first, BTN/SB acts last.
# Heads-up and 6-max charts share position labels; shorter tables reuse 6-max rows.
_CANONICAL_TABLE_SIZE = 6

_POSTFLOP_PRIORITY: dict[str, int] = {
    "SB": 0,
    "BB": 1,
    "UTG": 2,
    "UTG+1": 3,
    "MP": 4,
    "LJ": 5,
    "HJ": 6,
    "CO": 7,
    "BTN": 8,
    "BTN/SB": 8,
}

_POT_QUANT = Decimal("0.0001")
_BB_QUANT = Decimal("0.01")

# Validation constants.
# Shared with solver-wasm/src/lib.rs — keep in sync.
# Minimum SPR (stack-to-pot ratio) before we reject the envelope outright.
# At SPR < 0.5 with force_allin_threshold: 0.15, all bet sizes collapse to
# all-in and the engine produces a degenerate tree.
_MIN_SPR = Decimal("0.5")
_MAX_REASONABLE_STACK_BB = 500.0
_MAX_REASONABLE_POT_BB = 1000.0

# Minimum hand classes required in a range after board removal.
# Real-world tight ranges (e.g. 99-JJ,AJs-AQs,KQs,QJs,AQo) produce 8–9
# classes after card removal.  We set the floor at 5 to catch truly
# degenerate inputs without rejecting legitimate tight-range spots.
_MIN_HAND_CLASSES = 5
# Minimum total weight for a range to be considered non-empty.
_MIN_RANGE_WEIGHT = Decimal("0.01")
# Weights below this threshold are dropped as near-zero noise.
_MIN_RANGE_WEIGHT_THRESHOLD = Decimal("0.001")

# Chips-per-bb conversion factor; shared with solver-wasm/src/lib.rs.
_CHIPS_PER_BB = 100

# Force-allin threshold: if a bet uses ≥ this fraction of effective stack,
# the solver forces it to all-in.  Copied from solver-wasm/src/lib.rs
# TreeConfig::force_allin_threshold.  Keep in sync.
_FORCE_ALLIN_THRESHOLD = Decimal("0.15")

# Confidence tier constants.
_CONF_HIGH = "high"
_CONF_MEDIUM = "medium"
_CONF_LOW = "low"
_CONF_ERROR = "error"

_REASON_HU_CLEAN = "hu_clean"
_REASON_HU_LIBRARY_FALLBACK = "hu_library_fallback"
_REASON_MULTIWAY_HU_APPROX = "multiway_hu_approx"
_REASON_RANGE_GAP = "range_gap"
_REASON_SOLVER_INPUT_BORDERLINE = "solver_input_borderline"
_REASON_SCENARIO_INVALID = "scenario_invalid"
_REASON_UNSOLVABLE_SHALLOW_SPR = "solver_input_unsolvable_shallow_spr"


class ScenarioBuildError(ValueError):
    """Raised when scenario construction can't proceed (no flop, hero folded, ...)."""


# A range lookup callable returns either a RangeLibrary row or None.
RangeLookup = Callable[[int, str, str], Awaitable[RangeLibrary | None]]


class _PlayerLike(Protocol):
    seat: int
    screen_name: str
    position: str | None
    starting_stack: Decimal
    is_hero: bool
    final_cards: list[str] | None


@dataclass
class _SeatState:
    name: str
    position: str
    seat: int
    starting_stack: Decimal
    invested: Decimal
    alive: bool
    is_hero: bool
    final_cards: list[str] | None


async def build_scenario(
    hand: Hand,
    players: Sequence[_PlayerLike],
    actions: Sequence[HandAction],
    street: str,
    range_lookup: RangeLookup,
) -> dict[str, Any]:
    """Build the scenario envelope for the given postflop street.

    Parameters
    ----------
    hand:
        The :class:`~app.models.Hand` row for the hand being analyzed.
    players:
        Sequence of seat-level rows (typically ``HandPlayer``), one per
        seated player. Position labels must be set.
    actions:
        Pre-sorted sequence of ``HandAction`` rows. They will be sorted again
        defensively.
    street:
        Target postflop street: ``"flop"``, ``"turn"``, or ``"river"``.
    range_lookup:
        Async callable ``(table_size, position, action_sequence)`` returning a
        :class:`~app.models.RangeLibrary` row or ``None``.

    Returns
    -------
    dict
        ``{"scenario": <envelope>, "metadata": {...}, "scenario_hash": ...}``.
    """
    if street not in _POSTFLOP_STREETS:
        raise ScenarioBuildError(f"unsupported street: {street}")

    board = _board_for_street(hand, street)
    if board is None:
        raise ScenarioBuildError(f"hand has no {street} board")

    sorted_actions = sorted(
        actions,
        key=lambda a: (
            _STREET_ORDER.index(a.street) if a.street in _STREET_ORDER else 99,
            a.action_order,
            getattr(a, "id", 0) or 0,
        ),
    )

    seats = _build_seat_states(players)
    _replay_until(sorted_actions, seats, until_street=street)

    hero_state = _hero_state(seats.values())
    if not hero_state.alive:
        raise ScenarioBuildError("hero folded before this street")

    alive_states = [s for s in seats.values() if s.alive]
    if len(alive_states) < 2:
        raise ScenarioBuildError("fewer than two players alive at this street")

    is_multiway = len(alive_states) > 2

    villain_state, villain_reason = _select_villain(
        hero_state, alive_states, sorted_actions
    )
    if villain_state is None:
        raise ScenarioBuildError("could not pick a villain for this street")

    bb = Decimal(hand.stake_bb) if hand.stake_bb else Decimal("1")
    if bb <= 0:
        bb = Decimal("1")

    # HU pot model: only hero + selected villain contributions, not full table
    # dead money from folded players. This prevents inflated pot sizing that
    # causes WASM traps in multiway hands.
    pot_chips_hu = hero_state.invested + villain_state.invested
    pot_chips_total_table = sum(
        (s.invested for s in seats.values()), Decimal("0")
    )

    hero_remaining = max(
        hero_state.starting_stack - hero_state.invested, Decimal("0")
    )
    villain_remaining = max(
        villain_state.starting_stack - villain_state.invested, Decimal("0")
    )
    effective_stack_chips = min(hero_remaining, villain_remaining)

    pot_bb = _to_bb(pot_chips_hu, bb)
    eff_bb = _to_bb(effective_stack_chips, bb)

    oop_state, ip_state = _determine_in_position(hero_state, villain_state)

    hero_seq = _action_sequence_for(
        hero_state, villain_state, sorted_actions, seats
    )
    villain_seq = _action_sequence_for(
        villain_state, hero_state, sorted_actions, seats
    )

    hero_range_raw, hero_lookup_conf = await _lookup_range(
        range_lookup, int(hand.table_size), hero_state.position, hero_seq
    )
    villain_range_raw, villain_lookup_conf = await _lookup_range(
        range_lookup, int(hand.table_size), villain_state.position, villain_seq
    )

    # Multiway range tightening.
    range_adjustment_hero: str | None = None
    range_adjustment_villain: str | None = None

    if is_multiway:
        # Try multiway-specific DB rows first, then code-side tightening.
        hero_range_mw, hero_mw_hit = await _lookup_multiway_tightened(
            range_lookup,
            int(hand.table_size),
            hero_state.position,
            hero_seq,
            base_weights=hero_range_raw,
        )
        villain_range_mw, villain_mw_hit = await _lookup_multiway_tightened(
            range_lookup,
            int(hand.table_size),
            villain_state.position,
            villain_seq,
            base_weights=villain_range_raw,
        )

        if hero_mw_hit:
            hero_range_raw = hero_range_mw
            range_adjustment_hero = "multiway_tighten"
        else:
            hero_range_raw = _tighten_range_for_multiway(hero_range_raw)
            range_adjustment_hero = "multiway_tighten_code"

        if villain_mw_hit:
            villain_range_raw = villain_range_mw
            range_adjustment_villain = "multiway_tighten"
        else:
            villain_range_raw = _tighten_range_for_multiway(villain_range_raw)
            range_adjustment_villain = "multiway_tighten_code"

    if hero_state.is_hero and hand.hero_cards:
        hero_range_raw = remove_combo_from_range(hero_range_raw, hand.hero_cards, board)
    if villain_state.final_cards:
        villain_range_raw = remove_combo_from_range(
            villain_range_raw, villain_state.final_cards, board
        )

    if not hero_range_raw or not villain_range_raw:
        raise ScenarioBuildError(
            "could not resolve non-empty hero and villain ranges for this spot"
        )

    # Build bet_tree from envelope or use default.
    bet_tree = BET_TREE

    envelope: dict[str, Any] = {
        "board": list(board),
        "pot_bb": pot_bb,
        "effective_stack_bb": eff_bb,
        "oop_player": oop_state.position,
        "ip_player": ip_state.position,
        "hero_range": _normalize_range(hero_range_raw),
        "villain_range": _normalize_range(villain_range_raw),
        "bet_tree": bet_tree,
    }

    # Validate envelope before returning.
    validate_scenario_envelope(envelope, street)

    scenario_hash = canonical_hash(envelope)

    # Compute structured confidence.
    confidence, confidence_reasons, confidence_detail = _compute_confidence(
        is_multiway=is_multiway,
        hero_lookup_conf=hero_lookup_conf,
        villain_lookup_conf=villain_lookup_conf,
        pot_bb=Decimal(str(pot_bb)),
        eff_bb=Decimal(str(eff_bb)),
    )

    # Compute pot_error_pct: how much the HU pot understates the real table pot.
    # 0 % for true HU, > 0 % for multiway (where folded-player dead money is excluded).
    _total_chips_float = float(pot_chips_total_table.quantize(_POT_QUANT))
    _hu_chips_float = float(pot_chips_hu.quantize(_POT_QUANT))
    pot_error_pct = (
        (_total_chips_float - _hu_chips_float) / _total_chips_float * 100.0
        if _total_chips_float > 0
        else 0.0
    )

    # Compute effective bet sizes for histograms (what the solver actually sees
    # after force_allin dedup).
    _effective_sizes = _compute_effective_bet_sizes(bet_tree, Decimal(str(pot_bb)), Decimal(str(eff_bb)))

    # SPR for histogram bucketing.
    _spr_val = float(eff_bb / pot_bb) if pot_bb > 0 else float("inf")

    metadata: dict[str, Any] = {
        "scenario_hash": scenario_hash,
        "confidence": confidence,
        "confidence_reasons": confidence_reasons,
        "confidence_detail": confidence_detail,
        "alive_players": len(alive_states),
        "is_multiway_approximation": is_multiway,
        "hu_pot_mode": True,
        "oop_position": oop_state.position,
        "ip_position": ip_state.position,
        "hero_position": hero_state.position,
        "villain_position": villain_state.position,
        "hero_screen_name": hero_state.name,
        "villain_screen_name": villain_state.name,
        "hero_action_sequence": hero_seq,
        "villain_action_sequence": villain_seq,
        "villain_selection_reason": villain_reason,
        "pot_chips_hu_model": str(pot_chips_hu.quantize(_POT_QUANT)),
        "pot_chips_total_table": str(pot_chips_total_table.quantize(_POT_QUANT)),
        "effective_stack_chips": str(effective_stack_chips.quantize(_POT_QUANT)),
        "stake_bb_chips": str(bb),
        "range_adjustment_hero": range_adjustment_hero,
        "range_adjustment_villain": range_adjustment_villain,
        # ── Telemetry block (P0.6) ──
        "pot_error_pct": round(pot_error_pct, 3),
        "spr": round(_spr_val, 4),
        "pot_bb_telemetry": float(pot_bb),
        "eff_bb_telemetry": float(eff_bb),
        "multiway_alive_count": len(alive_states),
        "hero_lookup_hit": hero_lookup_conf == "high",
        "villain_lookup_hit": villain_lookup_conf == "high",
        "effective_bet_sizes_flop": _effective_sizes.get("flop", []),
        "effective_bet_sizes_turn": _effective_sizes.get("turn", []),
        "effective_bet_sizes_river": _effective_sizes.get("river", []),
        # ── Decision-node history (P2.4 / P2.5) ──
        # All actions on the target street before hero's last (graded) action,
        # in chronological order. The worker uses these to navigate the solver
        # tree to the exact decision node for export_strategy().
        "actions_before_hero": _build_solver_history_actions(
            sorted_actions, hero_state.name, street, float(pot_bb), bb
        ),
        # Pot size in bb at the moment hero is to act (used for P2.5 bet-size
        # mapping: hero bet / pot_at_hero_action_bb → pot fraction).
        "pot_at_hero_action_bb": _pot_at_hero_action_bb(
            sorted_actions, hero_state.name, street, float(pot_bb), bb
        ),
    }

    return {
        "scenario": envelope,
        "metadata": metadata,
        "scenario_hash": scenario_hash,
    }


def canonical_hash(envelope: dict[str, Any]) -> str:
    """SHA-256 of a sort-keyed JSON serialisation of the envelope."""
    payload = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_scenario_envelope(
    envelope: dict[str, Any], street: str
) -> None:
    """Validate a scenario envelope before it is returned to the caller.

    Raises :class:`ScenarioBuildError` with an actionable message if any
    invariant is violated.  Call this after building the envelope and before
    returning the response.

    Rules shared with solver-wasm/src/lib.rs preflight() — keep in sync.
    """
    pot_bb = Decimal(str(envelope.get("pot_bb", 0)))
    eff_bb = Decimal(str(envelope.get("effective_stack_bb", 0)))
    hero_range = envelope.get("hero_range", {})
    villain_range = envelope.get("villain_range", {})
    board = envelope.get("board", [])
    bet_tree = envelope.get("bet_tree", {})

    if pot_bb <= 0:
        raise ScenarioBuildError(
            f"scenario invalid: pot_bb ({float(pot_bb):.2f}) must be positive"
        )
    if eff_bb <= 0:
        raise ScenarioBuildError(
            f"scenario invalid: effective_stack_bb ({float(eff_bb):.2f}) must be positive"
        )

    if pot_bb > _MAX_REASONABLE_POT_BB:
        raise ScenarioBuildError(
            f"scenario invalid: pot_bb ({float(pot_bb):.1f}) exceeds "
            f"maximum reasonable pot ({_MAX_REASONABLE_POT_BB:.0f} bb)"
        )

    if eff_bb > _MAX_REASONABLE_STACK_BB:
        raise ScenarioBuildError(
            f"scenario invalid: effective_stack_bb ({float(eff_bb):.0f}) exceeds "
            f"maximum reasonable stack ({_MAX_REASONABLE_STACK_BB:.0f} bb)"
        )

    # --- SPR validation (shared _MIN_SPR = 0.5 with Rust preflight) ---
    spr_value = float(eff_bb / pot_bb) if pot_bb > 0 else float("inf")
    if spr_value < float(_MIN_SPR):
        raise ScenarioBuildError(
            f"scenario invalid: SPR ({spr_value:.2f}) is below minimum "
            f"({float(_MIN_SPR):.1f}); solver may produce degenerate trees "
            f"(reason: {_REASON_UNSOLVABLE_SHALLOW_SPR})"
        )

    # --- Range hygiene ---
    _validate_range_hygiene(hero_range, board, "hero_range")
    _validate_range_hygiene(villain_range, board, "villain_range")

    # --- Board length ---
    expected_board_len: dict[str, int] = {"flop": 3, "turn": 4, "river": 5}
    expected = expected_board_len.get(street)
    if expected is not None and len(board) != expected:
        raise ScenarioBuildError(
            f"scenario invalid: board has {len(board)} cards, "
            f"expected {expected} for {street} street"
        )

    # --- Degenerate bet tree detection ---
    _validate_bet_tree_degeneracy(bet_tree, pot_bb, eff_bb)


def _validate_range_hygiene(
    range_dict: dict[str, float],
    board: list[str],
    label: str,
) -> None:
    """Validate range meets minimum quality thresholds.

    Checks:
    - At least _MIN_HAND_CLASSES hand classes present.
    - Total weight ≥ _MIN_RANGE_WEIGHT.
    - Drops weights below _MIN_RANGE_WEIGHT_THRESHOLD (near-zero noise).
    """
    board_set = set(board)

    # Filter out near-zero weights and count viable hand classes.
    viable_count = 0
    total_weight = Decimal("0")
    for hand_class, weight in range_dict.items():
        w = Decimal(str(weight))
        if w < _MIN_RANGE_WEIGHT_THRESHOLD:
            continue
        # Check if this hand class has any combos not blocked by the board.
        from app.scenario.ranges import combos_in_class
        available = combos_in_class(hand_class, blocked=board_set)
        if len(available) > 0:
            viable_count += 1
            total_weight += w

    if viable_count < _MIN_HAND_CLASSES:
        raise ScenarioBuildError(
            f"scenario invalid: {label} has only {viable_count} hand classes "
            f"after board removal (minimum {_MIN_HAND_CLASSES}); "
            f"total weight {float(total_weight):.4f}"
        )

    if total_weight < _MIN_RANGE_WEIGHT:
        raise ScenarioBuildError(
            f"scenario invalid: {label} total weight ({float(total_weight):.4f}) "
            f"below minimum ({float(_MIN_RANGE_WEIGHT):.2f})"
        )


def _validate_bet_tree_degeneracy(
    bet_tree: dict[str, Any],
    pot_bb: Decimal,
    eff_bb: Decimal,
) -> None:
    """Detect degenerate bet trees where all sizes collapse to all-in.

    For each street, compute the effective chip amounts of each bet size
    after applying force_allin_threshold logic.  If only one distinct
    effective size remains and allin_always is true, the tree is degenerate
    and would crash the solver.

    Rules shared with solver-wasm/src/lib.rs preflight_inner() — keep in sync.
    """
    allin_always = bet_tree.get("allin_always", False)

    # Convert pot and stack to chip counts (100 chips per bb).
    pot_chips = pot_bb * _CHIPS_PER_BB
    eff_chips = eff_bb * _CHIPS_PER_BB
    allin_threshold_chips = eff_chips * _FORCE_ALLIN_THRESHOLD

    for street in ("flop", "turn", "river"):
        sizes: list[str] = bet_tree.get(street, [])
        if not sizes:
            continue

        distinct_effective_chips: set[int] = set()
        degenerate = False

        for size_str in sizes:
            size_str = size_str.strip()
            if not size_str:
                continue
            chips = _bet_string_to_chips(size_str, pot_chips, eff_chips)
            if chips is None:
                continue
            # Apply force_allin_threshold: if bet uses ≥ threshold fraction
            # of effective stack, it becomes an all-in.
            if chips >= allin_threshold_chips:
                # Represents all-in; use effective stack as the chip amount.
                chips = int(eff_chips)
            distinct_effective_chips.add(chips)

        if len(distinct_effective_chips) == 0:
            # No valid bet sizes on this street.
            if not allin_always:
                raise ScenarioBuildError(
                    f"degenerate bet tree: {street} has no bet sizes and "
                    f"allin_always is false"
                )
            continue

        if len(distinct_effective_chips) <= 1 and allin_always:
            degenerate = True

        if degenerate:
            raise ScenarioBuildError(
                f"degenerate bet tree: all {street} bet sizes collapse to "
                f"the same effective chip amount ({distinct_effective_chips}) "
                f"with allin_always; SPR is too shallow for this tree. "
                f"(pot={float(pot_bb):.1f}bb eff={float(eff_bb):.1f}bb "
                f"SPR={float(eff_bb/pot_bb):.2f})"
            )


def _bet_string_to_chips(
    size_str: str,
    pot_chips: Decimal,
    eff_chips: Decimal,
) -> int | None:
    """Convert a bet size string (e.g. "33%", "75%", "a") to chip count.

    Returns None if the string cannot be parsed.
    """
    s = size_str.strip()
    if s.lower() == "a":
        return int(eff_chips)
    if s.endswith("%"):
        try:
            pct = Decimal(s[:-1]) / Decimal("100")
            return int(pot_chips * pct)
        except Exception:
            return None
    return None


def _compute_effective_bet_sizes(
    bet_tree: dict[str, Any],
    pot_bb: Decimal,
    eff_bb: Decimal,
) -> dict[str, list[str]]:
    """Compute the effective bet-size labels per street after force_allin dedup.

    This mirrors what the solver engine actually sees: if a bet size exceeds
    ``force_allin_threshold`` of the effective stack, it becomes an all-in
    action. Duplicate effective actions are collapsed.

    Returns a dict mapping street -> list of distinct effective size labels
    (e.g. ``{"flop": ["50%"], "turn": ["all-in"], "river": ["66%", "all-in"]}``).
    """
    pot_chips = pot_bb * _CHIPS_PER_BB
    eff_chips = eff_bb * _CHIPS_PER_BB
    allin_threshold_chips = eff_chips * _FORCE_ALLIN_THRESHOLD

    result: dict[str, list[str]] = {}
    for street in ("flop", "turn", "river"):
        sizes: list[str] = bet_tree.get(street, [])
        if not sizes:
            result[street] = []
            continue

        seen_chips: set[int] = set()
        labels: list[str] = []

        for size_str in sizes:
            size_str = size_str.strip()
            if not size_str:
                continue
            chips = _bet_string_to_chips(size_str, pot_chips, eff_chips)
            if chips is None:
                continue

            # Apply force_allin_threshold.
            if chips >= int(allin_threshold_chips):
                chips = int(eff_chips)
                label = "all-in"
            else:
                label = size_str

            if chips not in seen_chips:
                seen_chips.add(chips)
                labels.append(label)

        result[street] = labels

    return result


# ---------------------------------------------------------------------------
# Confidence tiers
# ---------------------------------------------------------------------------


def _compute_confidence(
    *,
    is_multiway: bool,
    hero_lookup_conf: str,
    villain_lookup_conf: str,
    pot_bb: Decimal,
    eff_bb: Decimal,
) -> tuple[str, list[str], str]:
    """Compute structured confidence tier, reason codes, and detail message."""
    reasons: list[str] = []
    borderlines: list[str] = []

    # SPR check for borderline.
    spr_value = float(eff_bb / pot_bb) if pot_bb > 0 else float("inf")
    if spr_value < 1.0:
        borderlines.append(f"very low SPR ({spr_value:.1f})")
    if float(eff_bb) > 400:
        borderlines.append("deep stack (>{:.0f} bb)".format(_MAX_REASONABLE_STACK_BB * 0.8))
    if float(pot_bb) > 500:
        borderlines.append("large pot (>{:.0f} bb)".format(_MAX_REASONABLE_POT_BB / 2))

    any_fallback = hero_lookup_conf == "low" or villain_lookup_conf == "low"

    if is_multiway:
        tier = _CONF_LOW
        reasons.append(_REASON_MULTIWAY_HU_APPROX)
    elif any_fallback:
        tier = _CONF_MEDIUM
        reasons.append(_REASON_HU_LIBRARY_FALLBACK)
    elif borderlines:
        tier = _CONF_MEDIUM
        reasons.append(_REASON_SOLVER_INPUT_BORDERLINE)
    else:
        tier = _CONF_HIGH
        reasons.append(_REASON_HU_CLEAN)

    # Add range_gap if either lookup fell back.
    if any_fallback and not is_multiway:
        reasons.append(_REASON_RANGE_GAP)

    if borderlines and _REASON_SOLVER_INPUT_BORDERLINE not in reasons:
        reasons.append(_REASON_SOLVER_INPUT_BORDERLINE)

    # Deduplicate preserving order.
    seen: set[str] = set()
    unique_reasons: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    detail = _build_confidence_detail(tier, unique_reasons, is_multiway, spr_value)
    return tier, unique_reasons, detail


def _build_confidence_detail(
    tier: str,
    reasons: list[str],
    is_multiway: bool,
    spr_value: float,
) -> str:
    """Build a one-sentence human-readable confidence explanation."""
    if tier == _CONF_HIGH:
        return "Clean heads-up spot with exact range library hits and validated inputs."

    if tier == _CONF_MEDIUM:
        if is_multiway:
            return "Multiway pot approximated as heads-up with tightened ranges."
        parts: list[str] = []
        if _REASON_HU_LIBRARY_FALLBACK in reasons:
            parts.append("range library fallback used")
        if _REASON_RANGE_GAP in reasons:
            parts.append("some range keys missing from library")
        if _REASON_SOLVER_INPUT_BORDERLINE in reasons:
            parts.append(
                f"solver inputs near limits (SPR {spr_value:.1f})"
            )
        return "Heads-up spot but " + "; ".join(parts) + "."

    if tier == _CONF_LOW:
        parts: list[str] = []
        if _REASON_MULTIWAY_HU_APPROX in reasons:
            parts.append("multiway collapsed to heads-up — strategy is approximate, not true multiway GTO")
        if _REASON_RANGE_GAP in reasons:
            parts.append("range library gaps filled with built-in fallback")
        if _REASON_SOLVER_INPUT_BORDERLINE in reasons:
            parts.append(f"solver inputs near limits (SPR {spr_value:.1f})")
        return "; ".join(parts) + "."

    return "Unknown confidence state."


# ---------------------------------------------------------------------------
# Smart villain selection
# ---------------------------------------------------------------------------


def _select_villain(
    hero: _SeatState,
    alive_states: Sequence[_SeatState],
    actions: Sequence[HandAction],
) -> tuple[_SeatState | None, str]:
    """Pick the primary villain with a weighted scoring system.

    Heads-up: the only other alive player (score irrelevant).
    Multi-way: score all alive non-hero candidates across these dimensions:
      - Preflop aggressor bonus
      - Preflop investment vs hero
      - Stack proximity to hero
      - Postflop position vs hero (prefer IP)

    Returns ``(villain, reason_string)``.
    """
    candidates = [s for s in alive_states if s.name != hero.name]
    if not candidates:
        return None, "no candidates"

    if len(candidates) == 1:
        return candidates[0], "heads-up — only other player"

    # Determine last preflop aggressor (excluding hero).
    last_aggressor_name: str | None = None
    for action in reversed(actions):
        if action.street != "preflop":
            continue
        if action.action != "raise":
            continue
        if action.screen_name == hero.name:
            continue
        last_aggressor_name = action.screen_name
        break

    hero_invested = hero.invested
    hero_pos_priority = _POSTFLOP_PRIORITY.get(hero.position, 4)

    scored: list[tuple[float, _SeatState]] = []
    for candidate in candidates:
        score = 0.0

        # 1. Preflop aggressor bonus (large).
        if last_aggressor_name and candidate.name == last_aggressor_name:
            score += 10.0

        # 2. Preflop investment relative to hero (meaningful money in).
        invest_ratio = (
            float(candidate.invested / hero_invested) if hero_invested > 0 else 1.0
        )
        score += min(invest_ratio, 2.0) * 3.0

        # 3. Stack proximity to hero (prefer similar effective stacks).
        hero_rem = max(float(hero.starting_stack - hero.invested), 0.0)
        cand_rem = max(
            float(candidate.starting_stack - candidate.invested), 0.0
        )
        if hero_rem > 0 and cand_rem > 0:
            max_stack = max(hero_rem, cand_rem)
            min_stack = min(hero_rem, cand_rem)
            stack_similarity = min_stack / max_stack  # 0..1
            score += stack_similarity * 4.0

        # 4. Position proximity — prefer the player most likely to be hero's
        #    "main" decision opponent (closer in priority = more interaction).
        cand_priority = _POSTFLOP_PRIORITY.get(candidate.position, 4)
        pos_dist = abs(cand_priority - hero_pos_priority)
        score += max(0.0, 3.0 - pos_dist * 0.5)

        scored.append((score, candidate))

    # Debug: build candidate summary.
    debug_parts: list[str] = []
    for s, c in sorted(scored, key=lambda x: -x[0]):
        debug_parts.append(f"{c.position}({c.name[:6]}):{s:.1f}")
    debug_str = ", ".join(debug_parts)

    scored.sort(key=lambda x: (-x[0], -_POSTFLOP_PRIORITY.get(x[1].position, -1)))
    best_score, best = scored[0]

    reason = (
        f"highest composite score {best_score:.1f} "
        f"(aggressor={'yes' if last_aggressor_name and best.name == last_aggressor_name else 'no'}, "
        f"candidates: {debug_str})"
    )
    return best, reason


# ---------------------------------------------------------------------------
# Multiway range tightening
# ---------------------------------------------------------------------------


async def _lookup_multiway_tightened(
    range_lookup: RangeLookup,
    table_size: int,
    position: str,
    action_sequence: str,
    base_weights: dict[str, float],
) -> tuple[dict[str, float], bool]:
    """Try to find a dedicated multiway range row in the library.

    Looks for rows with action_sequence suffixed by ``_multiway`` (e.g.
    ``vs_UTG_open_call_multiway``). If found, returns the row's weights
    as a more accurate multiway approximation. Otherwise returns the
    original ``base_weights`` unchanged.
    """
    sizes: list[int] = [table_size]
    if table_size != _CANONICAL_TABLE_SIZE:
        sizes.append(_CANONICAL_TABLE_SIZE)

    for size in sizes:
        for suffix in ("_multiway", "_mw"):
            mw_key = f"{action_sequence}{suffix}"
            weights = await _lookup_range_row(range_lookup, size, position, mw_key)
            if weights:
                return weights, True

    return base_weights, False


def _tighten_range_for_multiway(weights: dict[str, float]) -> dict[str, float]:
    """Code-side multiway tightening: remove bottom ~20% of combos by weight.

    This is a fallback when no dedicated multiway range_library row exists.
    It sorts hand classes by descending weight, keeps top classes accounting
    for ~80% of total weight (cumulative), and zeroes out the rest.

    This is NOT exact GTO multiway — it is an HU-engine approximation that
    narrows ranges to reflect multiway caution.
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


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_seat_states(
    players: Sequence[_PlayerLike],
) -> dict[str, _SeatState]:
    seats: dict[str, _SeatState] = {}
    for player in players:
        position = (player.position or "").strip() or f"SEAT{player.seat}"
        seats[player.screen_name] = _SeatState(
            name=player.screen_name,
            position=position,
            seat=int(player.seat),
            starting_stack=Decimal(player.starting_stack),
            invested=Decimal("0"),
            alive=True,
            is_hero=bool(player.is_hero),
            final_cards=list(player.final_cards) if player.final_cards else None,
        )
    return seats


def _replay_until(
    actions: Sequence[HandAction],
    seats: dict[str, _SeatState],
    until_street: str,
) -> None:
    """Replay actions through the END of every street prior to `until_street`."""
    target_idx = _STREET_ORDER.index(until_street)
    streets_to_replay = _STREET_ORDER[:target_idx]

    for street in streets_to_replay:
        street_actions = [a for a in actions if a.street == street]
        if not street_actions:
            continue
        in_street = {name: Decimal("0") for name in seats}
        for action in street_actions:
            name = action.screen_name
            if name not in seats:
                continue
            seat = seats[name]
            verb = action.action
            if verb in ("post_sb", "post_bb"):
                in_street[name] += _decimal(action.amount)
            elif verb == "fold":
                seat.alive = False
            elif verb == "check":
                continue
            elif verb == "call":
                in_street[name] += _decimal(action.amount)
            elif verb == "bet":
                in_street[name] += _decimal(action.amount)
            elif verb == "raise":
                target = _decimal(action.raise_to)
                if target > in_street[name]:
                    in_street[name] = target
            elif verb == "all_in":
                # The CoinPoker parser collapses "is all-in" into the
                # accompanying call/bet/raise verb's flag, so a bare all_in
                # action carries no chip delta.
                continue
            else:
                # show / muck / collect happen on the showdown pseudo-street.
                continue
        for name, amount in in_street.items():
            seats[name].invested += amount


def _hero_state(states: Sequence[_SeatState]) -> _SeatState:
    for state in states:
        if state.is_hero:
            return state
    raise ScenarioBuildError("hand has no hero player")


def _determine_in_position(
    hero: _SeatState, villain: _SeatState
) -> tuple[_SeatState, _SeatState]:
    """Return (oop, ip) by postflop position priority."""
    hero_p = _POSTFLOP_PRIORITY.get(hero.position, -1)
    villain_p = _POSTFLOP_PRIORITY.get(villain.position, -1)
    if hero_p > villain_p:
        return villain, hero
    if villain_p > hero_p:
        return hero, villain
    # Tie-break by seat: smaller seat acts first → OOP.
    if hero.seat < villain.seat:
        return hero, villain
    return villain, hero


def _action_sequence_for(
    player: _SeatState,
    opponent: _SeatState,
    actions: Sequence[HandAction],
    seats: dict[str, _SeatState],
) -> str:
    """Derive the range_library action_sequence key from preflop history."""
    raises = [a for a in actions if a.street == "preflop" and a.action == "raise"]
    last_aggressor_pos = _last_preflop_aggressor_position(actions, seats)

    if not raises:
        # Limped pot.
        if player.position == "BB":
            return "limp_check"
        return "limp"

    raise_count = len(raises)
    label = _raise_label(raise_count)
    last_raise = raises[-1]

    if player.name == last_raise.screen_name:
        # Player is the aggressor heading into the flop.
        if raise_count == 1:
            return "open"
        prev_label = _raise_label(raise_count - 1)
        prev_raise = raises[-2]
        prev_pos = _seat_position_by_name(prev_raise.screen_name, seats)
        if prev_pos is None:
            return f"{label}"
        return f"vs_{prev_pos}_{label}_{label}"

    # Player is a caller of the last raise.
    aggressor_pos = (
        _seat_position_by_name(last_raise.screen_name, seats)
        or last_aggressor_pos
        or opponent.position
    )
    if raise_count == 1:
        return f"vs_{aggressor_pos}_open_call"
    return f"vs_{aggressor_pos}_{label}_call"


def _raise_label(raise_count: int) -> str:
    return {1: "open", 2: "3bet", 3: "4bet", 4: "5bet"}.get(
        raise_count, f"{raise_count}bet"
    )


def _seat_position_by_name(name: str, seats: dict[str, _SeatState]) -> str | None:
    state = seats.get(name)
    return state.position if state else None


def _last_preflop_aggressor_position(
    actions: Sequence[HandAction], seats: dict[str, _SeatState]
) -> str | None:
    for action in reversed(actions):
        if action.street == "preflop" and action.action == "raise":
            return _seat_position_by_name(action.screen_name, seats)
    return None


async def _lookup_range_row(
    range_lookup: RangeLookup,
    table_size: int,
    position: str,
    action_sequence: str,
) -> dict[str, float] | None:
    row = await range_lookup(table_size, position, action_sequence)
    if row is None:
        return None
    weights = parse_range_string(row.range_string)
    merged = apply_combo_weights(weights, row.combo_weights)
    return merged if merged else None


async def _lookup_range(
    range_lookup: RangeLookup,
    table_size: int,
    position: str,
    action_sequence: str,
) -> tuple[dict[str, float], str]:
    """Resolve hand-class weights for a (position, action_sequence) tuple.

    Lookup order: exact key on the hand's table size, exact key on the canonical
    6-max library, ``default_call`` on each size, then a built-in wide calling
    range so the WASM solver always receives positive weights.
    """
    sizes: list[int] = [table_size]
    if table_size != _CANONICAL_TABLE_SIZE:
        sizes.append(_CANONICAL_TABLE_SIZE)

    for size in sizes:
        weights = await _lookup_range_row(
            range_lookup, size, position, action_sequence
        )
        if weights:
            return weights, "high" if size == table_size else "low"

    for size in sizes:
        weights = await _lookup_range_row(range_lookup, size, position, "default_call")
        if weights:
            return weights, "low"

    return parse_range_string(DEFAULT_FALLBACK_RANGE_STRING), "low"


def _board_for_street(hand: Hand, street: str) -> list[str] | None:
    if hand.flop is None:
        return None
    if street == "flop":
        return list(hand.flop)
    if street == "turn":
        if hand.turn is None:
            return None
        return [*hand.flop, hand.turn]
    if street == "river":
        if hand.turn is None or hand.river is None:
            return None
        return [*hand.flop, hand.turn, hand.river]
    return None


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _to_bb(chips: Decimal, bb: Decimal) -> float:
    quantized = (chips / bb).quantize(_BB_QUANT, rounding=ROUND_HALF_UP)
    return float(quantized)


def _normalize_range(weights: dict[str, float]) -> dict[str, float]:
    """Round weights for hash stability, sort keys, and filter near-zero weights.

    Weights below _MIN_RANGE_WEIGHT_THRESHOLD (0.001) are dropped as noise.
    """
    rounded: dict[str, float] = {}
    for hand_class, weight in weights.items():
        if weight is None:
            continue
        try:
            value = float(weight)
        except (TypeError, ValueError):
            continue
        # Drop near-zero weights (shared threshold with range_convert.rs).
        if value < float(_MIN_RANGE_WEIGHT_THRESHOLD):
            continue
        rounded[hand_class] = round(value, 4)
    return dict(sorted(rounded.items()))


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"unserialisable: {type(value)!r}")


# ---------------------------------------------------------------------------
# Decision-node history helpers (P2.4 / P2.5)
# ---------------------------------------------------------------------------

_SKIP_VERBS = frozenset({"show", "muck", "collect", "all_in"})
_TERMINAL_VERBS = frozenset({"fold"})


def _build_solver_history_actions(
    sorted_actions: "Sequence[HandAction]",
    hero_name: str,
    street: str,
    pot_bb_street_start: float,
    bb: Decimal,
) -> list[dict]:
    """Return all actions on `street` before hero's last (graded) action.

    Each entry carries ``player_is_hero``, ``action`` verb, ``amount_bb``,
    ``raise_to_bb``, and ``pot_bb_before`` (total pot in bb *before* this
    action was committed).  The worker uses this list to navigate the solver
    tree to the exact decision node via ``export_strategy(handle, history)``.

    For v1 the graded action is hero's *last* non-terminal action on the
    street. Actions that are show/muck/collect/all_in flags are skipped.
    """
    street_actions = [
        a for a in sorted_actions
        if a.street == street and a.action not in _SKIP_VERBS
    ]

    # Find index of hero's last non-terminal action on this street.
    last_hero_idx = -1
    for i, action in enumerate(street_actions):
        if action.screen_name == hero_name and action.action not in _TERMINAL_VERBS:
            last_hero_idx = i

    if last_hero_idx < 0:
        return []

    result: list[dict] = []
    # Track total chips committed on this street by each player.
    in_street: dict[str, Decimal] = {}
    pot_on_street = Decimal("0")

    for action in street_actions[:last_hero_idx]:
        verb = action.action
        name = action.screen_name

        if name not in in_street:
            in_street[name] = Decimal("0")

        pot_bb_before = pot_bb_street_start + float(
            _to_bb(pot_on_street, bb)
        )
        amount_bb: float | None = None
        raise_to_bb: float | None = None

        if verb in ("bet", "call"):
            inc = _decimal(action.amount)
            in_street[name] += inc
            pot_on_street += inc
            amount_bb = float(_to_bb(inc, bb))
        elif verb == "raise":
            target = _decimal(action.raise_to or action.amount or "0")
            inc = max(target - in_street[name], Decimal("0"))
            in_street[name] = max(in_street[name], target)
            pot_on_street += inc
            amount_bb = float(_to_bb(target, bb))
            raise_to_bb = amount_bb
        elif verb == "check":
            pass
        elif verb == "fold":
            pass
        else:
            continue

        result.append(
            {
                "player_is_hero": (name == hero_name),
                "action": verb,
                "amount_bb": amount_bb,
                "raise_to_bb": raise_to_bb,
                "pot_bb_before": round(pot_bb_before, 4),
            }
        )

    return result


def _pot_at_hero_action_bb(
    sorted_actions: "Sequence[HandAction]",
    hero_name: str,
    street: str,
    pot_bb_street_start: float,
    bb: Decimal,
) -> float:
    """Pot size in bb at the moment hero is to make their last graded action.

    = pot at street start + all chips committed on this street before hero's
    last non-terminal action (by any player, including earlier hero actions).
    """
    street_actions = [
        a for a in sorted_actions
        if a.street == street and a.action not in _SKIP_VERBS
    ]

    last_hero_idx = -1
    for i, action in enumerate(street_actions):
        if action.screen_name == hero_name and action.action not in _TERMINAL_VERBS:
            last_hero_idx = i

    if last_hero_idx < 0:
        return pot_bb_street_start

    in_street: dict[str, Decimal] = {}
    pot_on_street = Decimal("0")

    for action in street_actions[:last_hero_idx]:
        verb = action.action
        name = action.screen_name
        if name not in in_street:
            in_street[name] = Decimal("0")

        if verb in ("bet", "call"):
            inc = _decimal(action.amount)
            in_street[name] += inc
            pot_on_street += inc
        elif verb == "raise":
            target = _decimal(action.raise_to or action.amount or "0")
            inc = max(target - in_street[name], Decimal("0"))
            in_street[name] = max(in_street[name], target)
            pot_on_street += inc

    return round(pot_bb_street_start + float(_to_bb(pot_on_street, bb)), 4)