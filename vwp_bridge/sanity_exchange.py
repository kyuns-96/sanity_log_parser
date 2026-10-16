"""vwp_bridge evidence exchange responsibilities."""
import hashlib
from common.sanity_common import ReviewError, pack_json
from common.sanity_db import Database, Queue, uuid_value
from common.sanity_privacy import SecretGuard
from common.sanity_exchange import request_message_id, validate_selector


def _approved_records(db, request):
    selector = request["payload"]
    roots = db.execute("SELECT * FROM {vwp} WHERE record_type='RUN' AND status='SEALED' "
        "AND state->>'bundle_id'=%s AND payload->'run'->>'project'=%s AND payload->'run'->>'run_id'=%s",
        (request["bundle_id"], request["project"], request["run_id"])).fetchall()
    if len(roots) != 1:
        raise ReviewError("EVIDENCE_NOT_FOUND", "No unique sealed analysis matches the request")
    root = roots[0]
    uid = selector["review_unit_id"]
    unit = db.execute("SELECT * FROM {vwp} WHERE analysis_id=%s AND record_type='UNIT' AND entity_key=%s",
                      (root["analysis_id"], uid)).fetchone()
    # Bound the full fetch before materializing up to 100 potentially large records.
    size = db.execute("SELECT coalesce(sum(octet_length(raw_bytes)+octet_length(payload::text)),0) AS n "
                      "FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE' "
                      "AND review_unit_id=%s AND entity_key=ANY(%s)",
                      (root["analysis_id"], uid, selector["record_ids"])).fetchone()["n"]
    if size > 64 * 1024 * 1024:
        raise ReviewError("SIZE_LIMIT", "Select a smaller set of evidence records")
    rows = db.execute("SELECT * FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE' "
                      "AND review_unit_id=%s AND entity_key=ANY(%s) ORDER BY entity_key",
                      (root["analysis_id"], uid, selector["record_ids"])).fetchall()
    if unit is None or {r["entity_key"] for r in rows} != set(selector["record_ids"]):
        raise ReviewError("EVIDENCE_NOT_FOUND", "Requested records must all exist in the selected unit")
    files = {row["entity_key"]: row for row in db.execute(
        "SELECT * FROM {vwp} WHERE analysis_id=%s AND record_type='FILE'", (root["analysis_id"],))}
    records = []
    guard = SecretGuard()
    for row in rows:
        evidence = row["payload"]
        source = files.get(evidence.get("file_id"))
        raw = row["raw_bytes"]
        if (row["parent_id"] != unit["record_id"] or evidence.get("review_unit_id") != uid
                or evidence.get("record_id") != row["entity_key"] or source is None
                or source["review_unit_id"] != uid or source["payload"]["path"] != unit["payload"]["report"]
                or raw is None or hashlib.sha256(raw).hexdigest() != row["raw_sha256"]):
            raise ReviewError("EVIDENCE_CONFLICT", "Requested evidence integrity failed")
        try:
            decoded = raw.decode("utf-8")
        except UnicodeError as exc:
            raise ReviewError("EVIDENCE_CONFLICT", "Invalid evidence encoding") from exc
        safe = guard.redact({**{key: value for key, value in evidence.items() if key != "example"}, "raw": decoded})
        redacted = safe != {**{key: value for key, value in evidence.items() if key != "example"}, "raw": decoded}
        records.append({**safe, "raw_sha256": hashlib.sha256(safe["raw"].encode()).hexdigest(),
                        "source_raw_sha256": row["raw_sha256"], "redacted": redacted,
                        "redaction_policy": "credentials-v1"})
    return records


def respond_evidence(request_id=None, approve_export=True):
    message_id = request_message_id(request_id) if request_id is not None else None
    with Database("vwp") as db:
        incoming, outgoing = Queue(db, "oa_to_vwp"), Queue(db, "vwp_to_oa")
        old = incoming.get(message_id) if message_id else None
        if old and old["status"] == "RECEIVED":
            response = outgoing.get("response-" + str(uuid_value(request_id)))
            if response is None:
                raise ReviewError("STATE_CORRUPT", "Handled evidence request has no final response")
            return {"status": "ALREADY_RESPONDED", "request_id": str(uuid_value(request_id)), "result": response["payload"]["result"]}
        request = incoming.claim(("EVIDENCE_REQUEST",), message_id)
        if request is None:
            return {"status": "NO_MESSAGE"}
        message_id = request["message_id"]
        request_id = str(uuid_value(message_id.removeprefix("request-")))
        response_id = "response-" + request_id
        try:
            validate_selector(request["payload"])
            clock = db.conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            packed = {}
            if request["expires_at"] <= clock:
                result, reason = "EXPIRED", "REQUEST_EXPIRED"
            elif not approve_export:
                result, reason = "REJECTED", "EXPORT_NOT_APPROVED"
            else:
                try:
                    with db.transaction(readonly=True):
                        records = _approved_records(db, request)
                    packed = pack_json({"schema_version": 1, "request_id": message_id,
                        "bundle_id": request["bundle_id"], "review_unit_id": request["payload"]["review_unit_id"], "records": records})
                    result, reason = "FULFILLED", "REPORT_ACCESS_ALLOWED"
                except ReviewError as exc:
                    result, reason = "REJECTED", exc.code
            with db.transaction():
                outgoing.enqueue({"message_id": response_id, "message_type": "EVIDENCE_RESPONSE",
                    "payload": {"result": result, "reason": reason}, "project": request["project"],
                    "run_id": request["run_id"], "bundle_id": request["bundle_id"], "correlation_id": message_id, **packed})
                incoming.finish(request)
            return {"status": "RESPONDED", "request_id": str(uuid_value(request_id)), "result": result}
        except ReviewError as exc:
            if exc.code != "LEASE_LOST":
                with db.transaction():
                    incoming.finish(request, "REJECTED", exc.code)
            raise
