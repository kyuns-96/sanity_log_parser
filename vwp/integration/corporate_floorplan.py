"""Corporate VWP adapter points for Floorplan Hard Macro classification, instance attributes, and BIST mapping.

Internal VWP AI allowed scope (`INTERNAL_AI_GUIDE.md` §3.18):
- Implement `create_macro_classifier()` returning a `FloorplanMacroClassifier` subclass (and helper modules
  under `vwp/integration/company_floorplan/`) for company-specific master naming, non-standard LEF classes,
  exact Hard Macro instance lists, and internal instance attributes (`power_domain`, `macro_family`, etc.).
- Implement `extract_bist_groups(...)` to map extracted SRAM instances to `{bist_group, controller}`.
- Read ONLY local files in the VWP run directory; NEVER write to a DB or upload raw DEF/LEF/netlists.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from common.floorplan.classifier import FloorplanMacroClassifier


class CorporateBistNotConfigured(RuntimeError):
    """Raised when --use-corporate-bist is requested before wiring the internal BIST extractor."""


class CorporateClassifierNotConfigured(RuntimeError):
    """Raised when --use-corporate-classifier is requested before wiring a FloorplanMacroClassifier."""


def create_macro_classifier() -> FloorplanMacroClassifier | None:
    """Return a corporate `FloorplanMacroClassifier` instance, or `None` to use built-in LEF/master classification."""
    return None


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
