"""Persistent human judgments, confirmed criteria and auditable reference proposals."""
from common.sanity_common import ReviewError, digest
from common.sanity_db import json_value, lock_key, uuid_value
from common.sanity_review_advice import ApprovedCriteriaAdvisor, report_facts, validate_advice
from oa.sanity_workspace import WorkspaceStore, record_id, utcnow


class WorkspaceReviewStore:
    max_cases = 1000
    head_kind, event_kind, proposal_kind = "WS_REVIEW", "WS_REVIEW_EVENT", "WS_REVIEW_PROPOSAL"
    report_ref, event_ref = "workspace_review_report_ref", "workspace_review_event_ref"

    def __init__(self, db):
        self.db, self.ws = db, WorkspaceStore(db)

    def row(self, kind, key):
        return self.db.execute("SELECT * FROM {oa} WHERE record_type=%s AND workspace_key=%s", (kind, key)).fetchone()

    def context(self, user_id, block_id, version_id, report_id, write=False):
        block, _ = self.ws.access(user_id, block_id, write=write)
        version = self.ws.version(user_id, block_id, version_id)["payload"]
        report = self.ws.report(user_id, block_id, version_id, report_id)
        scope = {key: report.get(key) for key in ("project_id", "block_id", "item_id", "family", "parser", "rule_version", "eco")}
        scope["layout"] = version["layout"]
        return {"report": report, "version": version, "scope": scope, "facts": report_facts(report),
                "source_sha256": digest(report), "can_review": user_id in block["payload"]["owners"],
                "ready": bool(report.get("analysis_ready") and report.get("parse_status") == "COMPLETE" and report.get("raw_report"))}

    @staticmethod
    def metadata(context):
        report = context["report"]
        return {"report_id": report["id"], "report_record_id": str(record_id("WS_REPORT", report["id"])),
                "version_id": report["version_id"], "version_record_id": report["version_record_id"],
                "project_id": report["project_id"], "block_id": report["block_id"], "eco": report["eco"],
                "scope": context["scope"], "source_sha256": context["source_sha256"]}

    def head_key(self, report_id, group_id=None):
        return report_id

    def locked_context(self, context, user_id, block_id, version_id, report_id):
        return context

    def decision_stale(self, event, context):
        return False

    def current(self, report_id, group_id=None):
        head = self.row(self.head_kind, self.head_key(report_id, group_id))
        if not head:
            return None, 0
        event = self.row(self.event_kind, head["state"].get("event_id"))
        if (not event or event["payload"]["revision"] != head["version"]
                or event["payload"]["report_id"] != report_id
                or event["record_id"] != head[self.event_ref]):
            raise ReviewError("STATE_CORRUPT", "Review head and immutable event differ")
        return event["payload"], head["version"]

    def checked_current(self, context):
        current, revision = self.current(context["report"]["id"])
        if current and current["source_sha256"] != context["source_sha256"]:
            raise ReviewError("STATE_CORRUPT", "Reviewed source differs")
        return current, revision

    def cases(self, context):
        rows = self.db.execute(
            "SELECT e.payload FROM {oa} h JOIN {oa} e ON e.record_id=h." + self.event_ref + " "
            "WHERE h.record_type=%s AND h.workspace_block_ref=%s "
            "AND e.payload->'scope'=%s AND e.payload->>'report_id'<>%s "
            "ORDER BY e.created_at DESC,e.workspace_key LIMIT %s",
            (self.head_kind, record_id("WS_BLOCK", context["report"]["block_id"]), json_value(context["scope"]), context["report"]["id"], self.max_cases + 1)).fetchall()
        if len(rows) > self.max_cases:
            raise ReviewError("CASE_LIMIT", "Narrow the case scope; no partial catalog accepted")
        result, sources = [], {}
        for row in rows:
            event = row["payload"]
            if (event["conclusion"] in ("PASS", "FAIL")
                    and (event["version_id"] == context["report"]["version_id"] or event["compatible_versions"])):
                # References must still point to the immutable source that was reviewed.
                if event["report_id"] not in sources:
                    source = self.row("WS_REPORT", event["report_id"])
                    sources[event["report_id"]] = digest(source["payload"]) if source else None
                if sources[event["report_id"]] != event["source_sha256"]:
                    raise ReviewError("STATE_CORRUPT", "Confirmed case source differs")
                result.append(event)
        return result

    def stale(self, proposal, context, revision, cases=None):
        if (proposal["source_sha256"] != context["source_sha256"] or proposal["review_revision"] != revision
                or self.decision_stale(proposal, context)):
            return True
        cases = self.cases(context) if cases is None else cases
        if proposal.get("case_catalog_sha256") != digest([case["id"] for case in cases]):
            return True
        for reference in proposal["references"]:
            current, _ = self.current(reference["report_id"], reference.get("group_id"))
            source = self.row("WS_REPORT", reference["report_id"])
            if (not current or current["id"] != reference["id"] or not source
                    or digest(source["payload"]) != reference["source_sha256"]):
                return True
        return False

    def view(self, user_id, block_id, version_id, report_id):
        context = self.context(user_id, block_id, version_id, report_id)
        current, revision = self.checked_current(context)
        history = self.db.execute("SELECT payload FROM {oa} WHERE record_type=%s "
                                  "AND " + self.report_ref + "=%s AND COALESCE(payload->>'group_id','')=%s "
                                  "ORDER BY created_at DESC,workspace_key LIMIT 50",
                                  (self.event_kind, record_id("WS_REPORT", report_id), context.get("group_id", ""))).fetchall()
        proposal = self.db.execute("SELECT payload FROM {oa} WHERE record_type=%s "
                                   "AND " + self.report_ref + "=%s AND COALESCE(payload->>'group_id','')=%s "
                                   "ORDER BY created_at DESC,workspace_key LIMIT 1",
                                   (self.proposal_kind, record_id("WS_REPORT", report_id), context.get("group_id", ""))).fetchone()
        cases = self.cases(context)
        value = proposal["payload"] if proposal else None
        return {"current": current, "revision": revision, "history": [row["payload"] for row in history],
                "facts": context["facts"], "ready": context["ready"], "can_review": context["can_review"],
                "cases": cases[:50], "case_count": len(cases), "reusable_count": sum(case["reusable"] for case in cases),
                "cases_displayed": min(50, len(cases)), "proposal": {**value, "stale": self.stale(value, context, revision, cases)} if value else None,
                "corporate_ai_connected": False, "current_stale": bool(current and self.decision_stale(current, context)),
                "blocked_reason": context.get("blocked_reason")}

    def save(self, user_id, block_id, version_id, report_id, *, conclusion, reason, criteria, conditions,
             reusable, compatible_versions, expected_revision, request_id, proposal_id=None):
        context = self.context(user_id, block_id, version_id, report_id, write=True)
        if (conclusion not in ("PASS", "FAIL", "DEFER") or type(expected_revision) is not int or expected_revision < 0
                or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000
                or not isinstance(criteria, str) or len(criteria) > 2000 or type(reusable) is not bool
                or type(compatible_versions) is not bool or not isinstance(conditions, dict)):
            raise ReviewError("INVALID_REQUEST", "Review fields are invalid")
        if conclusion != "DEFER" and not context["ready"]:
            raise ReviewError("REVIEW_BLOCKED", "Resolve missing or incomplete evidence before a final decision")
        if reusable:
            mandatory = {key for key in ("rule", "mode") if key in context["facts"]}
            if (conclusion == "DEFER" or not criteria.strip() or not conditions or "rule" not in mandatory
                    or not mandatory <= set(conditions) or not context["scope"]["item_id"] or not context["scope"]["rule_version"]
                    or any(key not in context["facts"] or value != context["facts"][key] for key, value in conditions.items())):
                raise ReviewError("INVALID_CRITERIA", "Confirm explicit conditions from the source facts")
        elif conditions or criteria.strip() or compatible_versions:
            raise ReviewError("INVALID_CRITERIA", "One-time decisions have no reusable conditions")
        request_id = str(uuid_value(request_id))
        proposal_id = str(uuid_value(proposal_id)) if proposal_id else None
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "conclusion": conclusion, "reason": reason.strip(), "criteria": criteria.strip(), "conditions": conditions,
                   "reusable": reusable, "compatible_versions": compatible_versions, "expected_revision": expected_revision,
                   "proposal_id": proposal_id}
        if context.get("group_id"):
            command.update(group_id=context["group_id"], cluster_revision=context["cluster_revision"])
        # Ten-user workflow: serialize judgment/criteria changes so references cannot
        # be superseded between validation and commit; PoR writes remain independent.
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        context = self.locked_context(context, user_id, block_id, version_id, report_id)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-request-" + request_id),))
        previous = self.row(self.event_kind, request_id)
        if previous:
            if previous["payload"]["command"] != command:
                raise ReviewError("REQUEST_CONFLICT", "Review UUID has different contents")
            return {"status": "REVIEW_SAVED", "event": previous["payload"]}
        # Lock before the initial head is inserted too; two first decisions cannot race.
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-head-" + report_id),))
        old, revision = self.checked_current(context)
        if revision != expected_revision:
            raise ReviewError("REVISION_CONFLICT", "Review changed; reload before saving")
        proposal = self.row(self.proposal_kind, proposal_id) if proposal_id else None
        if proposal_id and (not proposal or proposal["payload"]["report_id"] != report_id
                            or proposal["payload"]["version_id"] != version_id
                            or proposal["payload"].get("group_id") != context.get("group_id")
                            or self.stale(proposal["payload"], context, revision)):
            raise ReviewError("PROPOSAL_STALE", "Proposal or referenced human criteria changed")
        event = {**self.metadata(context), "id": request_id, "command": command, "revision": revision + 1,
                 "previous_event_id": old["id"] if old else None, "actor": self.ws.user(user_id), "at": utcnow().isoformat(),
                 "conclusion": conclusion, "reason": reason.strip(), "criteria": criteria.strip(), "conditions": conditions,
                 "reusable": reusable, "compatible_versions": compatible_versions, "proposal_id": proposal_id,
                 "proposal_conclusion": proposal["payload"]["conclusion"] if proposal else None,
                 "proposal_agreed": proposal["payload"]["conclusion"] == conclusion if proposal else None,
                 "source_version": context["version"]}
        self.ws.put(self.event_kind, request_id, event, project=event["project_id"], block=block_id)
        if old is None:
            self.ws.put(self.head_kind, self.head_key(report_id), self.metadata(context), project=event["project_id"], block=block_id)
        self.db.execute("UPDATE {oa} SET state=%s,version=%s,updated_at=clock_timestamp() "
                        "WHERE record_type=%s AND workspace_key=%s",
                        (json_value({"event_id": request_id, "event_record_id": str(record_id(self.event_kind, request_id))}), revision + 1,
                         self.head_kind, self.head_key(report_id)))
        return {"status": "REVIEW_SAVED", "event": event}

    def propose(self, user_id, block_id, version_id, report_id, *, expected_revision, request_id, mode="CRITERIA"):
        context = self.context(user_id, block_id, version_id, report_id, write=True)
        if mode != "CRITERIA":
            raise ReviewError("AI_NOT_CONFIGURED", "Company VWP review advisor is not connected")
        request_id = str(uuid_value(request_id))
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        context = self.locked_context(context, user_id, block_id, version_id, report_id)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-proposal-request-" + request_id),))
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "expected_revision": expected_revision, "mode": mode}
        if context.get("group_id"):
            command.update(group_id=context["group_id"], cluster_revision=context["cluster_revision"])
        previous = self.row(self.proposal_kind, request_id)
        if previous:
            if previous["payload"]["command"] != command:
                raise ReviewError("REQUEST_CONFLICT", "Proposal UUID has different contents")
            return previous["payload"]
        _, revision = self.checked_current(context)
        if type(expected_revision) is not int or revision != expected_revision:
            raise ReviewError("REVISION_CONFLICT", "Review changed; reload before requesting advice")
        cases = self.cases(context)
        advice = validate_advice(ApprovedCriteriaAdvisor().propose(context, cases), cases)
        if not context["ready"]:
            advice = {"conclusion": "DEFER", "reason": "검사 근거가 미수신 또는 미완료입니다. 먼저 원본과 파싱 상태를 확인해 주세요.", "reference_ids": []}
        references = [case for case in cases if case["id"] in advice["reference_ids"]]
        proposal = {**self.metadata(context), **advice, "id": request_id, "command": command, "review_revision": revision,
                    "at": utcnow().isoformat(), "actor": self.ws.user(user_id), "references": references,
                    "advisor": "CONFIRMED_CRITERIA_V1", "model_called": False,
                    "case_catalog_sha256": digest([case["id"] for case in cases]),
                    "input_case_count": len(cases), "input_sha256": digest({"context": context, "case_ids": [case["id"] for case in cases]})}
        self.ws.put(self.proposal_kind, request_id, proposal, project=context["report"]["project_id"], block=block_id)
        return proposal


def review_summaries(db, report_ids):
    if not report_ids:
        return {}
    rows = db.execute("SELECT h.workspace_key,e.payload FROM {oa} h JOIN {oa} e ON e.record_id=h.workspace_review_event_ref "
                      "WHERE h.record_type='WS_REVIEW' AND h.workspace_key=ANY(%s)", (report_ids,)).fetchall()
    return {row["workspace_key"]: {key: row["payload"][key] for key in ("conclusion", "actor", "at", "revision", "reusable")} for row in rows}
