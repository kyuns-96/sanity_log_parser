"""Plain-text rendering for NET_VER block and TOP summary result mails."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

D = Decimal


def _fmt_num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    text = format(value, "f") if isinstance(value, Decimal) and value.is_finite() else str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text + suffix


def _fmt_signed(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    dec = value if isinstance(value, Decimal) else D(str(value))
    prefix = "+" if dec > 0 else ""
    return prefix + _fmt_num(dec, suffix)


def render_block_mail_text(snapshot: dict[str, Any], *, mail_id: str | None = None) -> tuple[str, str]:
    """Build subject and plain-text body for one block owner's NET_VER result mail."""
    project = snapshot.get("project_name") or "프로젝트"
    block = snapshot.get("block_name") or "블록"
    net_ver = snapshot.get("net_ver") or ""
    revised = bool(snapshot.get("revised"))
    post_top = bool(snapshot.get("post_top_mail_revised"))
    tag = " (TOP 발송 후 수정본)" if post_top else (" (수정본)" if revised else "")
    totals = snapshot.get("totals") or {}
    subject = " ".join(
        f"[Design Atlas] {project} · {block} · {net_ver}{tag} · 이상 {totals.get('unexpected', 0)}건 · 확인 필요 {totals.get('needs_review', 0)}건".split()
    )[:200]

    lines = [
        f"Design Atlas NET_VER 자동 추적 블록 결과입니다.{tag}",
        "",
        f"프로젝트: {project}",
        f"블록: {block}" + (" (Full-chip)" if snapshot.get("is_full_chip") else ""),
        f"NET_VER: {net_ver}" + (f" · REVISION {snapshot['revision_label']}" if snapshot.get("revision_label") else ""),
        f"결과 요약: 이상 {totals.get('unexpected', 0)}건 · 확인 필요 {totals.get('needs_review', 0)}건 · 설명됨 {totals.get('expected', 0)}건(코드 대조 {totals.get('code_explained', 0)}건) · 변화 미미 {totals.get('minor', 0)}건",
    ]
    power_eval = snapshot.get("power_evaluation") or {}
    typ = power_eval.get("typical") or {}
    target = typ.get("target") or {}
    if typ.get("current_mw") is not None:
        tline = f"대표 전력(Typical): {_fmt_num(typ.get('baseline_mw'), ' mW')} → {_fmt_num(typ.get('current_mw'), ' mW')} ({_fmt_signed(typ.get('delta_pct'), '%')})"
        if typ.get("condition_changes"):
            tline += " · 조건 변경: " + ", ".join(typ["condition_changes"])
        lines.append(tline)
    if target.get("status") == "exceeded":
        lines.append(f"목표 전력 초과: 목표 {_fmt_num(target.get('target_mw'), ' mW')} 대비 {_fmt_num(target.get('exceeded_mw'), ' mW')} 초과")
    elif target.get("status") == "met":
        lines.append(f"목표 전력 달성: 목표 {_fmt_num(target.get('target_mw'), ' mW')} 이하")

    sc = power_eval.get("same_condition") or {}
    if sc.get("status") == "comparable" and not typ.get("same_conditions"):
        lines.append(f"동일 조건 전력: {_fmt_num(sc.get('baseline_mw'), ' mW')} → {_fmt_num(sc.get('current_mw'), ' mW')} ({_fmt_signed(sc.get('delta_pct'), '%')})")
    elif sc.get("status") == "conditions_unconfirmed":
        lines.append("동일 조건 전력: 조건 미확인")
    elif sc.get("status") == "no_same_condition_data" and not typ.get("same_conditions"):
        lines.append("동일 조건 전력: 동일 조건 데이터 없음")

    for corner in snapshot.get("corners") or []:
        lines += [
            "",
            f"■ Corner: {corner.get('corner_label') or corner.get('corner_id')}",
            f"  Logic Area: {_fmt_num(corner.get('logic_area_baseline'))} → {_fmt_num(corner.get('logic_area_current'))} ({_fmt_signed(corner.get('logic_area_delta_pct'), '%')})",
            f"  Total Power: {_fmt_num(corner.get('power_baseline_mw'), ' mW')} → {_fmt_num(corner.get('power_current_mw'), ' mW')} ({_fmt_signed(corner.get('power_delta_pct'), '%')})",
            f"  설명됨 {corner.get('expected_count', 0)}건 · 이상 {corner.get('unexpected_count', 0)}건 · 확인 필요 {corner.get('needs_review_count', 0)}건",
        ]
        highlights = corner.get("highlights") or []
        if highlights:
            for item in highlights[:25]:
                lines.append(
                    f"  - [{item.get('verdict')}] {item.get('path')} · Logic Area {_fmt_signed(item.get('logic_area_delta_pct'), '%')} · Power {_fmt_signed(item.get('power_delta_pct'), '%')} · {item.get('explanation', '')}"
                )
        else:
            lines.append("  검토가 필요한 이상 항목 없음")

    if mail_id:
        lines += ["", f"발송 당시 고정 결과 링크: /?page=area-power&tracking_mail={mail_id}"]
    lines += [
        "최신 현황에서 후속 수정본 여부를 함께 확인할 수 있습니다.",
        "이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다.",
    ]
    return subject, "\n".join(lines)


def render_top_mail_text(snapshot: dict[str, Any], *, mail_id: str | None = None) -> tuple[str, str]:
    """Build subject and plain-text body for one project × NET_VER TOP summary mail."""
    project = snapshot.get("project_name") or "프로젝트"
    net_ver = snapshot.get("net_ver") or ""
    trigger = "전체 분석 완료" if snapshot.get("trigger") == "all_completed" else "마감 도달"
    full_chip = snapshot.get("full_chip")
    sub_counts = snapshot.get("sub_block_counts") or {}
    fc_counts = snapshot.get("full_chip_counts") or {}

    subject = " ".join(
        f"[Design Atlas] TOP 요약 · {project} · {net_ver} · {trigger} · 하위 블록 완료 {sub_counts.get('completed', 0)}/{sub_counts.get('total', 0)}".split()
    )[:200]

    lines = [
        f"Design Atlas NET_VER 자동 추적 TOP 요약입니다. ({trigger})",
        "",
        f"프로젝트: {project} · NET_VER: {net_ver}",
        f"netlist 도착일: {snapshot.get('netlist_arrived_at') or '—'} · 마감: {snapshot.get('deadline_display') or snapshot.get('deadline_at') or '—'}",
        f"Full-chip: 도착 {fc_counts.get('arrived', 0)}/{fc_counts.get('total', 0)} · 분석 완료 {fc_counts.get('completed', 0)}/{fc_counts.get('total', 0)}",
        f"하위 블록: 도착 {sub_counts.get('arrived', 0)}/{sub_counts.get('total', 0)} · 분석 완료 {sub_counts.get('completed', 0)}/{sub_counts.get('total', 0)}",
    ]

    if full_chip and full_chip.get("arrived_corners", 0) > 0:
        typ = (full_chip.get("power_evaluation") or {}).get("typical") or {}
        target = typ.get("target") or {}
        if typ.get("current_mw") is not None:
            fc_line = f"Full-chip 대표 전력: {_fmt_num(typ.get('current_mw'), ' mW')}"
            if typ.get("delta_pct") is not None:
                fc_line += f" (기준 대비 {_fmt_signed(typ.get('delta_pct'), '%')})"
            if target.get("status") == "exceeded":
                fc_line += f" · 목표 {_fmt_num(target.get('target_mw'), ' mW')} 대비 {_fmt_num(target.get('exceeded_mw'), ' mW')} 초과"
            elif target.get("status") == "met":
                fc_line += f" · 목표 {_fmt_num(target.get('target_mw'), ' mW')} 달성"
            if typ.get("condition_changes"):
                fc_line += " · " + ", ".join(typ["condition_changes"])
            lines.append(fc_line)
        else:
            lines.append("Full-chip: 데이터 도착(대표 전력 계산 대기)")
    else:
        lines.append("Full-chip 미도착 · 전체 목표 판정 대기")

    lines += ["", "■ 대상 블록 요약 (주의 필요 블록 우선)"]
    rows = list(snapshot.get("blocks") or [])
    rows.sort(key=lambda row: (0 if row.get("needs_attention") else 1, 0 if row.get("is_full_chip") else 1, row.get("block_name") or ""))
    for row in rows:
        name = row.get("block_name") or str(row.get("block_id"))
        if row.get("is_full_chip"):
            name = f"{project} · Full-chip"
        rev = f" · {row['revision_label']}" if row.get("revision_label") else ""
        lines.append(
            f"- {name}{rev} | 필수 corner 도착 {row.get('arrived_corners', 0)}/{row.get('required_corners', 0)} | "
            f"분석 완료 {row.get('completed_corners', 0)}/{row.get('required_corners', 0)} | {row.get('status_summary') or '대기'}"
        )

    if mail_id:
        lines += ["", f"발송 당시 고정 요약 링크: /?page=area-power&tracking_mail={mail_id}"]
    lines += [
        "이 요약은 발송 요청 시점의 결과를 고정한 것입니다. 이후 도착한 수정본은 현황에서 '발송 후 수정'으로 확인할 수 있습니다.",
    ]
    return subject, "\n".join(lines)
