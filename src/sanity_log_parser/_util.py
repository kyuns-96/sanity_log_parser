from __future__ import annotations


def as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return default


def as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def as_optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def first_non_empty(*values: str | None) -> str:
    for value in values:
        if value:
            return value
    return ""


def trim_to_none(value: str | None) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed else None
    return None
