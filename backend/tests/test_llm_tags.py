"""Unit tests for the leak-tag whitelist and LLM response parser.

These cover the contract acceptance criterion *"invalid tags stripped"* —
the model can return anything but we only ever surface enumerated tags.
"""

from __future__ import annotations

import pytest

from app.llm.tags import LEAK_TAGS, filter_leak_tags, parse_llm_response


# ---------------------------------------------------------------------------
# filter_leak_tags
# ---------------------------------------------------------------------------


def test_filter_leak_tags_keeps_known_tags() -> None:
    tags = ["overfold_turn", "missed_thin_value"]
    assert filter_leak_tags(tags) == ["overfold_turn", "missed_thin_value"]


def test_filter_leak_tags_strips_unknown_tags() -> None:
    tags = ["overfold_turn", "made_up_leak", "another_invented_tag"]
    assert filter_leak_tags(tags) == ["overfold_turn"]


def test_filter_leak_tags_strips_non_strings() -> None:
    tags = ["overfold_turn", 42, None, {"x": 1}, ["nested"]]
    assert filter_leak_tags(tags) == ["overfold_turn"]


def test_filter_leak_tags_deduplicates_preserving_order() -> None:
    tags = ["overfold_turn", "missed_thin_value", "overfold_turn"]
    assert filter_leak_tags(tags) == ["overfold_turn", "missed_thin_value"]


def test_filter_leak_tags_empty_returns_empty() -> None:
    assert filter_leak_tags([]) == []
    assert filter_leak_tags(["not_a_real_tag"]) == []


def test_leak_tags_is_non_empty_frozenset() -> None:
    assert isinstance(LEAK_TAGS, frozenset)
    assert len(LEAK_TAGS) >= 25
    assert "overfold_turn" in LEAK_TAGS
    assert "missed_thin_value" in LEAK_TAGS


# ---------------------------------------------------------------------------
# parse_llm_response
# ---------------------------------------------------------------------------


def test_parse_response_plain_json() -> None:
    payload = '{"analysis": "You bet too thin.", "leak_tags": ["missed_thin_value"]}'
    analysis, tags = parse_llm_response(payload)
    assert analysis == "You bet too thin."
    assert tags == ["missed_thin_value"]


def test_parse_response_strips_invalid_tags() -> None:
    payload = (
        '{"analysis": "x", "leak_tags": '
        '["overfold_turn", "bogus_tag", "missed_thin_value"]}'
    )
    _, tags = parse_llm_response(payload)
    assert tags == ["overfold_turn", "missed_thin_value"]


def test_parse_response_handles_code_fence() -> None:
    payload = (
        '```json\n'
        '{"analysis": "fenced reply", "leak_tags": ["overfold_river"]}\n'
        '```'
    )
    analysis, tags = parse_llm_response(payload)
    assert analysis == "fenced reply"
    assert tags == ["overfold_river"]


def test_parse_response_handles_bare_code_fence() -> None:
    payload = (
        '```\n{"analysis": "x", "leak_tags": ["overfold_turn"]}\n```'
    )
    analysis, tags = parse_llm_response(payload)
    assert analysis == "x"
    assert tags == ["overfold_turn"]


def test_parse_response_falls_back_when_not_json() -> None:
    payload = "Not valid JSON, just prose."
    analysis, tags = parse_llm_response(payload)
    assert analysis == "Not valid JSON, just prose."
    assert tags == []


def test_parse_response_empty_string() -> None:
    analysis, tags = parse_llm_response("")
    assert analysis == ""
    assert tags == []


def test_parse_response_missing_leak_tags_field() -> None:
    payload = '{"analysis": "only analysis"}'
    analysis, tags = parse_llm_response(payload)
    assert analysis == "only analysis"
    assert tags == []


def test_parse_response_non_list_leak_tags() -> None:
    payload = '{"analysis": "x", "leak_tags": "not_a_list"}'
    _, tags = parse_llm_response(payload)
    assert tags == []


def test_parse_response_non_dict_json_falls_back() -> None:
    payload = '["just", "an", "array"]'
    analysis, tags = parse_llm_response(payload)
    assert analysis == payload.strip()
    assert tags == []


@pytest.mark.parametrize(
    "tag",
    [
        "overfold_turn",
        "overcall_river",
        "missed_thin_value",
        "skipped_3bet_value",
        "played_passive_oop",
    ],
)
def test_known_tags_present_in_whitelist(tag: str) -> None:
    assert tag in LEAK_TAGS
