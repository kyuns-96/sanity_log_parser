"""OA private state and append-only dashboard reviews; publication is disabled."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from common.sanity_common import ReviewError, coverage, render_report, validate_content_id
from common.sanity_db import json_value, stable_uuid, uuid_value


def now():
    return datetime.now(timezone.utc).isoformat()


class OAStore:
    def __init__(self, db):
        self.db = db

    def put(self, record_type, payload, project, run_id, *, bundle_id=None, review_unit_id=None,
            review_revision=None, request_id=None, payload_bytes=None,
            state=None, status="RECORDED"):
        identity = (record_type, project, run_id, bundle_id, review_unit_id, review_revision)
        record_id = stable_uuid("oa-record", *identity)
        immutable = {"record_id": record_id, "record_type": record_type, "project": project, "run_id": run_id,
            "bundle_id": bundle_id, "review_unit_id": review_unit_id, "review_revision": review_revision,
            "request_id": request_id, "payload": payload, "payload_bytes": payload_bytes,
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes is not None else None}
        keys = list(immutable)
        params = [json_value(immutable[key]) if key == "payload" else immutable[key] for key in keys]
        self.db.execute("INSERT INTO {oa} (" + ",".join(keys) + ",state,status) VALUES (" +
            ",".join(["%s"] * len(keys)) + ",%s,%s) ON CONFLICT DO NOTHING",
            (*params, json_value(state or {}), status))
        stored = self.db.execute("SELECT * FROM {oa} WHERE record_id=%s", (record_id,)).fetchone()
        if stored is None or any(stored[key] != value for key, value in immutable.items()):
            raise ReviewError("STATE_CONFLICT", "Existing immutable record or logical key differs")
        return stored

    def root(self, bundle_id, lock=False):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='RECEIVED_BUNDLE' AND bundle_id=%s" +
                              (" FOR UPDATE" if lock else ""), (bundle_id,)).fetchone()
        if row is None:
            raise ReviewError("BUNDLE_NOT_FOUND", "Bundle has not been received")
        return row

    def load(self, bundle_id, lock=False):
        root = self.root(bundle_id, lock)
        payload, context = root["payload"].get("bundle"), root["payload"].get("context")
        if not isinstance(payload, dict) or not isinstance(context, dict):
            raise ReviewError("STATE_CORRUPT", "Invalid received bundle state")
        validate_content_id(payload)
        if (payload["bundle_id"] != bundle_id or payload["run"]["project"] != root["project"]
                or payload["run"]["run_id"] != root["run_id"]):
            raise ReviewError("STATE_CORRUPT", "Received bundle scope mismatch")
        revision = root["state"].get("current_review_revision")
        if type(revision) is not int or revision < 0 or root["version"] != revision:
            raise ReviewError("STATE_CORRUPT", "Invalid review revision checkpoint")
        units = self.db.execute("SELECT * FROM {oa} WHERE bundle_id=%s AND record_type='REVIEW_UNIT'", (bundle_id,)).fetchall()
        expected = {unit["review_unit_id"]: unit for unit in payload["review_units"]}
        if (len(units) != len(expected) or any(row["payload"] != expected.get(row["review_unit_id"])
                or row["project"] != root["project"] or row["run_id"] != root["run_id"] for row in units)):
            raise ReviewError("STATE_CORRUPT", "Stored review units do not cover the expected inventory")
        events = self.db.execute("SELECT * FROM {oa} WHERE bundle_id=%s AND record_type='REVIEW_EVENT' "
                                 "ORDER BY review_revision", (bundle_id,)).fetchall()
        if len(events) != revision + 1 or any(event["review_revision"] != i for i, event in enumerate(events)):
            raise ReviewError("STATE_CORRUPT", "Missing or unexpected review revision")
        decisions = {}
        readiness = coverage(payload)
        ready_units = {unit["review_unit_id"]: unit for unit in readiness["units"]}
        for event in events:
            changes = event["payload"].get("decisions")
            if (not isinstance(changes, list) or (event["review_revision"] == 0 and changes)
                    or (event["review_revision"] > 0 and len(changes) != 1)
                    or event["project"] != root["project"] or event["run_id"] != root["run_id"]):
                raise ReviewError("STATE_CORRUPT", "Invalid review event")
            for decision in changes:
                if not isinstance(decision, dict):
                    raise ReviewError("STATE_CORRUPT", "Invalid decision")
                uid = decision.get("review_unit_id")
                if (uid not in expected or decision.get("bundle_id") != bundle_id
                        or decision.get("conclusion") not in ("PASS", "FAIL")
                        or any(not isinstance(decision.get(k), str) or not decision[k].strip()
                               for k in ("reviewer", "reason", "reviewed_at"))
                        or readiness["global_blockers"] or not ready_units[uid]["analysis_ready"]
                        or not expected[uid]["applicable"]
                        or (expected[uid]["check_status"] == "FAIL" and decision["conclusion"] == "PASS")):
                    raise ReviewError("STATE_CORRUPT", "Stored decision is not valid for this bundle")
                decisions[uid] = decision
        return payload, decisions, context, revision

    def save_report(self, payload, decisions, context, revision):
        body = render_report(payload, decisions, context)
        self.put("REPORT", {"format": "markdown-utf8"}, payload["run"]["project"], payload["run"]["run_id"],
                 bundle_id=payload["bundle_id"], review_revision=revision, payload_bytes=body.encode("utf-8"))
        return body

    def receive(self, payload, context):
        existing = self.db.execute("SELECT * FROM {oa} WHERE bundle_id=%s AND record_type='RECEIVED_BUNDLE'",
                                   (payload["bundle_id"],)).fetchone()
        if existing:
            if existing["payload"].get("bundle") != payload:
                raise ReviewError("BUNDLE_CONFLICT", "Received ID has different contents")
            status = "ALREADY_RECEIVED"
        else:
            scope = {"project": payload["run"]["project"], "run_id": payload["run"]["run_id"], "bundle_id": payload["bundle_id"]}
            self.put("RECEIVED_BUNDLE", {"bundle": payload, "context": context}, **scope,
                     state={"current_review_revision": 0})
            for unit in payload["review_units"]:
                self.put("REVIEW_UNIT", unit, **scope, review_unit_id=unit["review_unit_id"])
            self.put("REVIEW_EVENT", {"decisions": [], "actor": "INITIAL"}, **scope, review_revision=0)
            self.save_report(payload, {}, context, 0)
            status = "RECEIVED"
        stored, decisions, context, revision = self.load(payload["bundle_id"])
        return {"status": status, "bundle_id": stored["bundle_id"], "coverage": coverage(stored, decisions),
                "context": context, "review_revision": revision}

    def report(self, bundle_id):
        payload, decisions, context, revision = self.load(bundle_id)
        row = self.db.execute("SELECT * FROM {oa} WHERE bundle_id=%s AND record_type='REPORT' AND review_revision=%s",
                              (bundle_id, revision)).fetchone()
        expected = render_report(payload, decisions, context).encode("utf-8")
        if (row is None or row["payload_bytes"] != expected
                or row["payload_sha256"] != hashlib.sha256(expected).hexdigest()
                or row["project"] != payload["run"]["project"] or row["run_id"] != payload["run"]["run_id"]):
            raise ReviewError("STATE_CORRUPT", "Immutable report does not match review revision")
        return expected.decode("utf-8")

    def review(self, bundle_id, unit_id, reviewer, conclusion, reason, request_id, expected_revision=None):
        if not reviewer.strip() or not reason.strip() or conclusion not in ("PASS", "FAIL"):
            raise ReviewError("INVALID_REVIEW", "Valid reviewer, reason and conclusion are required")
        request_id = uuid_value(request_id)
        command = {"bundle_id": bundle_id, "review_unit_id": unit_id, "reviewer": reviewer,
                   "conclusion": conclusion, "reason": reason}
        payload, decisions, context, revision = self.load(bundle_id, lock=True)
        # A new revision must not hide a damaged current report, even when the
        # command comes directly from the API without a preceding detail GET.
        self.report(bundle_id)
        previous = self.db.execute("SELECT * FROM {oa} WHERE request_id=%s", (request_id,)).fetchone()
        if previous:
            if previous["record_type"] != "REVIEW_EVENT" or previous["payload"].get("request") != command:
                raise ReviewError("REQUEST_CONFLICT", "Review request ID was already used for different content")
            return {"status": "ALREADY_REVIEWED", "bundle_id": bundle_id, "coverage": coverage(payload, decisions),
                    "review_revision": revision, "request_revision": previous["review_revision"]}
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision != revision):
            raise ReviewError("REVISION_CONFLICT", "Review changed; refresh before submitting another decision")
        unit = next((u for u in payload["review_units"] if u["review_unit_id"] == unit_id), None)
        if unit is None:
            raise ReviewError("UNIT_NOT_FOUND", "Unit is not in this bundle")
        summary = coverage(payload, decisions)
        ready = next(u for u in summary["units"] if u["review_unit_id"] == unit_id)
        if summary["global_blockers"] or not unit["applicable"] or not ready["analysis_ready"]:
            raise ReviewError("REVIEW_BLOCKED", "Resolve source blockers before review")
        if unit["check_status"] == "FAIL" and conclusion == "PASS":
            raise ReviewError("FAIL_CANNOT_PASS", "Known FAIL cannot be approved as PASS")
        decision = {**command, "reviewed_at": now()}
        revision += 1
        self.put("REVIEW_EVENT", {"decisions": [decision], "request": command}, payload["run"]["project"],
                 payload["run"]["run_id"], bundle_id=bundle_id, review_revision=revision, request_id=request_id)
        self.db.execute("UPDATE {oa} SET state=%s,version=%s,updated_at=clock_timestamp() "
                        "WHERE record_type='RECEIVED_BUNDLE' AND bundle_id=%s",
                        (json_value({"current_review_revision": revision}), revision, bundle_id))
        decisions[unit_id] = decision
        self.save_report(payload, decisions, context, revision)
        return {"status": "REVIEWED", "bundle_id": bundle_id, "coverage": coverage(payload, decisions), "review_revision": revision}

    def publish(self, bundle_id, dry_run, simulate):
        raise ReviewError("JIRA_DISABLED", "Jira publishing is disabled; use the OA dashboard")
