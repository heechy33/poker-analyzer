from __future__ import annotations

import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "coinpoker"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Rewrite CoinPoker parser golden snapshots from fixture text files.",
    )


@pytest.fixture(scope="session")
def parser_module_path() -> str:
    return "app.parser.coinpoker"


@pytest.fixture(scope="session")
def coinpoker_fixture_dir() -> Path:
    return FIXTURE_ROOT


@pytest.fixture
def assert_decimal_equal() -> Callable[[Decimal, Decimal | str], None]:
    def _assert_decimal_equal(actual: Decimal, expected: Decimal | str) -> None:
        assert actual == Decimal(expected).quantize(Decimal("0.0001"))

    return _assert_decimal_equal
