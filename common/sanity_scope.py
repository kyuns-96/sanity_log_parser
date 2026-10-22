"""Canonical project/block identity for explicitly scoped legacy reports.

The source's project/design strings remain descriptive metadata.  This contract
does not infer identifiers or authenticate the uploader; callers check the
registered hierarchy and business permissions on the receiving server.
"""
from __future__ import annotations


def validate_scope(value, *, code="INVALID_SCOPE"):
    """Return an independent, exact version-1 scope or fail without echoing IDs."""
    # Keep this helper usable from sanity_common's own validators.
    from common.sanity_common import ReviewError, require_id

    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "project_id", "block_id"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ReviewError(code, "Invalid report project/block scope")
    try:
        require_id(value["project_id"], "project_id")
        require_id(value["block_id"], "block_id")
    except ReviewError:
        raise ReviewError(code, "Invalid report project/block scope") from None
    return dict(value)


def run_scope(run, *, required=False, code="INVALID_SCOPE"):
    """Read optional run.scope; a present null value is never an unscoped run."""
    from common.sanity_common import ReviewError

    if not isinstance(run, dict):
        raise ReviewError(code, "Invalid report run")
    if "scope" not in run:
        if required:
            raise ReviewError(code, "Explicit report project/block scope is required")
        return None
    return validate_scope(run["scope"], code=code)
