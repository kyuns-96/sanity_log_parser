"""OA-owned classification revisions. Sealed analysis/review records are never rewritten."""
from __future__ import annotations

from common.sanity_common import ReviewError, canonical_json, digest, unpack_json, require_id
from common.sanity_db import Database, Queue, json_value, stable_uuid, uuid_value
from oa.sanity_oa_store import OAStore, now
from common.sanity_clustering import check, scope_for, validate_rules, validate_known_groups, classify_record, MAX_GROUPS, PART_ROWS, PART_BYTES
from common.sanity_privacy import SecretGuard

CATALOG_PAGE_SIZE = 32
MAX_CATALOG_SOURCES = 1000


def job_id(value):
    return "cj-" + str(uuid_value(value))


class ClusterStore:
    def __init__(self, db, bundle_id, unit_id, lock=False):
        self.db, self.bundle_id, self.unit_id = db, bundle_id, unit_id
        self.bundle, _, _, _ = OAStore(db).load(bundle_id, lock=lock)
        self.unit = next((u for u in self.bundle["review_units"] if u["review_unit_id"] == unit_id), None)
        check(self.unit is not None, "UNIT_NOT_FOUND")
        self.scope = scope_for(self.bundle["run"], self.unit)

    def get(self, kind, key):
        return self.db.execute("SELECT * FROM {oa} WHERE bundle_id=%s AND review_unit_id=%s AND record_type=%s AND entity_key=%s",
                               (self.bundle_id, self.unit_id, kind, key)).fetchone()

    def put(self, kind, key, payload, state=None, request_id=None, revision=None):
        immutable = {"record_id": stable_uuid("cluster-oa", self.bundle_id, self.unit_id, kind, key),
                     "record_type": kind, "project": self.bundle["run"]["project"], "run_id": self.bundle["run"]["run_id"],
                     "bundle_id": self.bundle_id, "review_unit_id": self.unit_id, "entity_key": key,
                     "payload": payload, "request_id": request_id, "classification_revision": revision}
        keys = list(immutable)
        self.db.execute("INSERT INTO {oa}(" + ",".join(keys) + ",state) VALUES (" +
                        ",".join(["%s"] * len(keys)) + ",%s) ON CONFLICT DO NOTHING",
                        [json_value(v) if k == "payload" else v for k, v in immutable.items()] + [json_value(state or {})])
        row = self.get(kind, key)
        check(row is not None and all(row[k] == v for k, v in immutable.items()), "STATE_CONFLICT")
        return row

    def head(self):
        row = self.get("CLUSTER_STATE", "head")
        return row["state"] if row else {"revision": 0, "active_job": None, "confirmed": False, "confirmed_revision": None}

    def lock_head(self):
        # Callers hold the immutable bundle's row lock to serialize initial creation too.
        return self.put("CLUSTER_STATE", "head", {"scope_hash": digest(self.scope)}, self.head())

    def saved_rules(self):
        row = self.db.execute("SELECT payload FROM {oa} WHERE record_type='CLUSTER_RULE' AND payload->>'scope_hash'=%s "
                              "ORDER BY created_at DESC,record_id DESC LIMIT 1", (digest(self.scope),)).fetchone()
        return row["payload"]["rules"] if row else []

    def catalog(self):
        # Consider all group IDs before filtering: invalid sources must not silently
        # consume the group limit or conceal a usable, older occurrence of a group.
        source_query = (" FROM {oa} h JOIN {oa} m ON m.bundle_id=h.bundle_id AND m.review_unit_id=h.review_unit_id "
            "AND m.record_type='CLUSTER_MEMBER' AND m.payload->>'job_id'=h.state->>'active_job' "
            "WHERE h.record_type='CLUSTER_STATE' AND h.payload->>'scope_hash'=%s AND h.state->>'confirmed'='true' "
            "AND NOT (h.bundle_id=%s AND h.review_unit_id=%s) "
            "AND m.payload->>'check_status'='FAIL' AND m.payload->>'group_id' IS NOT NULL ")
        scope = (digest(self.scope), self.bundle_id, self.unit_id)
        groups = self.db.execute("SELECT DISTINCT m.payload->>'group_id' AS group_id" + source_query +
            "ORDER BY group_id LIMIT %s", (*scope, MAX_GROUPS + 1)).fetchall()
        check(len(groups) <= MAX_GROUPS, "CATALOG_LIMIT", "Narrow the comparison scope; no historical groups were silently omitted")
        result, excluded, checked, scanned = [], [], {}, 0
        for group in groups:
            after, selected = None, None
            while selected is None:
                rows = self.db.execute("SELECT DISTINCT h.record_id AS head_id,h.updated_at,h.bundle_id,h.review_unit_id,"
                    "h.state,m.payload->>'job_id' AS job_id,m.payload->'identity' AS identity" + source_query +
                    "AND m.payload->>'group_id'=%s " +
                    ("AND (h.updated_at,h.record_id)<(%s,%s) " if after else "") +
                    "ORDER BY h.updated_at DESC,h.record_id DESC LIMIT %s",
                    (*scope, group["group_id"], *(after or ()), CATALOG_PAGE_SIZE)).fetchall()
                if not rows:
                    break
                for row in rows:
                    scanned += 1
                    check(scanned <= MAX_CATALOG_SOURCES, "CATALOG_LIMIT", "Too many historical sources; no partial catalog was accepted")
                    reference = {"group_id": group["group_id"], "group_revision": row["state"]["confirmed_revision"],
                                 "source_bundle": row["bundle_id"], "source_unit": row["review_unit_id"], "source_job": row["job_id"]}
                    key = (row["bundle_id"], row["review_unit_id"], row["job_id"], reference["group_revision"])
                    if key not in checked:
                        checked[key] = self.reference_problem({"known_groups": [reference]})
                    problem = checked[key]
                    check(problem != "REFERENCE_LIMIT", "CATALOG_LIMIT", "Dependency verification exceeded its bound")
                    if problem:
                        excluded.append({**reference, "reason": problem})
                        continue
                    selected = {**reference, "identity": row["identity"]}
                    break
                after = (rows[-1]["updated_at"], rows[-1]["head_id"])
            if selected is not None:
                refs = self.db.execute("SELECT payload->>'record_id' AS rid FROM {oa} WHERE record_type='CLUSTER_MEMBER' "
                    "AND bundle_id=%s AND review_unit_id=%s AND payload->>'job_id'=%s AND payload->>'group_id'=%s "
                    "AND payload->>'check_status'='FAIL' ORDER BY entity_key LIMIT 3",
                    (selected["source_bundle"], selected["source_unit"], selected["source_job"], selected["group_id"])).fetchall()
                result.append({**selected, "fail_evidence_ids": [v["rid"] for v in refs]})
        return validate_known_groups(result), excluded

    def request(self, request_id, expected_revision, instruction, rules, mode, reviewer):
        check(mode in ("AUTO", "AI", "RULES") and type(expected_revision) is int and expected_revision >= 0)
        check(isinstance(instruction, str) and len(instruction) <= 4000)
        if rules is not None:
            validate_rules(rules)
        command = {"request_id": str(uuid_value(request_id)), "expected_revision": expected_revision,
                   "instruction": instruction, "rules": rules, "mode": mode, "reviewer": reviewer}
        jid = job_id(request_id)
        existing = Queue(self.db, "oa_to_vwp").get(jid)
        if existing:
            check(existing["bundle_id"] == self.bundle_id and existing["payload"].get("command") == command
                  and existing["payload"].get("unit_id") == self.unit_id, "REQUEST_CONFLICT")
            return {"status": "ALREADY_REQUESTED", "job_id": jid}
        self.lock_head()
        head = self.head()
        check(head["revision"] == expected_revision, "REVISION_CONFLICT")
        check(self.unit["applicable"], "CLUSTER_BLOCKED")
        base = self.get("CLUSTER_JOB", head["active_job"]) if head["active_job"] else None
        effective = rules if rules is not None else base["state"]["header"]["rules"] if base else self.saved_rules()
        known_groups, exclusions = self.catalog()
        request = {"schema_version": 1, "job_id": jid, "bundle_id": self.bundle_id, "unit_id": self.unit_id,
                   "scope": self.scope, "base_revision": expected_revision, "base_job": head["active_job"],
                   "rules": effective, "known_groups": known_groups, "catalog_exclusions": exclusions, "command": command}
        SecretGuard().ensure_public(request)
        check(len(canonical_json(request)) <= 256 * 1024, "SIZE_LIMIT")
        expires = self.db.conn.execute("SELECT clock_timestamp()+interval '24 hours' AS value").fetchone()["value"]
        Queue(self.db, "oa_to_vwp").enqueue({"message_id": jid, "message_type": "CLUSTER_REQUEST", "payload": request,
            "project": self.bundle["run"]["project"], "run_id": self.bundle["run"]["run_id"],
            "bundle_id": self.bundle_id, "expires_at": expires})
        self.put("CLUSTER_JOB", jid, request, {"phase": "QUEUED", "received_parts": 0, "received_records": 0})
        return {"status": "REQUESTED", "job_id": jid}

    def update_job(self, jid, state):
        self.db.execute("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() "
                        "WHERE bundle_id=%s AND review_unit_id=%s AND record_type='CLUSTER_JOB' AND entity_key=%s",
                        (json_value(state), self.bundle_id, self.unit_id, jid))

    def accept(self, message):
        jid = message["correlation_id"]
        job = self.get("CLUSTER_JOB", jid)
        check(job is not None, "CLUSTER_NOT_FOUND")
        request = job["payload"]
        check(all(message[k] == v for k, v in (("project", self.bundle["run"]["project"]),
              ("run_id", self.bundle["run"]["run_id"]), ("bundle_id", self.bundle_id))))
        state = job["state"]
        if message["message_type"] == "CLUSTER_PART":
            data = unpack_json(message)
            SecretGuard().ensure_public(data)
            check(type(data.get("schema_version")) is int and data["schema_version"] == 1 and data.get("job_id") == jid)
            number, header, members = data.get("part"), data.get("header"), data.get("members")
            check(type(number) is int and number >= 0 and message["payload"].get("part") == number)
            self.validate_header(header)
            check(not state.get("header") or state["header"] == header, "STATE_CONFLICT")
            check(isinstance(members, list) and 1 <= len(members) <= PART_ROWS and len(canonical_json(data)) <= PART_BYTES + 65536)
            seen = set()
            for row in members:
                check(isinstance(row, dict))
                rid, index = row.get("record_id"), row.get("record_index")
                check(isinstance(rid, str) and rid not in seen)
                seen.add(rid)
                check(type(index) is int and 1 <= index <= self.unit["record_count"])
                source = next((f for f in self.bundle["source_files"] if f["file_id"] == row.get("file_id")), None)
                check(source is not None and source["path"] == self.unit["report"] and row.get("review_unit_id") == self.unit_id)
                check(rid == "r-" + digest([self.bundle["run"]["run_id"], self.unit_id, row["file_id"], index]))
                check(row.get("check_status") in ("PASS", "FAIL", "UNKNOWN") and isinstance(row.get("fields"), dict))
                check(isinstance(row.get("location"), dict))
                ev = {k: row[k] for k in ("record_id", "record_index", "file_id", "review_unit_id", "check_status", "fields")}
                check(row == classify_record({**ev, **row["location"]}, self.scope, header["rules"], request["known_groups"]))
                # A record occurring in two different parts is not an idempotent replay.
                old = self.get("CLUSTER_MEMBER", jid + ":" + rid)
                check(old is None or old["payload"]["part"] == number, "DUPLICATE_MEMBER")
                self.put("CLUSTER_MEMBER", jid + ":" + rid, {**row, "job_id": jid, "part": number})
            self.put("CLUSTER_PART", jid + ":" + str(number), {"job_id": jid, "part": number,
                      "sha256": digest(data), "count": len(members)})
            state = {**state, "header": header}
        else:
            result = message["payload"]
            check(type(result.get("schema_version")) is int and result["schema_version"] == 1 and result.get("job_id") == jid)
            check(result.get("result") in ("COMPLETE", "FAILED"))
            check(type(result.get("part_count")) is int and result["part_count"] >= 0)
            check(not state.get("result") or state["result"] == result, "STATE_CONFLICT")
            if result["result"] == "COMPLETE":
                self.validate_header(result.get("header"))
                check(not state.get("header") or state["header"] == result["header"], "STATE_CONFLICT")
                state = {**state, "header": result["header"]}
            state = {**state, "result": result}
        self.update_job(jid, self.progress(jid, state))

    def validate_header(self, header):
        check(isinstance(header, dict) and set(header) == {"scope_hash", "rules", "provenance", "explanation", "needs_clarification"})
        check(header["scope_hash"] == digest(self.scope))
        validate_rules(header["rules"])
        check(type(header["needs_clarification"]) is bool and isinstance(header["explanation"], str)
              and len(header["explanation"]) <= 4000)
        check(isinstance(header["provenance"], dict) and len(canonical_json(header)) <= 65536)
        SecretGuard().ensure_public(header)

    def stats(self, jid):
        row = self.db.execute("SELECT count(*) AS records,count(*) FILTER (WHERE payload->>'check_status'<>'PASS') AS targets,"
            "count(*) FILTER (WHERE payload->>'group_id' IS NOT NULL) AS assigned,"
            "count(DISTINCT payload->>'group_id') AS groups,"
            "count(*) FILTER (WHERE payload->>'method'='UNDECIDED') AS unresolved,"
            "count(*) FILTER (WHERE jsonb_array_length(payload->'candidates')>0) AS candidate_records "
            "FROM {oa} WHERE record_type='CLUSTER_MEMBER' AND bundle_id=%s AND review_unit_id=%s AND payload->>'job_id'=%s",
            (self.bundle_id, self.unit_id, jid)).fetchone()
        return row

    def progress(self, jid, state):
        part = self.db.execute("SELECT count(*) AS n,coalesce(sum((payload->>'count')::bigint),0) AS records,"
            "max((payload->>'part')::bigint) AS last FROM {oa} WHERE bundle_id=%s AND review_unit_id=%s "
            "AND record_type='CLUSTER_PART' AND payload->>'job_id'=%s", (self.bundle_id, self.unit_id, jid)).fetchone()
        state = {**state, "phase": "RECEIVING", "received_parts": part["n"], "received_records": int(part["records"])}
        if state.get("validation_error"):
            return {**state, "phase": "REJECTED"}
        result = state.get("result")
        if not result:
            return state
        check(part["n"] <= result["part_count"] and (part["last"] is None or part["last"] < result["part_count"]))
        if result["result"] == "FAILED":
            return {**state, "phase": "FAILED"}
        if part["n"] < result["part_count"]:
            return state
        chain = digest([])
        for record in self.db.execute("SELECT payload FROM {oa} WHERE bundle_id=%s AND review_unit_id=%s "
            "AND record_type='CLUSTER_PART' AND payload->>'job_id'=%s ORDER BY (payload->>'part')::bigint",
            (self.bundle_id, self.unit_id, jid)):
            p = record["payload"]
            chain = digest([chain, p["part"], p["sha256"]])
        check(chain == result.get("parts_hash"), "PARTS_INTEGRITY")
        check(part["records"] == self.unit["record_count"], "COVERAGE_MISMATCH")
        counts = {r["status"]: r["n"] for r in self.db.execute("SELECT payload->>'check_status' AS status,count(*) AS n FROM {oa} "
            "WHERE bundle_id=%s AND review_unit_id=%s AND record_type='CLUSTER_MEMBER' AND payload->>'job_id'=%s "
            "GROUP BY payload->>'check_status'", (self.bundle_id, self.unit_id, jid))}
        check(all(counts.get(k, 0) == n for k, n in self.unit["status_counts"].items()), "COVERAGE_MISMATCH")
        stats = self.stats(jid)
        check(stats["targets"] == stats["assigned"] + stats["unresolved"], "COVERAGE_MISMATCH")
        return {**state, "phase": "NEEDS_CLARIFICATION" if state["header"]["needs_clarification"] else "READY", "stats": stats}

    def references_current(self, request, lock=False):
        return self.reference_problem(request, lock=lock) is None

    def reference_problem(self, request, lock=False):
        pending, visited = list(request["known_groups"]), set()
        while pending:
            group = pending.pop()
            if (group["source_bundle"], group["source_unit"]) == (self.bundle_id, self.unit_id):
                # Applying/confirming this job would invalidate its own dependency.
                # Also rejects already queued cyclic jobs made by older versions.
                return "SELF_DEPENDENCY"
            key = (group["source_bundle"], group["source_unit"], group["source_job"], group["group_revision"])
            if key in visited:
                continue
            visited.add(key)
            if len(visited) > 1000:
                return "REFERENCE_LIMIT"
            row = self.db.execute("SELECT state,payload FROM {oa} WHERE record_type='CLUSTER_STATE' AND bundle_id=%s AND review_unit_id=%s" +
                                  (" FOR SHARE" if lock else ""),
                                  (group["source_bundle"], group["source_unit"])).fetchone()
            if (row is None or not row["state"]["confirmed"] or row["state"]["confirmed_revision"] != group["group_revision"]
                    or row["state"]["active_job"] != group["source_job"]):
                return "STALE_REFERENCE"
            if row["payload"].get("scope_hash") != digest(self.scope):
                return "REFERENCE_SCOPE_MISMATCH"
            source = self.db.execute("SELECT payload FROM {oa} WHERE record_type='CLUSTER_JOB' AND bundle_id=%s "
                "AND review_unit_id=%s AND entity_key=%s", (group["source_bundle"], group["source_unit"], group["source_job"])).fetchone()
            if source is None:
                return "STALE_REFERENCE"
            # Verify every transitive dependency, not just the catalog's first
            # page. Old queued jobs must not bridge unrelated report scopes.
            if source["payload"].get("scope") != self.scope:
                return "REFERENCE_SCOPE_MISMATCH"
            pending.extend(source["payload"]["known_groups"])
        return None

    def action(self, request_id, expected_revision, action, jid, reason, reviewer, record_id=None, group_id=None, decision=None):
        check(action in ("APPLY", "CONFIRM", "SAVE_RULES", "CANDIDATE", "DEFER"))
        check(isinstance(reason, str) and 1 <= len(reason.strip()) <= 2000)
        rid = uuid_value(request_id)
        command = {"bundle_id": self.bundle_id, "unit_id": self.unit_id, "expected_revision": expected_revision,
                   "action": action, "job_id": jid, "reason": reason, "reviewer": reviewer,
                   "record_id": record_id, "group_id": group_id, "decision": decision}
        SecretGuard().ensure_public(command)
        old = self.db.execute("SELECT payload FROM {oa} WHERE request_id=%s", (rid,)).fetchone()
        if old:
            check(old["payload"].get("command") == command, "REQUEST_CONFLICT")
            return {"status": "ALREADY_APPLIED", **self.head()}
        self.lock_head()
        head = self.head()
        check(type(expected_revision) is int and expected_revision == head["revision"], "REVISION_CONFLICT")
        job = self.get("CLUSTER_JOB", jid)
        check(job is not None, "CLUSTER_NOT_FOUND")
        check(job["state"]["phase"] == "READY", "CLUSTER_NOT_READY")
        # Withdrawing confirmation is safe even when dependencies have changed.
        # Every approval-like action still verifies and locks all live references.
        if action != "DEFER":
            check(self.references_current(job["payload"], lock=True), "STALE_REFERENCE")
        if action == "APPLY":
            check(job["payload"]["base_revision"] == head["revision"], "REVISION_CONFLICT")
            head = {**head, "active_job": jid, "confirmed": False, "confirmed_revision": None}
        else:
            check(head["active_job"] == jid, "REVISION_CONFLICT")
        if action == "CANDIDATE":
            check(decision in ("DIFFERENT", "CONFIRM_MATCH", "DEFER"))
            member = self.get("CLUSTER_MEMBER", jid + ":" + str(record_id))
            check(member is not None and any(c["group_id"] == group_id for c in member["payload"]["candidates"]))
            # Association confirmation is independent of membership and the source sanity verdict.
            head = {**head, "confirmed": False, "confirmed_revision": None}
        if action == "DEFER":
            head = {**head, "confirmed": False, "confirmed_revision": None}
        if action == "CONFIRM":
            check(self.stats(jid)["unresolved"] == 0 and not self.bundle["global_blockers"]
                  and self.unit["parse_status"] == "COMPLETE", "CLUSTER_BLOCKED")
            check(self.unresolved_candidates(jid) == 0, "CANDIDATES_UNRESOLVED")
            head = {**head, "confirmed": True, "confirmed_revision": head["revision"] + 1}
        if action == "SAVE_RULES":
            check(head["confirmed"], "CLUSTER_BLOCKED")
            self.put("CLUSTER_RULE", str(rid), {"scope_hash": digest(self.scope), "scope": self.scope,
                "rules": job["state"]["header"]["rules"], "job_id": jid, "confirmed_revision": head["confirmed_revision"],
                "reviewer": reviewer, "reason": reason})
        head = {**head, "revision": head["revision"] + 1}
        self.put("CLUSTER_EVENT", str(head["revision"]), {"command": command, "at": now(), "head": head},
                 request_id=rid, revision=head["revision"])
        self.db.execute("UPDATE {oa} SET state=%s,version=%s,updated_at=clock_timestamp() WHERE record_type='CLUSTER_STATE' "
                        "AND bundle_id=%s AND review_unit_id=%s", (json_value(head), head["revision"], self.bundle_id, self.unit_id))
        return {"status": action, **head}

    def unresolved_candidates(self, jid):
        # Latest decision is per candidate, not per record or group.
        return self.db.execute("SELECT count(*) AS n FROM {oa} m CROSS JOIN LATERAL jsonb_array_elements(m.payload->'candidates') c "
            "LEFT JOIN LATERAL (SELECT e.payload->'command'->>'decision' AS decision FROM {oa} e "
            "WHERE e.bundle_id=m.bundle_id AND e.review_unit_id=m.review_unit_id AND e.record_type='CLUSTER_EVENT' "
            "AND e.payload->'command'->>'action'='CANDIDATE' AND e.payload->'command'->>'job_id'=%s "
            "AND e.payload->'command'->>'record_id'=m.payload->>'record_id' AND e.payload->'command'->>'group_id'=c->>'group_id' "
            "ORDER BY e.classification_revision DESC LIMIT 1) d ON true WHERE m.bundle_id=%s AND m.review_unit_id=%s "
            "AND m.record_type='CLUSTER_MEMBER' AND m.payload->>'job_id'=%s "
            "AND (d.decision IS NULL OR d.decision='DEFER')", (jid, self.bundle_id, self.unit_id, jid)).fetchone()["n"]

    def members(self, jid, offset=0, limit=20, filter="all", group_id=None):
        job = self.get("CLUSTER_JOB", jid)
        check(job is not None, "CLUSTER_NOT_FOUND")
        check(filter in ("all", "unresolved", "candidates"))
        clause = {"all": "", "unresolved": " AND payload->>'method'='UNDECIDED'",
                  "candidates": " AND jsonb_array_length(payload->'candidates')>0"}[filter]
        params = [self.bundle_id, self.unit_id, jid]
        if group_id:
            clause += " AND payload->>'group_id'=%s"
            params.append(group_id)
        where = " WHERE bundle_id=%s AND review_unit_id=%s AND record_type='CLUSTER_MEMBER' AND payload->>'job_id'=%s" + clause
        total = self.db.execute("SELECT count(*) AS n FROM {oa}" + where, params).fetchone()["n"]
        rows = self.db.execute("SELECT payload FROM {oa}" + where + " ORDER BY entity_key LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
        current = self.references_current(job["payload"])
        for row in rows:
            base = self.get("CLUSTER_MEMBER", job["payload"]["base_job"] + ":" + row["payload"]["record_id"]) if job["payload"]["base_job"] else None
            row["payload"]["previous_group_id"] = base["payload"]["group_id"] if base else None
            row["payload"]["membership_changed"] = row["payload"]["previous_group_id"] != row["payload"]["group_id"]
            for candidate in row["payload"]["candidates"]:
                event = self.db.execute("SELECT payload FROM {oa} WHERE bundle_id=%s AND review_unit_id=%s AND record_type='CLUSTER_EVENT' "
                    "AND payload->'command'->>'action'='CANDIDATE' AND payload->'command'->>'job_id'=%s "
                    "AND payload->'command'->>'record_id'=%s AND payload->'command'->>'group_id'=%s "
                    "ORDER BY classification_revision DESC LIMIT 1",
                    (self.bundle_id, self.unit_id, jid, row["payload"]["record_id"], candidate["group_id"])).fetchone()
                candidate["decision"] = event["payload"] if event else None
                candidate["needs_recheck"] = not current
        return {"items": [r["payload"] for r in rows], "total": total, "offset": offset, "limit": limit}

    def view(self, offset=0, limit=20):
        head = self.head()
        total = self.db.execute("SELECT count(*) AS n FROM {oa} WHERE bundle_id=%s AND review_unit_id=%s AND record_type='CLUSTER_JOB'",
                                (self.bundle_id, self.unit_id)).fetchone()["n"]
        rows = self.db.execute("SELECT entity_key,payload,state,created_at FROM {oa} WHERE bundle_id=%s AND review_unit_id=%s "
                              "AND record_type='CLUSTER_JOB' ORDER BY created_at DESC,entity_key DESC LIMIT %s OFFSET %s",
                              (self.bundle_id, self.unit_id, limit, offset)).fetchall()
        jobs = [{"job_id": r["entity_key"], "base_revision": r["payload"]["base_revision"], "created_at": r["created_at"],
                 "instruction": r["payload"]["command"]["instruction"], "state": r["state"],
                 "catalog_exclusions": r["payload"].get("catalog_exclusions", []),
                 "stale": r["entity_key"] != head["active_job"] and r["payload"]["base_revision"] != head["revision"],
                 "references_current": self.references_current(r["payload"])} for r in rows]
        history = self.db.execute("SELECT payload,classification_revision FROM {oa} WHERE bundle_id=%s AND review_unit_id=%s "
            "AND record_type='CLUSTER_EVENT' ORDER BY classification_revision DESC LIMIT 50", (self.bundle_id, self.unit_id)).fetchall()
        return {"head": head, "scope": self.scope, "jobs": jobs, "total": total, "offset": offset, "limit": limit,
                "saved_rules": self.saved_rules(), "history": history,
                "input_blockers": self.unit["blockers"] + self.bundle["global_blockers"]}


def receive_cluster(jid=None):
    with Database("oa") as db:
        queue = Queue(db, "vwp_to_oa")
        if jid:
            # Resolve only an available message for this job; do not drain another user's scope.
            row = db.execute("SELECT message_id FROM {vwp_to_oa} WHERE correlation_id=%s AND message_type IN ('CLUSTER_PART','CLUSTER_RESULT') "
                "AND ((status IN ('READY','RETRY') AND next_attempt_at<=clock_timestamp()) OR "
                "(status='PROCESSING' AND lease_expires_at<=clock_timestamp())) ORDER BY created_at,message_id LIMIT 1", (jid,)).fetchone()
            if not row:
                return {"status": "NO_MESSAGE"}
            message = queue.claim(("CLUSTER_PART", "CLUSTER_RESULT"), row["message_id"])
        else:
            message = queue.claim(("CLUSTER_PART", "CLUSTER_RESULT"))
        if message is None:
            return {"status": "NO_MESSAGE"}
        request = None
        try:
            request = Queue(db, "oa_to_vwp").get(message["correlation_id"])
            check(request is not None and request["message_type"] == "CLUSTER_REQUEST")
            with db.transaction():
                store = ClusterStore(db, request["bundle_id"], request["payload"]["unit_id"], lock=True)
                store.accept(message)
                queue.finish(message)
            return {"status": "RECEIVED", "job_id": request["message_id"]}
        except ReviewError as exc:
            if exc.code != "LEASE_LOST":
                with db.transaction():
                    queue.finish(message, "REJECTED", exc.code)
                    if request and request["message_type"] == "CLUSTER_REQUEST":
                        store = ClusterStore(db, request["bundle_id"], request["payload"]["unit_id"], lock=True)
                        job = store.get("CLUSTER_JOB", request["message_id"])
                        if job:
                            store.update_job(request["message_id"], {**job["state"], "phase": "REJECTED", "validation_error": exc.code})
            raise
