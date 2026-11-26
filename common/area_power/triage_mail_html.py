"""Rich HTML email templates for Area/Power AI triage digests and alert notifications."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .jsonutil import dumps, public
from .mail import MAIL_MAX_HTML_BODY_BYTES, truncate_utf8_bytes, utf8_len
from .mail_html import (
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
from .tracking.mail_render import _fmt_num as _number, _fmt_signed as _signed


def _dec_val(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None


def _attribution_html(attribution: dict[str, Any] | None) -> str:
    if not attribution:
        return ""
    blocks: list[str] = []
    for part, title, unit, tone in (
        ("power", "Total Power", " mW", "warning"),
        ("area", "Logic Area", "", "info"),
    ):
        info = attribution.get(part) or {}
        items = info.get("items") or []
        if not info.get("analyzed") or not items:
            continue
        r_delta = _dec_val(info.get("root_delta"))
        r_pct = _dec_val(info.get("root_delta_pct"))
        sub_badge = (
            f'전체 증가 {delta_html(_signed(r_delta, unit), raw_value=r_delta)} '
            f'({delta_html(_signed(r_pct, "%"), raw_value=r_pct)})'
        )
        rows: list[list[str]] = []
        for idx, row in enumerate(items, start=1):
            d = _dec_val(row.get("delta"))
            sh = _dec_val(row.get("share_of_increase_pct"))
            dp = _dec_val(row.get("delta_pct"))
            rows.append([
                f'<span style="font-family:{FONT_MONO};color:#64748b;">{idx}</span>',
                f'<span style="font-family:{FONT_MONO};font-weight:600;color:#0f172a;">{esc(row.get("path") or "—")}</span>',
                f'<span style="font-family:{FONT_MONO};font-size:11.5px;color:#475569;">{esc(_number(_dec_val(row.get("baseline")), unit))} &rarr; {esc(_number(_dec_val(row.get("current")), unit))}</span>',
                delta_html(_signed(d, unit), raw_value=d),
                delta_html(_signed(dp, "%"), raw_value=dp, bold=False),
                f'{share_bar(sh, tone=tone, width_px=68)}&nbsp;<span style="font-family:{FONT_MONO};font-size:11.5px;color:#334155;">{esc(_number(sh, "%"))}</span>',
            ])
        rest_n = int(info.get("rest_count") or 0)
        if rest_n > 0:
            rows.append([
                '<span style="color:#94a3b8;">&middot;</span>',
                f'<span style="color:#64748b;">외 {rest_n}개 블록 합계</span>',
                '<span style="color:#94a3b8;">—</span>',
                delta_html(_signed(_dec_val(info.get("rest_delta")), unit), raw_value=_dec_val(info.get("rest_delta")), bold=False),
                '<span style="color:#94a3b8;">—</span>',
                f'<span style="font-family:{FONT_MONO};font-size:11.5px;color:#64748b;">{esc(_number(_dec_val(info.get("rest_share_pct")), "%"))}</span>',
            ])
        blocks.append(
            f'<div style="margin:10px 0 4px 0;font-family:{FONT_SANS};font-size:12.5px;font-weight:700;color:#1e293b;">'
            f'{esc(title)} &middot; {sub_badge}</div>'
            + data_table(
                ["#", "1단계 Sub-block", "기준 → 현재", "증가량 (Δ)", "Δ%", "전체 증가 지분"],
                rows,
                aligns=["center", "left", "right", "right", "right", "right"],
            )
        )
    if not blocks:
        return ""
    return section_heading("1단계 주요 기여 Sub-block", subtitle="전체 증가량 중 1단계 하위 블록이 차지하는 지분입니다.") + "".join(blocks)


def _dual_ranking_html(candidates: list[dict[str, Any]], part: str, title: str, unit: str) -> str:
    metric_key = "power_mw" if part == "power" else "area"
    valid = []
    for item in candidates:
        card = item.get("card") or {}
        m = card.get(metric_key) or {}
        if "excluded" in m:
            continue
        delta = _dec_val(m.get("delta"))
        damped = _dec_val(m.get("damped_pct"))
        raw_pct = _dec_val(m.get("delta_pct"))
        eff = damped if damped is not None else raw_pct
        if delta is not None and delta > 0:
            valid.append((delta, eff if eff is not None else delta, raw_pct, damped, item))

    head = section_heading(f"{title} 주요 증가 랭킹", badge_html=pill("이상 · 확인 필요 기준", "neutral"))
    if not valid:
        return head + callout(f"{title} 특이 증가 항목 없음", ["이상·확인 필요 항목 중 양(+)의 증가가 감지된 대상이 없습니다."], tone="neutral")

    abs_sorted = sorted(valid, key=lambda r: (-r[0], -r[1], (r[4]["card"].get("current_path") or "")))[:10]
    abs_ids = {r[4]["group_id"] for r in abs_sorted}
    spike_sorted = sorted(
        [r for r in valid if r[4]["group_id"] not in abs_ids],
        key=lambda r: (-r[1], -r[0], (r[4]["card"].get("current_path") or "")),
    )[:10]

    def build_rows(rows):
        out = []
        for idx, (delta, _eff, raw_pct, damped, item) in enumerate(rows, start=1):
            card = item["card"]
            path = card.get("current_path") or card.get("baseline_path") or card["id"]
            d1 = card.get("depth1_block")
            d1_html = f'{pill(str(d1), "neutral", mono=True)}&nbsp;' if d1 else ""
            eff_html = delta_html(_signed(damped, "%"), raw_value=damped) if damped is not None else '<span style="color:#94a3b8;">—</span>'
            out.append([
                f'<span style="font-family:{FONT_MONO};color:#64748b;">{idx}</span>',
                verdict_pill(item.get("verdict")),
                f'{d1_html}<span style="font-family:{FONT_MONO};font-weight:600;color:#0f172a;word-break:break-all;">{esc(path)}</span>',
                delta_html(_signed(delta, unit), raw_value=delta),
                delta_html(_signed(raw_pct, "%"), raw_value=raw_pct, bold=False),
                eff_html,
            ])
        return out

    html_parts = [
        head,
        f'<div style="margin:6px 0 4px 0;font-family:{FONT_SANS};font-size:12px;font-weight:700;color:#334155;">절대 증가량 Top {len(abs_sorted)}</div>',
        data_table(
            ["#", "판정", "Instance", "증가량 (Δ)", "단순 Δ%", "Effective Δ%"],
            build_rows(abs_sorted),
            aligns=["center", "center", "left", "right", "right", "right"],
        ),
    ]
    if spike_sorted:
        html_parts.append(
            f'<div style="margin:10px 0 4px 0;font-family:{FONT_SANS};font-size:12px;font-weight:700;color:#334155;">Effective Δ% Top {len(spike_sorted)} (절대 Top 중복 제외)</div>'
        )
        html_parts.append(
            data_table(
                ["#", "판정", "Instance", "증가량 (Δ)", "단순 Δ%", "Effective Δ%"],
                build_rows(spike_sorted),
                aligns=["center", "center", "left", "right", "right", "right"],
            )
        )
    return "".join(html_parts)


def _item_card_html(
    item: dict[str, Any],
    *,
    causes: dict[str, str],
    flags_map: dict[str, str],
    share_label_fn,
    distributed_line_fn,
) -> str:
    card = item["card"]
    path = card.get("current_path") or card.get("baseline_path") or card["id"]
    renamed = (
        f'<div style="margin-top:2px;font-family:{FONT_MONO};font-size:11px;color:#64748b;">기준 경로: {esc(card["baseline_path"])}</div>'
        if card.get("baseline_path") and card.get("current_path") and card["baseline_path"] != card["current_path"]
        else ""
    )
    d1_badge = f'&nbsp;{pill(str(card["depth1_block"]), "neutral", mono=True)}' if card.get("depth1_block") else ""
    watched_badge = f'&nbsp;{pill("주요", "info")}' if card.get("watched") else ""

    area = card.get("area") or {}
    power = card.get("power_mw") or {}
    metric_cells = []
    a_damped = _dec_val(area.get("damped_pct"))
    area_eff = f' &middot; Eff. {delta_html(_signed(a_damped, "%"), raw_value=a_damped)}' if a_damped is not None else ""
    metric_cells.append(
        f'<td valign="top" style="padding:7px 10px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;">'
        f'<div style="font-family:{FONT_SANS};font-size:11px;font-weight:700;color:#475569;">Logic Area</div>'
        f'<div style="margin-top:2px;font-family:{FONT_MONO};font-size:12px;color:#0f172a;">'
        f'{esc(_number(_dec_val(area.get("baseline"))))} &rarr; <strong>{esc(_number(_dec_val(area.get("current"))))}</strong></div>'
        f'<div style="margin-top:2px;font-size:11.5px;">'
        f'{delta_html(_signed(_dec_val(area.get("delta"))), raw_value=_dec_val(area.get("delta")))}'
        f' &middot; {delta_html(_signed(_dec_val(area.get("delta_pct")), "%"), raw_value=_dec_val(area.get("delta_pct")), bold=False)}'
        f'{area_eff}</div></td>'
    )
    if "excluded" not in power:
        p_damped = _dec_val(power.get("damped_pct"))
        pwr_eff = f' &middot; Eff. {delta_html(_signed(p_damped, "%"), raw_value=p_damped)}' if p_damped is not None else ""
        metric_cells.append('<td width="8">&nbsp;</td>')
        metric_cells.append(
            f'<td valign="top" style="padding:7px 10px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;">'
            f'<div style="font-family:{FONT_SANS};font-size:11px;font-weight:700;color:#475569;">Total Power</div>'
            f'<div style="margin-top:2px;font-family:{FONT_MONO};font-size:12px;color:#0f172a;">'
            f'{esc(_number(_dec_val(power.get("baseline")), " mW"))} &rarr; <strong>{esc(_number(_dec_val(power.get("current")), " mW"))}</strong></div>'
            f'<div style="margin-top:2px;font-size:11.5px;">'
            f'{delta_html(_signed(_dec_val(power.get("delta")), " mW"), raw_value=_dec_val(power.get("delta")))}'
            f' &middot; {delta_html(_signed(_dec_val(power.get("delta_pct")), "%"), raw_value=_dec_val(power.get("delta_pct")), bold=False)}'
            f'{pwr_eff}</div></td>'
        )

    metrics_grid = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin-top:8px;border-collapse:separate;border-spacing:0;">'
        f'<tr>{"".join(metric_cells)}</tr></table>'
    )

    cause_label = causes.get(item.get("cause"), item.get("cause") or "원인 불명")
    explanation = " ".join(str(item.get("explanation") or "").split())
    reason_html = (
        f'<div style="margin-top:8px;font-family:{FONT_SANS};font-size:12.5px;color:#1e293b;line-height:1.55;">'
        f'{pill(f"원인: {cause_label}", "neutral")}&nbsp;&nbsp;{esc(explanation)}</div>'
    )

    extra_lines: list[str] = []
    for part, unit in (("power", " mW"), ("area", "")):
        dist = distributed_line_fn(card, part, unit)
        if dist:
            extra_lines.append(
                f'<div style="margin-top:5px;font-family:{FONT_SANS};font-size:11.5px;color:#475569;">{esc(dist)}</div>'
            )

    drive_title = "Drive (세부)" if card.get("drive_groups") else "Drive"
    for title, key in (("Vth", "vth"), ("Drive (그룹)", "drive_groups"), (drive_title, "drive")):
        chips = []
        for share in card.get(key, [])[:6]:
            lbl = share_label_fn(share["name"], key)
            chips.append(
                f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 6px;border-radius:4px;'
                f'background-color:#f1f5f9;border:1px solid #e2e8f0;font-family:{FONT_MONO};font-size:11px;color:#334155;">'
                f'{esc(lbl)} {esc(_number(share["baseline_pct"], "%"))}&rarr;{esc(_number(share["current_pct"], "%"))} '
                f'({delta_html(_signed(share["delta_pp"], "%p"), raw_value=share["delta_pp"])})</span>'
            )
        if chips:
            extra_lines.append(
                f'<div style="margin-top:6px;font-family:{FONT_SANS};font-size:11.5px;color:#475569;">'
                f'<strong>{esc(title)}:</strong> {"".join(chips)}</div>'
            )

    if item.get("quotes"):
        quote_items = " / ".join(f'"{esc(q)}"' for q in item["quotes"][:3])
        extra_lines.append(
            f'<div style="margin-top:6px;padding:6px 10px;background-color:#f8fafc;border-left:3px solid #94a3b8;'
            f'font-family:{FONT_SANS};font-size:11.5px;color:#475569;line-height:1.5;">'
            f'<strong>근거 메모:</strong> {quote_items}</div>'
        )

    flag_list = [flags_map.get(flag, flag) for flag in card.get("flags", [])]
    if item.get("failed"):
        flag_list.append(f"AI 판정 실패({item.get('failure')})")
    if card.get("rolled_up_ancestors"):
        flag_list.append("영향받은 상위 " + ", ".join(card["rolled_up_ancestors"][:4]))
    if flag_list:
        flag_pills = " ".join(pill(f, "warning") for f in flag_list)
        extra_lines.append(f'<div style="margin-top:7px;">{flag_pills}</div>')

    border_color = {"unexpected": "#fecaca", "needs_review": "#fde68a", "expected": "#bbf7d0"}.get(str(item.get("verdict")), "#e2e8f0")
    accent_color = {"unexpected": "#dc2626", "needs_review": "#d97706", "expected": "#16a34a"}.get(str(item.get("verdict")), "#64748b")
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 10px 0;border-collapse:separate;border-spacing:0;background-color:#ffffff;'
        f'border:1px solid {border_color};border-left:4px solid {accent_color};border-radius:6px;">'
        f'<tr><td style="padding:11px 14px;">'
        f'<div style="line-height:1.5;">{verdict_pill(item.get("verdict"))}{d1_badge}{watched_badge}&nbsp;&nbsp;'
        f'<span style="font-family:{FONT_MONO};font-size:13px;font-weight:700;color:#0f172a;word-break:break-all;">{esc(path)}</span></div>'
        f'{renamed}{metrics_grid}{reason_html}{"".join(extra_lines)}'
        f'</td></tr></table>'
    )


def render_triage_digest_html(
    *,
    request: dict[str, Any],
    run: dict[str, Any],
    comparison: dict[str, Any],
    items: list[dict[str, Any]],
    shown: list[dict[str, Any]],
    excluded: set[str],
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
    execution_fn,
    causes: dict[str, str],
    flags_map: dict[str, str],
    exclusions_map: dict[str, str],
    share_label_fn,
    distributed_line_fn,
    dashboard_url: str | None = None,
) -> str:
    """Render an email-client-safe HTML document for one AI triage digest mail."""
    payload = run["payload"]
    by = lambda verdict: [item for item in shown if item["verdict"] == verdict]
    unexpected, review, expected_items, minor_items = by("unexpected"), by("needs_review"), by("expected"), by("minor")
    watched = [item for item in shown if item["card"].get("watched") and item["verdict"] in ("expected", "minor")]
    labels = payload.get("labels") or {}
    thresholds = payload.get("thresholds") or {}
    context = payload.get("side_context") or {}
    kind = "같은 프로젝트의 이전 버전" if payload.get("comparison_kind") == "previous_version" else "기준 프로젝트"
    current_title = labels.get("current") or (current.get("execution_name") if current else "") or "비교 결과"

    header_badges = [
        pill(f"이상 {len(unexpected)}건", "dark_danger" if unexpected else "dark"),
        pill(f"확인 필요 {len(review)}건", "dark_warning" if review else "dark"),
        pill(f"{kind} 비교", "dark_info"),
    ]
    if payload.get("same_rtl"):
        header_badges.append(pill("RTL 동일 (같은 NET_VER)", "dark_success"))

    parts: list[str] = []
    parts.append(
        kpi_strip([
            {"label": "이상 (Unexpected)", "value": f"{len(unexpected)}건", "tone": "danger" if unexpected else "neutral"},
            {"label": "확인 필요 (Review)", "value": f"{len(review)}건", "tone": "warning" if review else "neutral"},
            {"label": "설명됨 (Expected)", "value": f"{len(expected_items)}건", "tone": "success" if expected_items else "neutral"},
            {"label": "변화 미미 (Minor)", "value": f"{len(minor_items)}건", "tone": "neutral"},
        ])
    )

    threshold_str = (
        f"Logic Area ≥ {_number(_dec_val(thresholds.get('area_increase_pct')), '%')} · "
        f"Power ≥ {_number(_dec_val(thresholds.get('power_increase_pct')), '%')} · "
        f"기본 노이즈 {_number(_dec_val(thresholds.get('noise_share_pct')) or _dec_val('0.1'), '%')} · "
        f"최소 Area {_number(_dec_val(thresholds.get('min_area')))}"
    )
    meta_rows = [
        ("비교 유형", esc(f"{kind} 비교")),
        ("현재 실행", f'<span style="font-family:{FONT_MONO};font-size:12px;">{esc(execution_fn(current, labels.get("current")))}</span>'),
        ("기준 실행", f'<span style="font-family:{FONT_MONO};font-size:12px;">{esc(execution_fn(baseline, labels.get("baseline")))}</span>'),
        ("판정 기준 수치", f'<span style="font-family:{FONT_MONO};font-size:12px;">{esc(threshold_str)}</span>'),
        ("적용 메모", esc(f"{len(payload.get('notes', []))}건" + (" · RTL 동일(같은 NET_VER)" if payload.get("same_rtl") else ""))),
    ]
    if excluded:
        excluded_count = len(excluded & {item["group_id"] for item in items})
        meta_rows.append(("제외 항목", esc(f"발송자가 목록에서 제외한 항목 {excluded_count}건")))
    parts.append(meta_table(meta_rows))

    warnings: list[str] = []
    for part, title in (("power", "Power"), ("area", "Area")):
        if context.get(part) and not context[part].get("analyzed"):
            reason = exclusions_map.get(context[part].get("reason"), context[part].get("reason"))
            warnings.append(f"<strong>분석 제외 ({esc(title)}):</strong> {esc(reason)}")
    if any(w.get("code") == "net_ver_unresolved" for w in payload.get("note_warnings", [])):
        warnings.append("공통 카탈로그와 연결되지 않은 버전이 계보에 있어 일부 NET_VER 메모를 적용하지 못했습니다.")
    failed = sum(bool(item.get("failed")) for item in items)
    if run["state"] != "completed" or failed:
        warnings.append(f"<strong>분석 미완료:</strong> AI 판정을 받지 못한 instance {failed}건은 '확인 필요'로 남았습니다.")
    if payload.get("missing_watched"):
        warnings.append("<strong>현재 실행에 없는 주요 instance:</strong> " + esc(", ".join(payload["missing_watched"][:20])))
    if warnings:
        parts.append(callout("분석 유의사항", warnings, tone="warning"))

    parts.append(_attribution_html(payload.get("attribution")))

    actionable = unexpected + review
    if context.get("power", {}).get("analyzed", True):
        parts.append(_dual_ranking_html(actionable, "power", "Power", " mW"))
    if context.get("area", {}).get("analyzed", True):
        parts.append(_dual_ranking_html(actionable, "area", "Logic Area", ""))

    footer_meta = meta_table([
        ("분석 실행 ID", f'<span style="font-family:{FONT_MONO};font-size:11.5px;color:#475569;">{esc(run["id"])}</span>'),
        ("비교 ID", f'<span style="font-family:{FONT_MONO};font-size:11.5px;color:#475569;">{esc(comparison["id"])}</span>'),
        ("요청자", esc(request["payload"].get("requested_by") or "—")),
    ])
    cta = cta_block(
        label="Design Atlas에서 Area/Power 비교 화면 열기 →",
        url=safe_dashboard_url(dashboard_url, "/?page=area-power"),
        fallback_path=None,
        note="비교 이력 또는 직접 비교 화면에서 전체 Hierarchy와 AI 판정 상세를 확인할 수 있습니다.",
    )
    preheader = f"AI 분석 · {current_title} — 이상 {len(unexpected)}건 · 확인 필요 {len(review)}건 · 설명됨 {len(expected_items)}건"
    footer_notes = ("이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다.",)

    base_overhead = utf8_len(
        render_email_shell(
            preheader=preheader,
            category="AI ANOMALY TRIAGE",
            title=f"AI 이상치 분석 · {current_title}",
            subtitle="수치는 코드가 계산했으며, AI는 등록된 메모와 대조해 판정만 수행했습니다.",
            header_badges=header_badges,
            body_html="".join(parts) + footer_meta + cta,
            footer_notes=footer_notes,
        )
    )
    cards_budget = max(0, MAIL_MAX_HTML_BODY_BYTES - base_overhead - 2_500)
    used_cards = 0
    omitted_total = 0

    for title, group, badge_tone in (
        ("이상 (Unexpected)", unexpected, "danger"),
        ("확인 필요 (Needs Review)", review, "warning"),
        ("주요 instance (설명됨 · 변화 미미)", watched, "info"),
    ):
        head_html = section_heading(title, badge_html=pill(f"{len(group)}건", badge_tone if group else "neutral"))
        head_bytes = utf8_len(head_html)
        if used_cards + head_bytes > cards_budget:
            omitted_total += len(group)
            continue
        parts.append(head_html)
        used_cards += head_bytes
        if group:
            for idx, item in enumerate(group):
                card_html = _item_card_html(
                    item,
                    causes=causes,
                    flags_map=flags_map,
                    share_label_fn=share_label_fn,
                    distributed_line_fn=distributed_line_fn,
                )
                card_bytes = utf8_len(card_html)
                if used_cards + card_bytes > cards_budget:
                    omitted_total += len(group) - idx
                    break
                parts.append(card_html)
                used_cards += card_bytes
        else:
            empty_html = callout(f"{title} 없음", ["해당 분류에 포함된 항목이 없습니다."], tone="neutral")
            empty_bytes = utf8_len(empty_html)
            if used_cards + empty_bytes <= cards_budget:
                parts.append(empty_html)
                used_cards += empty_bytes

    if omitted_total:
        parts.append(
            callout(
                f"메일 본문 크기 한도(1MB)로 나머지 {omitted_total}건의 상세 카드가 생략되었습니다.",
                ["상단 요약·1단계 기여·Top-10 랭킹은 유지되며, 전체 상세 목록은 Design Atlas에서 확인할 수 있습니다."],
                tone="info",
            )
        )

    parts.append(footer_meta)
    if cta:
        parts.append(cta)

    return render_email_shell(
        preheader=preheader,
        category="AI ANOMALY TRIAGE",
        title=f"AI 이상치 분석 · {current_title}",
        subtitle="수치는 코드가 계산했으며, AI는 등록된 메모와 대조해 판정만 수행했습니다.",
        header_badges=header_badges,
        body_html="".join(parts),
        footer_notes=footer_notes,
    )


def render_alert_mail_html(
    *,
    summary: str,
    alert: dict[str, Any],
    event: dict[str, Any] | None,
    comparison: dict[str, Any],
    metric: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
    execution_fn,
    dashboard_url: str | None = None,
) -> str:
    """Render an email-client-safe HTML document for one Area/Power alert notification."""
    payload = alert["payload"]
    metric_payload = metric["payload"] if metric else payload
    kind = str(payload.get("kind") or "unavailable")
    hier = str(payload.get("hier_name") or metric_payload.get("hier_name") or "unavailable")
    metric_name = str(payload.get("metric") or metric_payload.get("metric") or "unavailable")
    unit = str(metric_payload.get("unit") or "")
    unit_suffix = f" {unit}" if unit else ""
    reason = str(payload.get("reason") or metric_payload.get("reason") or "—")

    base_val = metric_payload.get("baseline")
    cur_val = metric_payload.get("current")
    delta_val = metric_payload.get("delta")

    parts: list[str] = []
    parts.append(
        kpi_strip([
            {"label": "기준 (Baseline)", "value": _number(_dec_val(base_val), unit_suffix) if base_val is not None else "—", "tone": "neutral"},
            {"label": "현재 (Current)", "value": _number(_dec_val(cur_val), unit_suffix) if cur_val is not None else "—", "tone": "info"},
            {"label": "변화량 (Delta)", "value": _signed(_dec_val(delta_val), unit_suffix) if delta_val is not None else "—", "tone": "danger" if (_dec_val(delta_val) or Decimal(0)) > 0 else "info"},
        ])
    )
    parts.append(
        meta_table([
            ("Alert 유형", pill(kind, "warning", mono=True)),
            ("대상 Hierarchy", f'<span style="font-family:{FONT_MONO};font-weight:700;color:#0f172a;word-break:break-all;">{esc(hier)}</span>'),
            ("지표 (Metric)", f'<span style="font-family:{FONT_MONO};font-weight:600;">{esc(metric_name)}</span>' + (f' <span style="color:#64748b;">({esc(unit)})</span>' if unit else "")),
            ("사유 (Reason)", esc(reason)),
            ("현재 실행", f'<span style="font-family:{FONT_MONO};font-size:12px;">{esc(execution_fn(current, None))}</span>'),
            ("기준 실행", f'<span style="font-family:{FONT_MONO};font-size:12px;">{esc(execution_fn(baseline, None))}</span>'),
            ("Alert / Comparison ID", f'<span style="font-family:{FONT_MONO};font-size:11.5px;color:#475569;">{esc(alert["id"])} &middot; {esc(comparison["id"])}</span>'),
        ])
    )

    evidence_blocks = [
        ("Metric Evidence", dumps(public(metric_payload), canonical=True)),
        ("Comparison Fixed Inputs", dumps(public(comparison["payload"]), canonical=True)),
    ]
    if event is not None:
        evidence_blocks.append(("Alert Event", dumps(public(event), canonical=True)))
    cta = cta_block(
        label="Design Atlas에서 Area/Power 화면 열기 →",
        url=safe_dashboard_url(dashboard_url, "/?page=area-power"),
        fallback_path=None,
    )
    preheader = f"{summary} ({hier} · {metric_name})"
    footer_notes = (
        "This notification records a tracking observation, not a measurement of recipient acknowledgement.",
        "이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다.",
    )
    base_overhead = utf8_len(
        render_email_shell(
            preheader=preheader,
            category="AREA / POWER ALERT",
            title=summary,
            subtitle=f"{hier} · {metric_name}",
            header_badges=(pill(kind, "dark_warning", mono=True),),
            body_html="".join(parts) + cta,
            footer_notes=footer_notes,
        )
    )
    json_budget = max(4_000, MAIL_MAX_HTML_BODY_BYTES - base_overhead - 2_000)
    per_block_budget = max(1_000, json_budget // max(1, len(evidence_blocks)) - 512)
    parts.append(section_heading("고정 입력 및 상세 근거 (JSON)"))
    for label, raw_json in evidence_blocks:
        # Leave margin for HTML entity expansion (&quot; etc.) inside <pre>.
        safe_raw = truncate_utf8_bytes(
            raw_json,
            per_block_budget // 2,
            suffix=" ... (메일 본문 1MB 한도로 일부 생략)",
        )
        parts.append(
            f'<div style="margin:8px 0 4px 0;font-family:{FONT_SANS};font-size:11.5px;font-weight:700;color:#475569;">{esc(label)}</div>'
            f'<pre style="margin:0 0 10px 0;padding:10px 12px;background-color:#f8fafc;border:1px solid #e2e8f0;'
            f'border-radius:6px;font-family:{FONT_MONO};font-size:11px;color:#334155;line-height:1.5;'
            f'white-space:pre-wrap;word-break:break-all;overflow-wrap:anywhere;">{esc(safe_raw)}</pre>'
        )

    if cta:
        parts.append(cta)

    return render_email_shell(
        preheader=preheader,
        category="AREA / POWER ALERT",
        title=summary,
        subtitle=f"{hier} · {metric_name}",
        header_badges=(pill(kind, "dark_warning", mono=True),),
        body_html="".join(parts),
        footer_notes=footer_notes,
    )
