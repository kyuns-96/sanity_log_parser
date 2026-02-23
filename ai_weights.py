from __future__ import annotations


def extract_variable_tail(full_pattern: str, tail_levels: int = 1, tail_weights: list[int] | None = None, variable_position_weights: list[int] | None = None) -> str:
    """
    Extract tail part of VLSI variables (tail part is more important)
    Also supports position-based weighting of variables

    Args:
        full_pattern: Form like 'BLK_CPU/A/B/C/mem_top_ABC'
        tail_levels: How many levels from the tail to extract (default 1)
        tail_weights: Weight list for each level
                      Example: [2, 3] -> last is 2x, previous is 3x
        variable_position_weights: Position-based weights for variables
                      Example: [3, 2, 1] -> 1st variable 3x, 2nd 2x, 3rd 1x

    Returns:
        Tail string with weights applied

    Example:
        full_pattern = 'BLK_CPU/A/B/C/mem_top_ABC'

        tail_levels=1, tail_weights=[2]
        -> 'ABC ABC'

        tail_levels=2, tail_weights=[3, 2]
        -> 'mem_top mem_top mem_top ABC ABC'

        If variable tuple is ('var1', 'var2', 'var3') and
        variable_position_weights=[3, 2, 1], then
        -> 'var1 var1 var1 var2 var2 var3'
    """
    if ' / ' not in full_pattern:
        return full_pattern

    parts = full_pattern.split(' / ')

    # Extract tail levels
    tail_parts = parts[-tail_levels:] if tail_levels <= len(parts) else parts

    # Set weights (default: all 1)
    if tail_weights is None:
        tail_weights = [1] * len(tail_parts)
    else:
        # Pad tail_weights with last value if insufficient
        while len(tail_weights) < len(tail_parts):
            tail_weights.append(tail_weights[-1] if tail_weights else 1)

    # Repeat each part by its weight
    result = []
    for part, weight in zip(tail_parts, tail_weights):
        result.extend([part] * weight)

    # Apply variable position weights if provided
    if variable_position_weights:
        result = apply_variable_position_weights(result, variable_position_weights)

    return ' '.join(result)


def apply_variable_position_weights(parts: list[str], variable_position_weights: list[int]) -> list[str]:
    """
    Apply position-based weights to variable substrings
    Example: parts=['mem_top', 'ABC'], variable_position_weights=[3, 2]
    -> ['mem_top', 'mem_top', 'mem_top', 'ABC', 'ABC']
    """
    if not parts or not variable_position_weights:
        return parts

    result = []
    for i, part in enumerate(parts):
        # Get position weight (use last if insufficient)
        weight_idx = min(i, len(variable_position_weights) - 1)
        weight = variable_position_weights[weight_idx]
        result.extend([part] * weight)

    return result
