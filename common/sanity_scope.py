"""Canonical project/block identity for explicitly scoped legacy reports.

The source's project/design strings remain descriptive metadata.  This contract
does not infer identifiers or authenticate the uploader; callers check the
registered hierarchy and business permissions on the receiving server.
"""
from __future__ import annotations

from common.sanity_common import run_scope, validate_scope

__all__ = ["run_scope", "validate_scope"]
