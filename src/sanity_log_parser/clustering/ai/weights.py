from __future__ import annotations


def extract_variable_tail(
    full_pattern: str,
    tail_levels: int = 1,
    tail_weights: list[int] | None = None,
    variable_position_weights: list[int] | None = None,
) -> str:
    if " / " not in full_pattern:
        return full_pattern

    parts = full_pattern.split(" / ")
    tail_parts = parts[-tail_levels:] if tail_levels <= len(parts) else parts

    if tail_weights is None:
        tail_weights = [1] * len(tail_parts)
    else:
        while len(tail_weights) < len(tail_parts):
            tail_weights.append(tail_weights[-1] if tail_weights else 1)

    result = []
    for part, weight in zip(tail_parts, tail_weights):
        result.extend([part] * weight)

    if variable_position_weights:
        result = apply_variable_position_weights(result, variable_position_weights)

    return " ".join(result)


def apply_variable_position_weights(
    parts: list[str], variable_position_weights: list[int]
) -> list[str]:
    if not parts or not variable_position_weights:
        return parts

    result = []
    for i, part in enumerate(parts):
        weight_idx = min(i, len(variable_position_weights) - 1)
        weight = variable_position_weights[weight_idx]
        result.extend([part] * weight)

    return result
