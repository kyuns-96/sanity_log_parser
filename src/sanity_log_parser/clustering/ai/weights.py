from __future__ import annotations


def select_levels(
    path: str,
    levels: list[int] | None = None,
    separator: str = "/",
) -> str:
    """Select specific hierarchy levels from a path string.

    ``levels`` uses Python-style indexing (negative indices count from the end).
    Returns space-separated selected parts.  If ``levels`` is ``None``, all
    parts are returned (with the separator replaced by spaces).
    """
    parts = [p.strip() for p in path.split(separator)]

    if levels is None:
        return " ".join(parts)

    selected: list[str] = []
    for idx in levels:
        try:
            selected.append(parts[idx])
        except IndexError:
            continue

    return " ".join(selected)
