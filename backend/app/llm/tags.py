"""Enumerated leak tags used by the Anthropic hand-analysis pipeline.

The model is instructed to respond with JSON whose ``leak_tags`` field is a
list of strings. Any tag not in :data:`LEAK_TAGS` is dropped so the dashboard
aggregation never has to deal with arbitrary free-form strings.
"""

from __future__ import annotations

import json
from typing import Iterable

LEAK_TAGS: frozenset[str] = frozenset(
    {
        # Preflop
        "overfold_preflop",
        "overcall_preflop",
        "skipped_3bet_value",
        "skipped_3bet_bluff",
        "open_too_wide",
        "open_too_tight",
        "limp_strong_hand",
        "called_dominated_offsuit",
        "isolation_too_loose",
        # Flop
        "overfold_vs_cbet",
        "underfold_vs_cbet",
        "skipped_cbet_value",
        "overuse_small_cbet",
        "missed_check_raise_value",
        "check_raise_too_thin",
        "donk_bet_misuse",
        # Turn
        "overfold_turn",
        "overcall_turn",
        "failed_to_barrel_turn",
        "barrel_turn_too_thin",
        "missed_protection_bet",
        "over_size_turn_bet",
        "under_size_turn_bet",
        # River
        "overfold_river",
        "overcall_river",
        "missed_thin_value",
        "overbluff_river",
        "underbluff_river",
        "bluff_without_blockers",
        "thin_value_too_large",
        "called_blocker_heavy_bluff_catcher",
        # General / strategic
        "played_passive_ip",
        "played_passive_oop",
        "stacked_off_top_pair",
        "ignored_position_disadvantage",
        "wrong_bet_sizing_polar",
    }
)


def filter_leak_tags(tags: Iterable[object]) -> list[str]:
    """Return the subset of ``tags`` that are known leak labels.

    Non-string entries are silently dropped. Order is preserved and
    duplicates are removed.
    """
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            continue
        if tag not in LEAK_TAGS:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def parse_llm_response(text: str) -> tuple[str, list[str]]:
    """Parse the model's JSON response.

    The model is instructed to reply with ``{"analysis": "...", "leak_tags": [...]}``.
    We are lenient about Markdown code-fence wrapping and fall back to the
    raw text if JSON parsing fails entirely (so the user still gets *some*
    analysis even if the model strayed from the schema).
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        # Drop opening fence (```json or ```).
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return text.strip(), []

    if not isinstance(data, dict):
        return text.strip(), []

    analysis_raw = data.get("analysis", "")
    analysis = str(analysis_raw).strip() if analysis_raw is not None else ""
    if not analysis:
        analysis = text.strip()

    raw_tags = data.get("leak_tags") or []
    if not isinstance(raw_tags, list):
        raw_tags = []
    leak_tags = filter_leak_tags(raw_tags)

    return analysis, leak_tags
