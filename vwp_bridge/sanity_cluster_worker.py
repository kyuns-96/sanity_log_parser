"""VWP worker: immutable proposals, resumable record batches, DB-only bridge transport."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import heapq

from vwp_bridge.sanity_ai import AIAdapter
from common.sanity_common import ReviewError, canonical_json, digest, pack_json, unpack_bundle
from common.sanity_clustering import check, scope_for, validate_rules, validate_known_groups, assignment, classify_record, validate_ai_response, PART_ROWS, PART_BYTES, SAMPLE_ROWS, SAMPLE_BYTES
from common.sanity_db import Database, Queue, json_value, stable_uuid, lock_key
from common.sanity_privacy import SecretGuard


def put(db, analysis, unit, kind, key, payload, state=None):
    rid = stable_uuid("cluster-vwp", str(analysis), kind, key)
    db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,review_unit_id,parent_id,payload,state,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (rid, kind, analysis, key, unit, analysis, json_value(payload), json_value(state or {}),
         "WORKING" if kind == "CLUSTER_JOB" else "RECORDED"))
    row = db.execute("SELECT * FROM {vwp} WHERE record_id=%s", (rid,)).fetchone()
    check(row is not None and row["payload"] == payload and row["parent_id"] == analysis
          and row["review_unit_id"] == unit, "STATE_CONFLICT")
    return row


def update(db, job, state, status="WORKING"):
    db.execute("UPDATE {vwp} SET state=%s,status=%s,version=version+1,updated_at=clock_timestamp() WHERE record_id=%s",
               (json_value(state), status, job["record_id"]))


def evidence_pages(db, analysis, unit, after=""):
    while True:
        rows = db.execute("SELECT entity_key,payload FROM {vwp} WHERE analysis_id=%s AND review_unit_id=%s "
                          "AND record_type='EVIDENCE' AND entity_key>%s ORDER BY entity_key LIMIT %s",
                          (analysis, unit, after, PART_ROWS)).fetchall()
        if not rows:
            return
        yield rows
        after = rows[-1]["entity_key"]


def sample_input(db, analysis, unit, request, queue, message):
    sample, scanned, unresolved, omitted_large = [], 0, 0, 0
    guard = SecretGuard()
    for rows in evidence_pages(db, analysis, unit):
        for row in rows:
            ev = row["payload"]
            fields = ev["fields"]
            value = {"record_id": ev["record_id"], "fields": fields, "check_status": ev["check_status"]}
            guard.ensure_public(value)
            scanned += 1
            missing = ev["check_status"] != "PASS" and assignment(fields, request["scope"], request["rules"])["group_id"] is None
            unresolved += int(missing)
            # Bounded deterministic sampling, prioritizing unresolved records. Full application is separate.
            if len(canonical_json(value)) > SAMPLE_BYTES // SAMPLE_ROWS:
                omitted_large += 1
                continue
            priority = (int(missing) << 256) + int(digest(value), 16)
            entry = (priority, row["entity_key"], value)
            if len(sample) < SAMPLE_ROWS:
                heapq.heappush(sample, entry)
            elif entry[:2] > sample[0][:2]:
                heapq.heapreplace(sample, entry)
        queue.renew(message)
    values = [e[2] for e in sorted(sample, reverse=True)]
    return {"scope": request["scope"], "sample": values, "scanned_records": scanned, "unresolved_records": unresolved,
            "omitted_records": scanned - len(values), "oversized_sample_records": omitted_large,
            "sample_method": "unresolved-first-deterministic-hash-v1", "existing_rules": request["rules"],
            "feedback": request["command"]["instruction"], "base_revision": request["base_revision"]}


def validate_rule_evidence(db, request):
    refs = {rid for rule in request["rules"] for rid in rule["evidence_ids"]}
    if not refs:
        return
    rows = db.execute("SELECT e.entity_key,u.payload AS unit,r.payload->'run' AS run FROM {vwp} e "
        "JOIN {vwp} u ON u.record_id=e.parent_id AND u.record_type='UNIT' "
        "JOIN {vwp} r ON r.record_id=e.analysis_id AND r.record_type='RUN' AND r.status='SEALED' "
        "WHERE e.record_type='EVIDENCE' AND e.entity_key=ANY(%s)", (sorted(refs),))
    valid = {r["entity_key"] for r in rows if scope_for(r["run"], r["unit"]) == request["scope"]}
    check(refs <= valid, "RULE_EVIDENCE_NOT_FOUND", "Rule examples must belong to a sealed analysis in the same scope")


def process_cluster(jid=None, adapter_factory=AIAdapter):
    with Database("vwp") as db:
        incoming, outgoing = Queue(db, "oa_to_vwp"), Queue(db, "vwp_to_oa")
        message = incoming.claim(("CLUSTER_REQUEST",), jid)
        if message is None:
            return {"status": "NO_MESSAGE"}
        request, job, state = message["payload"], None, {"part_count": 0, "after": "", "parts_hash": digest([])}
        held = None
        try:
            check(type(request.get("schema_version")) is int and request["schema_version"] == 1)
            check(request.get("job_id") == message["message_id"] and request.get("bundle_id") == message["bundle_id"])
            validate_rules(request.get("rules"))
            validate_known_groups(request.get("known_groups"))
            check(isinstance(request.get("command"), dict) and request["command"].get("mode") in ("AUTO", "AI", "RULES"))
            bundle_message = outgoing.get(message["bundle_id"])
            check(bundle_message is not None, "BUNDLE_NOT_FOUND")
            bundle = unpack_bundle(bundle_message)
            unit = next((u for u in bundle["review_units"] if u["review_unit_id"] == request.get("unit_id")), None)
            check(unit is not None and request.get("scope") == scope_for(bundle["run"], unit))
            check(message["project"] == bundle["run"]["project"] and message["run_id"] == bundle["run"]["run_id"])
            validate_rule_evidence(db, request)
            roots = db.execute("SELECT * FROM {vwp} WHERE record_type='RUN' AND status='SEALED' AND state->>'bundle_id'=%s",
                               (message["bundle_id"],)).fetchall()
            check(len(roots) == 1, "EVIDENCE_NOT_FOUND")
            analysis = roots[0]["analysis_id"]
            # Session lock fences a paused/slow worker even if its queue lease expires.
            held = lock_key("cluster:" + message["message_id"])
            check(db.conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (held,)).fetchone()["ok"], "CLUSTER_BUSY")
            with db.transaction():
                job = put(db, analysis, unit["review_unit_id"], "CLUSTER_JOB", message["message_id"], request, state)
                state = job["state"]
            if db.conn.execute("SELECT clock_timestamp()>%s AS expired", (message["expires_at"],)).fetchone()["expired"]:
                raise ReviewError("REQUEST_EXPIRED", "Classification request expired")
            if state.get("ai_calling"):
                raise ReviewError("AI_OUTCOME_UNKNOWN", "Interrupted AI calls are not automatically repeated")
            if "header" not in state:
                model_input = sample_input(db, analysis, unit["review_unit_id"], request, incoming, message)
                check(model_input["scanned_records"] == unit["record_count"], "COVERAGE_MISMATCH")
                mode = request["command"]["mode"]
                needs_ai = mode == "AI" or mode == "AUTO" and (bool(request["command"]["instruction"]) or model_input["unresolved_records"] > 0)
                response = {"rules": request["rules"], "explanation": "선택한 규칙을 전체 기록에 시험 적용합니다.", "needs_clarification": False}
                provenance = {"adapter": "RULES_ONLY", "input_hash": digest(model_input),
                              **{k: model_input[k] for k in ("scanned_records", "omitted_records", "sample_method", "oversized_sample_records")},
                              "sample_ids": [r["record_id"] for r in model_input["sample"]]}
                if needs_ai and model_input["scanned_records"]:
                    adapter = adapter_factory()
                    state = {**state, "ai_calling": True}
                    update(db, job, state)  # durable before call, no open DB transaction over network I/O
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(adapter.propose, model_input)
                        while True:
                            try:
                                response = future.result(timeout=20)
                                break
                            except FutureTimeout:
                                incoming.renew(message)
                    validate_ai_response(response, model_input["sample"])
                    provenance = {**provenance, **adapter.metadata(), "response_hash": digest(response)}
                header = {"scope_hash": digest(request["scope"]), "rules": response["rules"],
                          "explanation": response["explanation"], "needs_clarification": response["needs_clarification"],
                          "provenance": provenance}
                SecretGuard().ensure_public(header)
                state = {**state, "header": header, "ai_calling": False}
                with db.transaction():
                    incoming.renew(message)
                    update(db, job, state)
            header = state["header"]
            guard = SecretGuard()

            def publish(members, after):
                nonlocal state
                part = {"schema_version": 1, "job_id": message["message_id"], "part": state["part_count"],
                        "header": header, "members": members}
                guard.ensure_public(part)
                check(len(canonical_json(part)) <= PART_BYTES + 65536, "SIZE_LIMIT")
                with db.transaction():
                    incoming.renew(message)
                    put(db, analysis, unit["review_unit_id"], "CLUSTER_PART", message["message_id"] + ":" + str(state["part_count"]), part)
                    outgoing.enqueue({"message_id": message["message_id"] + "-p-" + str(state["part_count"]),
                        "message_type": "CLUSTER_PART", "payload": {"part": state["part_count"]},
                        "project": message["project"], "run_id": message["run_id"], "bundle_id": message["bundle_id"],
                        "correlation_id": message["message_id"], **pack_json(part)})
                    state = {**state, "parts_hash": digest([state["parts_hash"], state["part_count"], digest(part)]),
                             "part_count": state["part_count"] + 1, "after": after}
                    update(db, job, state)

            pending, size = [], 0
            for rows in evidence_pages(db, analysis, unit["review_unit_id"], state["after"]):
                for row in rows:
                    member = classify_record(row["payload"], request["scope"], header["rules"], request["known_groups"])
                    n = len(canonical_json(member))
                    check(n <= PART_BYTES, "SIZE_LIMIT")
                    if pending and (len(pending) >= PART_ROWS or size + n > PART_BYTES):
                        publish(pending, pending[-1]["record_id"])
                        pending, size = [], 0
                    pending.append(member)
                    size += n
                incoming.renew(message)
            if pending:
                publish(pending, pending[-1]["record_id"])
            result = {"schema_version": 1, "job_id": message["message_id"], "result": "COMPLETE",
                      "part_count": state["part_count"], "parts_hash": state["parts_hash"], "header": header}
            with db.transaction():
                outgoing.enqueue({"message_id": message["message_id"] + "-result", "message_type": "CLUSTER_RESULT",
                    "payload": result, "project": message["project"], "run_id": message["run_id"],
                    "bundle_id": message["bundle_id"], "correlation_id": message["message_id"]})
                update(db, job, state, "COMPLETE")
                incoming.finish(message)
            return {"status": "COMPLETE", "job_id": message["message_id"], "parts": state["part_count"]}
        except ReviewError as exc:
            if exc.code == "LEASE_LOST":
                raise
            if exc.code == "CLUSTER_BUSY":
                with db.transaction():
                    incoming.finish(message, "RETRY", exc.code)
                return {"status": "RETRY", "code": exc.code}
            with db.transaction():
                outgoing.enqueue({"message_id": message["message_id"] + "-result", "message_type": "CLUSTER_RESULT",
                    "payload": {"schema_version": 1, "job_id": message["message_id"], "result": "FAILED",
                                "error_code": exc.code, "part_count": state["part_count"]},
                    "project": message["project"], "run_id": message["run_id"],
                    "bundle_id": message["bundle_id"], "correlation_id": message["message_id"]})
                if job:
                    update(db, job, state, "FAILED")
                incoming.finish(message)
            return {"status": "FAILED", "job_id": message["message_id"], "code": exc.code}
        finally:
            if held is not None:
                db.conn.execute("SELECT pg_advisory_unlock(%s)", (held,))
