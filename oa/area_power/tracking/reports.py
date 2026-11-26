"""Read-only web report viewer and frozen mail snapshot queries for NET_VER tracking."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from common.area_power.tracking import (
    build_live_round_mail_snapshots,
    render_block_mail,
    render_top_mail,
)
from ..schemas import reject
from .overview import is_tracking_admin, mail_delivery_info, overview


def _dashboard_url(service) -> str | None:
    return getattr(getattr(service, "db", None), "settings", None) and getattr(service.db.settings, "dashboard_url", None)


def _render_report_mail(kind: str, snapshot: dict[str, Any], *, mail_id: str | None = None, dashboard_url: str | None = None) -> tuple[str, str, str]:
    if kind == "tracking_top_mail":
        return render_top_mail(snapshot, mail_id=mail_id, dashboard_url=dashboard_url)
    return render_block_mail(snapshot, mail_id=mail_id, dashboard_url=dashboard_url)


def project_reports(service, project_id: UUID) -> dict[str, Any]:
    """Read-only web view of sent TOP/block reports and live round summaries."""
    data = overview(service, project_id)
    is_admin = is_tracking_admin(service)
    dashboard_url = _dashboard_url(service)
    with service.db.connect() as conn:
        top_rows = service.all_records(conn, "tracking_top_mail", project_id=project_id)
        block_rows = service.all_records(conn, "tracking_block_mail", project_id=project_id)
        all_rows = sorted(
            [*top_rows, *block_rows],
            key=lambda r: str(r.get("created_at") or ""),
            reverse=True,
        )
        rounds_by_id = {str(r["id"]): r for r in data["rounds"]}
        sent_reports = []
        for row in all_rows:
            snap = row.get("payload", {}).get("snapshot") or {}
            delivery = mail_delivery_info(service, conn, row, include_recipients=is_admin)
            subject, body, html_body = _render_report_mail(
                row["record_type"], snap, mail_id=str(row["id"]), dashboard_url=dashboard_url
            )
            rid = str(row.get("parent_id") or "")
            round_info = rounds_by_id.get(rid)
            has_newer = False
            if round_info:
                if row["record_type"] == "tracking_top_mail":
                    has_newer = bool(round_info.get("has_post_top_revisions"))
                else:
                    blk = next((b for b in round_info["blocks"] if str(b["block_id"]) == str(row.get("block_id"))), None)
                    has_newer = bool(blk and blk.get("post_top_mail_revised"))
            sent_reports.append({
                "id": row["id"],
                "kind": row["record_type"],
                "round_id": row.get("parent_id"),
                "net_ver": snap.get("net_ver") or (round_info["net_ver"] if round_info else ""),
                "block_id": row.get("block_id"),
                "block_name": snap.get("block_name") or ("Full-chip · TOP 요약" if row["record_type"] == "tracking_top_mail" else ""),
                "is_full_chip": bool(snap.get("is_full_chip")) if row["record_type"] == "tracking_block_mail" else True,
                "revision_label": snap.get("revision_label") or "",
                "eco_label": snap.get("eco_label") or "",
                "trigger": row.get("payload", {}).get("trigger") or snap.get("trigger") or "",
                "created_at": row.get("created_at"),
                "frozen_at": snap.get("frozen_at") or row.get("created_at"),
                "delivery": delivery,
                "has_newer_results": has_newer,
                "subject": subject,
                "body": body,
                "html_body": html_body,
                "snapshot": snap,
            })

    live_reports = []
    for chosen in data["rounds"]:
        top_snapshot, block_snapshots = build_live_round_mail_snapshots(data["project_name"], chosen)
        top_subject, top_body, top_html = render_top_mail(top_snapshot, dashboard_url=dashboard_url)
        block_items = []
        for block, bsnap in zip(chosen["blocks"], block_snapshots):
            b_subj, b_body, b_html = render_block_mail(bsnap, dashboard_url=dashboard_url)
            block_items.append({
                "block_id": block["block_id"],
                "block_name": block["block_name"],
                "is_full_chip": block["is_full_chip"],
                "revision_label": block["revision_label"],
                "eco_label": block.get("eco_label") or "",
                "mail_sent": bool((block.get("block_mail") or {}).get("mail_id")),
                "subject": b_subj,
                "body": b_body,
                "html_body": b_html,
                "snapshot": bsnap,
            })
        live_reports.append({
            "round_id": chosen["id"],
            "net_ver": chosen["net_ver"],
            "deadline_date": chosen.get("deadline_date"),
            "deadline_time": chosen.get("deadline_time"),
            "top_mail_sent": bool((chosen.get("top_mail") or {}).get("mail_id")),
            "top_report": {"subject": top_subject, "body": top_body, "html_body": top_html, "snapshot": top_snapshot},
            "block_reports": block_items,
        })

    return {
        "project_id": project_id,
        "project_name": data["project_name"],
        "sent_reports": sent_reports,
        "live_reports": live_reports,
    }


def mail_snapshot(service, mail_id: UUID) -> dict[str, Any]:
    """Open a frozen mail snapshot (block or TOP) alongside pointers to the latest live status."""
    is_admin = is_tracking_admin(service)
    with service.db.connect() as conn:
        row = service.db.record(conn, mail_id)
        if row["record_type"] not in ("tracking_block_mail", "tracking_top_mail"):
            reject("Record is not a tracking mail snapshot.", "not_found", 404)
        delivery = mail_delivery_info(service, conn, row, include_recipients=is_admin)
        round_row = service.db.record(conn, row["parent_id"], kind="tracking_round")
        latest_slots = service.all_records(conn, "tracking_slot", parent_id=round_row["id"])
        has_newer = any(
            s["payload"].get("post_top_mail_revised")
            for s in latest_slots
            if row["record_type"] == "tracking_top_mail" or str(s["block_id"]) == str(row.get("block_id"))
        )
        snap = row["payload"].get("snapshot") or {}
        subject, body, html_body = _render_report_mail(
            row["record_type"], snap, mail_id=str(row["id"]), dashboard_url=_dashboard_url(service)
        )
        return {
            "id": row["id"],
            "kind": row["record_type"],
            "project_id": row["project_id"],
            "block_id": row.get("block_id"),
            "round_id": round_row["id"],
            "created_at": row.get("created_at"),
            "delivery": delivery,
            "subject": subject,
            "body": body,
            "html_body": html_body,
            "snapshot": snap,
            "has_newer_results": has_newer,
        }
