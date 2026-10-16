"""oa evidence exchange responsibilities."""
import hashlib
from common.sanity_common import ReviewError, atomic_json, unpack_json
from common.sanity_db import Database, Queue, uuid_value
from common.sanity_privacy import SecretGuard
from common.sanity_exchange import request_message_id, validate_selector


def request_evidence(bundle_id, unit_id, record_ids, reason, request_id, ttl_seconds=3600):
    from oa.sanity_oa_store import OAStore
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
        raise ReviewError("INVALID_REQUEST", "Request lifetime must be between 1 and 86400 seconds")
    message_id = request_message_id(request_id)
    selector = {"schema_version": 1, "review_unit_id": unit_id, "record_ids": record_ids,
                "reason": reason, "ttl_seconds": ttl_seconds}
    validate_selector(selector)
    SecretGuard().ensure_public(selector)
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


def _validate_response(db, response):
    from oa.sanity_oa_store import OAStore
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
        if any(k in record for k in ("source_raw_sha256", "redacted", "redaction_policy")):
            source_hash = record.get("source_raw_sha256")
            if (type(record.get("redacted")) is not bool or record.get("redaction_policy") != "credentials-v1"
                    or not isinstance(source_hash, str) or len(source_hash) != 64
                    or any(c not in "0123456789abcdef" for c in source_hash)
                    or not record["redacted"] and source_hash != record["raw_sha256"]):
                raise ReviewError("INVALID_RESPONSE", "Invalid redaction provenance")
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
