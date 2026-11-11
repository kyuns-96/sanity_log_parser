"""Consume OA mail jobs without holding DB locks during SMTP delivery.

SMTP acceptance is not recipient delivery and cannot be atomic with a DB commit.
Each attempt is recorded before sending; ambiguous attempts are never retried here.
"""
from __future__ import annotations

import argparse
import logging
import smtplib
import socket
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from uuid import UUID, uuid4

from area_power.common.config import Settings
from area_power.common.db import Database
from area_power.common.errors import DomainError
from area_power.common.jsonutil import dumps, public
from .service import Service, addresses, reject

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


def message_for(settings, job, alert, event, comparison, metric, baseline, current):
    job_recipients = job["payload"].get("recipients") or []
    alert_recipients = alert["payload"].get("recipients") or []
    recipients = addresses([*job_recipients, *alert_recipients])
    if not recipients:
        recipients = addresses(settings.smtp_recipients)
    if not recipients:
        reject("No SMTP recipients are configured or requested.", "smtp_recipients_missing")
    if not settings.smtp_host or not settings.smtp_from:
        reject("SMTP host and sender must be configured.", "smtp_unconfigured")
    sender = addresses([settings.smtp_from])[0]
    if settings.smtp_ssl and settings.smtp_starttls:
        reject("SMTP SSL and STARTTLS cannot both be enabled.", "smtp_configuration_invalid")
    if bool(settings.smtp_username) != bool(settings.smtp_password):
        reject("SMTP authentication requires both username and password.", "smtp_configuration_invalid")
    payload = alert["payload"]
    metric_payload = metric["payload"] if metric else payload
    summary = str(payload.get("summary") or payload.get("kind") or "Design Atlas observation")
    summary = " ".join(summary.splitlines())[:180]
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = format_datetime(now())
    message["Message-ID"] = make_msgid(idstring=str(job["id"]))
    message["Subject"] = f"[Design Atlas] {summary}"
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
    message.set_content("\n".join(body))
    return message, sender, recipients


def deliver(settings, message, sender, recipients, emission):
    context = ssl.create_default_context()
    cls = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
    kwargs = {"host": settings.smtp_host, "port": settings.smtp_port, "timeout": 30}
    if settings.smtp_ssl:
        kwargs["context"] = context
    smtp = cls(**kwargs)
    try:
        smtp.ehlo()
        if settings.smtp_starttls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        # Returning means DATA was accepted for all non-refused recipients.
        emission["phase"] = "data_submitting"
        return smtp.send_message(message, from_addr=sender, to_addrs=recipients)
    finally:
        # A failure in QUIT must not erase an already observed DATA acceptance.
        try:
            smtp.quit()
        except Exception:
            smtp.close()


def consume_once(db, settings, owner=None):
    owner = owner or f"oa-mail:{socket.gethostname()}:{uuid4()}"
    job = db.claim_job("oa_server", owner)
    if job is None:
        return False
    emission = {"attempt_id": str(uuid4()), "started_at": now(), "phase": "preparing", "smtp_accepted": False}
    sending = False
    accepted = False
    try:
        if job["job_kind"] != "mail":
            reject("OA consumer only supports mail jobs.", "invalid_job_kind")
        context = mail_context(db, job, owner)
        message, sender, recipients = message_for(settings, *context)
        emission.update(phase="sending", recipients=recipients, message_id=str(message["Message-ID"]), alert_id=context[1]["id"], event_id=context[2]["id"] if context[2] else None)
        # Durable before the external side effect. A lost process stays running;
        # operators must decide whether a new explicit rerun is safe.
        record_emission(db, job["id"], owner, emission)
        sending = True
        refused = deliver(settings, message, sender, recipients, emission)
        accepted = len(refused) < len(recipients)
        emission.update(phase="accepted" if not refused else "partially_accepted", smtp_accepted=accepted, accepted_recipients=[recipient for recipient in recipients if recipient not in refused], refused_recipients=list(refused), finished_at=now())
        record_emission(db, job["id"], owner, emission)
        error = {"code": "smtp_partial_acceptance", "message": "SMTP accepted only some recipients; inspect emission details before any explicit rerun."} if refused else None
        db.finish_job(job["id"], owner, error=error)
    except Exception as exc:
        if accepted:
            code = "smtp_accepted_db_ambiguous"
            text = "SMTP acceptance was observed, but durable completion was not confirmed. Do not resend without operator review."
        elif isinstance(exc, DomainError):
            code, text = exc.code, str(exc)
        elif isinstance(exc, smtplib.SMTPRecipientsRefused):
            code, text = "smtp_recipients_rejected", "SMTP rejected all recipients."
        elif isinstance(exc, smtplib.SMTPDataError):
            code, text = "smtp_message_rejected", "SMTP explicitly rejected the message data."
        elif isinstance(exc, smtplib.SMTPAuthenticationError):
            code, text = "smtp_authentication_failed", "SMTP authentication failed."
        elif sending and emission["phase"] == "data_submitting" and isinstance(exc, (smtplib.SMTPServerDisconnected, OSError, TimeoutError)):
            code, text = "smtp_delivery_ambiguous", "SMTP delivery outcome is unknown. Inspect the server before any explicit rerun."
        else:
            code, text = "smtp_delivery_failed", "Mail delivery was not confirmed; inspect configuration and server availability."
        if isinstance(exc, smtplib.SMTPResponseException):
            emission["smtp_response_code"] = exc.smtp_code
        error = {"code": code, "message": text}
        emission.update(phase="ambiguous" if "ambiguous" in code else "failed", error=error, finished_at=now(), smtp_accepted=accepted)
        logger.error("Mail job failed (%s): %s", type(exc).__name__, code)
        try:
            record_emission(db, job["id"], owner, emission)
            db.finish_job(job["id"], owner, error=error)
        except Exception as persistence_error:
            # Remains claimed/running if DB is unavailable. Never automatically reclaim.
            logger.error("Mail outcome could not be persisted (%s); job %s requires operator review; outcome=%s", type(persistence_error).__name__, job["id"], code)
    return True


def main():
    parser = argparse.ArgumentParser(description="Consume OA mail jobs using configured SMTP; no automatic retries.")
    parser.add_argument("--once", action="store_true", help="Consume at most one pending job and exit")
    parser.add_argument("--poll-seconds", type=float, default=None, help="Polling interval (default from environment)")
    args = parser.parse_args()
    if args.poll_seconds is not None and args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    settings = Settings.from_env()
    db = Database(settings)
    db.check_schema()
    owner = f"oa-mail:{socket.gethostname()}:{uuid4()}"
    interval = args.poll_seconds if args.poll_seconds is not None else settings.worker_poll_seconds
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            handled = consume_once(db, settings, owner)
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
    raise SystemExit(main())
