from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas import FilterOptionsResponse, HandsListParams
from app.table_formats import table_format_from_size


@pytest.mark.parametrize(
    ("table_size", "expected"),
    [(2, "hu_2max"), (6, "6max"), (9, "9max")],
)
def test_table_size_maps_to_exact_table_format(table_size: int, expected: str) -> None:
    assert table_format_from_size(table_size) == expected


def test_unknown_table_size_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported stored table size"):
        table_format_from_size(4)


def test_table_format_schema_rejects_legacy_aggregate_terms() -> None:
    with pytest.raises(ValidationError):
        HandsListParams(table_format="multiway")  # type: ignore[arg-type]


def test_filter_options_use_table_format_contract() -> None:
    payload = FilterOptionsResponse(stakes=[]).model_dump()
    assert payload["table_formats"] == ["hu_2max", "6max", "9max"]
    assert "game_modes" not in payload


def test_hands_openapi_separates_table_format_from_future_state_fields() -> None:
    schema = app.openapi()
    parameters = schema["paths"]["/hands"]["get"]["parameters"]
    parameter_names = {parameter["name"] for parameter in parameters}

    assert "table_format" in parameter_names
    assert "game_mode" not in parameter_names

    hand_summary = schema["components"]["schemas"]["HandSummary"]["properties"]
    assert "table_format" in hand_summary
    assert "table_size" not in hand_summary
    assert "players_reached_flop" not in hand_summary
    assert "solver_eligibility" not in hand_summary
