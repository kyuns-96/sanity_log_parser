"""High-throughput VWP-local DEF(.gz) + LEF(.gz) streaming & parallel extractor.

Raw DEF/LEF files are parsed strictly locally on VWP in bounded chunk memory and NEVER uploaded.
Optimizations for multi-GB DEF files (1.5 GB - 10+ GB):
1. Raw 16 MiB byte-chunk scanner skips `NETS`, `SPECIALNETS`, `FILLS`, `VIAS`, `SCANCHAINS`, etc.
   at C `memmem` / `sre` speed (~2-5 GB/s) with zero per-line Python string allocations.
2. `parse_lef_macros` jumps directly from `MACRO <name>` header (`CLASS`/`SIZE`) to `END <name>`,
   bypassing millions of `PIN` / `OBS` geometry `RECT` lines.
3. `COMPONENTS` uses a zero-regex `bytes` fast path with per-master area/macro memoization, flat
   1024-entry tile arrays, integer DBU binning, and multi-core `fork` range parallelism on Linux
   for uncompressed `.def` files >= 32 MiB (plus `pigz`/`gzip -dc` pipeline for `.def.gz`).
"""
from __future__ import annotations

import gzip
import multiprocessing as mp
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, BinaryIO, Iterable
import zlib

from .classifier import (
    DefaultMacroClassifier,
    FloorplanMacroClassifier,
    MacroClassification,
    sanitize_macro_attributes,
)
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

CHUNK_BYTES = 16 * 1024 * 1024  # 16 MiB streaming chunks
COMP_BLOCK_BYTES = 8 * 1024 * 1024  # 8 MiB semicolon-aligned component blocks
PARALLEL_DEF_MIN_BYTES = 32 * 1024 * 1024  # Use fork workers for >= 32 MiB uncompressed DEF

_COMMENT_B_RE = re.compile(rb"#[^\n]*")
_HALO_B_RE = re.compile(rb"\+\s*HALO(?:\s+SOFT)?\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", re.IGNORECASE)
_PT_B_RE = re.compile(rb"\(\s*([+-]?\d+|\*)\s+([+-]?\d+|\*)\s*\)")

# Top-level DEF keyword matcher (skips ROW/TRACKS/GCELLGRID/comments in C without line splitting)
_TOP_KW_B_RE = re.compile(
    rb"^[ \t]*(DESIGN|UNITS|DIEAREA|COMPONENTS|PINS|BLOCKAGES|REGIONS|"
    rb"VIAS|NONDEFAULTRULES|PROPERTYDEFINITIONS|PINPROPERTIES|SLOTS|FILLS|"
    rb"SPECIALNETS|NETS|SCANCHAINS|GROUPS|STYLES|BEGINEXT)\b",
    re.IGNORECASE | re.MULTILINE,
)

_END_SEC_RES: dict[bytes, re.Pattern[bytes]] = {
    s: re.compile(rb"^[ \t]*END[ \t]+" + s + rb"\b", re.IGNORECASE | re.MULTILINE)
    for s in (b"COMPONENTS", b"PINS", b"BLOCKAGES", b"REGIONS")
}

_SKIP_SECTIONS: dict[bytes, bytes] = {
    b"VIAS": b"END VIAS",
    b"NONDEFAULTRULES": b"END NONDEFAULTRULES",
    b"PROPERTYDEFINITIONS": b"END PROPERTYDEFINITIONS",
    b"PINPROPERTIES": b"END PINPROPERTIES",
    b"SLOTS": b"END SLOTS",
    b"FILLS": b"END FILLS",
    b"SPECIALNETS": b"END SPECIALNETS",
    b"NETS": b"END NETS",
    b"SCANCHAINS": b"END SCANCHAINS",
    b"GROUPS": b"END GROUPS",
    b"STYLES": b"END STYLES",
    b"BEGINEXT": b"ENDEXT",
}

_LEF_TOP_B_RE = re.compile(
    rb"^[ \t]*(?:(PROPERTYDEFINITIONS)\b|MACRO[ \t]+([^\s;]+)[ \t]*\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_LEF_PROPDEF_END_B_RE = re.compile(rb"^[ \t]*END[ \t]+PROPERTYDEFINITIONS\b", re.IGNORECASE | re.MULTILINE)
_LEF_CLASS_B_RE = re.compile(rb"^[ \t]*CLASS[ \t]+([^\s;]+)(?:[ \t]+([^\s;]+))?", re.IGNORECASE | re.MULTILINE)
_LEF_SIZE_B_RE = re.compile(rb"^[ \t]*SIZE[ \t]+([0-9.eE+-]+)[ \t]+BY[ \t]+([0-9.eE+-]+)", re.IGNORECASE | re.MULTILINE)

_VALID_ORIENTS_B: dict[bytes, Orient] = {k.encode("ascii"): k for k in VALID_ORIENTS}


def _iter_raw_chunks(path: Path, chunk_size: int = CHUNK_BYTES) -> Iterable[bytes]:
    """Yield raw uncompressed byte chunks using C `pigz`/`gzip` when `.gz`, else direct file reads."""
    if path.name.lower().endswith(".gz"):
        decomp_bin = shutil.which("pigz") or shutil.which("gzip")
        if decomp_bin:
            proc = subprocess.Popen(
                [decomp_bin, "-dc", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=chunk_size,
            )
            assert proc.stdout is not None
            try:
                while True:
                    chunk = proc.stdout.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.stdout.close()
                proc.wait()
            return
        with path.open("rb") as raw_f:
            zobj = zlib.decompressobj(16 + zlib.MAX_WBITS)
            while True:
                compressed = raw_f.read(4 * 1024 * 1024)
                if not compressed:
                    tail = zobj.flush()
                    if tail:
                        yield tail
                    break
                out = zobj.decompress(compressed)
                if out:
                    yield out
        return

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _find_lef_macro_end(data: bytes, name_b: bytes, start: int) -> tuple[int, int] | None:
    """Locate line-anchored `END <name>` with exact token boundary (avoids matching `END RAM10` for `RAM1`)."""
    n = len(data)
    end_tok = b"END " + name_b
    tok_len = len(end_tok)
    search_from = start
    while search_from < n:
        ep = data.find(end_tok, search_from)
        if ep < 0:
            break
        # Verify line start (preceded only by spaces/tabs after '\n' or start-of-file)
        ls = data.rfind(b"\n", 0, ep)
        lead_ok = all(ch in (32, 9) for ch in data[ls + 1 : ep])
        after_idx = ep + tok_len
        trail_ok = after_idx >= n or data[after_idx] in (32, 9, 10, 13, 59)  # space/tab/LF/CR/';'
        if lead_ok and trail_ok:
            return ep, after_idx
        search_from = ep + tok_len
    # Fallback for lowercase `end` or extra whitespace (`END  <name>` / `END\t<name>`)
    end_re = re.compile(rb"^[ \t]*END[ \t]+" + re.escape(name_b) + rb"(?=[ \t;\r\n]|$)", re.IGNORECASE | re.MULTILINE)
    em = end_re.search(data, start)
    if em:
        return em.start(), em.end()
    return None


def parse_lef_macros(lef_paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Fast byte-scanning LEF macro extractor; skips PROPERTYDEFINITIONS, PIN, and OBS blocks safely."""
    macros: dict[str, dict[str, Any]] = {}
    for path in lef_paths:
        data = b"".join(_iter_raw_chunks(path))
        if b"#" in data:
            data = _COMMENT_B_RE.sub(b"", data)
        pos = 0
        n = len(data)
        while pos < n:
            m = _LEF_TOP_B_RE.search(data, pos)
            if not m:
                break
            if m.group(1) is not None:
                # Skip PROPERTYDEFINITIONS ... END PROPERTYDEFINITIONS (contains `MACRO <prop> STRING ;`)
                pe = _LEF_PROPDEF_END_B_RE.search(data, m.end())
                pos = pe.end() if pe else m.end()
                continue

            name_b = m.group(2)
            body_start = m.end()
            end_hit = _find_lef_macro_end(data, name_b, body_start)
            if end_hit is None:
                # Missing END <name>: resync to the next line instead of aborting the rest of the LEF file
                pos = body_start
                continue
            end_pos, next_pos = end_hit

            # CLASS and SIZE are in the macro header right after MACRO <name> (before PIN/OBS)
            probe_end = min(end_pos, body_start + 8192)
            header = data[body_start:probe_end]
            sm = _LEF_SIZE_B_RE.search(header)
            if sm is None and probe_end < end_pos:
                header = data[body_start:end_pos]
                sm = _LEF_SIZE_B_RE.search(header)
            if sm is not None:
                w = float(sm.group(1))
                h = float(sm.group(2))
                if w > 0 and h > 0:
                    cm = _LEF_CLASS_B_RE.search(header)
                    mclass = cm.group(1).decode("ascii", "replace").upper() if cm else "CORE"
                    msub = cm.group(2).decode("ascii", "replace").upper() if (cm and cm.group(2)) else ""
                    macros[name_b.decode("utf-8", "replace")] = {
                        "class": mclass,
                        "subclass": msub,
                        "width": w,
                        "height": h,
                    }
            pos = next_pos
    return macros


def _expand_def_points_b(raw_pts: list[tuple[bytes, bytes]], dbu: float) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    prev_x, prev_y = 0.0, 0.0
    for rx, ry in raw_pts:
        x = prev_x if rx == b"*" else int(rx) / dbu
        y = prev_y if ry == b"*" else int(ry) / dbu
        pts.append((round(x, 4), round(y, 4)))
        prev_x, prev_y = x, y
    if len(pts) == 2:
        (x1, y1), (x2, y2) = pts
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return pts


def _pin_side(x: float, y: float, die_bbox: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = die_bbox
    dists = [("L", abs(x - x0)), ("R", abs(x1 - x)), ("B", abs(y - y0)), ("T", abs(y1 - y))]
    dists.sort(key=lambda item: item[1])
    return dists[0][0]


class _FastModAcc:
    __slots__ = ("count", "area", "sum_ix", "sum_iy", "ixs", "iys", "tiles")

    def __init__(self) -> None:
        self.count = 0
        self.area = 0.0
        self.sum_ix = 0
        self.sum_iy = 0
        self.ixs: list[int] = []
        self.iys: list[int] = []
        self.tiles: list[int] = [0] * 1024

    def merge_from(self, other: "_FastModAcc") -> None:
        self.count += other.count
        self.area += other.area
        self.sum_ix += other.sum_ix
        self.sum_iy += other.sum_iy
        if len(self.ixs) < 2048:
            room = 2048 - len(self.ixs)
            self.ixs.extend(other.ixs[:room])
            self.iys.extend(other.iys[:room])
        t_self = self.tiles
        t_other = other.tiles
        for idx in range(1024):
            v = t_other[idx]
            if v:
                t_self[idx] += v


def _classify_master_with_fallback(
    master: str,
    lef_info: dict[str, Any] | None,
    *,
    scope_kind: str,
    classifier: FloorplanMacroClassifier | None,
    default_classifier: DefaultMacroClassifier,
) -> MacroClassification:
    if classifier is not None:
        res = classifier.classify_master(master, lef_info, scope_kind=scope_kind)
        if res is not None:
            return res
    return default_classifier.classify_master(master, lef_info, scope_kind=scope_kind)


def _build_master_seed(
    lef_macros: dict[str, dict[str, Any]],
    scope_kind: str,
    classifier: FloorplanMacroClassifier | None,
    default_classifier: DefaultMacroClassifier,
) -> dict[bytes, float | MacroClassification]:
    """Pre-classify all LEF masters once into bytes -> float (stdcell area) or MacroClassification (Hard Macro)."""
    seed: dict[bytes, float | MacroClassification] = {}
    for master, info in lef_macros.items():
        mb = master.encode("utf-8")
        res = _classify_master_with_fallback(
            master, info, scope_kind=scope_kind, classifier=classifier, default_classifier=default_classifier
        )
        w = float(info.get("width") or 0.5)
        h = float(info.get("height") or 0.5)
        if res.is_hard_macro:
            if res.width_um is None or res.height_um is None:
                res = MacroClassification(
                    is_hard_macro=True,
                    kind=res.kind,
                    width_um=res.width_um or w,
                    height_um=res.height_um or h,
                    bist_group=res.bist_group,
                    bist_controller=res.bist_controller,
                    attributes=res.attributes,
                )
            seed[mb] = res
        else:
            seed[mb] = w * h
    return seed


def _process_components_block(
    block: bytes,
    *,
    master_cache: dict[bytes, float | MacroClassification],
    lef_macros: dict[str, dict[str, Any]],
    classifier: FloorplanMacroClassifier | None,
    default_classifier: DefaultMacroClassifier,
    scope_kind: str,
    exact_macro_b_set: set[bytes],
    has_exact_macros: bool,
    bist_b_lookup: dict[bytes, tuple[str, str | None]],
    has_bist: bool,
    dbu: float,
    die_x0_i: int,
    die_y0_i: int,
    inv_span_x_grid: float,
    inv_span_y_grid: float,
    inv_span_x_tile: float,
    inv_span_y_tile: float,
    grid_size: int,
    sep_b: bytes,
    macros_out: list[MacroBox],
    density_out: list[float],
    mod_accs: dict[bytes, _FastModAcc],
) -> tuple[int, float]:
    """Ultra-fast zero-regex bytes parser for a semicolon-aligned COMPONENTS block.

    NEVER runs regexes against `inst_name` hierarchy paths (avoids false positives on std cells inside
    e.g. `u_sram_ctrl/...`). Macro promotion uses ONLY `master` / LEF / corporate classifier / exact instance sets.
    """
    if b"#" in block:
        block = _COMMENT_B_RE.sub(b"", block)

    std_count = 0
    std_area = 0.0
    grid_m1 = grid_size - 1

    for st in block.split(b";"):
        st_s = st.lstrip()
        if len(st_s) < 10 or st_s[0] != 45 or st_s[1] > 32:  # Must start with '- '
            continue
        head = st_s[2:].split(None, 2)
        if len(head) < 3:
            continue
        raw_inst_b, master_b, rest_b = head

        info = master_cache.get(master_b)
        if info is None:
            raw_master_str = master_b.decode("utf-8", "replace")
            clean_master_str = master_b.replace(b"\\", b"").decode("utf-8", "replace") if b"\\" in master_b else raw_master_str
            lef_info = lef_macros.get(clean_master_str) or lef_macros.get(raw_master_str)
            res = _classify_master_with_fallback(
                clean_master_str, lef_info, scope_kind=scope_kind, classifier=classifier, default_classifier=default_classifier
            )
            if res.is_hard_macro:
                if lef_info and (res.width_um is None or res.height_um is None):
                    res = MacroClassification(
                        is_hard_macro=True,
                        kind=res.kind,
                        width_um=res.width_um or float(lef_info.get("width") or 40.0),
                        height_um=res.height_um or float(lef_info.get("height") or 40.0),
                        bist_group=res.bist_group,
                        bist_controller=res.bist_controller,
                        attributes=res.attributes,
                    )
                info = res
            else:
                if lef_info:
                    info = float(lef_info.get("width") or 0.5) * float(lef_info.get("height") or 0.5)
                else:
                    info = 0.25
            master_cache[master_b] = info

        # Optional exact instance promotion (O(1) bytes lookup ONLY when corporate set or BIST map is non-empty)
        bist_entry = None
        if has_bist:
            bist_entry = bist_b_lookup.get(raw_inst_b)
            if bist_entry is None and b"\\" in raw_inst_b:
                bist_entry = bist_b_lookup.get(raw_inst_b.replace(b"\\", b""))

        exact_hit = False
        if has_exact_macros:
            exact_hit = raw_inst_b in exact_macro_b_set
            if not exact_hit and b"\\" in raw_inst_b:
                exact_hit = raw_inst_b.replace(b"\\", b"") in exact_macro_b_set

        is_macro = isinstance(info, MacroClassification) or (bist_entry is not None) or exact_hit

        # Fast coordinate extraction
        kw = rest_b.find(b"PLACED")
        status_str = "PLACED"
        if kw < 0:
            kw = rest_b.find(b"FIXED")
            if kw >= 0:
                status_str = "FIXED"
            else:
                kw = rest_b.find(b"COVER")
                if kw >= 0:
                    status_str = "FIXED"
                else:
                    continue  # UNPLACED component

        lp = rest_b.find(b"(", kw + 5)
        if lp < 0:
            continue
        rp = rest_b.find(b")", lp + 1)
        if rp < 0:
            continue
        xy = rest_b[lp + 1 : rp].split()
        if len(xy) < 2:
            continue
        ix = int(xy[0])
        iy = int(xy[1])

        if not is_macro:
            # === HOT PATH: 99.99% Standard Cell ===
            c_area: float = info  # type: ignore[assignment]
            std_count += 1
            std_area += c_area

            gx = int((ix - die_x0_i) * inv_span_x_grid)
            if gx < 0:
                gx = 0
            elif gx > grid_m1:
                gx = grid_m1
            gy = int((iy - die_y0_i) * inv_span_y_grid)
            if gy < 0:
                gy = 0
            elif gy > grid_m1:
                gy = grid_m1
            density_out[gy * grid_size + gx] += c_area

            s1 = raw_inst_b.find(sep_b)
            if s1 > 0:
                s2 = raw_inst_b.find(sep_b, s1 + 1)
                prefix_b = raw_inst_b[:s2] if s2 > 0 else raw_inst_b[:s1]
                acc = mod_accs.get(prefix_b)
                if acc is None:
                    if len(mod_accs) < 600:
                        acc = _FastModAcc()
                        mod_accs[prefix_b] = acc
                    else:
                        continue
                cnt = acc.count + 1
                acc.count = cnt
                acc.area += c_area
                acc.sum_ix += ix
                acc.sum_iy += iy
                if (cnt <= 512 or (cnt & 63) == 0) and len(acc.ixs) < 2048:
                    acc.ixs.append(ix)
                    acc.iys.append(iy)
                tx = int((ix - die_x0_i) * inv_span_x_tile)
                if tx < 0:
                    tx = 0
                elif tx > 31:
                    tx = 31
                ty = int((iy - die_y0_i) * inv_span_y_tile)
                if ty < 0:
                    ty = 0
                elif ty > 31:
                    ty = 31
                acc.tiles[(ty << 5) | tx] += 1
            continue

        # === COLD PATH: Hard Macro (SRAM / IP / Custom Macro / Sub-block) ===
        if len(macros_out) >= 4000:
            continue
        after_paren = rest_b[rp + 1 :].split(None, 1)
        orient_b = after_paren[0] if after_paren else b"N"
        orient: Orient = _VALID_ORIENTS_B.get(orient_b, "N")
        inst_str = raw_inst_b.replace(b"\\", b"").decode("utf-8", "replace")
        master_str = master_b.decode("utf-8", "replace")
        llx_um = round(ix / dbu, 4)
        lly_um = round(iy / dbu, 4)

        if isinstance(info, MacroClassification):
            base_cls = info
        else:
            base_cls = MacroClassification(
                is_hard_macro=True,
                kind="sram" if bist_entry is not None else "macro",
            )

        final_cls: MacroClassification | None = base_cls
        if classifier is not None:
            final_cls = classifier.enrich_macro_instance(
                inst_str,
                master_str,
                base_cls,
                status=status_str,
                llx=llx_um,
                lly=lly_um,
                orient=orient,
            )
            if final_cls is None or not final_cls.is_hard_macro:
                continue

        mkind = "sram" if bist_entry is not None else final_cls.kind
        w = float(final_cls.width_um or 40.0)
        h = float(final_cls.height_um or 40.0)
        bist_grp = (bist_entry[0] if bist_entry else None) or final_cls.bist_group
        bist_ctrl = (bist_entry[1] if bist_entry else None) or final_cls.bist_controller
        attrs = sanitize_macro_attributes(final_cls.attributes)

        halo = (0.0, 0.0, 0.0, 0.0)
        if b"HALO" in rest_b or b"halo" in rest_b:
            hm = _HALO_B_RE.search(rest_b)
            if hm:
                halo = tuple(round(int(hm.group(i)) / dbu, 3) for i in range(1, 5))  # type: ignore[assignment]

        sep_str = sep_b.decode("ascii")
        hier_pref = ""
        if sep_str in inst_str:
            parts = [p for p in inst_str.split(sep_str) if p]
            if len(parts) > 1:
                hier_pref = sep_str.join(parts[: min(2, len(parts) - 1)])

        macros_out.append(
            MacroBox(
                inst_name=inst_str,
                master=master_str,
                kind=mkind,  # type: ignore[arg-type]
                llx=llx_um,
                lly=lly_um,
                width=round(w, 4),
                height=round(h, 4),
                orient=orient,
                status="FIXED" if status_str == "FIXED" else "PLACED",
                halo_um=halo,
                bist_group=bist_grp,
                bist_controller=bist_ctrl,
                hier_prefix=hier_pref,
                attributes=attrs,
            )
        )

    return std_count, std_area


# Globals populated right before `fork` so child processes inherit read-only state with zero IPC pickling
_FORK_CTX: dict[str, Any] = {}


def _fork_parse_range(args: tuple[int, int]) -> tuple[list[MacroBox], list[float], dict[bytes, _FastModAcc], int, float]:
    start_off, end_off = args
    ctx = _FORK_CTX
    def_path: Path = ctx["def_path"]
    grid_size: int = ctx["grid_size"]
    master_cache = dict(ctx["master_seed"])
    lef_macros: dict[str, dict[str, Any]] = ctx["lef_macros"]
    classifier: FloorplanMacroClassifier | None = ctx["classifier"]
    default_classifier: DefaultMacroClassifier = ctx["default_classifier"]
    scope_kind: str = ctx["scope_kind"]
    exact_macro_b_set: set[bytes] = ctx["exact_macro_b_set"]
    has_exact_macros: bool = ctx["has_exact_macros"]
    bist_b_lookup: dict[bytes, tuple[str, str | None]] = ctx["bist_b_lookup"]
    has_bist: bool = ctx["has_bist"]
    dbu: float = ctx["dbu"]
    die_x0_i: int = ctx["die_x0_i"]
    die_y0_i: int = ctx["die_y0_i"]
    inv_span_x_grid: float = ctx["inv_span_x_grid"]
    inv_span_y_grid: float = ctx["inv_span_y_grid"]
    inv_span_x_tile: float = ctx["inv_span_x_tile"]
    inv_span_y_tile: float = ctx["inv_span_y_tile"]
    sep_b: bytes = ctx["sep_b"]

    macros_part: list[MacroBox] = []
    density_part = [0.0] * (grid_size * grid_size)
    mod_accs_part: dict[bytes, _FastModAcc] = {}
    total_cnt = 0
    total_area = 0.0

    rem = b""
    pos = start_off
    with def_path.open("rb") as f:
        f.seek(start_off)
        while pos < end_off:
            to_read = min(COMP_BLOCK_BYTES, end_off - pos)
            chunk = f.read(to_read)
            if not chunk:
                break
            pos += len(chunk)
            buf = rem + chunk
            last_semi = buf.rfind(b";")
            if last_semi < 0:
                rem = buf
                continue
            block = buf[: last_semi + 1]
            rem = buf[last_semi + 1 :]
            c, a = _process_components_block(
                block,
                master_cache=master_cache,
                lef_macros=lef_macros,
                classifier=classifier,
                default_classifier=default_classifier,
                scope_kind=scope_kind,
                exact_macro_b_set=exact_macro_b_set,
                has_exact_macros=has_exact_macros,
                bist_b_lookup=bist_b_lookup,
                has_bist=has_bist,
                dbu=dbu,
                die_x0_i=die_x0_i,
                die_y0_i=die_y0_i,
                inv_span_x_grid=inv_span_x_grid,
                inv_span_y_grid=inv_span_y_grid,
                inv_span_x_tile=inv_span_x_tile,
                inv_span_y_tile=inv_span_y_tile,
                grid_size=grid_size,
                sep_b=sep_b,
                macros_out=macros_part,
                density_out=density_part,
                mod_accs=mod_accs_part,
            )
            total_cnt += c
            total_area += a

    if rem.strip():
        c, a = _process_components_block(
            rem,
            master_cache=master_cache,
            lef_macros=lef_macros,
            classifier=classifier,
            default_classifier=default_classifier,
            scope_kind=scope_kind,
            exact_macro_b_set=exact_macro_b_set,
            has_exact_macros=has_exact_macros,
            bist_b_lookup=bist_b_lookup,
            has_bist=has_bist,
            dbu=dbu,
            die_x0_i=die_x0_i,
            die_y0_i=die_y0_i,
            inv_span_x_grid=inv_span_x_grid,
            inv_span_y_grid=inv_span_y_grid,
            inv_span_x_tile=inv_span_x_tile,
            inv_span_y_tile=inv_span_y_tile,
            grid_size=grid_size,
            sep_b=sep_b,
            macros_out=macros_part,
            density_out=density_part,
            mod_accs=mod_accs_part,
        )
        total_cnt += c
        total_area += a

    return macros_part, density_part, mod_accs_part, total_cnt, total_area


def _parse_pins_bytes(buf: bytes, dbu: float, die_bbox: tuple[float, float, float, float], pins_out: list[PinMarker]) -> None:
    if b"#" in buf:
        buf = _COMMENT_B_RE.sub(b"", buf)
    dir_re = re.compile(rb"\+\s*DIRECTION\s+(INPUT|OUTPUT|INOUT|FEEDTHRU)", re.IGNORECASE)
    loc_re = re.compile(rb"\+\s*(?:PLACED|FIXED|COVER)\s*\(\s*([+-]?\d+)\s+([+-]?\d+)\s*\)", re.IGNORECASE)
    lay_re = re.compile(rb"\+\s*LAYER\s+(\S+)", re.IGNORECASE)
    use_pg_re = re.compile(rb"\+\s*USE\s+(?:POWER|GROUND)\b", re.IGNORECASE)
    for st in buf.split(b";"):
        if len(pins_out) >= 4000:
            break
        st_s = st.lstrip()
        if len(st_s) < 6 or st_s[0] != 45 or st_s[1] > 32:
            continue
        if use_pg_re.search(st_s):
            continue
        parts = st_s[2:].split(None, 1)
        if not parts:
            continue
        name = parts[0].decode("utf-8", "replace")
        lm = loc_re.search(st_s)
        if lm:
            px, py = int(lm.group(1)) / dbu, int(lm.group(2)) / dbu
        else:
            pts = _PT_B_RE.findall(st_s)
            if not pts or pts[0][0] == b"*" or pts[0][1] == b"*":
                continue
            px, py = int(pts[0][0]) / dbu, int(pts[0][1]) / dbu
        dm = dir_re.search(st_s)
        direction = dm.group(1).decode("ascii").upper() if dm else "INOUT"
        lay_m = lay_re.search(st_s)
        pins_out.append(
            PinMarker(
                name=name,
                direction=direction,  # type: ignore[arg-type]
                x=round(px, 4),
                y=round(py, 4),
                layer=lay_m.group(1).decode("utf-8", "replace") if lay_m else None,
                side=_pin_side(px, py, die_bbox),  # type: ignore[arg-type]
            )
        )


def _parse_regions_or_blockages_bytes(buf: bytes, sec: bytes, dbu: float, regions_out: list[RegionBox]) -> None:
    if b"#" in buf:
        buf = _COMMENT_B_RE.sub(b"", buf)
    is_blk = sec == b"BLOCKAGES"
    for st in buf.split(b";"):
        if len(regions_out) >= 1000:
            break
        st_s = st.lstrip()
        if len(st_s) < 6 or st_s[0] != 45:
            continue
        raw_pts = _PT_B_RE.findall(st_s)
        if len(raw_pts) < 2:
            continue
        if is_blk:
            up = st_s.upper()
            if b"LAYER" in up and b"PLACEMENT" not in up:
                continue
            kind = "soft_blockage" if (b"SOFT" in up or b"PARTIAL" in up) else "blockage"
            for i in range(0, len(raw_pts) - 1, 2):
                box = _expand_def_points_b([raw_pts[i], raw_pts[i + 1]], dbu)
                if len(box) >= 3 and len(regions_out) < 1000:
                    regions_out.append(RegionBox(name=f"BLK_{len(regions_out) + 1}", kind=kind, points=box))  # type: ignore[arg-type]
        else:
            parts = st_s[1:].split(None, 1)
            rname = parts[0].decode("utf-8", "replace") if parts else f"REG_{len(regions_out) + 1}"
            for i in range(0, len(raw_pts) - 1, 2):
                box = _expand_def_points_b([raw_pts[i], raw_pts[i + 1]], dbu)
                if len(box) >= 3 and len(regions_out) < 1000:
                    regions_out.append(RegionBox(name=rname, kind="region", points=box))


def _compute_bist_summary(macros: list[MacroBox], die_bbox: tuple[float, float, float, float]) -> list[BistGroupSummary]:
    x0, y0, x1, y1 = die_bbox
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
    return bist_summary


def apply_bist_map_to_scene(
    scene: FloorplanScene,
    bist_map: dict[str, dict[str, str | None] | str] | None,
) -> FloorplanScene:
    """Attach/update BIST group assignments on an already-parsed FloorplanScene in O(macros) time."""
    if not bist_map:
        return scene
    bist_lookup: dict[str, tuple[str, str | None]] = {}
    for k, v in bist_map.items():
        clean_k = k.strip().replace("\\", "")
        if isinstance(v, str) and v.strip():
            bist_lookup[clean_k] = (v.strip(), None)
        elif isinstance(v, dict) and v.get("bist_group"):
            bist_lookup[clean_k] = (str(v["bist_group"]).strip(), str(v["controller"]).strip() if v.get("controller") else None)
    if not bist_lookup:
        return scene

    updated_macros: list[MacroBox] = []
    for m in scene.macros:
        entry = bist_lookup.get(m.inst_name)
        if entry:
            updated_macros.append(m.model_copy(update={"kind": "sram", "bist_group": entry[0], "bist_controller": entry[1]}))
        else:
            updated_macros.append(m)
    bist_summary = _compute_bist_summary(updated_macros, scene.die_bbox)
    new_stats = dict(scene.stats)
    new_stats["sram_count"] = sum(1 for m in updated_macros if m.kind == "sram")
    new_stats["bist_group_count"] = len(bist_summary)
    return scene.model_copy(update={"macros": updated_macros, "bist_summary": bist_summary, "stats": new_stats})


def parse_def_to_scene(
    def_path: Path,
    lef_macros: dict[str, dict[str, Any]],
    *,
    fp_ver: str,
    prev_fp_ver: str | None = None,
    scope_kind: str = "subblock",
    bist_map: dict[str, dict[str, str | None] | str] | None = None,
    macro_classifier: FloorplanMacroClassifier | None = None,
    hierarchy_separator: str = "/",
    grid_size: int = 64,
    workers: int | None = None,
) -> FloorplanScene:
    """High-speed DEF(.gz) + LEF extractor using C byte-chunk skipping, FloorplanMacroClassifier, and fork workers."""
    bist_b_lookup: dict[bytes, tuple[str, str | None]] = {}
    for k, v in (bist_map or {}).items():
        clean_b = k.strip().replace("\\", "").encode("utf-8")
        if isinstance(v, str) and v.strip():
            bist_b_lookup[clean_b] = (v.strip(), None)
        elif isinstance(v, dict) and v.get("bist_group"):
            bist_b_lookup[clean_b] = (
                str(v["bist_group"]).strip(),
                str(v["controller"]).strip() if v.get("controller") else None,
            )
    has_bist = bool(bist_b_lookup)
    default_classifier = DefaultMacroClassifier()
    exact_macro_b_set: set[bytes] = set()
    if macro_classifier is not None:
        for raw_inst in macro_classifier.exact_hard_macro_instances() or ():
            clean_inst = str(raw_inst).strip().replace("\\", "")
            if clean_inst:
                exact_macro_b_set.add(clean_inst.encode("utf-8"))
    has_exact_macros = bool(exact_macro_b_set)
    sep_b = hierarchy_separator.encode("ascii")
    master_seed = _build_master_seed(lef_macros, scope_kind, macro_classifier, default_classifier)
    master_cache = dict(master_seed)

    design_name = def_path.stem.removesuffix(".def")
    dbu = 1000.0
    die_polygon: list[tuple[float, float]] = []
    die_bbox: tuple[float, float, float, float] = (0.0, 0.0, 1000.0, 1000.0)
    macros: list[MacroBox] = []
    pins: list[PinMarker] = []
    regions: list[RegionBox] = []
    density = [0.0] * (grid_size * grid_size)
    mod_accs: dict[bytes, _FastModAcc] = {}
    std_cell_count = 0
    std_cell_area = 0.0

    is_gz = def_path.name.lower().endswith(".gz")
    file_size = def_path.stat().st_size if def_path.is_file() else 0
    can_fork = (
        not is_gz
        and file_size >= PARALLEL_DEF_MIN_BYTES
        and sys.platform.startswith("linux")
        and "fork" in mp.get_all_start_methods()
        and (workers or (os.cpu_count() or 1)) >= 2
    )
    comp_ranges: list[tuple[int, int]] = []  # Recorded during scan if can_fork is True

    def _grid_factors() -> tuple[int, int, float, float, float, float]:
        x0, y0, x1, y1 = die_bbox
        dx_i = max(int(round((x1 - x0) * dbu)), 1)
        dy_i = max(int(round((y1 - y0) * dbu)), 1)
        return (
            int(round(x0 * dbu)),
            int(round(y0 * dbu)),
            grid_size / dx_i,
            grid_size / dy_i,
            32.0 / dx_i,
            32.0 / dy_i,
        )

    # State machine over 16 MiB raw byte chunks
    mode: bytes = b"TOP"  # TOP | SKIP | COMPONENTS | PINS | BLOCKAGES | REGIONS | HEADER_STMT
    skip_end_tok: bytes = b""
    header_kw: bytes = b""
    sec_buf = bytearray()
    comp_rem = b""
    comp_start_abs = 0
    buf = b""
    buf_abs_start = 0  # Absolute file byte offset corresponding to buf[0]

    for chunk in _iter_raw_chunks(def_path, CHUNK_BYTES):
        buf = buf + chunk
        pos = 0
        blen = len(buf)

        while pos < blen:
            if mode == b"SKIP":
                # C memmem scan across multi-hundred-MB NETS / SPECIALNETS / FILLS / VIAS
                ep = buf.find(skip_end_tok, pos)
                if ep < 0:
                    # Keep last 64 bytes in case 'END NETS' straddles a 16 MiB chunk boundary
                    keep = min(64, blen - pos)
                    drop = blen - keep
                    buf = buf[drop:]
                    buf_abs_start += drop
                    pos = blen  # exit inner while -> read next chunk
                    break
                nl = buf.find(b"\n", ep + len(skip_end_tok))
                pos = (nl + 1) if nl >= 0 else (ep + len(skip_end_tok))
                mode = b"TOP"
                continue

            if mode == b"HEADER_STMT":
                semi = buf.find(b";", pos)
                if semi < 0:
                    sec_buf.extend(buf[pos:])
                    buf_abs_start += blen
                    buf = b""
                    break
                sec_buf.extend(buf[pos : semi + 1])
                pos = semi + 1
                stmt_b = bytes(sec_buf)
                sec_buf.clear()
                if b"#" in stmt_b:
                    stmt_b = _COMMENT_B_RE.sub(b"", stmt_b)
                if header_kw == b"DESIGN":
                    dm = re.search(rb"DESIGN\s+([^\s;]+)", stmt_b, re.IGNORECASE)
                    if dm:
                        design_name = dm.group(1).decode("utf-8", "replace")
                elif header_kw == b"UNITS":
                    um = re.search(rb"MICRONS\s+(\d+)", stmt_b, re.IGNORECASE)
                    if um:
                        dbu = float(int(um.group(1)))
                elif header_kw == b"DIEAREA":
                    pts = _expand_def_points_b(_PT_B_RE.findall(stmt_b), dbu)
                    if len(pts) >= 3:
                        die_polygon = pts
                        xs = [p[0] for p in pts]
                        ys = [p[1] for p in pts]
                        die_bbox = (min(xs), min(ys), max(xs), max(ys))
                mode = b"TOP"
                continue

            if mode == b"COMPONENTS":
                if can_fork:
                    ep = buf.find(b"END COMPONENTS", pos)
                    if ep >= 0:
                        end_m = _END_SEC_RES[b"COMPONENTS"].search(buf, max(pos, ep - 16), min(blen, ep + 32))
                    elif buf.find(b"END  COMPONENTS", pos) >= 0 or buf.find(b"END\tCOMPONENTS", pos) >= 0 or buf.find(b"end components", pos) >= 0:
                        end_m = _END_SEC_RES[b"COMPONENTS"].search(buf, pos)
                    else:
                        end_m = None
                    if end_m is None:
                        keep = min(64, blen - pos)
                        drop = blen - keep
                        buf = buf[drop:]
                        buf_abs_start += drop
                        break
                    comp_end_abs = buf_abs_start + end_m.start()
                    comp_ranges.append((comp_start_abs, comp_end_abs))
                    pos = end_m.end()
                    mode = b"TOP"
                    continue

                end_m = _END_SEC_RES[b"COMPONENTS"].search(buf, pos)
                # Streaming single-process bytes fast path (for .def.gz or < 32 MiB .def)
                die_x0_i, die_y0_i, inv_gx, inv_gy, inv_tx, inv_ty = _grid_factors()
                if end_m is None:
                    work = comp_rem + buf[pos:]
                    last_semi = work.rfind(b";")
                    if last_semi >= 0:
                        c, a = _process_components_block(
                            work[: last_semi + 1],
                            master_cache=master_cache,
                            lef_macros=lef_macros,
                            classifier=macro_classifier,
                            default_classifier=default_classifier,
                            scope_kind=scope_kind,
                            exact_macro_b_set=exact_macro_b_set,
                            has_exact_macros=has_exact_macros,
                            bist_b_lookup=bist_b_lookup,
                            has_bist=has_bist,
                            dbu=dbu,
                            die_x0_i=die_x0_i,
                            die_y0_i=die_y0_i,
                            inv_span_x_grid=inv_gx,
                            inv_span_y_grid=inv_gy,
                            inv_span_x_tile=inv_tx,
                            inv_span_y_tile=inv_ty,
                            grid_size=grid_size,
                            sep_b=sep_b,
                            macros_out=macros,
                            density_out=density,
                            mod_accs=mod_accs,
                        )
                        std_cell_count += c
                        std_cell_area += a
                        comp_rem = work[last_semi + 1 :]
                    else:
                        comp_rem = work
                    buf_abs_start += blen
                    buf = b""
                    break

                work = comp_rem + buf[pos : end_m.start()]
                comp_rem = b""
                if work:
                    c, a = _process_components_block(
                        work,
                        master_cache=master_cache,
                        lef_macros=lef_macros,
                        classifier=macro_classifier,
                        default_classifier=default_classifier,
                        scope_kind=scope_kind,
                        exact_macro_b_set=exact_macro_b_set,
                        has_exact_macros=has_exact_macros,
                        bist_b_lookup=bist_b_lookup,
                        has_bist=has_bist,
                        dbu=dbu,
                        die_x0_i=die_x0_i,
                        die_y0_i=die_y0_i,
                        inv_span_x_grid=inv_gx,
                        inv_span_y_grid=inv_gy,
                        inv_span_x_tile=inv_tx,
                        inv_span_y_tile=inv_ty,
                        grid_size=grid_size,
                        sep_b=sep_b,
                        macros_out=macros,
                        density_out=density,
                        mod_accs=mod_accs,
                    )
                    std_cell_count += c
                    std_cell_area += a
                pos = end_m.end()
                mode = b"TOP"
                continue

            if mode in (b"PINS", b"BLOCKAGES", b"REGIONS"):
                em = _END_SEC_RES[mode].search(buf, pos)
                cap_reached = (mode == b"PINS" and len(pins) >= 4000) or (mode != b"PINS" and len(regions) >= 1000)
                if cap_reached:
                    skip_end_tok = b"END " + mode
                    mode = b"SKIP"
                    continue
                if em is None:
                    # Flush complete statements incrementally so memory stays bounded even on huge PINS
                    work = bytes(sec_buf) + buf[pos:]
                    sec_buf.clear()
                    last_semi = work.rfind(b";")
                    if last_semi >= 0:
                        ready = work[: last_semi + 1]
                        sec_buf.extend(work[last_semi + 1 :])
                        if mode == b"PINS":
                            _parse_pins_bytes(ready, dbu, die_bbox, pins)
                        else:
                            _parse_regions_or_blockages_bytes(ready, mode, dbu, regions)
                    else:
                        sec_buf.extend(work)
                    buf_abs_start += blen
                    buf = b""
                    break

                ready = bytes(sec_buf) + buf[pos : em.start()]
                sec_buf.clear()
                if ready:
                    if mode == b"PINS":
                        _parse_pins_bytes(ready, dbu, die_bbox, pins)
                    else:
                        _parse_regions_or_blockages_bytes(ready, mode, dbu, regions)
                pos = em.end()
                mode = b"TOP"
                continue

            # === mode == b"TOP": C regex jumps over ROW / TRACKS / GCELLGRID / comments ===
            km = _TOP_KW_B_RE.search(buf, pos)
            if km is None:
                keep = min(64, blen - pos)
                drop = blen - keep
                buf = buf[drop:]
                buf_abs_start += drop
                break

            kw_upper = km.group(1).upper()
            if kw_upper in (b"DESIGN", b"UNITS", b"DIEAREA"):
                header_kw = kw_upper
                sec_buf.clear()
                pos = km.start(1)
                mode = b"HEADER_STMT"
                continue

            if kw_upper in _SKIP_SECTIONS:
                skip_end_tok = _SKIP_SECTIONS[kw_upper]
                pos = km.end()
                mode = b"SKIP"
                continue

            # COMPONENTS | PINS | BLOCKAGES | REGIONS -> advance past header ';'
            semi = buf.find(b";", km.end())
            if semi < 0:
                # Section header ';' is in the next chunk; keep from keyword start
                drop = km.start()
                buf = buf[drop:]
                buf_abs_start += drop
                break
            pos = semi + 1
            if kw_upper == b"COMPONENTS":
                comp_rem = b""
                comp_start_abs = buf_abs_start + pos
                mode = b"COMPONENTS"
            else:
                sec_buf.clear()
                mode = kw_upper

        else:
            buf_abs_start += blen
            buf = b""

    # Execute parallel fork range workers for large uncompressed DEF COMPONENTS
    if can_fork and comp_ranges:
        die_x0_i, die_y0_i, inv_gx, inv_gy, inv_tx, inv_ty = _grid_factors()
        n_workers = min(workers or (os.cpu_count() or 4), 16)
        sub_ranges: list[tuple[int, int]] = []
        with def_path.open("rb") as f:
            for c_start, c_end in comp_ranges:
                span = c_end - c_start
                if span <= 0:
                    continue
                actual_w = max(1, min(n_workers, span // (4 * 1024 * 1024) or 1))
                cuts = [c_start]
                for wi in range(1, actual_w):
                    approx = c_start + (span * wi) // actual_w
                    f.seek(approx)
                    probe = f.read(min(65536, c_end - approx))
                    semi_off = probe.find(b";")
                    if semi_off >= 0:
                        cuts.append(approx + semi_off + 1)
                cuts.append(c_end)
                for i in range(len(cuts) - 1):
                    if cuts[i + 1] > cuts[i]:
                        sub_ranges.append((cuts[i], cuts[i + 1]))

        _FORK_CTX.clear()
        _FORK_CTX.update(
            def_path=def_path,
            grid_size=grid_size,
            master_seed=master_seed,
            lef_macros=lef_macros,
            classifier=macro_classifier,
            default_classifier=default_classifier,
            scope_kind=scope_kind,
            exact_macro_b_set=exact_macro_b_set,
            has_exact_macros=has_exact_macros,
            bist_b_lookup=bist_b_lookup,
            has_bist=has_bist,
            dbu=dbu,
            die_x0_i=die_x0_i,
            die_y0_i=die_y0_i,
            inv_span_x_grid=inv_gx,
            inv_span_y_grid=inv_gy,
            inv_span_x_tile=inv_tx,
            inv_span_y_tile=inv_ty,
            sep_b=sep_b,
        )
        try:
            ctx_mp = mp.get_context("fork")
            with ctx_mp.Pool(processes=min(len(sub_ranges), n_workers)) as pool:
                results = pool.map(_fork_parse_range, sub_ranges)
        finally:
            _FORK_CTX.clear()

        for m_part, d_part, mod_part, cnt_part, area_part in results:
            if len(macros) < 4000:
                macros.extend(m_part[: 4000 - len(macros)])
            for idx in range(len(density)):
                density[idx] += d_part[idx]
            for pk, pacc in mod_part.items():
                existing_acc = mod_accs.get(pk)
                if existing_acc is not None:
                    existing_acc.merge_from(pacc)
                elif len(mod_accs) < 800:
                    mod_accs[pk] = pacc
            std_cell_count += cnt_part
            std_cell_area += area_part

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

    # Clean escaped backslashes on the <= 600 module keys once (instead of 10M times per cell)
    cleaned_mods: dict[str, _FastModAcc] = {}
    for raw_pk, acc in mod_accs.items():
        if acc.count == 0:
            continue
        pk_str = raw_pk.replace(b"\\", b"").decode("utf-8", "replace").strip(hierarchy_separator)
        if not pk_str:
            continue
        prev_acc = cleaned_mods.get(pk_str)
        if prev_acc is not None:
            prev_acc.merge_from(acc)
        else:
            cleaned_mods[pk_str] = acc

    inv_dbu = 1.0 / dbu
    sorted_mods = sorted(cleaned_mods.items(), key=lambda kv: kv[1].area, reverse=True)[:250]
    module_regions: list[ModuleRegionSummary] = []
    for path_key, acc in sorted_mods:
        sxs = sorted(acc.ixs)
        sys_ = sorted(acc.iys)
        n = len(sxs)
        p10_x = sxs[int(n * 0.1)] * inv_dbu
        p90_x = sxs[min(n - 1, int(n * 0.9))] * inv_dbu
        p10_y = sys_[int(n * 0.1)] * inv_dbu
        p90_y = sys_[min(n - 1, int(n * 0.9))] * inv_dbu
        max_tile = max(acc.tiles) if acc.tiles else 1
        max_tile = max(max_tile, 1)
        bins = [
            (t_idx & 31, t_idx >> 5, max(1, int(round(cnt / max_tile * 100))))
            for t_idx, cnt in enumerate(acc.tiles)
            if cnt > 0 and (cnt / max_tile) >= 0.05
        ][:512]
        module_regions.append(
            ModuleRegionSummary(
                hier_path=path_key,
                cell_count=acc.count,
                area_um2=round(acc.area, 2),
                cx=round((acc.sum_ix / acc.count) * inv_dbu, 2),
                cy=round((acc.sum_iy / acc.count) * inv_dbu, 2),
                p10_x=round(p10_x, 2),
                p10_y=round(p10_y, 2),
                p90_x=round(p90_x, 2),
                p90_y=round(p90_y, 2),
                bins=bins,
            )
        )

    bist_summary = _compute_bist_summary(macros, die_bbox)
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
            "ip_count": sum(1 for m in macros if m.kind == "ip"),
            "subblock_count": sum(1 for m in macros if m.kind == "subblock"),
            "custom_macro_count": sum(1 for m in macros if m.kind == "macro"),
            "bist_group_count": len(bist_summary),
            "pin_count": len(pins),
            "std_cell_count": std_cell_count,
            "std_cell_area_um2": round(std_cell_area, 2),
            "macro_area_um2": round(macro_area, 2),
        },
    )
