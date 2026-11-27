"""Corporate VWP adapter point for instance-level Memory BIST group extraction.

Internal VWP AI scope:
- Implement `extract_bist_groups` (and optional helper modules under `vwp/integration/company_floorplan/`).
- Read only local BIST summary/spec files in the VWP run directory; never write to a DB or upload raw DEF/LEF.
- Return a mapping `{inst_path: {"bist_group": "MBIST_G0", "controller": "u_mbist_ctrl_0"}}` (or `{inst_path: "MBIST_G0"}`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class CorporateBistNotConfigured(RuntimeError):
    """Raised when --use-corporate-bist is requested before wiring the internal BIST extractor."""


def extract_bist_groups(
    sram_instances: list[dict[str, Any]],
    *,
    work_dir: Path,
    project: str,
    block: str,
    fp_ver: str,
) -> dict[str, dict[str, str | None] | str]:
    """Return instance-to-BIST-group assignments for extracted SRAM/Memory macros."""
    raise CorporateBistNotConfigured(
        "Corporate BIST extractor is not configured yet. Implement vwp/integration/corporate_floorplan.py "
        "or pass --bist-map <json|csv>."
    )
