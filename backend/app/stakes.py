from __future__ import annotations

from decimal import Decimal


STAKE_QUANTUM = Decimal("0.01")


def format_stake(value: Decimal | str) -> str:
    """Return the canonical two-decimal API representation of a stake."""
    return f"{Decimal(value).quantize(STAKE_QUANTUM):.2f}"
