"""Prompt assembly for the per-hand Anthropic analysis call.

The prompt is structured so the model has every piece of context it needs
(stakes, positions, actions, hero cards, optional solver diff) and is told
to emit a strict JSON envelope containing the analysis text plus a list of
enumerated leak tags. See :mod:`app.llm.tags` for the tag whitelist.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from app.models import Hand, HandAction, HandPlayer
from app.llm.tags import LEAK_TAGS

SYSTEM_PROMPT = (
    "You are an expert No-Limit Hold'em poker coach analysing CoinPoker "
    "cash hand histories. You ground every observation in GTO baseline "
    "expectations and the supplied solver diff when present. Be concise, "
    "concrete, and actionable. Always respond with a single JSON object "
    "matching the schema described in the user message — no markdown, no "
    "code fences, no commentary outside the JSON."
)

_STREET_ORDER: tuple[str, ...] = ("preflop", "flop", "turn", "river", "showdown")


def compute_prompt_hash(
    hand_id: str | object, street: str, scenario_hash: str | None
) -> str:
    """SHA-256 of the cache key tuple ``(hand_id, street, scenario_hash)``.

    ``scenario_hash`` may be ``None`` or empty when the analysis is being
    run on a hand with no solver scenario attached.
    """
    payload = f"{hand_id}:{street}:{scenario_hash or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_analysis_prompt(
    hand: Hand,
    players: Sequence[HandPlayer],
    actions: Sequence[HandAction],
    *,
    street: str,
    scenario_hash: str | None = None,
    solver_summary: dict[str, Any] | None = None,
) -> str:
    """Return the user message body sent to Claude."""

    bb = _safe_decimal(hand.stake_bb) or Decimal("1")
    sb = _safe_decimal(hand.stake_sb) or Decimal("0")

    hero_player = next((p for p in players if p.is_hero), None)
    hero_cards = " ".join(hand.hero_cards or [])

    lines: list[str] = []
    lines.append("# Hand")
    lines.append(f"- coinpoker_hand_id: {hand.coinpoker_hand_id}")
    lines.append(f"- stakes: {sb}/{bb} (big blind = {bb})")
    lines.append(f"- table_size: {hand.table_size}-max")
    lines.append(f"- hero_position: {hand.hero_position}")
    lines.append(f"- hero_cards: {hero_cards or '?'}")
    lines.append(
        f"- hero_net_bb: {hand.hero_net_bb} ({hand.hero_net} chips)"
    )
    if hero_player is not None:
        lines.append(f"- hero_starting_stack: {hero_player.starting_stack}")
    lines.append(f"- focus_street: {street}")
    if scenario_hash:
        lines.append(f"- scenario_hash: {scenario_hash}")

    lines.append("")
    lines.append("# Board")
    lines.append(f"- flop: {_join_cards(hand.flop)}")
    lines.append(f"- turn: {hand.turn or '-'}")
    lines.append(f"- river: {hand.river or '-'}")

    lines.append("")
    lines.append("# Seats")
    for player in players:
        marker = " (hero)" if player.is_hero else ""
        lines.append(
            f"- seat {player.seat}: {player.screen_name} "
            f"[{player.position or '?'}] stack={player.starting_stack}{marker}"
        )

    lines.append("")
    lines.append("# Action sequence")
    lines.extend(_format_actions(actions, bb))

    if solver_summary:
        lines.append("")
        lines.append("# Solver context")
        lines.extend(_format_solver_summary(solver_summary))
    else:
        lines.append("")
        lines.append("# Solver context")
        lines.append("- (no solver scenario attached)")

    lines.append("")
    lines.append("# Allowed leak tags")
    lines.append(
        "Use only tags from this list. Pick the 1–4 that best describe "
        "concrete mistakes hero made; pick none if hero played the spot "
        "well."
    )
    for tag in sorted(LEAK_TAGS):
        lines.append(f"- {tag}")

    lines.append("")
    lines.append("# Response schema")
    lines.append(
        "Respond with a single JSON object and nothing else:"
    )
    lines.append(
        '{"analysis": "<two-to-four-sentence plain-English review of the hand, '
        "focusing on the focus_street and citing the solver diff if "
        'present>", "leak_tags": ["<allowed_tag>", ...]}'
    )

    return "\n".join(lines)


def _format_actions(
    actions: Sequence[HandAction], bb: Decimal
) -> list[str]:
    out: list[str] = []
    by_street: dict[str, list[HandAction]] = {s: [] for s in _STREET_ORDER}
    for action in actions:
        by_street.setdefault(action.street, []).append(action)

    for street in _STREET_ORDER:
        street_actions = by_street.get(street) or []
        if not street_actions:
            continue
        out.append(f"## {street}")
        for action in street_actions:
            out.append("  - " + _format_single_action(action, bb))
    return out


def _format_single_action(action: HandAction, bb: Decimal) -> str:
    name = action.screen_name
    verb = action.action
    flag = " (all-in)" if action.is_all_in else ""

    if verb in ("post_sb", "post_bb"):
        amt = _format_money(action.amount, bb)
        label = "small blind" if verb == "post_sb" else "big blind"
        return f"{name}: posts {label} {amt}"
    if verb == "fold":
        return f"{name}: folds"
    if verb == "check":
        return f"{name}: checks"
    if verb == "call":
        return f"{name}: calls {_format_money(action.amount, bb)}{flag}"
    if verb == "bet":
        return f"{name}: bets {_format_money(action.amount, bb)}{flag}"
    if verb == "raise":
        amt = _format_money(action.amount, bb)
        to = _format_money(action.raise_to, bb)
        return f"{name}: raises {amt} to {to}{flag}"
    if verb == "all_in":
        return f"{name}: is all-in"
    if verb == "show":
        return f"{name}: shows"
    if verb == "muck":
        return f"{name}: mucks"
    if verb == "collect":
        return f"{name}: collects {_format_money(action.amount, bb)}"
    return f"{name}: {verb}"


def _format_money(value: object, bb: Decimal) -> str:
    if value is None:
        return "?"
    dec = _safe_decimal(value)
    if dec is None:
        return str(value)
    if bb and bb != 0:
        bb_units = (dec / bb).quantize(Decimal("0.01"))
        return f"{dec} ({bb_units}bb)"
    return f"{dec}"


def _format_solver_summary(summary: dict[str, Any]) -> list[str]:
    out: list[str] = []
    hero_action = summary.get("hero_action")
    if hero_action:
        out.append(f"- hero_action: {hero_action}")
    solver_best = summary.get("solver_best_action")
    if solver_best:
        out.append(f"- solver_best_action: {solver_best}")
    ev_diff = summary.get("ev_diff_bb")
    if ev_diff is not None:
        out.append(f"- ev_diff_bb: {ev_diff}")
    freqs = summary.get("action_frequencies") or {}
    if isinstance(freqs, dict) and freqs:
        items = ", ".join(
            f"{k}={float(v):.2%}" for k, v in freqs.items() if v is not None
        )
        out.append(f"- solver_action_frequencies: {items}")
    notes = summary.get("notes")
    if notes:
        out.append(f"- notes: {notes}")
    if not out:
        out.append("- (solver summary was empty)")
    return out


def _join_cards(cards: Iterable[str] | None) -> str:
    if not cards:
        return "-"
    return " ".join(cards)


def _safe_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - prompt formatting must never raise
        return None
