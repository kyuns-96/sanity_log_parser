"""Export Designer Memory Placement proposals to Innovus Tcl, ICC2/FC Tcl, and Delta CSV."""
from __future__ import annotations

import csv
import io

from .models import DEF_TO_ICC2_ORIENT, FloorplanScene, MacroOverride, oriented_size


def check_placement_collisions(
    scene: FloorplanScene,
    overrides: list[MacroOverride] | None,
) -> dict[str, list[str]]:
    """Verify Die boundary containment and Macro+Halo overlaps in real micron coordinates."""
    by_name = {ov.inst_name: ov for ov in (overrides or [])}
    x0, y0, x1, y1 = scene.die_bbox
    out_of_bounds: list[str] = []
    boxes: list[tuple[str, float, float, float, float]] = []
    for m in scene.macros:
        ov = by_name.get(m.inst_name)
        llx = ov.llx if ov else m.llx
        lly = ov.lly if ov else m.lly
        orient = ov.orient if ov else m.orient
        halo = ov.halo_um if (ov and ov.halo_um is not None) else m.halo_um
        ow, oh = oriented_size(m.width, m.height, orient)
        urx, ury = llx + ow, lly + oh
        if llx < x0 - 0.01 or lly < y0 - 0.01 or urx > x1 + 0.01 or ury > y1 + 0.01:
            out_of_bounds.append(m.inst_name)
        hl, hb, hr, ht = halo
        boxes.append((m.inst_name, llx - hl, lly - hb, urx + hr, ury + ht))

    overlaps: list[str] = []
    for i in range(len(boxes)):
        n1, ax0, ay0, ax1, ay1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            n2, bx0, by0, bx1, by1 = boxes[j]
            if ax0 < bx1 - 0.01 and ax1 > bx0 + 0.01 and ay0 < by1 - 0.01 and ay1 > by0 + 0.01:
                overlaps.append(f"{n1} <-> {n2}")
    return {"out_of_bounds": out_of_bounds, "overlaps": overlaps}


def export_innovus_tcl(
    scene: FloorplanScene,
    overrides: list[MacroOverride],
    *,
    proposal_title: str = "Designer Placement Proposal",
    only_changed: bool = False,
) -> str:
    """Generate Cadence Innovus macro placement Tcl script."""
    by_name = {ov.inst_name: ov for ov in overrides}
    checks = check_placement_collisions(scene, overrides)
    lines = [
        "# Design Atlas — Cadence Innovus Macro/SRAM Placement Script",
        f"# Design: {scene.design_name} | Base FP_VER: {scene.fp_ver} | Proposal: {proposal_title}",
        f"# Out-of-bounds: {len(checks['out_of_bounds'])} | Halo/Macro Overlaps: {len(checks['overlaps'])}",
        "",
    ]
    for m in scene.macros:
        ov = by_name.get(m.inst_name)
        if only_changed and ov is None:
            continue
        llx = ov.llx if ov else m.llx
        lly = ov.lly if ov else m.lly
        orient = ov.orient if ov else m.orient
        halo = ov.halo_um if (ov and ov.halo_um is not None) else m.halo_um
        changed = ov is not None and (abs(llx - m.llx) > 1e-3 or abs(lly - m.lly) > 1e-3 or orient != m.orient)
        if only_changed and not changed:
            continue
        tag = " ;# MODIFIED" if changed else ""
        lines.append(f"placeInstance {{{m.inst_name}}} {llx:.4f} {lly:.4f} {orient} -fixed{tag}")
        if any(v > 0 for v in halo):
            hl, hb, hr, ht = halo
            lines.append(f"deleteHaloFromBlock {{{m.inst_name}}}")
            lines.append(f"addHaloToBlock {hl:.3f} {hb:.3f} {hr:.3f} {ht:.3f} {{{m.inst_name}}}")
    lines.append("")
    return "\n".join(lines)


def export_icc2_tcl(
    scene: FloorplanScene,
    overrides: list[MacroOverride],
    *,
    proposal_title: str = "Designer Placement Proposal",
    only_changed: bool = False,
) -> str:
    """Generate Synopsys ICC2 / Fusion Compiler macro placement Tcl script."""
    by_name = {ov.inst_name: ov for ov in overrides}
    checks = check_placement_collisions(scene, overrides)
    lines = [
        "# Design Atlas — Synopsys ICC2 / Fusion Compiler Macro/SRAM Placement Script",
        f"# Design: {scene.design_name} | Base FP_VER: {scene.fp_ver} | Proposal: {proposal_title}",
        f"# Out-of-bounds: {len(checks['out_of_bounds'])} | Halo/Macro Overlaps: {len(checks['overlaps'])}",
        "",
    ]
    for m in scene.macros:
        ov = by_name.get(m.inst_name)
        if only_changed and ov is None:
            continue
        llx = ov.llx if ov else m.llx
        lly = ov.lly if ov else m.lly
        orient = ov.orient if ov else m.orient
        changed = ov is not None and (abs(llx - m.llx) > 1e-3 or abs(lly - m.lly) > 1e-3 or orient != m.orient)
        if only_changed and not changed:
            continue
        icc2_orient = DEF_TO_ICC2_ORIENT.get(orient, "R0")
        lines.extend([
            f"set _da_cell [get_cells -quiet {{{m.inst_name}}}]",
            "if {[sizeof_collection $_da_cell] > 0} {",
            f"  set_attribute $_da_cell orientation {icc2_orient}",
            f"  set_attribute $_da_cell origin {{{llx:.4f} {lly:.4f}}}",
            "  set_attribute $_da_cell physical_status fixed",
            "}",
        ])
    lines.append("unset -nocomplain _da_cell\n")
    return "\n".join(lines)


def export_delta_csv(scene: FloorplanScene, overrides: list[MacroOverride]) -> str:
    """Export per-macro coordinate & orientation comparison CSV."""
    by_name = {ov.inst_name: ov for ov in overrides}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "inst_name", "master", "kind", "bist_group",
        "orig_llx_um", "orig_lly_um", "orig_orient",
        "new_llx_um", "new_lly_um", "new_orient",
        "dx_um", "dy_um", "manhattan_shift_um", "changed",
    ])
    for m in scene.macros:
        ov = by_name.get(m.inst_name)
        nx = ov.llx if ov else m.llx
        ny = ov.lly if ov else m.lly
        no = ov.orient if ov else m.orient
        dx = round(nx - m.llx, 4)
        dy = round(ny - m.lly, 4)
        changed = abs(dx) > 1e-3 or abs(dy) > 1e-3 or no != m.orient
        writer.writerow([
            m.inst_name, m.master, m.kind, m.bist_group or "",
            f"{m.llx:.4f}", f"{m.lly:.4f}", m.orient,
            f"{nx:.4f}", f"{ny:.4f}", no,
            f"{dx:.4f}", f"{dy:.4f}", f"{abs(dx) + abs(dy):.4f}",
            "Y" if changed else "N",
        ])
    return out.getvalue()
