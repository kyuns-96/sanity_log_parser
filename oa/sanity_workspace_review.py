"""Persistent human judgments, confirmed criteria and auditable reference proposals."""
import re

from common.sanity_clustering import check
from common.sanity_ai_knowledge import shared_case_views
from common.sanity_common import ReviewError, digest
from common.sanity_db import json_value, lock_key, uuid_value
from common.sanity_review_advice import (ApprovedCriteriaAdvisor, normalize_review_exception,
    report_facts, validate_advice, validate_stored_review_exception)
from common.sanity_submission import source_digest
from oa.sanity_workspace import WorkspaceStore, record_id, utcnow


def validate_ai_receipt(metadata, retrieval, cases):
    """Only trusted worker receipts use this path; browser commands cannot mint one."""
    check(isinstance(metadata, dict) and metadata.get("model_called") is True
          and metadata.get("provider") in ("OA_OPENAI_COMPATIBLE", "VWP_OPENAI_COMPATIBLE")
          and isinstance(metadata.get("model"), str) and metadata["model"]
          and isinstance(metadata.get("prompt_version"), str) and metadata["prompt_version"]
          and isinstance(metadata.get("input_sha256"), str)
          and re.fullmatch(r"[0-9a-f]{64}", metadata["input_sha256"]), "INVALID_AI_RECEIPT")
    if metadata["provider"] == "VWP_OPENAI_COMPATIBLE":
        from common.sanity_vwp_ai import identifier
        check(metadata.get("execution_site") == "vwp" and identifier(metadata.get("profile_id"))
              and isinstance(metadata.get("profile_sha256"), str)
              and re.fullmatch(r"[a-f0-9]{64}", metadata["profile_sha256"])
              and isinstance(metadata.get("connection_id"), str)
              and str(uuid_value(metadata["connection_id"])) == metadata["connection_id"]
              and type(metadata.get("connection_revision")) is int and metadata["connection_revision"] >= 1,
              "INVALID_AI_RECEIPT")
        if "batch_receipt" not in metadata:
            execution = metadata.get("execution")
            check(isinstance(execution, dict) and set(execution) == {"site", "profile_id", "profile_sha256", "call_id", "job_id"}
                  and execution["site"] == "vwp" and execution["profile_id"] == metadata["profile_id"]
                  and execution["profile_sha256"] == metadata["profile_sha256"]
                  and all(isinstance(execution[k], str) and str(uuid_value(execution[k])) == execution[k]
                          for k in ("call_id", "job_id")), "INVALID_AI_RECEIPT")
    if 'batch_receipt' in metadata or 'input_hash_kind' in metadata:
        value=metadata.get('batch_receipt')
        check(metadata.get('input_hash_kind')=='BATCH_MANIFEST' and isinstance(value,dict)
              and set(value)=={'version','root','calls','records','plan'}
              and type(value['version'])is int and value['version']==1
              and type(value['calls'])is int and value['calls']>0
              and type(value['records'])is int and value['records']>0
              and isinstance(value['root'],str) and re.fullmatch('[0-9a-f]{64}',value['root'])
              and isinstance(value['plan'],dict) and set(value['plan'])=={'key','sha256'}
              and metadata['input_sha256']==digest([value['plan'],value['root']]),'INVALID_AI_RECEIPT')
    check(isinstance(retrieval, dict) and isinstance(cases, list)
          and all(isinstance(case, dict) and isinstance(case.get("id"), str) for case in cases)
          and len({case["id"] for case in cases}) == len(cases), "INVALID_AI_RECEIPT")
    selected = retrieval.get("selected")
    check(isinstance(selected, list) and all(isinstance(row, dict) for row in selected)
          and [row.get("id") for row in selected] == [case["id"] for case in cases]
          and all(row.get("revision") == case["revision"]
                  and row.get("source_sha256") == case["source_sha256"]
                  for row, case in zip(selected, cases)), "INVALID_AI_RECEIPT")


class WorkspaceReviewStore:
    max_cases = 1000
    head_kind, event_kind, proposal_kind = "WS_REVIEW", "WS_REVIEW_EVENT", "WS_REVIEW_PROPOSAL"
    report_ref, event_ref = "workspace_review_report_ref", "workspace_review_event_ref"

    def __init__(self, db, ws=None):
        self.db, self.ws = db, ws if ws is not None else WorkspaceStore(db)

    @property
    def ai_case_policy(self):
        return getattr(self.ws, "case_policy", None) == "OA_RETRIEVAL_V1"

    def row(self, kind, key):
        return self.db.execute("SELECT * FROM {oa} WHERE record_type=%s AND workspace_key=%s", (kind, key)).fetchone()

    def context(self, user_id, block_id, version_id, report_id, write=False, *, hydrate_source=True):
        block, _ = self.ws.access(user_id, block_id, write=write)
        version = self.ws.version(user_id, block_id, version_id)["payload"]
        report = self.ws.report(user_id, block_id, version_id, report_id,hydrate_source=hydrate_source)
        scope = {key: report.get(key) for key in ("project_id", "block_id", "item_id", "family", "parser", "rule_version", "eco")}
        scope["layout"] = version["layout"]
        return {"report": report, "version": version, "scope": scope, "facts": report_facts(report),
                "_actor_user_id": user_id,
                "source_sha256": source_digest(report), "can_review": self.ws.can_write(user_id, block),
                "ready": bool(report.get("analysis_ready") and report.get("parse_status") == "COMPLETE"
                              and (report.get('source_complete') and report.get('delivery_ready')
                                   if report.get('source_storage')=='PARTS_V2' else report.get("raw_report")))}

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
        if self.ai_case_policy:
            return self.context(user_id, block_id, version_id, report_id, write=True)
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
        validate_stored_review_exception(event["payload"])
        return event["payload"], head["version"]

    def checked_current(self, context):
        current, revision = self.current(context["report"]["id"])
        if current and current["source_sha256"] != context["source_sha256"]:
            raise ReviewError("STATE_CORRUPT", "Reviewed source differs")
        return current, revision

    def cases(self, context):
        if self.ai_case_policy:
            from oa.sanity_ai_cases import CaseRetriever
            return CaseRetriever(self.db, self.ws).select(context, stage="REVIEW")["cases"]
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
            validate_stored_review_exception(event)
            if (event["conclusion"] in ("PASS", "FAIL")
                    and (event["version_id"] == context["report"]["version_id"] or event["compatible_versions"])):
                # References must still point to the immutable source that was reviewed.
                if event["report_id"] not in sources:
                    source = self.row("WS_REPORT", event["report_id"])
                    sources[event["report_id"]] = source_digest(source["payload"]) if source else None
                if sources[event["report_id"]] != event["source_sha256"]:
                    raise ReviewError("STATE_CORRUPT", "Confirmed case source differs")
                result.append(event)
        return result

    def stale(self, proposal, context, revision, cases=None):
        if (proposal["source_sha256"] != context["source_sha256"] or proposal["review_revision"] != revision
                or self.decision_stale(proposal, context)):
            return True
        if proposal.get("case_policy") == "OA_RETRIEVAL_V1":
            from oa.sanity_ai_cases import CaseRetriever
            if "criteria_sha256" in proposal:
                if proposal["criteria_sha256"] != digest(self.current_criteria(context)):
                    return True
            resolved = CaseRetriever(self.db, self.ws).resolve_references(
                context, proposal["references"], stage="REVIEW")
            return bool(resolved["stale_ids"] or resolved["over_budget_ids"])
        cases = self.cases(context) if cases is None else cases
        if proposal.get("case_catalog_sha256") != digest([case["id"] for case in cases]):
            return True
        for reference in proposal["references"]:
            current, _ = self.current(reference["report_id"], reference.get("group_id"))
            source = self.row("WS_REPORT", reference["report_id"])
            if (not current or current["id"] != reference["id"] or not source
                    or source_digest(source["payload"]) != reference["source_sha256"]):
                return True
        return False

    def view(self, user_id, block_id, version_id, report_id):
        context = self.context(user_id, block_id, version_id, report_id)
        current, revision = self.checked_current(context)
        history = self.db.execute("SELECT payload FROM {oa} WHERE record_type=%s "
                                  "AND " + self.report_ref + "=%s AND COALESCE(payload->>'group_id','')=%s "
                                  "ORDER BY (payload->>'revision')::int DESC,created_at DESC,workspace_key LIMIT 50",
                                  (self.event_kind, record_id("WS_REPORT", report_id), context.get("group_id", ""))).fetchall()
        proposal = self.db.execute("SELECT payload FROM {oa} WHERE record_type=%s "
                                   "AND " + self.report_ref + "=%s AND COALESCE(payload->>'group_id','')=%s "
                                   "ORDER BY created_at DESC,workspace_key LIMIT 1",
                                   (self.proposal_kind, record_id("WS_REPORT", report_id), context.get("group_id", ""))).fetchone()
        cases = self.cases(context)
        for row in history:
            validate_stored_review_exception(row["payload"])
        value = proposal["payload"] if proposal else None
        return shared_case_views({"current": current, "revision": revision, "history": [row["payload"] for row in history],
                "facts": context["facts"], "ready": context["ready"], "can_review": context["can_review"],
                "cases": cases[:50], "case_count": len(cases), "reusable_count": sum(case["reusable"] for case in cases),
                "cases_displayed": min(50, len(cases)), "proposal": {**value, "stale": self.stale(value, context, revision, cases)} if value else None,
                "corporate_ai_connected": False, "current_stale": bool(current and self.decision_stale(current, context)),
                "blocked_reason": context.get("blocked_reason")})

    def save(self, user_id, block_id, version_id, report_id, *, conclusion, reason, criteria, conditions,
             reusable, compatible_versions, expected_revision, request_id, proposal_id=None, exception=None):
        context = self.context(user_id, block_id, version_id, report_id, write=True)
        if (conclusion not in ("PASS", "FAIL", "DEFER") or type(expected_revision) is not int or expected_revision < 0
                or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000
                or not isinstance(criteria, str) or len(criteria) > 2000 or type(reusable) is not bool
                or type(compatible_versions) is not bool or not isinstance(conditions, dict)):
            raise ReviewError("INVALID_REQUEST", "Review fields are invalid")
        exception = normalize_review_exception(exception)
        if exception is not None and (conclusion == "DEFER" or reusable):
            raise ReviewError("INVALID_EXCEPTION", "An exception is a final human case, not an automatic rule approval")
        if conclusion != "DEFER" and not context["ready"]:
            raise ReviewError("REVIEW_BLOCKED", "Resolve missing or incomplete evidence before a final decision")
        if reusable:
            mandatory = {key for key in ("rule", "mode") if key in context["facts"]}
            if (conclusion == "DEFER" or not criteria.strip() or not conditions or "rule" not in mandatory
                    or not mandatory <= set(conditions) or not context["scope"]["item_id"] or not context["scope"]["rule_version"]
                    or any(key not in context["facts"] or value != context["facts"][key] for key, value in conditions.items())):
                raise ReviewError("INVALID_CRITERIA", "Confirm explicit conditions from the source facts")
        elif conditions or criteria.strip():
            raise ReviewError("INVALID_CRITERIA", "One-time decisions have no reusable conditions")
        request_id = str(uuid_value(request_id))
        proposal_id = str(uuid_value(proposal_id)) if proposal_id else None
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "conclusion": conclusion, "reason": reason.strip(), "criteria": criteria.strip(), "conditions": conditions,
                   "reusable": reusable, "compatible_versions": compatible_versions, "expected_revision": expected_revision,
                   "proposal_id": proposal_id, "exception": exception}
        if context.get("group_id"):
            command.update(group_id=context["group_id"], cluster_revision=context["cluster_revision"])
        # Ten-user workflow: serialize judgment/criteria changes so references cannot
        # be superseded between validation and commit; PoR writes remain independent.
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        context = self.locked_context(context, user_id, block_id, version_id, report_id)
        if conclusion != "DEFER" and not context["ready"]:
            raise ReviewError("REVIEW_BLOCKED", "Resolve missing or incomplete evidence before a final decision")
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-request-" + request_id),))
        previous = self.row(self.event_kind, request_id)
        if previous:
            # Receipts written before exception annotations had no such key.
            # Treat only that historical absence as null for replay comparison;
            # preserve the immutable payload exactly as it was stored.
            previous_command = previous["payload"]["command"]
            if {**previous_command, "exception": previous_command.get("exception")} != command:
                raise ReviewError("REQUEST_CONFLICT", "Review UUID has different contents")
            validate_stored_review_exception(previous["payload"])
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
                 "reusable": reusable, "compatible_versions": compatible_versions, "exception": exception, "proposal_id": proposal_id,
                 "proposal_conclusion": proposal["payload"]["conclusion"] if proposal else None,
                 "proposal_agreed": proposal["payload"]["conclusion"] == conclusion if proposal else None,
                 "source_version": context["version"]}
        if proposal and proposal["payload"].get("case_policy") == "OA_RETRIEVAL_V1":
            supplied = proposal["payload"]
            event.update(case_policy="OA_RETRIEVAL_V1", ai_proposal={key: supplied[key] for key in
                         ("id", "conclusion", "reason", "evidence_ids", "reference_ids", "criterion_ids",
                          "model_metadata", "retrieval")})
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
