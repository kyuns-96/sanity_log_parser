"""Self-contained SVG floorplan renderer and pure-Python 8-bit PNG snapshot encoder."""
from __future__ import annotations

from html import escape
import struct
from typing import Any
import zlib

from .models import DatapathFlow, FloorplanScene, MacroBox, MacroOverride, ReviewPin, oriented_size, pin1_corner_offset

BIST_PALETTE: tuple[str, ...] = (
    "#2563eb", "#dc2626", "#16a34a", "#d97706", "#9333ea",
    "#0891b2", "#db2777", "#4f46e5", "#65a30d", "#ea580c",
)

KIND_FILL: dict[str, tuple[str, str]] = {
    "sram": ("#dbeafe", "#1d4ed8"),
    "ip": ("#fef3c7", "#b45309"),
    "subblock": ("#e0e7ff", "#4338ca"),
    "macro": ("#f1f5f9", "#475569"),
}


def apply_overrides(scene: FloorplanScene, overrides: list[MacroOverride] | None) -> list[MacroBox]:
    if not overrides:
        return list(scene.macros)
    by_name = {ov.inst_name: ov for ov in overrides}
    out: list[MacroBox] = []
    for m in scene.macros:
        ov = by_name.get(m.inst_name)
        if ov is None:
            out.append(m)
        else:
            out.append(
                m.model_copy(
                    update={
                        "llx": ov.llx,
                        "lly": ov.lly,
                        "orient": ov.orient,
                        **({"halo_um": ov.halo_um} if ov.halo_um is not None else {}),
                    }
                )
            )
    return out


def render_scene_svg(
    scene: FloorplanScene,
    *,
    mode: str = "overview",
    placement_overrides: list[MacroOverride] | None = None,
    datapaths: list[DatapathFlow] | None = None,
    review_pins: list[ReviewPin] | None = None,
) -> bytes:
    """Render a clean, standalone SVG document in micron coordinates (Y flipped for CAD origin)."""
    x0, y0, x1, y1 = scene.die_bbox
    w = max(x1 - x0, 10.0)
    h = max(y1 - y0, 10.0)
    pad = max(w, h) * 0.04
    vb_x = x0 - pad
    vb_y = -y1 - pad
    vb_w = w + pad * 2
    vb_h = h + pad * 2

    def sy(y_val: float) -> float:
        return -y_val

    bist_colors = {
        b.bist_group: BIST_PALETTE[idx % len(BIST_PALETTE)]
        for idx, b in enumerate(scene.bist_summary)
    }
    macros = apply_overrides(scene, placement_overrides)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x:.2f} {vb_y:.2f} {vb_w:.2f} {vb_h:.2f}" '
        f'width="1600" height="{max(600, int(round(1600 * vb_h / vb_w)))}" '
        f'style="background:#f8fafc;font-family:monospace">',
        f'<title>{escape(scene.design_name)} ({escape(scene.fp_ver)})</title>',
    ]

    # Die boundary
    pts_attr = " ".join(f"{px:.2f},{sy(py):.2f}" for px, py in scene.die_polygon)
    parts.append(f'<g data-layer="die"><polygon points="{pts_attr}" fill="#ffffff" stroke="#0f172a" stroke-width="{max(w, h) * 0.0025:.2f}"/></g>')

    # Coarse std-cell density
    gsize = scene.density_grid_size
    if scene.density_grid and len(scene.density_grid) == gsize * gsize:
        tw = w / gsize
        th = h / gsize
        d_parts = ['<g data-layer="density">']
        for gy in range(gsize):
            for gx in range(gsize):
                val = scene.density_grid[gy * gsize + gx]
                if val < 10:
                    continue
                alpha = min(0.32, val / 280.0)
                rx = x0 + gx * tw
                ry = sy(y0 + (gy + 1) * th)
                d_parts.append(f'<rect x="{rx:.2f}" y="{ry:.2f}" width="{tw:.2f}" height="{th:.2f}" fill="rgba(14,116,144,{alpha:.2f})"/>')
        d_parts.append("</g>")
        parts.append("".join(d_parts))

    # Regions & blockages
    if scene.regions:
        r_parts = ['<g data-layer="regions">']
        for reg in scene.regions:
            rpts = " ".join(f"{px:.2f},{sy(py):.2f}" for px, py in reg.points)
            if reg.kind in ("blockage", "soft_blockage"):
                r_parts.append(f'<polygon points="{rpts}" fill="rgba(239,68,68,0.10)" stroke="#ef4444" stroke-dasharray="4 3" stroke-width="{max(w, h) * 0.0012:.2f}"/>')
            else:
                r_parts.append(f'<polygon points="{rpts}" fill="rgba(99,102,241,0.08)" stroke="#6366f1" stroke-width="{max(w, h) * 0.0015:.2f}"/>')
        r_parts.append("</g>")
        parts.append("".join(r_parts))

    # Macros / SRAMs / Subblocks
    m_parts = ['<g data-layer="macros">']
    stroke_w = max(w, h) * 0.0016
    for m in macros:
        ow, oh = oriented_size(m.width, m.height, m.orient)
        rx = m.llx
        ry = sy(m.lly + oh)
        if mode == "bist" and m.bist_group and m.bist_group in bist_colors:
            stroke = bist_colors[m.bist_group]
            fill = stroke + "28"
        else:
            fill, stroke = KIND_FILL.get(m.kind, KIND_FILL["macro"])
        hl, hb, hr, ht = m.halo_um
        if any(v > 0 for v in m.halo_um):
            m_parts.append(
                f'<rect x="{rx - hl:.2f}" y="{ry - ht:.2f}" width="{ow + hl + hr:.2f}" height="{oh + hb + ht:.2f}" '
                f'fill="none" stroke="#94a3b8" stroke-dasharray="3 3" stroke-width="{stroke_w * 0.7:.2f}"/>'
            )
        m_parts.append(
            f'<rect data-inst="{escape(m.inst_name)}" data-bist="{escape(m.bist_group or "")}" '
            f'x="{rx:.2f}" y="{ry:.2f}" width="{ow:.2f}" height="{oh:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_w:.2f}"/>'
        )
        # Orientation notch at pin-1 corner
        dx, dy = pin1_corner_offset(m.width, m.height, m.orient)
        px = m.llx + dx
        py = sy(m.lly + dy)
        notch = min(ow, oh) * 0.18
        m_parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{max(notch * 0.45, stroke_w * 1.3):.2f}" fill="{stroke}"/>')
        short_label = m.inst_name.rsplit("/", 1)[-1]
        font_sz = max(min(ow / max(len(short_label) * 0.68, 4), oh * 0.34), max(w, h) * 0.008)
        if min(ow, oh) >= max(w, h) * 0.022:
            m_parts.append(
                f'<text x="{rx + ow * 0.5:.2f}" y="{ry + oh * 0.54:.2f}" text-anchor="middle" '
                f'font-size="{font_sz:.2f}" fill="#0f172a">{escape(short_label[:26])}</text>'
            )
    m_parts.append("</g>")
    parts.append("".join(m_parts))

    # BIST group bounding boxes in bist mode
    if mode == "bist" and scene.bist_summary:
        b_parts = ['<g data-layer="bist-boxes">']
        for b in scene.bist_summary:
            members = [m for m in macros if m.bist_group == b.bist_group]
            if not members:
                continue
            bx0 = min(m.llx for m in members)
            by0 = min(m.lly for m in members)
            bx1 = max(m.bbox()[2] for m in members)
            by1 = max(m.bbox()[3] for m in members)
            col = bist_colors.get(b.bist_group, "#2563eb")
            b_parts.append(
                f'<rect x="{bx0:.2f}" y="{sy(by1):.2f}" width="{max(bx1 - bx0, 1.0):.2f}" height="{max(by1 - by0, 1.0):.2f}" '
                f'fill="none" stroke="{col}" stroke-dasharray="8 4" stroke-width="{stroke_w * 1.6:.2f}"/>'
            )
        b_parts.append("</g>")
        parts.append("".join(b_parts))

    # IO Pins
    if scene.pins:
        pr = max(w, h) * 0.0045
        p_parts = ['<g data-layer="pins">']
        for p in scene.pins[:1200]:
            pcol = "#16a34a" if p.direction == "INPUT" else ("#ea580c" if p.direction == "OUTPUT" else "#64748b")
            p_parts.append(f'<circle cx="{p.x:.2f}" cy="{sy(p.y):.2f}" r="{pr:.2f}" fill="{pcol}"/>')
        p_parts.append("</g>")
        parts.append("".join(p_parts))

    # Datapath flows
    if datapaths:
        dp_parts = ['<g data-layer="datapaths">']
        lw = max(w, h) * 0.004
        for dp in datapaths:
            pts = " ".join(f"{float(wp['x']):.2f},{sy(float(wp['y'])):.2f}" for wp in dp.waypoints if "x" in wp and "y" in wp)
            if pts:
                dp_parts.append(f'<polyline points="{pts}" fill="none" stroke="{escape(dp.color)}" stroke-width="{lw:.2f}" stroke-linecap="round" stroke-linejoin="round"/>')
        dp_parts.append("</g>")
        parts.append("".join(dp_parts))

    # Review pins
    if review_pins:
        rp_parts = ['<g data-layer="review-pins">']
        rad = max(w, h) * 0.014
        for pin in review_pins:
            col = "#dc2626" if pin.severity == "ACTION" else ("#d97706" if pin.severity == "WARN" else "#16a34a")
            rp_parts.append(
                f'<circle cx="{pin.x:.2f}" cy="{sy(pin.y):.2f}" r="{rad:.2f}" fill="{col}" stroke="#ffffff" stroke-width="{rad * 0.2:.2f}"/>'
                f'<text x="{pin.x:.2f}" y="{sy(pin.y) + rad * 0.36:.2f}" text-anchor="middle" font-size="{rad * 1.05:.2f}" font-weight="700" fill="#ffffff">{pin.number}</text>'
            )
        rp_parts.append("</g>")
        parts.append("".join(rp_parts))

    parts.append("</svg>")
    return "".join(parts).encode("utf-8")


# Minimal 5x7 ASCII bitmap font for crisp pin numbers & labels in 8-bit PNG snapshots
_DIGITS_5X7: dict[str, tuple[int, ...]] = {
    "0": (0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110),
    "1": (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "2": (0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111),
    "3": (0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110),
    "4": (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    "5": (0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110),
    "6": (0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110),
    "7": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000),
    "8": (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    "9": (0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100),
    "#": (0b01010, 0b11111, 0b01010, 0b01010, 0b11111, 0b01010, 0b00000),
}


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def render_scene_png(
    scene: FloorplanScene,
    *,
    mode: str = "overview",
    placement_overrides: list[MacroOverride] | None = None,
    datapaths: list[DatapathFlow] | None = None,
    review_pins: list[ReviewPin] | None = None,
    max_dim: int = 1200,
) -> bytes:
    """Pure-Python 8-bit indexed PNG renderer (~30-140 KB per snapshot, zero external dependencies)."""
    palette_rgb: list[tuple[int, int, int]] = [
        (248, 250, 252),  # 0: canvas bg
        (255, 255, 255),  # 1: die bg
        (15, 23, 42),     # 2: dark ink / die border
        (219, 234, 254),  # 3: sram fill
        (29, 78, 216),    # 4: sram border
        (254, 243, 199),  # 5: ip fill
        (180, 83, 9),     # 6: ip border
        (224, 231, 255),  # 7: subblock fill
        (67, 56, 202),    # 8: subblock border
        (254, 226, 226),  # 9: blockage fill
        (220, 38, 38),    # 10: red / ACTION
        (217, 119, 6),    # 11: amber / WARN
        (22, 163, 74),    # 12: green / PASS
        (224, 242, 254),  # 13: density light
        (186, 230, 253),  # 14: density med
        (100, 116, 139),  # 15: muted slate
        # 16..25: BIST group strokes, 26..35: BIST group soft fills
        (37, 99, 235), (220, 38, 38), (22, 163, 74), (217, 119, 6), (147, 51, 234),
        (8, 145, 178), (219, 39, 119), (79, 70, 229), (101, 163, 13), (234, 88, 12),
        (219, 234, 254), (254, 226, 226), (220, 252, 231), (254, 243, 199), (243, 232, 255),
        (207, 250, 254), (252, 231, 243), (224, 231, 255), (236, 252, 203), (255, 237, 213),
    ]

    x0, y0, x1, y1 = scene.die_bbox
    dw = max(x1 - x0, 10.0)
    dh = max(y1 - y0, 10.0)
    pad = max(dw, dh) * 0.05
    ux0, uy0, ux1, uy1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    uw, uh = ux1 - ux0, uy1 - uy0
    if uw >= uh:
        img_w = max_dim
        img_h = max(360, int(round(max_dim * uh / uw)))
    else:
        img_h = max_dim
        img_w = max(360, int(round(max_dim * uw / uh)))

    buf = bytearray(img_w * img_h)  # initialized to 0 (canvas bg)

    def to_px(ux: float, uy: float) -> tuple[int, int]:
        px = int(round((ux - ux0) / uw * (img_w - 1)))
        py = int(round((uy1 - uy) / uh * (img_h - 1)))
        return max(0, min(img_w - 1, px)), max(0, min(img_h - 1, py))

    def fill_rect(px0: int, py0: int, px1: int, py1: int, col: int) -> None:
        lx, rx = max(0, min(px0, px1)), min(img_w - 1, max(px0, px1))
        ty, by = max(0, min(py0, py1)), min(img_h - 1, max(py0, py1))
        if lx > rx or ty > by:
            return
        span = rx - lx + 1
        row_bytes = bytes([col]) * span
        for yy in range(ty, by + 1):
            off = yy * img_w + lx
            buf[off : off + span] = row_bytes

    def stroke_rect(px0: int, py0: int, px1: int, py1: int, col: int, thick: int = 2) -> None:
        lx, rx = min(px0, px1), max(px0, px1)
        ty, by = min(py0, py1), max(py0, py1)
        fill_rect(lx, ty, rx, ty + thick - 1, col)
        fill_rect(lx, by - thick + 1, rx, by, col)
        fill_rect(lx, ty, lx + thick - 1, by, col)
        fill_rect(rx - thick + 1, ty, rx, by, col)

    def draw_line(ax: int, ay: int, bx: int, by: int, col: int, thick: int = 2) -> None:
        steps = max(abs(bx - ax), abs(by - ay), 1)
        half = thick // 2
        for s in range(steps + 1):
            cx = ax + (bx - ax) * s // steps
            cy = ay + (by - ay) * s // steps
            fill_rect(cx - half, cy - half, cx + half, cy + half, col)

    # Die box
    dx0, dy0 = to_px(x0, y1)
    dx1, dy1 = to_px(x1, y0)
    fill_rect(dx0, dy0, dx1, dy1, 1)

    # Density
    gsize = scene.density_grid_size
    if scene.density_grid and len(scene.density_grid) == gsize * gsize:
        tw, th = dw / gsize, dh / gsize
        for gy in range(gsize):
            for gx in range(gsize):
                val = scene.density_grid[gy * gsize + gx]
                if val < 20:
                    continue
                col = 14 if val >= 55 else 13
                p0x, p0y = to_px(x0 + gx * tw, y0 + (gy + 1) * th)
                p1x, p1y = to_px(x0 + (gx + 1) * tw, y0 + gy * th)
                fill_rect(p0x, p0y, p1x, p1y, col)

    stroke_rect(dx0, dy0, dx1, dy1, 2, thick=2)

    bist_idx = {b.bist_group: idx % 10 for idx, b in enumerate(scene.bist_summary)}
    macros = apply_overrides(scene, placement_overrides)
    for m in macros:
        ow, oh = oriented_size(m.width, m.height, m.orient)
        mx0, my0 = to_px(m.llx, m.lly + oh)
        mx1, my1 = to_px(m.llx + ow, m.lly)
        if mode == "bist" and m.bist_group and m.bist_group in bist_idx:
            bi = bist_idx[m.bist_group]
            fcol, scol = 26 + bi, 16 + bi
        elif m.kind == "ip":
            fcol, scol = 5, 6
        elif m.kind == "subblock":
            fcol, scol = 7, 8
        else:
            fcol, scol = 3, 4
        fill_rect(mx0, my0, mx1, my1, fcol)
        stroke_rect(mx0, my0, mx1, my1, scol, thick=2)
        ox, oy = pin1_corner_offset(m.width, m.height, m.orient)
        nx, ny = to_px(m.llx + ox, m.lly + oy)
        fill_rect(nx - 3, ny - 3, nx + 3, ny + 3, scol)

    if datapaths:
        for dp in datapaths:
            wps = [wp for wp in dp.waypoints if "x" in wp and "y" in wp]
            for i in range(len(wps) - 1):
                ax, ay = to_px(float(wps[i]["x"]), float(wps[i]["y"]))
                bx, by = to_px(float(wps[i + 1]["x"]), float(wps[i + 1]["y"]))
                draw_line(ax, ay, bx, by, 16, thick=4)

    if review_pins:
        for pin in review_pins:
            px, py = to_px(pin.x, pin.y)
            pcol = 10 if pin.severity == "ACTION" else (11 if pin.severity == "WARN" else 12)
            fill_rect(px - 11, py - 9, px + 11, py + 9, pcol)
            stroke_rect(px - 11, py - 9, px + 11, py + 9, 1, thick=1)
            label = str(pin.number)
            start_x = px - (len(label) * 6 - 1) // 2
            for ci, ch in enumerate(label):
                glyph = _DIGITS_5X7.get(ch)
                if not glyph:
                    continue
                for gr, mask in enumerate(glyph):
                    for gc in range(5):
                        if mask & (1 << (4 - gc)):
                            gx = start_x + ci * 6 + gc
                            gy = py - 3 + gr
                            if 0 <= gx < img_w and 0 <= gy < img_h:
                                buf[gy * img_w + gx] = 1

    # Pack indexed 8-bit PNG (color_type=3)
    plte = b"".join(bytes(rgb) for rgb in palette_rgb)
    raw_scanlines = bytearray()
    for yy in range(img_h):
        raw_scanlines.append(0)  # filter type 0 (None)
        raw_scanlines.extend(buf[yy * img_w : (yy + 1) * img_w])
    ihdr = struct.pack(">IIBBBBB", img_w, img_h, 8, 3, 0, 0, 0)
    idat = zlib.compress(bytes(raw_scanlines), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"PLTE", plte)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )
