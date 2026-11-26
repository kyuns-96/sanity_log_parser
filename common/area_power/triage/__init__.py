"""Instance-level anomaly triage over one frozen comparison view.

Pure code: candidate selection, diagnosis cards, the model request and the
checks applied to the model's answer. No database access and no HTTP. Numbers
come from the comparison view; the model never supplies or changes them.
"""
from .cards import (
    BatchVerdict,
    INSTRUCTIONS,
    ItemVerdict,
    batch_request,
    build_card,
    validate_batch,
)
from .selection import (
    AREA,
    BATCH_SIZE,
    CAUSES,
    CONCENTRATED_ROLLUP_SHARE,
    D,
    DEFAULT_NOISE_SHARE_PCT,
    ENGINE,
    Item,
    MAX_ITEMS,
    POWER_TOTAL,
    PROMPT_VERSION,
    ROLLUP_SHARE,
    RUN_KEYS,
    Selection,
    TOP_K_RANKING,
    Thresholds,
    VERDICTS,
    dominant_subblocks,
    select_items,
    side_context,
)

__all__ = [
    "AREA", "BATCH_SIZE", "BatchVerdict", "CAUSES", "CONCENTRATED_ROLLUP_SHARE", "D",
    "DEFAULT_NOISE_SHARE_PCT", "ENGINE", "INSTRUCTIONS", "Item", "ItemVerdict", "MAX_ITEMS",
    "POWER_TOTAL", "PROMPT_VERSION", "ROLLUP_SHARE", "RUN_KEYS", "Selection", "TOP_K_RANKING",
    "Thresholds", "VERDICTS", "batch_request", "build_card", "dominant_subblocks",
    "select_items", "side_context", "validate_batch",
]
