"""Shared Floorplan Review models, orientation geometry, and raw DEF/LEF upload guards."""
from __future__ import annotations

import gzip
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from common.area_power.errors import DomainError

Orient = Literal["N", "S", "E", "W", "FN", "FS", "FE", "FW"]
VALID_ORIENTS: tuple[Orient, ...] = ("N", "S", "E", "W", "FN", "FS", "FE", "FW")

# DEF orientation -> ICC2/Fusion Compiler orientation
DEF_TO_ICC2_ORIENT: dict[str, str] = {
    "N": "R0",
    "S": "R180",
    "FN": "MY",
    "FS": "MX",
    "W": "R90",
    "E": "R270",
    "FW": "MX90",
    "FE": "MY90",
}

_FORBIDDEN_EXTENSIONS = (".def", ".def.gz", ".lef", ".lef.gz", ".tlef", ".tlef.gz", ".oas", ".gds", ".gds.gz")
_RAW_DEF_LEF_HEADER_RE = re.compile(
    rb"(?:^\s*(?:VERSION\s+\d+(?:\.\d+)?\s*;|DESIGN\s+\S+\s*;|UNITS\s+DISTANCE\s+MICRONS|DIEAREA\s*\(|MACRO\s+\S+|BUSBITCHARS\s+\"))",
    re.MULTILINE,
)


def reject_raw_def_or_lef(data: bytes, filename: str = "") -> None:
    """Hard security & DB boundary: raw DEF/LEF/GDS files must NEVER enter VWP-bridge, OA, or PostgreSQL."""
    lower = (filename or "").strip().lower()
    if any(lower.endswith(ext) for ext in _FORBIDDEN_EXTENSIONS):
        raise DomainError(
            "Raw DEF/LEF physical design files cannot be uploaded to the server or database. Run vwp.floorplan to extract a sanitized SVG/scene first.",
            code="raw_def_lef_forbidden",
            status_code=422,
        )
    sample = data[:4096]
    if data[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=__import__("io").BytesIO(data[:65536])) as gz:
                sample = gz.read(4096)
        except Exception:
            sample = b""
    if sample and not sample.lstrip().startswith((b"<svg", b"<?xml", b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"%PDF")):
        if _RAW_DEF_LEF_HEADER_RE.search(sample):
            raise DomainError(
                "Raw DEF/LEF content detected and blocked. Only sanitized SVG/image artifacts are permitted in the database.",
                code="raw_def_lef_forbidden",
                status_code=422,
            )


def oriented_size(width: float, height: float, orient: str) -> tuple[float, float]:
    if orient in ("E", "W", "FE", "FW"):
        return (height, width)
    return (width, height)


def pin1_corner_offset(width: float, height: float, orient: str) -> tuple[float, float]:
    """Return (dx, dy) from (llx, lly) to the macro's logical origin (pin-1 reference corner)."""
    ow, oh = oriented_size(width, height, orient)
    if orient == "N":
        return (0.0, 0.0)
    if orient == "S":
        return (ow, oh)
    if orient == "FN":
        return (ow, 0.0)
    if orient == "FS":
        return (0.0, oh)
    if orient == "W":
        return (ow, 0.0)
    if orient == "E":
        return (0.0, oh)
    if orient == "FW":
        return (0.0, 0.0)
    if orient == "FE":
        return (ow, oh)
    return (0.0, 0.0)


class MacroBox(BaseModel):
    model_config = ConfigDict(extra="ignore")
    inst_name: str = Field(min_length=1, max_length=512)
    master: str = Field(min_length=1, max_length=256)
    kind: Literal["sram", "ip", "subblock", "macro"] = "sram"
    llx: float
    lly: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    orient: Orient = "N"
    status: Literal["FIXED", "PLACED"] = "FIXED"
    halo_um: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    bist_group: str | None = None
    bist_controller: str | None = None
    hier_prefix: str = ""

    def bbox(self) -> tuple[float, float, float, float]:
        ow, oh = oriented_size(self.width, self.height, self.orient)
        return (self.llx, self.lly, round(self.llx + ow, 4), round(self.lly + oh, 4))


class PinMarker(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=256)
    direction: Literal["INPUT", "OUTPUT", "INOUT", "FEEDTHRU"] = "INOUT"
    x: float
    y: float
    layer: str | None = None
    side: Literal["L", "R", "B", "T"] = "L"


class RegionBox(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=256)
    kind: Literal["region", "group", "blockage", "soft_blockage"] = "region"
    points: list[tuple[float, float]] = Field(min_length=3, max_length=256)


class ModuleRegionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hier_path: str = Field(min_length=1, max_length=512)
    cell_count: int = Field(ge=0)
    area_um2: float = Field(ge=0)
    cx: float
    cy: float
    p10_x: float
    p10_y: float
    p90_x: float
    p90_y: float
    bins: list[tuple[int, int, int]] = Field(default_factory=list, max_length=1024)


class BistGroupSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bist_group: str
    controller: str | None = None
    member_count: int
    bbox_um: tuple[float, float, float, float]
    max_manhattan_span_um: float
    dispersed_warning: bool = False


class FloorplanScene(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = 1
    scope_kind: Literal["top", "subblock"] = "subblock"
    design_name: str = Field(min_length=1, max_length=256)
    fp_ver: str = Field(min_length=1, max_length=120)
    prev_fp_ver: str | None = Field(default=None, max_length=120)
    die_bbox: tuple[float, float, float, float]
    die_polygon: list[tuple[float, float]] = Field(min_length=3, max_length=256)
    macros: list[MacroBox] = Field(default_factory=list, max_length=4000)
    pins: list[PinMarker] = Field(default_factory=list, max_length=4000)
    regions: list[RegionBox] = Field(default_factory=list, max_length=1000)
    density_grid_size: int = Field(default=64, ge=8, le=128)
    density_grid: list[int] = Field(default_factory=list, max_length=16384)
    module_regions: list[ModuleRegionSummary] = Field(default_factory=list, max_length=500)
    bist_summary: list[BistGroupSummary] = Field(default_factory=list, max_length=500)
    stats: dict[str, Any] = Field(default_factory=dict)


class MacroOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inst_name: str = Field(min_length=1, max_length=512)
    llx: float
    lly: float
    orient: Orient = "N"
    halo_um: tuple[float, float, float, float] | None = None
    locked: bool = False


class DatapathFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    color: str = Field(default="#2563eb", max_length=24)
    waypoints: list[dict[str, Any]] = Field(min_length=2, max_length=32)
    note: str = Field(default="", max_length=500)


class ReviewPin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    number: int = Field(ge=1, le=999)
    x: float
    y: float
    severity: Literal["PASS", "WARN", "ACTION"] = "ACTION"
    title: str = Field(min_length=1, max_length=200)
    comment: str = Field(default="", max_length=2000)
    resolved: bool = False
    carried_from_fp_ver: str | None = Field(default=None, max_length=120)
    author: str = Field(default="", max_length=120)
