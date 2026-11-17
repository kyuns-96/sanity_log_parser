"""Consume OA mail jobs through an explicitly injected company MailSender.

Service acceptance is not delivery and cannot be atomic with a DB commit.
Each attempt is recorded before sending; ambiguous attempts are never retried here.
"""
from __future__ import annotations

import argparse
import logging
import socket
import time
from datetime import datetime, timezone
from collections.abc import Callable, Sequence
from uuid import UUID, uuid4

from area_power.common.config import Settings
from area_power.common.db import Database
from area_power.common.errors import DomainError
from area_power.common.jsonutil import dumps, public
from area_power.common.mail import MailMessage, MailNotConfigured, MailReceipt, MailRejected, MailSender
from .service import addresses, reject

logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


def owned(db, conn, job_id, owner):
    job = db.record(conn, job_id, kind="job", lock=True)
    if job["consumer_server"] != "oa_server" or job["job_kind"] != "mail" or job["state"] != "running" or job["claim_owner"] != owner:
        reject("Mail job claim is no longer owned.", "claim_lost", 409)
    return job


def record_emission(db, job_id, owner, emission):
    with db.connect() as conn:
        job = owned(db, conn, job_id, owner)
        payload = dict(job["payload"])
        history = list(payload.get("emissions", []))
        # One attempt per job. Subsequent writes replace only that attempt's status.
        for index, old in enumerate(history):
            if old.get("attempt_id") == emission["attempt_id"]:
                history[index] = emission
                break
        else:
            history.append(emission)
        payload["emissions"] = history
        db.update_record(conn, job_id, owner, expected_row_version=job["row_version"], payload=payload)


def mail_context(db, job, owner):
    with db.connect() as conn:
        current_job = owned(db, conn, job["id"], owner)
        parent = db.record(conn, current_job["parent_id"])
        event = None
        if parent["record_type"] == "alert_event":
            event = parent
            alert = db.record(conn, parent["parent_id"], kind="alert")
        elif parent["record_type"] == "alert":
            alert = parent
        else:
            reject("Mail job parent must be an alert or alert event.", "invalid_mail_parent")
        requested = current_job["payload"].get("alert_id")
        if requested and UUID(str(requested)) != alert["id"]:
            reject("Mail alert reference does not match its parent.", "invalid_mail_parent")
        comparison = db.record(conn, alert["parent_id"], kind="comparison")
        metric = None
        if alert["payload"].get("metric_result_id"):
            metric = db.record(conn, UUID(str(alert["payload"]["metric_result_id"])), kind="metric_result")
            if metric.get("parent_id") != comparison["id"]:
                reject("Alert metric does not belong to its comparison.", "invalid_mail_context")
        baseline = db.upload(conn, alert["baseline_upload_id"]) if alert.get("baseline_upload_id") else None
        current = db.upload(conn, alert["upload_id"]) if alert.get("upload_id") else None
        return current_job, alert, event, comparison, metric, baseline, current


def message_for(settings, job, alert, event, comparison, metric, baseline, current, *, attempt_id):
    job_recipients = job["payload"].get("recipients") or []
    alert_recipients = alert["payload"].get("recipients") or []
    recipients = addresses([*job_recipients, *alert_recipients])
    if not recipients:
        recipients = addresses(settings.mail_recipients)
    if not recipients:
        reject("No mail recipients are configured or requested.", "mail_recipients_missing")
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
        "Comparison scope and fixed inputs:", dumps(public(comparison['payload']), canonical=True),
        "Metric evidence:", dumps(public(metric_payload), canonical=True),
        "Alert event:", dumps(public(event), canonical=True),
        "",
        "This notification records a tracking observation, not a measurement of recipient acknowledgement.",
    ]
    return MailMessage(
        job_id=str(job["id"]), attempt_id=attempt_id, recipients=tuple(recipients),
        subject=subject, text_body="\n".join(body),
    )


CAUSES = {"note": "메모", "vth_shift": "Vth 변화", "drive_shift": "Drive 변화", "new_instance": "신규 instance", "unknown": "원인 불명"}
FLAGS = {"area_conditions_unconfirmed": "Area 조건 미확인", "power_conditions_unconfirmed": "Power 조건 미확인",
         "library_unknown": "library 미확인", "library_different": "library 다름(Vth/Drive는 참고용)",
         "removed_in_current": "현재 실행에 없음"}
EXCLUSIONS = {"corner_different": "양쪽 corner가 다름", "analysis_conditions_different": "분석 조건이 다름",
              "area_unit_different": "area 단위가 다름", "baseline_missing": "기준 실행 없음"}


def digest_context(db, job, owner):
    with db.connect() as conn:
        current_job = owned(db, conn, job["id"], owner)
        request = db.record(conn, current_job["parent_id"], kind="triage_mail")
        run = db.record(conn, request["parent_id"], kind="ai_run")
        comparison = db.record(conn, run["parent_id"], kind="comparison")
        judgements, offset = [], 0
        while True:
            page = db.records(conn, "judgement", parent_id=run["id"], limit=500, offset=offset)
            judgements.extend(page)
            if len(page) < 500:
                break
            offset += len(page)
        baseline = db.upload(conn, comparison["baseline_upload_id"]) if comparison.get("baseline_upload_id") else None
        current = db.upload(conn, comparison["upload_id"])
        return current_job, request, run, comparison, judgements, baseline, current


def _number(value, suffix=""):
    if value is None:
        return "—"
    text = format(value, "f") if hasattr(value, "is_finite") else str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text + suffix


def _signed(value, suffix):
    return "—" if value is None else ("+" if value > 0 else "") + _number(value, suffix)


def _execution(upload, label):
    if upload is None:
        return "없음"
    corner = " / ".join(str(upload.get(key) if upload.get(key) is not None else "?") for key in ("process_label", "voltage_v", "temp_c", "pex"))
    return f"{label + ' · ' if label else ''}{upload.get('execution_name') or upload['id']} ({corner})"


def _item_lines(item):
    card = item["card"]
    path = card.get("current_path") or card.get("baseline_path") or card["id"]
    if card.get("baseline_path") and card.get("current_path") and card["baseline_path"] != card["current_path"]:
        path += f"  (기준: {card['baseline_path']})"
    area, power = card["area"], card["power_mw"]
    values = [f"Area {_number(area.get('baseline'))} → {_number(area.get('current'))} ({_signed(area.get('delta_pct'), '%')})"]
    if "excluded" not in power:
        values.append(f"Power {_number(power.get('baseline'))} → {_number(power.get('current'))} mW ({_signed(power.get('delta_pct'), '%')})")
    lines = [f"- {path}", "    " + " · ".join(values),
             f"    원인: {CAUSES.get(item['cause'], item['cause'])} · {' '.join(str(item['explanation']).split())}"]
    for title, key in (("Vth", "vth"), ("Drive", "drive")):
        shares = [f"{share['name']} {_number(share['baseline_pct'], '%')} → {_number(share['current_pct'], '%')} ({_signed(share['delta_pp'], '%p')})" for share in card.get(key, [])[:6]]
        if shares:
            lines.append(f"    {title}: " + ", ".join(shares))
    if item.get("quotes"):
        lines.append("    근거 메모: " + " / ".join(f"\"{quote}\"" for quote in item["quotes"][:3]))
    flags = [FLAGS.get(flag, flag) for flag in card.get("flags", [])]
    if item.get("failed"):
        flags.append(f"AI 판정 실패({item.get('failure')})")
    if card.get("rolled_up_ancestors"):
        flags.append("영향받은 상위 " + ", ".join(card["rolled_up_ancestors"][:4]))
    if flags:
        lines.append("    표시: " + " · ".join(flags))
    return lines


def digest_message(settings, job, request, run, comparison, judgements, baseline, current, *, attempt_id):
    """One summary mail for one reviewed analysis run."""
    recipients = addresses(request["payload"].get("recipients") or []) or addresses(settings.mail_recipients)
    if not recipients:
        reject("No mail recipients are configured or requested.", "mail_recipients_missing")
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
    subject = " ".join(f"[Design Atlas] AI 분석 · {labels.get('current') or (current.get('execution_name') if current else '')} · 이상 {len(unexpected)}건 · 확인 필요 {len(review)}건".split())[:200]
    body = [
        "Design Atlas AI 이상치 분석 결과입니다. 수치는 코드가 계산했고, AI는 메모와 대조해 판정만 했습니다.",
        "",
        f"비교 대상: {kind} 비교",
        f"  현재: {_execution(current, labels.get('current'))}",
        f"  기준: {_execution(baseline, labels.get('baseline'))}",
        f"기준 숫자: Area 증가 {_number(thresholds.get('area_increase_pct'), '%')} 이상 · Power 증가 {_number(thresholds.get('power_increase_pct'), '%')} 이상 · Area 최소 {_number(thresholds.get('min_area'))}",
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
    for title, group in (("이상", unexpected), ("확인 필요", review), ("주요 instance (설명됨 · 변화 미미)", watched)):
        body += ["", f"■ {title} ({len(group)}건)"]
        for item in group:
            body += _item_lines(item)
        if not group:
            body.append("  없음")
    body += ["", f"분석 실행: {run['id']}", f"비교: {comparison['id']}", f"요청: {request['payload'].get('requested_by')}",
             "", "이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다."]
    return MailMessage(job_id=str(job["id"]), attempt_id=attempt_id, recipients=tuple(recipients), subject=subject, text_body="\n".join(body))


def validate_receipt(receipt, recipients):
    if not isinstance(receipt, MailReceipt):
        raise ValueError("Company mail adapter must return MailReceipt")
    accepted = set(receipt.accepted_recipients)
    rejected = set(receipt.rejected_recipients)
    if (
        not isinstance(receipt.accepted_recipients, tuple)
        or not isinstance(receipt.rejected_recipients, tuple)
        or len(accepted) != len(receipt.accepted_recipients)
        or len(rejected) != len(receipt.rejected_recipients)
        or accepted & rejected
        or accepted | rejected != set(recipients)
        or (receipt.provider_message_id is not None and not isinstance(receipt.provider_message_id, str))
    ):
        raise ValueError("Company mail receipt does not account for the requested recipients")


def consume_once(db, settings, sender: MailSender, owner=None):
    if sender is None:
        raise MailNotConfigured("An explicit company mail adapter is required")
    owner = owner or f"oa-mail:{socket.gethostname()}:{uuid4()}"
    job = db.claim_job("oa_server", owner)
    if job is None:
        return False
    emission = {"attempt_id": str(uuid4()), "started_at": now(), "phase": "preparing", "provider_accepted": False}
    sending = False
    accepted = False
    try:
        if job["job_kind"] != "mail":
            reject("OA consumer only supports mail jobs.", "invalid_job_kind")
        with db.connect() as conn:
            parent_type = db.record(conn, job["parent_id"])["record_type"]
        if parent_type == "triage_mail":
            context = digest_context(db, job, owner)
            message = digest_message(settings, *context, attempt_id=emission["attempt_id"])
            emission.update(phase="sending", recipients=list(message.recipients), mail_request_id=context[1]["id"], analysis_run_id=context[2]["id"])
        else:
            context = mail_context(db, job, owner)
            message = message_for(settings, *context, attempt_id=emission["attempt_id"])
            emission.update(phase="sending", recipients=list(message.recipients), alert_id=context[1]["id"], event_id=context[2]["id"] if context[2] else None)
        # Durable before the external side effect. A lost process stays running;
        # operators must decide whether a new explicit rerun is safe.
        record_emission(db, job["id"], owner, emission)
        sending = True
        receipt = sender.send(message)
        validate_receipt(receipt, message.recipients)
        accepted = bool(receipt.accepted_recipients)
        rejected = bool(receipt.rejected_recipients)
        phase = "partially_accepted" if accepted and rejected else "accepted" if accepted else "rejected"
        emission.update(
            phase=phase, provider_accepted=accepted,
            accepted_recipients=list(receipt.accepted_recipients),
            rejected_recipients=list(receipt.rejected_recipients),
            provider_message_id=receipt.provider_message_id, finished_at=now(),
        )
        record_emission(db, job["id"], owner, emission)
        if rejected:
            error = {
                "code": "mail_partial_acceptance" if accepted else "mail_recipients_rejected",
                "message": "The company service rejected some or all recipients; review the receipt before an explicit rerun.",
            }
        else:
            error = None
        db.finish_job(job["id"], owner, error=error)
    except Exception as exc:
        if accepted:
            code = "mail_accepted_db_ambiguous"
            text = "Company mail acceptance was observed, but durable completion was not confirmed. Do not resend without operator review."
        elif isinstance(exc, MailNotConfigured):
            code, text = "mail_unconfigured", "The company mail adapter is not configured; no submission was attempted."
        elif isinstance(exc, MailRejected):
            code, text = "mail_recipients_rejected", "The company mail service explicitly rejected the request."
        elif sending:
            code, text = "mail_delivery_ambiguous", "Company mail submission outcome is unknown. Inspect the service before any explicit rerun."
        elif isinstance(exc, DomainError):
            code, text = exc.code, str(exc)
        else:
            code, text = "mail_preparation_failed", "Mail preparation failed before submission."
        error = {"code": code, "message": text}
        emission.update(phase="ambiguous" if "ambiguous" in code else "failed", error=error, finished_at=now(), provider_accepted=accepted)
        logger.error("Mail job failed (%s): %s", type(exc).__name__, code)
        try:
            record_emission(db, job["id"], owner, emission)
            db.finish_job(job["id"], owner, error=error)
        except Exception as persistence_error:
            # Remains claimed/running if DB is unavailable. Never automatically reclaim.
            logger.error("Mail outcome could not be persisted (%s); job %s requires operator review; outcome=%s", type(persistence_error).__name__, job["id"], code)
    return True


def main(*, sender_factory: Callable[[], MailSender], argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description="Consume OA mail jobs using an explicit company mail adapter; no automatic retries.")
    parser.add_argument("--once", action="store_true", help="Consume at most one pending job and exit")
    parser.add_argument("--poll-seconds", type=float, default=None, help="Polling interval (default from environment)")
    args = parser.parse_args(argv)
    if args.poll_seconds is not None and args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    try:
        sender = sender_factory()
        if sender is None or not callable(getattr(sender, "send", None)):
            raise MailNotConfigured
    except MailNotConfigured:
        parser.error("Company mail adapter is not configured. Connect oa/integration/corporate_mail.py; no jobs were claimed.")
    except Exception as exc:
        logger.error("Company mail adapter initialization failed (%s); no jobs were claimed", type(exc).__name__)
        return 1
    settings = Settings.from_env()
    db = Database(settings)
    db.check_schema()
    owner = f"oa-mail:{socket.gethostname()}:{uuid4()}"
    interval = args.poll_seconds if args.poll_seconds is not None else settings.worker_poll_seconds
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            handled = consume_once(db, settings, sender, owner)
        except Exception as exc:
            logger.error("Mail polling failed (%s)", type(exc).__name__)
            if args.once:
                return 1
            handled = False
        if args.once:
            return 0
        if not handled:
            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit("Use python -m oa.mail_app with the company mail adapter configured.")
