"""Streaming VWP-local DEF(.gz) + LEF(.gz) extractor producing a compact FloorplanScene.

Raw DEF/LEF files are read strictly locally on VWP in O(1) line memory and never uploaded.
"""
from __future__ import annotations

import gzip
from pathlib import Path
import re
from typing import Any, Iterable, TextIO

from .models import (
    BistGroupSummary,
    FloorplanScene,
    MacroBox,
    ModuleRegionSummary,
    Orient,
    PinMarker,
    RegionBox,
    VALID_ORIENTS,
    oriented_size,
)

_SRAM_RE = re.compile(r"(?:sram|rf_|regfile|rom_|ram_|mem_|spsram|dpsram|2prf|1prf)", re.IGNORECASE)
_PT_RE = re.compile(r"\(\s*([+-]?\d+|\*)\s+([+-]?\d+|\*)\s*\)")
_COMP_LINE_RE = re.compile(
    r"^-\s+(\S+)\s+(\S+)(.*?)\+\s*(FIXED|PLACED|COVER)\s*\(\s*([+-]?\d+)\s+([+-]?\d+)\s*\)\s*(\S+)",
)
_HALO_RE = re.compile(r"\+\s*HALO(?:\s+SOFT)?\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")


def _open_text(path: Path) -> TextIO:
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_lef_macros(lef_paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Extract only MACRO name, CLASS, and SIZE (width, height in um) from LEF headers."""
    macros: dict[str, dict[str, Any]] = {}
    macro_start = re.compile(r"^\s*MACRO\s+(\S+)", re.IGNORECASE)
    class_re = re.compile(r"^\s*CLASS\s+(\S+)(?:\s+(\S+))?", re.IGNORECASE)
    size_re = re.compile(r"^\s*SIZE\s+([0-9.eE+-]+)\s+BY\s+([0-9.eE+-]+)", re.IGNORECASE)

    for path in lef_paths:
        cur_name: str | None = None
        cur_class = "CORE"
        cur_sub = ""
        cur_w: float | None = None
        cur_h: float | None = None
        with _open_text(path) as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if cur_name is None:
                    m = macro_start.match(stripped)
                    if m:
                        cur_name = m.group(1)
                        cur_class, cur_sub, cur_w, cur_h = "CORE", "", None, None
                    continue
                if stripped.upper().startswith(f"END {cur_name.upper()}"):
                    if cur_w and cur_h and cur_w > 0 and cur_h > 0:
                        macros[cur_name] = {
                            "class": cur_class.upper(),
                            "subclass": cur_sub.upper(),
                            "width": float(cur_w),
                            "height": float(cur_h),
                        }
                    cur_name = None
                    continue
                cm = class_re.match(stripped)
                if cm:
                    cur_class = cm.group(1)
                    cur_sub = cm.group(2) or ""
                    continue
                sm = size_re.match(stripped)
                if sm:
                    cur_w = float(sm.group(1))
                    cur_h = float(sm.group(2))
    return macros


def _expand_def_points(raw_pts: list[tuple[str, str]], dbu: float) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    prev_x, prev_y = 0.0, 0.0
    for rx, ry in raw_pts:
        x = prev_x if rx == "*" else int(rx) / dbu
        y = prev_y if ry == "*" else int(ry) / dbu
        pts.append((round(x, 4), round(y, 4)))
        prev_x, prev_y = x, y
    if len(pts) == 2:
        (x1, y1), (x2, y2) = pts
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return pts


def _classify_macro(inst_name: str, master: str, lef_info: dict[str, Any] | None, in_bist: bool, scope_kind: str) -> str | None:
    if in_bist or _SRAM_RE.search(master) or _SRAM_RE.search(inst_name):
        return "sram"
    if not lef_info:
        return None
    mclass = lef_info.get("class", "CORE")
    if mclass in ("BLOCK", "RING", "COVER", "PAD", "ENDCAP"):
        if mclass in ("ENDCAP",):
            return None
        if scope_kind == "top" and lef_info.get("subclass") != "RING" and not _SRAM_RE.search(master):
            return "subblock"
        return "ip" if mclass in ("BLOCK", "RING", "PAD", "COVER") else None
    return None


def _pin_side(x: float, y: float, die_bbox: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = die_bbox
    dists = [("L", abs(x - x0)), ("R", abs(x1 - x)), ("B", abs(y - y0)), ("T", abs(y1 - y))]
    dists.sort(key=lambda item: item[1])
    return dists[0][0]


class _ModuleAcc:
    __slots__ = ("count", "area", "sum_x", "sum_y", "xs", "ys", "tiles")

    def __init__(self) -> None:
        self.count = 0
        self.area = 0.0
        self.sum_x = 0.0
        self.sum_y = 0.0
        self.xs: list[float] = []
        self.ys: list[float] = []
        self.tiles: dict[tuple[int, int], int] = {}

    def add(self, x: float, y: float, cell_area: float, tx: int, ty: int) -> None:
        self.count += 1
        self.area += cell_area
        self.sum_x += x
        self.sum_y += y
        # Reservoir-like stride sampling keeps memory tiny even for 10M cells
        if self.count <= 512 or self.count % 16 == 0:
            if len(self.xs) < 2048:
                self.xs.append(x)
                self.ys.append(y)
        key = (tx, ty)
        self.tiles[key] = self.tiles.get(key, 0) + 1


def parse_def_to_scene(
    def_path: Path,
    lef_macros: dict[str, dict[str, Any]],
    *,
    fp_ver: str,
    prev_fp_ver: str | None = None,
    scope_kind: str = "subblock",
    bist_map: dict[str, dict[str, str | None] | str] | None = None,
    hierarchy_separator: str = "/",
    grid_size: int = 64,
) -> FloorplanScene:
    """Stream-parse DEF(.gz) + LEF sizes into a lightweight FloorplanScene without retaining raw DEF."""
    bist_lookup: dict[str, tuple[str, str | None]] = {}
    for k, v in (bist_map or {}).items():
        clean_k = k.strip().replace("\\", "")
        if isinstance(v, str):
            bist_lookup[clean_k] = (v.strip(), None)
        elif isinstance(v, dict) and v.get("bist_group"):
            bist_lookup[clean_k] = (str(v["bist_group"]).strip(), str(v["controller"]).strip() if v.get("controller") else None)

    design_name = def_path.stem.removesuffix(".def")
    dbu = 1000.0
    die_polygon: list[tuple[float, float]] = []
    die_bbox = (0.0, 0.0, 1000.0, 1000.0)
    macros: list[MacroBox] = []
    pins: list[PinMarker] = []
    regions: list[RegionBox] = []
    density = [0.0] * (grid_size * grid_size)
    mod_accs: dict[str, _ModuleAcc] = {}
    std_cell_count = 0
    std_cell_area = 0.0

    section: str | None = None
    stmt_buf: list[str] = []

    def _hier_prefix(inst_name: str) -> str:
        clean = inst_name.replace("\\", "")
        if hierarchy_separator not in clean:
            return ""
        parts = [p for p in clean.split(hierarchy_separator) if p]
        if len(parts) <= 1:
            return ""
        return hierarchy_separator.join(parts[: min(2, len(parts) - 1)])

    def _handle_component(stmt: str) -> None:
        nonlocal std_cell_count, std_cell_area
        m = _COMP_LINE_RE.match(stmt)
        if not m:
            return
        raw_inst, master, middle, status, sx, sy, raw_orient = m.groups()
        inst = raw_inst.replace("\\", "")
        orient: Orient = raw_orient if raw_orient in VALID_ORIENTS else "N"  # type: ignore[assignment]
        x = int(sx) / dbu
        y = int(sy) / dbu
        lef_info = lef_macros.get(master)
        bist_entry = bist_lookup.get(inst) or bist_lookup.get(raw_inst)
        mkind = _classify_macro(inst, master, lef_info, bist_entry is not None, scope_kind)
        if mkind is not None or (status == "FIXED" and lef_info and lef_info["width"] * lef_info["height"] >= 25.0):
            w = lef_info["width"] if lef_info else 40.0
            h = lef_info["height"] if lef_info else 40.0
            halo = (0.0, 0.0, 0.0, 0.0)
            hm = _HALO_RE.search(stmt)
            if hm:
                halo = tuple(round(int(hm.group(i)) / dbu, 3) for i in range(1, 5))  # type: ignore[assignment]
            if len(macros) < 4000:
                macros.append(
                    MacroBox(
                        inst_name=inst,
                        master=master,
                        kind=mkind or "macro",  # type: ignore[arg-type]
                        llx=round(x, 4),
                        lly=round(y, 4),
                        width=round(w, 4),
                        height=round(h, 4),
                        orient=orient,
                        status="FIXED" if status == "FIXED" else "PLACED",
                        halo_um=halo,
                        bist_group=bist_entry[0] if bist_entry else None,
                        bist_controller=bist_entry[1] if bist_entry else None,
                        hier_prefix=_hier_prefix(inst),
                    )
                )
            return

        # Standard cell -> aggregate into coarse density grid and module summaries
        cw = lef_info["width"] if lef_info else 0.5
        ch = lef_info["height"] if lef_info else 0.5
        c_area = cw * ch
        std_cell_count += 1
        std_cell_area += c_area
        x0, y0, x1, y1 = die_bbox
        span_x = max(x1 - x0, 1e-6)
        span_y = max(y1 - y0, 1e-6)
        gx = min(grid_size - 1, max(0, int((x - x0) / span_x * grid_size)))
        gy = min(grid_size - 1, max(0, int((y - y0) / span_y * grid_size)))
        density[gy * grid_size + gx] += c_area
        prefix = _hier_prefix(inst)
        if prefix:
            tx = min(31, max(0, int((x - x0) / span_x * 32)))
            ty = min(31, max(0, int((y - y0) / span_y * 32)))
            acc = mod_accs.get(prefix)
            if acc is None and len(mod_accs) < 600:
                acc = _ModuleAcc()
                mod_accs[prefix] = acc
            if acc is not None:
                acc.add(x, y, c_area, tx, ty)

    def _handle_pin(stmt: str) -> None:
        if len(pins) >= 4000:
            return
        head = re.match(r"^-\s+(\S+)", stmt)
        if not head:
            return
        name = head.group(1)
        dir_m = re.search(r"\+\s*DIRECTION\s+(INPUT|OUTPUT|INOUT|FEEDTHRU)", stmt, re.IGNORECASE)
        direction = dir_m.group(1).upper() if dir_m else "INOUT"
        loc_m = re.search(r"\+\s*(?:PLACED|FIXED|COVER)\s*\(\s*([+-]?\d+)\s+([+-]?\d+)\s*\)", stmt, re.IGNORECASE)
        layer_m = re.search(r"\+\s*LAYER\s+(\S+)", stmt, re.IGNORECASE)
        if not loc_m:
            pts = _PT_RE.findall(stmt)
            if not pts or pts[0][0] == "*" or pts[0][1] == "*":
                return
            px, py = int(pts[0][0]) / dbu, int(pts[0][1]) / dbu
        else:
            px, py = int(loc_m.group(1)) / dbu, int(loc_m.group(2)) / dbu
        pins.append(
            PinMarker(
                name=name,
                direction=direction,  # type: ignore[arg-type]
                x=round(px, 4),
                y=round(py, 4),
                layer=layer_m.group(1) if layer_m else None,
                side=_pin_side(px, py, die_bbox),  # type: ignore[arg-type]
            )
        )

    def _handle_region_or_blockage(stmt: str, sec: str) -> None:
        if len(regions) >= 1000:
            return
        raw_pts = _PT_RE.findall(stmt)
        if len(raw_pts) < 2:
            return
        if sec == "BLOCKAGES":
            if "LAYER" in stmt.upper() and "PLACEMENT" not in stmt.upper():
                return
            kind = "soft_blockage" if "SOFT" in stmt.upper() or "PARTIAL" in stmt.upper() else "blockage"
            for i in range(0, len(raw_pts) - 1, 2):
                box = _expand_def_points([raw_pts[i], raw_pts[i + 1]], dbu)
                if len(box) >= 3 and len(regions) < 1000:
                    regions.append(RegionBox(name=f"BLK_{len(regions) + 1}", kind=kind, points=box))  # type: ignore[arg-type]
        else:
            nm = re.match(r"^-\s+(\S+)", stmt)
            rname = nm.group(1) if nm else f"REG_{len(regions) + 1}"
            for i in range(0, len(raw_pts) - 1, 2):
                box = _expand_def_points([raw_pts[i], raw_pts[i + 1]], dbu)
                if len(box) >= 3 and len(regions) < 1000:
                    regions.append(RegionBox(name=rname, kind="region", points=box))

    with _open_text(def_path) as handle:
        for raw_line in handle:
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if section is None:
                if line.upper().startswith("DESIGN "):
                    dm = re.match(r"^DESIGN\s+(\S+)", line, re.IGNORECASE)
                    if dm:
                        design_name = dm.group(1).rstrip(";")
                elif line.upper().startswith("UNITS DISTANCE MICRONS"):
                    um = re.search(r"MICRONS\s+(\d+)", line, re.IGNORECASE)
                    if um:
                        dbu = float(um.group(1))
                elif line.upper().startswith("DIEAREA"):
                    stmt_buf = [line]
                    if ";" in line:
                        pts = _expand_def_points(_PT_RE.findall(line), dbu)
                        if len(pts) >= 3:
                            die_polygon = pts
                            xs = [p[0] for p in pts]
                            ys = [p[1] for p in pts]
                            die_bbox = (min(xs), min(ys), max(xs), max(ys))
                        stmt_buf = []
                    else:
                        section = "DIEAREA"
                elif re.match(r"^(COMPONENTS|PINS|BLOCKAGES|REGIONS)\b", line, re.IGNORECASE):
                    section = line.split()[0].upper()
                    stmt_buf = []
                continue

            if section == "DIEAREA":
                stmt_buf.append(line)
                if ";" in line:
                    joined = " ".join(stmt_buf)
                    pts = _expand_def_points(_PT_RE.findall(joined), dbu)
                    if len(pts) >= 3:
                        die_polygon = pts
                        xs = [p[0] for p in pts]
                        ys = [p[1] for p in pts]
                        die_bbox = (min(xs), min(ys), max(xs), max(ys))
                    stmt_buf = []
                    section = None
                continue

            if line.upper().startswith(f"END {section}"):
                stmt_buf = []
                section = None
                continue

            if line.startswith("-") and stmt_buf:
                stmt_buf = [line]
            else:
                stmt_buf.append(line)

            if ";" in line:
                joined = " ".join(stmt_buf)
                stmt_buf = []
                if joined.startswith("-"):
                    if section == "COMPONENTS":
                        _handle_component(joined)
                    elif section == "PINS":
                        _handle_pin(joined)
                    elif section in ("BLOCKAGES", "REGIONS"):
                        _handle_region_or_blockage(joined, section)

    if not die_polygon:
        if macros:
            xs = [b for m in macros for b in (m.llx, m.bbox()[2])]
            ys = [b for m in macros for b in (m.lly, m.bbox()[3])]
            pad = 20.0
            die_bbox = (round(min(xs) - pad, 2), round(min(ys) - pad, 2), round(max(xs) + pad, 2), round(max(ys) + pad, 2))
        x0, y0, x1, y1 = die_bbox
        die_polygon = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    x0, y0, x1, y1 = die_bbox
    tile_area = max(((x1 - x0) / grid_size) * ((y1 - y0) / grid_size), 1e-6)
    density_pct = [min(100, int(round((val / tile_area) * 100))) for val in density]

    sorted_mods = sorted(mod_accs.items(), key=lambda kv: kv[1].area, reverse=True)[:250]
    module_regions: list[ModuleRegionSummary] = []
    for path_key, acc in sorted_mods:
        if acc.count == 0:
            continue
        sxs = sorted(acc.xs)
        sys_ = sorted(acc.ys)
        n = len(sxs)
        p10_x = sxs[int(n * 0.1)]
        p90_x = sxs[min(n - 1, int(n * 0.9))]
        p10_y = sys_[int(n * 0.1)]
        p90_y = sys_[min(n - 1, int(n * 0.9))]
        max_tile = max(acc.tiles.values()) if acc.tiles else 1
        bins = [
            (tx, ty, max(1, int(round(cnt / max_tile * 100))))
            for (tx, ty), cnt in sorted(acc.tiles.items())
            if cnt / max_tile >= 0.05
        ][:512]
        module_regions.append(
            ModuleRegionSummary(
                hier_path=path_key,
                cell_count=acc.count,
                area_um2=round(acc.area, 2),
                cx=round(acc.sum_x / acc.count, 2),
                cy=round(acc.sum_y / acc.count, 2),
                p10_x=round(p10_x, 2),
                p10_y=round(p10_y, 2),
                p90_x=round(p90_x, 2),
                p90_y=round(p90_y, 2),
                bins=bins,
            )
        )

    # Summarize BIST groups
    bist_groups: dict[str, list[MacroBox]] = {}
    for m in macros:
        if m.bist_group:
            bist_groups.setdefault(m.bist_group, []).append(m)
    die_diag_manhattan = max((x1 - x0) + (y1 - y0), 1.0)
    bist_summary: list[BistGroupSummary] = []
    for gname, members in sorted(bist_groups.items()):
        g_llx = min(item.llx for item in members)
        g_lly = min(item.lly for item in members)
        g_urx = max(item.bbox()[2] for item in members)
        g_ury = max(item.bbox()[3] for item in members)
        centers = [((it.llx + it.bbox()[2]) * 0.5, (it.lly + it.bbox()[3]) * 0.5) for it in members]
        max_span = 0.0
        for i in range(len(centers)):
            for j in range(i + 1, len(centers)):
                dist = abs(centers[i][0] - centers[j][0]) + abs(centers[i][1] - centers[j][1])
                if dist > max_span:
                    max_span = dist
        ctrl = next((it.bist_controller for it in members if it.bist_controller), None)
        bist_summary.append(
            BistGroupSummary(
                bist_group=gname,
                controller=ctrl,
                member_count=len(members),
                bbox_um=(round(g_llx, 2), round(g_lly, 2), round(g_urx, 2), round(g_ury, 2)),
                max_manhattan_span_um=round(max_span, 2),
                dispersed_warning=len(members) > 1 and (max_span / die_diag_manhattan) > 0.45,
            )
        )

    macro_area = sum(oriented_size(m.width, m.height, m.orient)[0] * oriented_size(m.width, m.height, m.orient)[1] for m in macros)
    die_area = max((x1 - x0) * (y1 - y0), 1.0)
    return FloorplanScene(
        scope_kind="top" if scope_kind == "top" else "subblock",
        design_name=design_name,
        fp_ver=fp_ver,
        prev_fp_ver=prev_fp_ver,
        die_bbox=die_bbox,
        die_polygon=die_polygon,
        macros=macros,
        pins=pins,
        regions=regions,
        density_grid_size=grid_size,
        density_grid=density_pct,
        module_regions=module_regions,
        bist_summary=bist_summary,
        stats={
            "die_width_um": round(x1 - x0, 2),
            "die_height_um": round(y1 - y0, 2),
            "die_area_um2": round(die_area, 2),
            "macro_count": len(macros),
            "sram_count": sum(1 for m in macros if m.kind == "sram"),
            "bist_group_count": len(bist_summary),
            "pin_count": len(pins),
            "std_cell_count": std_cell_count,
            "std_cell_area_um2": round(std_cell_area, 2),
            "macro_area_um2": round(macro_area, 2),
        },
    )
