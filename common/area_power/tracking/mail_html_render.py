"""Rich HTML email templates for NET_VER block and TOP summary result mails."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ..mail_html import (
    FONT_MONO,
    FONT_SANS,
    callout,
    cta_block,
    data_table,
    delta_html,
    esc,
    kpi_strip,
    meta_table,
    pill,
    render_email_shell,
    safe_dashboard_url,
    section_heading,
    share_bar,
    verdict_pill,
)
from .mail_render import _fmt_num, _fmt_signed


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None


def _dual_rankings(
    highlights: list[dict[str, Any]],
    delta_key: str,
    raw_key: str,
    damped_key: str,
) -> tuple[list[tuple[Decimal, Decimal, Decimal | None, Decimal | None, dict[str, Any]]], list[tuple[Decimal, Decimal, Decimal | None, Decimal | None, dict[str, Any]]]]:
    valid = []
    for it in highlights:
        d = _dec(it.get(delta_key))
        dp = _dec(it.get(damped_key))
        rp = _dec(it.get(raw_key))
        eff = dp if dp is not None else rp
        if d is not None and d > 0:
            valid.append((d, eff if eff is not None else d, rp, dp, it))
    if not valid:
        return [], []
    top_abs = sorted(valid, key=lambda r: (-r[0], -r[1], str(r[4].get("path") or "")))[:10]
    abs_ids = {r[4].get("group_id") or r[4].get("path") for r in top_abs}
    top_spike = sorted(
        [r for r in valid if (r[4].get("group_id") or r[4].get("path")) not in abs_ids],
        key=lambda r: (-r[1], -r[0], str(r[4].get("path") or "")),
    )[:10]
    return top_abs, top_spike


def _power_callout(power_eval: dict[str, Any], *, is_top: bool = False) -> str:
    typ = power_eval.get("typical") or {}
    target = typ.get("target") or {}
    sc = power_eval.get("same_condition") or {}
    lines: list[str] = []
    tone = "info"
    title = "대표 전력 평가 (Typical)"

    if typ.get("current_mw") is not None:
        cur_txt = _fmt_num(typ.get("current_mw"), " mW")
        base_txt = _fmt_num(typ.get("baseline_mw"), " mW") if typ.get("baseline_mw") is not None else None
        pct_txt = _fmt_signed(typ.get("delta_pct"), "%") if typ.get("delta_pct") is not None else None
        if base_txt is not None:
            line = (
                f'<strong>Typical 전력:</strong> '
                f'<span style="font-family:{FONT_MONO};">{esc(base_txt)} &rarr; <strong>{esc(cur_txt)}</strong></span>'
            )
        else:
            line = f'<strong>Typical 전력:</strong> <span style="font-family:{FONT_MONO};font-weight:700;">{esc(cur_txt)}</span>'
        if pct_txt is not None:
            line += f'&nbsp;({delta_html(pct_txt, raw_value=typ.get("delta_pct"))})'
        if typ.get("condition_changes"):
            changes = ", ".join(str(c) for c in typ["condition_changes"])
            line += f'&nbsp;&nbsp;{pill(f"조건 변경: {changes}", "warning")}'
        lines.append(line)

    if target.get("status") == "exceeded":
        tone = "danger"
        title = "목표 전력 초과 (Typical)"
        lines.append(
            f'{pill("목표 초과", "danger")}&nbsp;&nbsp;'
            f'목표 <span style="font-family:{FONT_MONO};font-weight:700;">{esc(_fmt_num(target.get("target_mw"), " mW"))}</span> 대비 '
            f'<span style="font-family:{FONT_MONO};font-weight:700;color:#b91c1c;">+{esc(_fmt_num(target.get("exceeded_mw"), " mW"))}</span> 초과'
        )
    elif target.get("status") == "met":
        tone = "success"
        title = "목표 전력 달성 (Typical)"
        lines.append(
            f'{pill("목표 달성", "success")}&nbsp;&nbsp;'
            f'목표 <span style="font-family:{FONT_MONO};font-weight:700;">{esc(_fmt_num(target.get("target_mw"), " mW"))}</span> 이하 유지'
        )

    if not is_top:
        if sc.get("status") == "comparable" and not typ.get("same_conditions"):
            lines.append(
                f'<strong>동일 조건 전력:</strong> '
                f'<span style="font-family:{FONT_MONO};">{esc(_fmt_num(sc.get("baseline_mw"), " mW"))} &rarr; {esc(_fmt_num(sc.get("current_mw"), " mW"))}</span> '
                f'({delta_html(_fmt_signed(sc.get("delta_pct"), "%"), raw_value=sc.get("delta_pct"))})'
            )
        elif sc.get("status") == "conditions_unconfirmed":
            lines.append(f'<strong>동일 조건 전력:</strong> {pill("조건 미확인", "neutral")}')
        elif sc.get("status") == "no_same_condition_data" and not typ.get("same_conditions"):
            lines.append(f'<strong>동일 조건 전력:</strong> {pill("동일 조건 데이터 없음", "neutral")}')

    if not lines:
        return ""
    return callout(title, lines, tone=tone)


def _render_attribution_table(attr: dict[str, Any]) -> str:
    blocks: list[str] = []
    for part, title, unit, tone in (
        ("power", "1단계 Total Power 증가 기여", " mW", "warning"),
        ("area", "1단계 Logic Area 증가 기여", "", "info"),
    ):
        info = attr.get(part) or {}
        rows = info.get("items") or []
        if not info.get("analyzed") or not rows:
            continue
        table_rows: list[list[str]] = []
        for idx, r in enumerate(rows[:5], start=1):
            share_pct = r.get("share_of_increase_pct")
            bar = share_bar(share_pct, tone=tone, width_px=72)
            table_rows.append([
                f'<span style="font-family:{FONT_MONO};color:#64748b;">{idx}</span>',
                f'<span style="font-family:{FONT_MONO};font-weight:600;color:#0f172a;">{esc(r.get("path") or "—")}</span>',
                delta_html(_fmt_signed(r.get("delta"), unit), raw_value=r.get("delta")),
                f'{bar}&nbsp;<span style="font-family:{FONT_MONO};font-size:11.5px;color:#334155;">{esc(_fmt_num(share_pct, "%"))}</span>',
            ])
        rest_count = int(info.get("rest_count") or 0)
        if rest_count > 0:
            rest_share = info.get("rest_share_pct")
            table_rows.append([
                '<span style="color:#94a3b8;">&middot;</span>',
                f'<span style="color:#64748b;">외 {rest_count}개 Sub-block 합계</span>',
                delta_html(_fmt_signed(info.get("rest_delta"), unit), raw_value=info.get("rest_delta"), bold=False),
                f'<span style="font-family:{FONT_MONO};font-size:11.5px;color:#64748b;">{esc(_fmt_num(rest_share, "%")) if rest_share is not None else "—"}</span>',
            ])
        blocks.append(
            f'<div style="margin:10px 0 4px 0;font-family:{FONT_SANS};font-size:12px;font-weight:700;color:#334155;">{esc(title)}</div>'
            + data_table(
                ["#", "1단계 Sub-block", "증가량 (Δ)", "전체 증가 지분"],
                table_rows,
                aligns=["center", "left", "right", "right"],
            )
        )
    return "".join(blocks)


def _render_ranking_table(
    title: str,
    rows: list[tuple[Decimal, Decimal, Decimal | None, Decimal | None, dict[str, Any]]],
    unit: str,
) -> str:
    if not rows:
        return ""
    table_rows: list[list[str]] = []
    for idx, (d, _eff, rp, dp, it) in enumerate(rows, start=1):
        d1 = it.get("depth1_block")
        d1_html = f'{pill(str(d1), "neutral", mono=True)}&nbsp;' if d1 else ""
        path_html = (
            f'{d1_html}<span style="font-family:{FONT_MONO};font-weight:600;color:#0f172a;word-break:break-all;">'
            f'{esc(it.get("path") or "—")}</span>'
        )
        expl = " ".join(str(it.get("explanation") or "").split())
        if expl:
            path_html += f'<div style="margin-top:3px;font-family:{FONT_SANS};font-size:11.5px;color:#475569;line-height:1.45;">{esc(expl)}</div>'
        eff_html = delta_html(_fmt_signed(dp, "%"), raw_value=dp) if dp is not None else '<span style="color:#94a3b8;">—</span>'
        table_rows.append([
            f'<span style="font-family:{FONT_MONO};color:#64748b;">{idx}</span>',
            verdict_pill(it.get("verdict")),
            path_html,
            delta_html(_fmt_signed(d, unit), raw_value=d),
            delta_html(_fmt_signed(rp, "%"), raw_value=rp, bold=False),
            eff_html,
        ])
    return (
        f'<div style="margin:10px 0 4px 0;font-family:{FONT_SANS};font-size:12px;font-weight:700;color:#334155;">{esc(title)}</div>'
        + data_table(
            ["#", "판정", "Instance / 설명", "증가량 (Δ)", "단순 Δ%", "Effective Δ%"],
            table_rows,
            aligns=["center", "center", "left", "right", "right", "right"],
        )
    )


def render_block_mail_html(
    snapshot: dict[str, Any],
    *,
    mail_id: str | None = None,
    dashboard_url: str | None = None,
) -> str:
    """Render an email-client-safe HTML document for one block owner's NET_VER result mail."""
    project = str(snapshot.get("project_name") or "프로젝트")
    block = str(snapshot.get("block_name") or "블록")
    net_ver = str(snapshot.get("net_ver") or "")
    revised = bool(snapshot.get("revised"))
    post_top = bool(snapshot.get("post_top_mail_revised"))
    totals = snapshot.get("totals") or {}
    unexp = int(totals.get("unexpected") or 0)
    review = int(totals.get("needs_review") or 0)
    expected = int(totals.get("expected") or 0)
    code_exp = int(totals.get("code_explained") or 0)
    minor = int(totals.get("minor") or 0)

    header_badges = [
        pill(f"이상 {unexp}건", "dark_danger" if unexp else "dark"),
        pill(f"확인 필요 {review}건", "dark_warning" if review else "dark"),
    ]
    if snapshot.get("is_full_chip"):
        header_badges.append(pill("Full-chip", "dark_info"))
    if post_top:
        header_badges.append(pill("TOP 발송 후 수정본", "dark_warning"))
    elif revised:
        header_badges.append(pill("수정본", "dark_info"))

    sub_parts = [f"NET_VER {net_ver}" if net_ver else ""]
    if snapshot.get("revision_label"):
        sub_parts.append(f"REVISION {snapshot['revision_label']}")
    if snapshot.get("eco_label"):
        sub_parts.append(f"ECO {snapshot['eco_label']}")
    subtitle = " · ".join(p for p in sub_parts if p)

    parts: list[str] = []
    parts.append(
        kpi_strip([
            {"label": "이상 (Unexpected)", "value": f"{unexp}건", "tone": "danger" if unexp else "neutral"},
            {"label": "확인 필요 (Review)", "value": f"{review}건", "tone": "warning" if review else "neutral"},
            {"label": "설명됨 (Expected)", "value": f"{expected}건", "sub": f"코드 대조 {code_exp}건", "tone": "success" if expected else "neutral"},
            {"label": "변화 미미 (Minor)", "value": f"{minor}건", "tone": "neutral"},
        ])
    )

    if post_top:
        parts.append(
            callout(
                "TOP 요약 발송 이후 도착한 수정본 결과입니다.",
                ["최신 현황 화면에서 기존 TOP 요약 대비 변경된 수치를 함께 비교할 수 있습니다."],
                tone="warning",
            )
        )

    power_html = _power_callout(snapshot.get("power_evaluation") or {}, is_top=False)
    if power_html:
        parts.append(power_html)

    corners = snapshot.get("corners") or []
    if corners:
        parts.append(section_heading("Corner별 수치 요약", badge_html=pill(f"{len(corners)}개 Corner", "info")))
        summary_rows: list[list[str]] = []
        for c in corners:
            c_label = str(c.get("corner_label") or c.get("corner_id") or "—")
            area_cell = (
                f'<div style="font-family:{FONT_MONO};font-size:11.5px;color:#475569;">'
                f'{esc(_fmt_num(c.get("logic_area_baseline")))} &rarr; {esc(_fmt_num(c.get("logic_area_current")))}</div>'
                f'<div style="margin-top:2px;">{delta_html(_fmt_signed(c.get("logic_area_delta_pct"), "%"), raw_value=c.get("logic_area_delta_pct"))}</div>'
            )
            pwr_cell = (
                f'<div style="font-family:{FONT_MONO};font-size:11.5px;color:#475569;">'
                f'{esc(_fmt_num(c.get("power_baseline_mw"), " mW"))} &rarr; {esc(_fmt_num(c.get("power_current_mw"), " mW"))}</div>'
                f'<div style="margin-top:2px;">{delta_html(_fmt_signed(c.get("power_delta_pct"), "%"), raw_value=c.get("power_delta_pct"))}</div>'
            )
            cu = int(c.get("unexpected_count") or 0)
            cr = int(c.get("needs_review_count") or 0)
            ce = int(c.get("expected_count") or 0)
            verdicts_cell = (
                f'{pill(f"이상 {cu}", "danger" if cu else "neutral")} '
                f'{pill(f"확인 {cr}", "warning" if cr else "neutral")} '
                f'{pill(f"설명됨 {ce}", "success" if ce else "neutral")}'
            )
            summary_rows.append([
                f'<strong style="font-family:{FONT_MONO};color:#0f172a;">{esc(c_label)}</strong>',
                area_cell,
                pwr_cell,
                verdicts_cell,
            ])
        parts.append(
            data_table(
                ["Corner", "Logic Area (기준 → 현재)", "Total Power (기준 → 현재)", "판정 요약"],
                summary_rows,
                aligns=["left", "right", "right", "left"],
            )
        )

        for c in corners:
            c_label = str(c.get("corner_label") or c.get("corner_id") or "Corner")
            parts.append(section_heading(f"Corner 상세 · {c_label}", badge_html=pill(c_label, "neutral", mono=True)))
            attr_html = _render_attribution_table(c.get("attribution") or {})
            if attr_html:
                parts.append(attr_html)

            highlights = c.get("highlights") or []
            if highlights:
                has_ranking = any(
                    item.get("power_delta_mw") is not None or item.get("logic_area_delta") is not None
                    for item in highlights
                )
                ranked_html = ""
                if has_ranking:
                    p_abs, p_spike = _dual_rankings(highlights, "power_delta_mw", "power_delta_pct", "power_damped_pct")
                    a_abs, a_spike = _dual_rankings(highlights, "logic_area_delta", "logic_area_delta_pct", "logic_area_damped_pct")
                    ranked_html += _render_ranking_table(f"Power 절대 증가 Top {len(p_abs)}", p_abs, " mW")
                    ranked_html += _render_ranking_table(f"Power Effective Δ% Top {len(p_spike)} (중복 제외)", p_spike, " mW")
                    ranked_html += _render_ranking_table(f"Logic Area 절대 증가 Top {len(a_abs)}", a_abs, "")
                    ranked_html += _render_ranking_table(f"Logic Area Effective Δ% Top {len(a_spike)} (중복 제외)", a_spike, "")
                if ranked_html:
                    parts.append(ranked_html)
                else:
                    fallback_rows = []
                    for item in highlights[:25]:
                        path_txt = f'<span style="font-family:{FONT_MONO};font-weight:600;">{esc(item.get("path") or "—")}</span>'
                        expl = " ".join(str(item.get("explanation") or "").split())
                        if expl:
                            path_txt += f'<div style="margin-top:3px;font-size:11.5px;color:#475569;">{esc(expl)}</div>'
                        fallback_rows.append([
                            verdict_pill(item.get("verdict")),
                            path_txt,
                            delta_html(_fmt_signed(item.get("logic_area_delta_pct"), "%"), raw_value=item.get("logic_area_delta_pct")),
                            delta_html(_fmt_signed(item.get("power_delta_pct"), "%"), raw_value=item.get("power_delta_pct")),
                        ])
                    parts.append(
                        data_table(
                            ["판정", "Instance / 설명", "Logic Area Δ%", "Power Δ%"],
                            fallback_rows,
                            aligns=["center", "left", "right", "right"],
                        )
                    )
            else:
                parts.append(callout("검토가 필요한 이상 항목 없음", ["이 Corner에서는 기준을 초과한 이상·확인 필요 항목이 검출되지 않았습니다."], tone="success"))

    if mail_id:
        rel_path = f"/?page=area-power&tracking_mail={mail_id}"
        parts.append(
            cta_block(
                label="Design Atlas에서 발송 당시 고정 결과 열기 →",
                url=safe_dashboard_url(dashboard_url, rel_path),
                fallback_path=rel_path,
                note="최신 현황에서 후속 수정본 도착 여부와 전체 Hierarchy 표를 바로 확인할 수 있습니다.",
            )
        )

    preheader = f"{project} · {block} ({net_ver}) — 이상 {unexp}건 · 확인 필요 {review}건 · 설명됨 {expected}건"
    return render_email_shell(
        preheader=preheader,
        category="NET_VER BLOCK RESULT",
        title=f"{project} · {block}",
        subtitle=subtitle,
        header_badges=header_badges,
        body_html="".join(parts),
        footer_notes=(
            "최신 현황에서 후속 수정본 여부를 함께 확인할 수 있습니다.",
            "이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다.",
        ),
    )


def render_top_mail_html(
    snapshot: dict[str, Any],
    *,
    mail_id: str | None = None,
    dashboard_url: str | None = None,
) -> str:
    """Render an email-client-safe HTML document for one project × NET_VER TOP summary mail."""
    project = str(snapshot.get("project_name") or "프로젝트")
    net_ver = str(snapshot.get("net_ver") or "")
    is_all_done = snapshot.get("trigger") == "all_completed"
    trigger = "전체 분석 완료" if is_all_done else "마감 도달"
    full_chip = snapshot.get("full_chip")
    sub_counts = snapshot.get("sub_block_counts") or {}
    fc_counts = snapshot.get("full_chip_counts") or {}

    rows = list(snapshot.get("blocks") or [])
    rows.sort(
        key=lambda row: (
            0 if row.get("needs_attention") else 1,
            0 if row.get("is_full_chip") else 1,
            row.get("block_name") or "",
        )
    )
    attention_count = sum(1 for r in rows if r.get("needs_attention"))
    deadline_txt = str(snapshot.get("deadline_display") or snapshot.get("deadline_at") or "—")
    arrived_txt = str(snapshot.get("netlist_arrived_at") or "—")

    header_badges = [
        pill(trigger, "dark_success" if is_all_done else "dark_warning"),
        pill(f"하위 블록 완료 {sub_counts.get('completed', 0)}/{sub_counts.get('total', 0)}", "dark_info"),
    ]
    if attention_count:
        header_badges.append(pill(f"주의 필요 {attention_count}개 블록", "dark_danger"))

    parts: list[str] = []
    parts.append(
        kpi_strip([
            {
                "label": "Full-chip 분석 완료",
                "value": f"{fc_counts.get('completed', 0)}/{fc_counts.get('total', 0)}",
                "sub": f"도착 {fc_counts.get('arrived', 0)}/{fc_counts.get('total', 0)}",
                "tone": "success" if fc_counts.get("total", 0) and fc_counts.get("completed", 0) == fc_counts.get("total", 0) else "info",
            },
            {
                "label": "하위 블록 완료",
                "value": f"{sub_counts.get('completed', 0)}/{sub_counts.get('total', 0)}",
                "sub": f"도착 {sub_counts.get('arrived', 0)}/{sub_counts.get('total', 0)}",
                "tone": "success" if sub_counts.get("total", 0) and sub_counts.get("completed", 0) == sub_counts.get("total", 0) else "info",
            },
            {
                "label": "주의 필요 블록",
                "value": f"{attention_count}건",
                "sub": f"전체 {len(rows)}개 대상 중",
                "tone": "danger" if attention_count else "success",
            },
        ])
    )

    parts.append(
        meta_table([
            ("프로젝트 / NET_VER", f'<strong>{esc(project)}</strong> &middot; <span style="font-family:{FONT_MONO};font-weight:700;">{esc(net_ver or "—")}</span>'),
            ("Netlist 도착일", f'<span style="font-family:{FONT_MONO};">{esc(arrived_txt)}</span>'),
            ("회차 마감 일시", f'<span style="font-family:{FONT_MONO};">{esc(deadline_txt)}</span>'),
        ])
    )

    if full_chip and full_chip.get("arrived_corners", 0) > 0:
        fc_power_html = _power_callout(full_chip.get("power_evaluation") or {}, is_top=True)
        if fc_power_html:
            parts.append(fc_power_html)
        else:
            parts.append(callout("Full-chip 데이터 도착", ["대표 전력 계산 및 분석 완료를 대기 중입니다."], tone="info"))
    else:
        parts.append(callout("Full-chip 미도착", ["Full-chip CSV가 아직 도착하지 않아 전체 목표 전력 판정이 대기 상태입니다."], tone="warning"))

    parts.append(
        section_heading(
            "대상 블록 요약",
            badge_html=pill(f"총 {len(rows)}개 블록", "neutral"),
            subtitle="주의가 필요한 블록(미도착·오류·이상 항목·목표 초과)이 상단에 우선 배치됩니다.",
        )
    )

    table_rows: list[list[str]] = []
    row_bgs: list[str | None] = []
    for row in rows:
        is_fc = bool(row.get("is_full_chip"))
        needs_attn = bool(row.get("needs_attention"))
        name = f"{project} · Full-chip" if is_fc else str(row.get("block_name") or row.get("block_id") or "—")
        name_html = f'<strong style="color:#0f172a;">{esc(name)}</strong>'
        if is_fc:
            name_html += f'&nbsp;{pill("Full-chip", "info")}'
        if needs_attn:
            name_html += f'&nbsp;{pill("주의", "warning")}'
        rev_parts = [p for p in (row.get("revision_label"), row.get("eco_label")) if p]
        if rev_parts:
            name_html += f'<div style="margin-top:2px;font-family:{FONT_MONO};font-size:11px;color:#64748b;">{esc(" · ".join(str(p) for p in rev_parts))}</div>'

        req = int(row.get("required_corners") or 0)
        arr = int(row.get("arrived_corners") or 0)
        comp = int(row.get("completed_corners") or 0)
        arr_tone = "success" if req > 0 and arr >= req else ("warning" if arr > 0 else "danger")
        comp_tone = "success" if req > 0 and comp >= req else ("info" if comp > 0 else "neutral")

        table_rows.append([
            name_html,
            pill(f"{arr}/{req}", arr_tone, mono=True),
            pill(f"{comp}/{req}", comp_tone, mono=True),
            f'<span style="color:{"#92400e" if needs_attn else "#334155"};font-weight:{"600" if needs_attn else "400"};">{esc(row.get("status_summary") or "대기")}</span>',
        ])
        row_bgs.append("#fffdf5" if needs_attn else None)

    parts.append(
        data_table(
            ["대상 블록", "필수 Corner 도착", "분석 완료", "상태 요약"],
            table_rows,
            aligns=["left", "center", "center", "left"],
            row_bgs=row_bgs,
            empty_text="등록된 대상 블록이 없습니다.",
        )
    )

    if mail_id:
        rel_path = f"/?page=area-power&tracking_mail={mail_id}"
        parts.append(
            cta_block(
                label="Design Atlas에서 발송 당시 TOP 고정 요약 열기 →",
                url=safe_dashboard_url(dashboard_url, rel_path),
                fallback_path=rel_path,
                note="이후 도착한 수정본은 현황 화면에서 '발송 후 수정' 배지로 확인할 수 있습니다.",
            )
        )

    preheader = f"{project} · {net_ver} TOP 요약 ({trigger}) — 하위 블록 완료 {sub_counts.get('completed', 0)}/{sub_counts.get('total', 0)}"
    return render_email_shell(
        preheader=preheader,
        category="NET_VER TOP SUMMARY",
        title=f"{project} · {net_ver} TOP 요약",
        subtitle=f"{trigger} · netlist 도착 {arrived_txt} · 마감 {deadline_txt}",
        header_badges=header_badges,
        body_html="".join(parts),
        footer_notes=(
            "이 요약은 발송 요청 시점의 결과를 고정한 것입니다. 이후 도착한 수정본은 현황에서 '발송 후 수정'으로 확인할 수 있습니다.",
            "이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다.",
        ),
    )
