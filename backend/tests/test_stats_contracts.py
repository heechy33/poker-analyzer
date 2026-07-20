from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.schemas import StatsSummaryResponse
from app.stats.compute import _row_to_position, _row_to_summary


def _stats_row(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "hands_count": 7,
        "hands": 7,
        "position": "BTN",
        "vpip_pct": Decimal("85.71"),
        "pfr_pct": Decimal("28.57"),
        "three_bet_pct": Decimal("20.00"),
        "wtsd_pct": Decimal("50.00"),
        "wsd_pct": Decimal("50.00"),
        "bb_per_100": Decimal("35.71"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_stats_calculation_layer_preserves_decimal_bb_per_100() -> None:
    summary = _row_to_summary(_stats_row())
    position = _row_to_position(_stats_row())

    assert summary["bb_per_100"] == Decimal("35.71")
    assert isinstance(summary["bb_per_100"], Decimal)
    assert position["bb_per_100"] == Decimal("35.71")
    assert isinstance(position["bb_per_100"], Decimal)


def test_stats_api_boundary_serializes_bb_per_100_as_number() -> None:
    payload = StatsSummaryResponse(**_row_to_summary(_stats_row())).model_dump(
        mode="json"
    )

    assert payload["bb_per_100"] == 35.71
    assert isinstance(payload["bb_per_100"], float)
