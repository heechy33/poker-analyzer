"""Prompt assembly for the per-hand Anthropic analysis call.

The Phase 0 prompt is intentionally solver-free. It supplies the uploaded
hand facts needed for general post-session coaching and asks the model for a
strict JSON envelope containing analysis text plus enumerated leak tags. See
:mod:`app.llm.tags` for the tag whitelist.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from decimal import Decimal
from app.llm.tags import LEAK_TAGS
from app.models import Hand, HandAction, HandPlayer
from app.table_formats import table_format_from_size

GENERAL_COACHING_LABEL = "General coaching—no verified solver result."
COACH_PROMPT_VERSION = "general-v1"

SYSTEM_PROMPT = (
    "You are an expert No-Limit Hold'em poker coach reviewing a player's own "
    "CoinPoker cash hand after the session. No verified solver result is "
    "available. Give conceptual general coaching only: do not claim that a "
    "solver or GTO model chose an action, and do not invent frequencies, EVs, "
    "ranges, or other numerical strategy facts. Be concise, concrete, and "
    "actionable. Always respond with a single JSON object matching the schema "
    "described in the user message—no markdown, code fences, or commentary "
    "outside the JSON."
)

_STREET_ORDER: tuple[str, ...] = ("preflop", "flop", "turn", "river", "showdown")


def compute_prompt_hash(hand_id: str | object, street: str) -> str:
    """Hash the hand, street, and coaching prompt contract version.

    Including the version prevents Phase 0 from replaying an older cached
    response that may have been grounded in an unverified solver summary.
    """
    payload = f"{COACH_PROMPT_VERSION}:{hand_id}:{street}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_analysis_prompt(
    hand: Hand,
    players: Sequence[HandPlayer],
    actions: Sequence[HandAction],
    *,
    street: str,
) -> str:
    """Return the solver-free user message body sent to the coach model."""

    bb = _safe_decimal(hand.stake_bb) or Decimal("1")
    sb = _safe_decimal(hand.stake_sb) or Decimal("0")

    hero_player = next((p for p in players if p.is_hero), None)
    hero_cards = " ".join(hand.hero_cards or [])

    lines: list[str] = []
    lines.append("# Hand")
    lines.append(f"- coinpoker_hand_id: {hand.coinpoker_hand_id}")
    lines.append(f"- stakes: {sb}/{bb} (big blind = {bb})")
    lines.append(f"- table_format: {table_format_from_size(hand.table_size)}")
    lines.append(f"- hero_position: {hand.hero_position}")
    lines.append(f"- hero_cards: {hero_cards or '?'}")
    lines.append(
        f"- hero_net_bb: {hand.hero_net_bb} ({hand.hero_net} chips)"
    )
    if hero_player is not None:
        lines.append(f"- hero_starting_stack: {hero_player.starting_stack}")
    lines.append(f"- focus_street: {street}")

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

    lines.append("")
    lines.append("# Coaching mode")
    lines.append(f"- {GENERAL_COACHING_LABEL}")
    lines.append("- Explain concepts from the hand facts above.")
    lines.append("- Do not say 'the solver says' or invent solver numbers.")

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
        'focusing on the focus_street and making no solver claim>", "leak_tags": '
        '["<allowed_tag>", ...]}'
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
