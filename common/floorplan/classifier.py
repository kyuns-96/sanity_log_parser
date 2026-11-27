"""Abstract Hard Macro classifier & corporate instance-attribute contract (VWP local only).

Why this abstraction exists:
- Generic DEF/LEF heuristics MUST NEVER match regexes against full `inst_name` hierarchy paths
  (e.g. standard cells under `u_sram_ctrl/u_arb/U1234` would cause hundreds of thousands of false positives).
- Company-specific master naming, non-standard LEF classes, and internal instance attributes
  (power/voltage domain, macro family, custom IP classification, BIST mapping, exact macro lists)
  are known only inside the corporate VWP environment.
- Internal VWP AI implements a `FloorplanMacroClassifier` subclass in `vwp/integration/company_floorplan/`
  and wires `vwp/integration/corporate_floorplan.py:create_macro_classifier()` without modifying any
  protected common parser code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Literal

MacroKind = Literal["sram", "ip", "subblock", "macro"]

# Master-name-only fallback patterns (NEVER applied to `inst_name` hierarchy paths!)
_SRAM_MASTER_RE = re.compile(r"(?:sram|rf_|regfile|rom_|ram_|mem_|spsram|dpsram|2prf|1prf|tcam|cam_)", re.IGNORECASE)
_HARD_IP_MASTER_RE = re.compile(r"(?:pll|phy|serdes|pcie|usb|mipi|ddr|adc|dac|ldo|bandgap|efuse|otp|tsensor|osc|analog|pmu_ip)", re.IGNORECASE)
_STD_EXCLUDE_MASTER_RE = re.compile(r"(?:^(?:fill|decap|welltap|tap_|endcap|bound|tie|ant_|spare)|_(?:fill|decap|tap|endcap)\b)", re.IGNORECASE)


def sanitize_macro_attributes(raw: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    """Keep only bounded scalar review attributes (max 24 keys, max 200 chars) safe for FloorplanScene JSON."""
    if not raw or not isinstance(raw, dict):
        return {}
    out: dict[str, str | int | float | bool] = {}
    for k, v in raw.items():
        if len(out) >= 24:
            break
        key = str(k).strip()[:64]
        if not key or v is None:
            continue
        if isinstance(v, (bool, int, float)):
            out[key] = v
        else:
            val = str(v).strip()
            if val:
                out[key] = val[:200]
    return out


@dataclass(frozen=True)
class MacroClassification:
    """Result of classifying a master or enriching a Hard Macro instance."""

    is_hard_macro: bool
    kind: MacroKind = "macro"
    width_um: float | None = None
    height_um: float | None = None
    bist_group: str | None = None
    bist_controller: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class FloorplanMacroClassifier(ABC):
    """Abstract corporate classifier & instance-property provider for VWP local DEF/LEF extraction.

    Performance & safety contract:
    1. `prepare(...)` runs ONCE in the parent process before `fork` (read-only inside local VWP files).
    2. `classify_master(...)` runs ONCE per unique DEF/LEF `master` (~500-3,000 calls, memoized by parser):
       - Return `MacroClassification(is_hard_macro=True, kind=...)` to treat all instances of this master as Hard Macros.
       - Return `MacroClassification(is_hard_macro=False)` to force standard-cell treatment (overrides built-in heuristics).
       - Return `None` to delegate to `DefaultMacroClassifier` (LEF class/size + master-name fallback).
    3. `exact_hard_macro_instances()` optionally returns exact `inst_name` paths known to be Hard Macros
       (checked via O(1) bytes set lookup without scanning regexes over standard-cell hierarchy paths).
    4. `enrich_macro_instance(...)` runs ONLY on identified Hard Macro instances (~10-4,000 calls) to attach
       internal instance attributes (`power_domain`, `macro_family`, `bist_group`, `kind`, `width_um`/`height_um`, etc.),
       or return `None` to exclude a specific instance from `scene.macros`.
    """

    def prepare(
        self,
        *,
        work_dir: Path,
        project: str,
        block: str,
        fp_ver: str,
        scope_kind: str,
        lef_macros: dict[str, dict[str, Any]],
    ) -> None:
        return None

    def exact_hard_macro_instances(self) -> set[str]:
        return set()

    @abstractmethod
    def classify_master(
        self,
        master: str,
        lef_info: dict[str, Any] | None,
        *,
        scope_kind: str,
    ) -> MacroClassification | None:
        """Classify a unique cell `master` (called once per unique master, never per std-cell instance)."""

    def enrich_macro_instance(
        self,
        inst_name: str,
        master: str,
        base: MacroClassification,
        *,
        status: str,
        llx: float,
        lly: float,
        orient: str,
    ) -> MacroClassification | None:
        """Optionally enrich or filter an identified Hard Macro instance."""
        return base


class DefaultMacroClassifier(FloorplanMacroClassifier):
    """Built-in LEF-class/size and master-name-only classifier (never matches `inst_name` hierarchy paths)."""

    def classify_master(
        self,
        master: str,
        lef_info: dict[str, Any] | None,
        *,
        scope_kind: str,
    ) -> MacroClassification:
        if _STD_EXCLUDE_MASTER_RE.search(master):
            return MacroClassification(is_hard_macro=False)
        if lef_info:
            mclass = str(lef_info.get("class") or "CORE").upper()
            msub = str(lef_info.get("subclass") or "").upper()
            w = float(lef_info.get("width") or 0.0)
            h = float(lef_info.get("height") or 0.0)
            if mclass in ("ENDCAP", "SPACER") or msub in ("SPACER", "WELLTAP", "TIEHIGH", "TIELOW", "ANTENNACELL", "BUMP"):
                return MacroClassification(is_hard_macro=False)
            if _SRAM_MASTER_RE.search(master):
                return MacroClassification(is_hard_macro=True, kind="sram", width_um=w or None, height_um=h or None)
            if mclass in ("BLOCK", "RING", "COVER", "PAD"):
                if scope_kind == "top" and mclass == "BLOCK" and msub != "RING" and not _HARD_IP_MASTER_RE.search(master):
                    return MacroClassification(is_hard_macro=True, kind="subblock", width_um=w or None, height_um=h or None)
                if _HARD_IP_MASTER_RE.search(master) or mclass in ("RING", "COVER", "PAD"):
                    return MacroClassification(is_hard_macro=True, kind="ip", width_um=w or None, height_um=h or None)
                return MacroClassification(is_hard_macro=True, kind="macro", width_um=w or None, height_um=h or None)
            if min(w, h) >= 5.0 and (w * h) >= 25.0:
                kind: MacroKind = "ip" if _HARD_IP_MASTER_RE.search(master) else "macro"
                return MacroClassification(is_hard_macro=True, kind=kind, width_um=w, height_um=h)
            return MacroClassification(is_hard_macro=False)

        # No LEF entry available: inspect ONLY `master` name (never `inst_name` hierarchy path!)
        if _SRAM_MASTER_RE.search(master):
            return MacroClassification(is_hard_macro=True, kind="sram")
        if _HARD_IP_MASTER_RE.search(master):
            return MacroClassification(is_hard_macro=True, kind="ip")
        return MacroClassification(is_hard_macro=False)
