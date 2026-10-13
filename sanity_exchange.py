"""Additional evidence requests/responses travel only through the two shared DB tables."""
from __future__ import annotations

import hashlib

from sanity_common import (ReviewError, atomic_json, pack_json, require_id, unpack_json)
from sanity_db import Database, Queue, uuid_value
from sanity_oa_store import OAStore


def request_message_id(request_id):
    return "request-" + str(uuid_value(request_id))


def validate_selector(value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ReviewError("INVALID_REQUEST", "Unsupported evidence request")
    require_id(value.get("review_unit_id"), "review_unit_id")
    records = value.get("record_ids")
    if not isinstance(records, list) or not 1 <= len(records) <= 100 or len(set(map(str, records))) != len(records):
        raise ReviewError("INVALID_REQUEST", "Select between 1 and 100 distinct record IDs")
    for record in records:
        require_id(record, "record_id")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip() or len(value["reason"]) > 1000:
        raise ReviewError("INVALID_REQUEST", "A bounded, public request reason is required")


def request_evidence(bundle_id, unit_id, record_ids, reason, request_id, ttl_seconds=3600):
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
        raise ReviewError("INVALID_REQUEST", "Request lifetime must be between 1 and 86400 seconds")
    message_id = request_message_id(request_id)
    selector = {"schema_version": 1, "review_unit_id": unit_id, "record_ids": record_ids,
                "reason": reason, "ttl_seconds": ttl_seconds}
    validate_selector(selector)
    selector["record_ids"] = sorted(record_ids)
    with Database("oa") as db, db.transaction():
        payload, _, _, _ = OAStore(db).load(bundle_id, lock=True)
        if unit_id not in {u["review_unit_id"] for u in payload["review_units"]}:
            raise ReviewError("UNIT_NOT_FOUND", "Unit is not part of this bundle")
        queue = Queue(db, "oa_to_vwp")
        existing = queue.get(message_id)
        expires = existing["expires_at"] if existing else db.conn.execute(
            "SELECT clock_timestamp()+(%s*interval '1 second') AS deadline", (ttl_seconds,)).fetchone()["deadline"]
        queue.enqueue({"message_id": message_id, "message_type": "EVIDENCE_REQUEST", "payload": selector,
            "project": payload["run"]["project"], "run_id": payload["run"]["run_id"],
            "bundle_id": bundle_id, "expires_at": expires})
        return {"status": "ALREADY_REQUESTED" if existing else "REQUESTED", "request_id": str(uuid_value(request_id)),
                "message_id": message_id, "bundle_id": bundle_id, "record_count": len(record_ids)}


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
        records.append({**{key: value for key, value in evidence.items() if key != "example"},
                        "raw": decoded, "raw_sha256": row["raw_sha256"]})
    return records


def respond_evidence(request_id, approve_export=False):
    message_id = request_message_id(request_id)
    response_id = "response-" + str(uuid_value(request_id))
    with Database("vwp") as db:
        incoming, outgoing = Queue(db, "oa_to_vwp"), Queue(db, "vwp_to_oa")
        old = incoming.get(message_id)
        if old and old["status"] == "RECEIVED":
            response = outgoing.get(response_id)
            if response is None:
                raise ReviewError("STATE_CORRUPT", "Handled evidence request has no final response")
            return {"status": "ALREADY_RESPONDED", "request_id": str(uuid_value(request_id)), "result": response["payload"]["result"]}
        request = incoming.claim(("EVIDENCE_REQUEST",), message_id)
        if request is None:
            return {"status": "NO_MESSAGE"}
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
                    result, reason = "FULFILLED", "EXPLICIT_EXPORT_APPROVAL"
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


def _validate_response(db, response):
    request = Queue(db, "oa_to_vwp").get(response["correlation_id"])
    if (request is None or request["message_type"] != "EVIDENCE_REQUEST"
            or any(response[key] != request[key] for key in ("project", "run_id", "bundle_id"))):
        raise ReviewError("INVALID_RESPONSE", "Response request scope differs")
    result = response["payload"].get("result")
    if result not in ("FULFILLED", "REJECTED", "EXPIRED"):
        raise ReviewError("INVALID_RESPONSE", "Invalid evidence response result")
    if result != "FULFILLED":
        return None
    data = unpack_json(response)
    validate_selector(request["payload"])
    records = data.get("records")
    if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or data.get("request_id") != request["message_id"] or data.get("bundle_id") != request["bundle_id"]
            or data.get("review_unit_id") != request["payload"]["review_unit_id"]
            or not isinstance(records, list) or len(records) != len(request["payload"]["record_ids"])
            or any(not isinstance(record, dict) or not isinstance(record.get("record_id"), str)
                   or not isinstance(record.get("file_id"), str) for record in records)
            or {record.get("record_id") for record in records} != set(request["payload"]["record_ids"])):
        raise ReviewError("INVALID_RESPONSE", "Response does not cover exactly the requested records")
    payload, _, _, _ = OAStore(db).load(request["bundle_id"])
    unit = next((u for u in payload["review_units"] if u["review_unit_id"] == data["review_unit_id"]), None)
    sources = {f["file_id"]: f for f in payload["source_files"]}
    for record in records:
        raw = record.get("raw")
        source = sources.get(record.get("file_id"))
        if (unit is None or record.get("review_unit_id") != unit["review_unit_id"] or source is None
                or source["path"] != unit["report"] or not isinstance(raw, str)
                or hashlib.sha256(raw.encode()).hexdigest() != record.get("raw_sha256")
                or record.get("check_status") not in ("PASS", "FAIL", "UNKNOWN")):
            raise ReviewError("INVALID_RESPONSE", "Evidence record integrity or unit mapping differs")
    return data


def receive_evidence(request_id, out=None):
    response_id = "response-" + str(uuid_value(request_id))
    with Database("oa") as db:
        queue = Queue(db, "vwp_to_oa")
        previous = queue.get(response_id)
        already = previous is not None and previous["status"] == "RECEIVED"
        response = previous if already else queue.claim(("EVIDENCE_RESPONSE",), response_id)
        if response is None:
            return {"status": "NO_MESSAGE"}
        try:
            with db.transaction(readonly=already):
                data = _validate_response(db, response)
                if not already:
                    queue.finish(response)
        except ReviewError as exc:
            if not already and exc.code != "LEASE_LOST":
                with db.transaction():
                    queue.finish(response, "REJECTED", exc.code)
            raise
        result = {"status": "ALREADY_RECEIVED" if already else "EVIDENCE_RECEIVED", "request_id": str(uuid_value(request_id)),
                  "bundle_id": response["bundle_id"], "result": response["payload"]["result"],
                  "record_count": len(data["records"]) if data else 0}
    if out is not None:
        atomic_json(out, data or result)
    return result
