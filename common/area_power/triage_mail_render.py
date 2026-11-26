"""Pure plain-text rendering for Area/Power alert notifications and AI triage digest mails."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .jsonutil import dumps, public
from .mail import MailMessage
from .tracking.mail_render import _fmt_num as _number, _fmt_signed as _signed
from .tracking.models import upload_corner_label

CAUSES = {
    "note": "메모",
    "vth_shift": "Vth 변화",
    "drive_shift": "Drive 변화",
    "new_instance": "신규 instance",
    "unknown": "원인 불명",
}
FLAGS = {
    "area_conditions_unconfirmed": "Area 조건 미확인",
    "power_conditions_unconfirmed": "Power 조건 미확인",
    "library_unknown": "library 미확인",
    "library_different": "library 다름(Vth/Drive는 참고용)",
    "removed_in_current": "현재 실행에 없음",
}
EXCLUSIONS = {
    "corner_different": "양쪽 corner가 다름",
    "analysis_conditions_different": "분석 조건이 다름",
    "area_unit_different": "area 단위가 다름",
    "baseline_missing": "기준 실행 없음",
}
DRIVE_SHARE_LABELS = {
    "drv_sub1x": "sub-1x (<D1)",
    "drv_d1_d3": "D1-D3",
    "drv_d4_plus": "D4+",
    "drv_other": "other",
}


def _share_label(name: str, key: str) -> str:
    if key in ("drive_groups", "drive"):
        return DRIVE_SHARE_LABELS.get(name, name[4:] if name.startswith("drv_") else name)
    return name


def _execution(upload, label) -> str:
    if upload is None:
        return "없음"
    corner = upload_corner_label(upload)
    if corner == "—":
        corner = " / ".join(
            str(upload.get(key) if upload.get(key) is not None else "?")
            for key in ("process_label", "voltage_v", "temp_c", "pex")
        )
    return f"{label + ' · ' if label else ''}{upload.get('execution_name') or upload['id']} ({corner})"


def _dec_val(value):
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None


def _distributed_line(card, part: str, unit: str) -> str | None:
    if part not in (card.get("distributed_metrics") or []):
        return None
    bucket = (card.get("top_children_by_metric") or {}).get(part) or {}
    rows = bucket.get("items") or []
    total_pos = int(bucket.get("positive_count") or len(rows))
    if not rows:
        return None
    top_str = ", ".join(f"{r['path']} ({_signed(_dec_val(r.get('delta')), unit)})" for r in rows[:3])
    rest = max(0, total_pos - len(rows[:3]))
    return f"하위 분산({part.upper()}): {top_str}" + (f" 외 {rest}개" if rest else "")


def _attribution_lines(attribution: dict | None) -> list[str]:
    if not attribution:
        return []
    out = []
    for part, title, unit in (("power", "Total Power", " mW"), ("area", "Logic Area", "")):
        info = attribution.get(part) or {}
        if not info.get("analyzed") or not info.get("items"):
            continue
        r_delta = _dec_val(info.get("root_delta"))
        r_pct = _dec_val(info.get("root_delta_pct"))
        out.append(f"  [{title}] 전체 증가 {_signed(r_delta, unit)} ({_signed(r_pct, '%')}) 중 1단계 Sub-block 기여:")
        for idx, row in enumerate(info["items"], start=1):
            d = _dec_val(row.get("delta"))
            sh = _dec_val(row.get("share_of_increase_pct"))
            dp = _dec_val(row.get("delta_pct"))
            out.append(
                f"    {idx}. {row.get('path')} : {_signed(d, unit)} (전체 증가의 {_number(sh, '%')}) · "
                f"{_number(_dec_val(row.get('baseline')), unit)} → {_number(_dec_val(row.get('current')), unit)} ({_signed(dp, '%')})"
            )
        rest_n = int(info.get("rest_count") or 0)
        if rest_n > 0:
            out.append(
                f"    외 {rest_n}개 블록 합계: {_signed(_dec_val(info.get('rest_delta')), unit)} ({_number(_dec_val(info.get('rest_share_pct')), '%')})"
            )
    return ["", "■ 1단계 주요 기여 Sub-block (전체 증가 지분)", *out] if out else []


def _dual_ranking_lines(candidates: list[dict], part: str, title: str, unit: str) -> list[str]:
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
    if not valid:
        return ["", f"■ {title} 주요 증가 랭킹 (이상 · 확인 필요 기준)", "  특이 증가 항목 없음"]
    abs_sorted = sorted(valid, key=lambda r: (-r[0], -r[1], (r[4]["card"].get("current_path") or "")))[:10]
    abs_ids = {r[4]["group_id"] for r in abs_sorted}
    spike_sorted = sorted(
        [r for r in valid if r[4]["group_id"] not in abs_ids],
        key=lambda r: (-r[1], -r[0], (r[4]["card"].get("current_path") or "")),
    )[:10]

    def fmt_row(idx, row):
        delta, _eff, raw_pct, damped, item = row
        card = item["card"]
        path = card.get("current_path") or card.get("baseline_path") or card["id"]
        d1 = card.get("depth1_block")
        prefix = f"[{d1}] " if d1 else ""
        rate_txt = f"단순 {_signed(raw_pct, '%')}" + (f" · Effective Δ% {_signed(damped, '%')}" if damped is not None else "")
        verdict_ko = {"unexpected": "이상", "needs_review": "확인 필요"}.get(item.get("verdict"), item.get("verdict"))
        return f"    {idx}. [{verdict_ko}] {prefix}{path} : {_signed(delta, unit)} ({rate_txt})"

    lines = ["", f"■ {title} 주요 증가 랭킹 (이상 · 확인 필요 기준)"]
    lines.append(f"  [절대 증가량 Top {len(abs_sorted)}]")
    lines.extend(fmt_row(i, r) for i, r in enumerate(abs_sorted, start=1))
    lines.append(f"  [Effective Δ% Top {len(spike_sorted)} — 중복 제외]")
    if spike_sorted:
        lines.extend(fmt_row(i, r) for i, r in enumerate(spike_sorted, start=1))
    else:
        lines.append("    추가 국소 급증 항목 없음")
    return lines


def _item_lines(item) -> list[str]:
    card = item["card"]
    path = card.get("current_path") or card.get("baseline_path") or card["id"]
    if card.get("baseline_path") and card.get("current_path") and card["baseline_path"] != card["current_path"]:
        path += f"  (기준: {card['baseline_path']})"
    if card.get("depth1_block"):
        path += f"  [소속 블록: {card['depth1_block']}]"
    area, power = card["area"], card["power_mw"]
    a_damped = _dec_val(area.get("damped_pct"))
    a_extra = f", Δ {_signed(_dec_val(area.get('delta')), '')}" + (f", Effective Δ% {_signed(a_damped, '%')}" if a_damped is not None else "")
    values = [f"Area {_number(_dec_val(area.get('baseline')))} → {_number(_dec_val(area.get('current')))} ({_signed(_dec_val(area.get('delta_pct')), '%')}{a_extra})"]
    if "excluded" not in power:
        p_damped = _dec_val(power.get("damped_pct"))
        p_extra = f", Δ {_signed(_dec_val(power.get('delta')), ' mW')}" + (f", Effective Δ% {_signed(p_damped, '%')}" if p_damped is not None else "")
        values.append(f"Power {_number(_dec_val(power.get('baseline')))} → {_number(_dec_val(power.get('current')))} mW ({_signed(_dec_val(power.get('delta_pct')), '%')}{p_extra})")
    lines = [
        f"- {path}",
        "    " + " · ".join(values),
        f"    원인: {CAUSES.get(item['cause'], item['cause'])} · {' '.join(str(item['explanation']).split())}",
    ]
    for part, unit in (("power", " mW"), ("area", "")):
        dist = _distributed_line(card, part, unit)
        if dist:
            lines.append(f"    {dist}")
    drive_title = "Drive (세부)" if card.get("drive_groups") else "Drive"
    for title, key in (("Vth", "vth"), ("Drive (그룹)", "drive_groups"), (drive_title, "drive")):
        shares = [
            f"{_share_label(share['name'], key)} {_number(share['baseline_pct'], '%')} → {_number(share['current_pct'], '%')} ({_signed(share['delta_pp'], '%p')})"
            for share in card.get(key, [])[:6]
        ]
        if shares:
            lines.append(f"    {title}: " + ", ".join(shares))
    if item.get("quotes"):
        lines.append("    근거 메모: " + " / ".join(f'"{quote}"' for quote in item["quotes"][:3]))
    flags = [FLAGS.get(flag, flag) for flag in card.get("flags", [])]
    if item.get("failed"):
        flags.append(f"AI 판정 실패({item.get('failure')})")
    if card.get("rolled_up_ancestors"):
        flags.append("영향받은 상위 " + ", ".join(card["rolled_up_ancestors"][:4]))
    if flags:
        lines.append("    표시: " + " · ".join(flags))
    return lines


def render_alert_mail_message(recipients: list[str], job, alert, event, comparison, metric, baseline, current, *, attempt_id: str) -> MailMessage:
    payload = alert["payload"]
    metric_payload = metric["payload"] if metric else payload
    summary = str(payload.get("summary") or payload.get("kind") or "Design Atlas observation")
    summary = " ".join(summary.splitlines())[:180]
    subject = f"[Design Atlas] {summary}"
    body = [
        summary,
        f"Alert: {alert['id']}",
        f"Comparison: {comparison['id']}",
        f"Kind: {payload.get('kind', 'unavailable')}",
        f"Hierarchy: {payload.get('hier_name', metric_payload.get('hier_name', 'unavailable'))}",
        f"Metric: {payload.get('metric', metric_payload.get('metric', 'unavailable'))}",
        f"Baseline: {metric_payload.get('baseline')}",
        f"Current: {metric_payload.get('current')}",
        f"Delta: {metric_payload.get('delta')}",
        f"Unit: {metric_payload.get('unit')}",
        f"Reason: {payload.get('reason', metric_payload.get('reason'))}",
        "",
        "Baseline execution/context:", dumps(public(baseline), canonical=True),
        "Current execution/context:", dumps(public(current), canonical=True),
        "Comparison scope and fixed inputs:", dumps(public(comparison["payload"]), canonical=True),
        "Metric evidence:", dumps(public(metric_payload), canonical=True),
        "Alert event:", dumps(public(event), canonical=True),
        "",
        "This notification records a tracking observation, not a measurement of recipient acknowledgement.",
    ]
    return MailMessage(
        job_id=str(job["id"]), attempt_id=attempt_id, recipients=tuple(recipients),
        subject=subject, text_body="\n".join(body),
    )


def render_triage_digest_message(recipients: list[str], job, request, run, comparison, judgements, baseline, current, *, attempt_id: str) -> MailMessage:
    payload = run["payload"]
    excluded = set(request["payload"].get("excluded_ids") or [])
    items = [row["payload"] for row in judgements]
    shown = [item for item in items if item["group_id"] not in excluded]
    shown.sort(key=lambda item: (item["card"].get("current_path") or item["card"].get("baseline_path") or ""))
    by = lambda verdict: [item for item in shown if item["verdict"] == verdict]
    unexpected, review = by("unexpected"), by("needs_review")
    watched = [item for item in shown if item["card"].get("watched") and item["verdict"] in ("expected", "minor")]
    labels = payload.get("labels") or {}
    thresholds = payload.get("thresholds") or {}
    context = payload.get("side_context") or {}
    kind = "같은 프로젝트의 이전 버전" if payload.get("comparison_kind") == "previous_version" else "기준 프로젝트"
    subject = " ".join(
        f"[Design Atlas] AI 분석 · {labels.get('current') or (current.get('execution_name') if current else '')} · 이상 {len(unexpected)}건 · 확인 필요 {len(review)}건".split()
    )[:200]
    body = [
        "Design Atlas AI 이상치 분석 결과입니다. 수치는 코드가 계산했고, AI는 메모와 대조해 판정만 했습니다.",
        "",
        f"비교 대상: {kind} 비교",
        f"  현재: {_execution(current, labels.get('current'))}",
        f"  기준: {_execution(baseline, labels.get('baseline'))}",
        f"기준 숫자: Logic Area 증가 {_number(_dec_val(thresholds.get('area_increase_pct')), '%')} 이상 · Power 증가 {_number(_dec_val(thresholds.get('power_increase_pct')), '%')} 이상 · 기본 노이즈 비율 {_number(_dec_val(thresholds.get('noise_share_pct')) or _dec_val('0.1'), '%')} · Logic Area 최소 {_number(_dec_val(thresholds.get('min_area')))}",
        f"적용 메모: {len(payload.get('notes', []))}건" + (" · RTL 동일(같은 NET_VER)" if payload.get("same_rtl") else ""),
        f"결과: 이상 {len(by('unexpected'))} · 확인 필요 {len(by('needs_review'))} · 설명됨 {len(by('expected'))} · 변화 미미 {len(by('minor'))}",
    ]
    for part, title in (("power", "Power"), ("area", "Area")):
        if context.get(part) and not context[part].get("analyzed"):
            body.append(f"분석 제외: {title} — {EXCLUSIONS.get(context[part].get('reason'), context[part].get('reason'))}")
    if any(warning.get("code") == "net_ver_unresolved" for warning in payload.get("note_warnings", [])):
        body.append("주의: 공통 카탈로그와 연결되지 않은 버전이 계보에 있어 일부 NET_VER 메모를 적용하지 못했습니다.")
    failed = sum(bool(item.get("failed")) for item in items)
    if run["state"] != "completed" or failed:
        body.append(f"주의: 이 분석은 완료되지 않았습니다. AI 판정을 받지 못한 instance {failed}건은 확인 필요로 남았습니다.")
    if payload.get("missing_watched"):
        body.append("현재 실행에 없는 주요 instance: " + ", ".join(payload["missing_watched"][:20]))
    if excluded:
        body.append(f"보낸 사람이 목록에서 뺀 항목: {len(excluded & {item['group_id'] for item in items})}건")
    body += _attribution_lines(payload.get("attribution"))
    actionable = unexpected + review
    if context.get("power", {}).get("analyzed", True):
        body += _dual_ranking_lines(actionable, "power", "Power", " mW")
    if context.get("area", {}).get("analyzed", True):
        body += _dual_ranking_lines(actionable, "area", "Logic Area", "")
    for title, group in (("이상", unexpected), ("확인 필요", review), ("주요 instance (설명됨 · 변화 미미)", watched)):
        body += ["", f"■ {title} ({len(group)}건)"]
        for item in group:
            body += _item_lines(item)
        if not group:
            body.append("  없음")
    body += [
        "",
        f"분석 실행: {run['id']}",
        f"비교: {comparison['id']}",
        f"요청: {request['payload'].get('requested_by')}",
        "",
        "이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다.",
    ]
    return MailMessage(
        job_id=str(job["id"]), attempt_id=attempt_id, recipients=tuple(recipients),
        subject=subject, text_body="\n".join(body),
    )
