from __future__ import annotations

from app.schemas import TableFormat

TABLE_SIZE_BY_FORMAT: dict[TableFormat, int] = {
    "hu_2max": 2,
    "6max": 6,
    "9max": 9,
}

_TABLE_FORMAT_BY_SIZE = {size: table_format for table_format, size in TABLE_SIZE_BY_FORMAT.items()}


def table_format_from_size(table_size: int) -> TableFormat:
    try:
        return _TABLE_FORMAT_BY_SIZE[table_size]
    except KeyError as exc:
        raise ValueError(f"Unsupported stored table size: {table_size}") from exc
