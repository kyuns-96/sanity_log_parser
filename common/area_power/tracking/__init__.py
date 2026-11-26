"""Pure Phase 2 domain models, evaluation rules, and mail rendering for NET_VER tracking."""
from .evaluation import (
    compute_deadline,
    effective_corners,
    evaluate_power_target,
    evaluate_single_expectation,
    evaluate_typical_and_same_condition,
    is_full_chip_block,
    match_approved_expectations,
    resolve_versioned_spec,
)
from .mail_html_render import render_block_mail_html, render_top_mail_html
from .mail_render import render_block_mail, render_block_mail_text, render_top_mail, render_top_mail_text
from .snapshots import (
    aggregate_block_tracking_row,
    build_live_round_mail_snapshots,
    compute_next_tracking_action,
    summarize_block_counts,
)
from .models import (
    ApprovedBaselineRef,
    BlockTrackingConfigInput,
    CornerSpec,
    DEFAULT_DEADLINE_DAYS,
    DEFAULT_DEADLINE_TIME,
    DEFAULT_DEADLINE_TIMEZONE,
    ExpectationApprovalInput,
    ExpectationInput,
    ExpectationProposalInput,
    PowerTargetSpec,
    ProjectTrackingConfigInput,
    RoundInput,
    TypicalSpec,
    block_mail_dedupe_key,
    round_dedupe_key,
    slot_dedupe_key,
    top_mail_dedupe_key,
    upload_corner_label,
    upload_corner_tuple,
)

__all__ = [
    "ApprovedBaselineRef", "BlockTrackingConfigInput", "CornerSpec",
    "DEFAULT_DEADLINE_DAYS", "DEFAULT_DEADLINE_TIME", "DEFAULT_DEADLINE_TIMEZONE",
    "ExpectationApprovalInput", "ExpectationInput", "ExpectationProposalInput",
    "PowerTargetSpec", "ProjectTrackingConfigInput", "RoundInput", "TypicalSpec",
    "aggregate_block_tracking_row", "block_mail_dedupe_key",
    "build_live_round_mail_snapshots", "compute_deadline",
    "compute_next_tracking_action", "effective_corners",
    "evaluate_power_target", "evaluate_single_expectation",
    "evaluate_typical_and_same_condition", "is_full_chip_block",
    "match_approved_expectations", "render_block_mail", "render_block_mail_html",
    "render_block_mail_text", "render_top_mail", "render_top_mail_html",
    "render_top_mail_text", "resolve_versioned_spec", "round_dedupe_key",
    "slot_dedupe_key", "summarize_block_counts", "top_mail_dedupe_key",
    "upload_corner_label", "upload_corner_tuple",
]
