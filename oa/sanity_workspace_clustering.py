"""Two-stage workspace classification, with immutable partitions and human cases.

The reference advisor remains available for synthetic workspaces. Trusted OA AI
receipts enter through apply_ai after the background worker validates its input.
"""
from collections import defaultdict
import re

from common.sanity_clustering import FIELDS, assignment, check, validate_rules
from common.sanity_ai_knowledge import shared_case_views
from common.sanity_clustering_advice import ClusteringAdvisor, MAX_RECORDS, MAX_GROUPS, evidence, partition, identity_fields_valid, signature, validate_clustering_advice
from common.sanity_common import digest
from common.sanity_submission import source_digest
from common.sanity_db import json_value, lock_key, uuid_value
from oa.sanity_workspace import WorkspaceStore, record_id, utcnow
from oa.sanity_workspace_review import WorkspaceReviewStore, validate_ai_receipt
import common.sanity_workspace_rationale as rationale

# Resource guards, not the expected/business report size. Typical is ~10,000;
# 100,000 and larger inputs use the same complete-input, paged workflow.
MAX_CASES = 1000
MAX_CASE_RECORDS = 1000000
MAX_CASE_BYTES = 128 * 1024 * 1024


def reference_rationales(groups, records, cases, fields, scope, report_rule):
    """Describe the actual reference engine, without claiming model inference."""
    index = {record["id"]: record for record in records}
    signatures = {key: signature(record, fields, scope, report_rule) for key, record in index.items()}
    historical, relationships, ambiguous = defaultdict(list), defaultdict(set), set()
    for case in cases:
        if not case["reusable"] or case["identity_fields"] != fields:
            continue
        fingerprints = {r["id"]: signature(r, fields, scope, case["report_rule"]) for r in case["records"]}
        occurrences = defaultdict(int)
        for group in case["groups"]:
            values = {fingerprints[key] for key in group["record_ids"]}
            if None in values:
                continue
            for value in values:
                relationships[value].add(tuple(sorted(values)))
                occurrences[value] += 1
                if occurrences[value] > 1:
                    ambiguous.add(value)
                historical[value].append({"case_id": case["id"], "group_id": group["id"],
                    "reason": "선택한 원본 필드가 일치하는 과거 확정 그룹을 참고했습니다. 의미상 같은 원인인지는 사람이 검토해야 합니다."})
    result = []
    for group in groups:
        values = {signatures[key] for key in group["record_ids"]}
        references = {(ref["case_id"], ref["group_id"]): ref for value in values for ref in historical.get(value, [])}
        check(len(references) <= 20, "CASE_LIMIT", "Too many references for a group; no silent truncation")
        common = []
        first = index[group["record_ids"][0]]["fields"]
        for field in fields:
            value = first.get(field)
            if value is not None and all(index[key]["fields"].get(field) == value for key in group["record_ids"]):
                common.append(f"{field}={str(value)[:120]}")
        reason = (", ".join(common) or "분류 필드 누락") + ". "
        conflicts = any(value in ambiguous or len(relationships.get(value, ())) > 1 for value in values if value is not None)
        reason += (f"과거 분류가 충돌하여 원본 {group['record_ids'][0]}를 개별 검토합니다. " if conflicts else
                   "과거 확정 그룹 관계를 참고한 초안입니다. " if references else
                   "필드 조합이 같은 로그의 초안입니다. " if None not in values else
                   "필드가 부족해 개별 검토합니다. ")
        reason += "다른 조합/관계와 구분하며, 동일 원인은 미확인입니다."
        result.append({"group_id": group["id"], "reason": reason, "evidence_ids": group["record_ids"][:3],
                       "reference_groups": list(references.values())})
    return result


class ConfirmedPartitionAdvisor(ClusteringAdvisor):
    def propose(self, context, cases, identity_fields):
        learned, sources = defaultdict(set), defaultdict(set)
        for case in cases:
            if not case["reusable"] or case["identity_fields"] != identity_fields:
                continue
            fingerprints = {record["id"]: signature(record, identity_fields, context["scope"], case["report_rule"])
                            for record in case["records"]}
            owners = defaultdict(set)
            for group in case["groups"]:
                values = frozenset(fingerprints[key] for key in group["record_ids"])
                if None in values:
                    continue
                for value in values:
                    owners[value].add(tuple(sorted(values)))
            # A human split of indistinguishable records is a counterexample,
            # not permission to infer membership from record order or line number.
            counts = defaultdict(int)
            for group in case["groups"]:
                for value in {fingerprints[key] for key in group["record_ids"]}:
                    counts[value] += 1
            for value, templates in owners.items():
                learned[value].update(templates)
                if counts[value] > 1:
                    learned[value].add(("AMBIGUOUS_SPLIT",))
                sources[value].add(case["id"])
        grouped, references, conflicts = defaultdict(list), set(), 0
        for record in evidence(context):
            value = signature(record, identity_fields, context["scope"], context["report"].get("rule"))
            templates = learned.get(value, set())
            references.update(sources.get(value, set()))
            if len(templates) == 1 and ("AMBIGUOUS_SPLIT",) not in templates:
                key = ["CONFIRMED_EXAMPLE", next(iter(templates))]
            elif templates:
                key = ["CONFLICT", record["id"]]; conflicts += 1
            else:
                key = ["EXACT_FIELDS", value] if value else ["MISSING_IDENTITY", record["id"]]
            gid = "wg-" + digest([context["scope"], key])[:40]
            grouped[gid].append(record["id"])
        groups = [{"id": key, "label": f"그룹 {index + 1}", "record_ids": values}
                  for index, (key, values) in enumerate(sorted(grouped.items()))]
        rationales = reference_rationales(groups, evidence(context), cases, identity_fields,
                                         context["scope"], context["report"].get("rule"))
        references = {ref["case_id"] for row in rationales for ref in row["reference_groups"]}
        return {"groups": groups, "reference_ids": sorted(references), "group_rationales": rationales,
                "reason": (f"확정 clustering 사례 {len(references)}건과 선택한 원본 필드를 참고한 초안입니다."
                           + (f" 충돌/구분 불가 로그 {conflicts}건은 개별 그룹으로 남겼습니다." if conflicts else ""))}


def ai_job_active(head):
    state = head["state"] if head else {}
    job = state.get("codex_jobs", {}).get(state.get("codex_job_id"), {})
    return job.get("status") in ("RUNNING", "RESULT_READY")


class WorkspaceClusteringStore:
    def __init__(self, db, ws=None):
        self.db, self.ws = db, ws if ws is not None else WorkspaceStore(db)
        self.reviews = WorkspaceReviewStore(db, ws=self.ws)

    @property
    def ai_case_policy(self):
        return getattr(self.ws, "case_policy", None) == "OA_RETRIEVAL_V1"

    def context(self, user_id, block_id, version_id, report_id, write=False):
        context = self.reviews.context(user_id, block_id, version_id, report_id, write)
        evidence(context)
        return context

    def current(self, report_id):
        head = self.reviews.row("WS_CLUSTER", report_id)
        if not head:
            return None, 0
        if not head["state"].get("event_id"):
            check(head["version"] == 0 and bool(head["state"].get("task") or head["state"].get("codex_jobs")), "STATE_CORRUPT")
            return None, 0
        row = self.reviews.row("WS_CLUSTER_EVENT", head["state"].get("event_id"))
        check(row and row["record_id"] == head["workspace_pipeline_event_ref"]
              and row["payload"]["revision"] == head["version"] and row["payload"]["report_id"] == report_id, "STATE_CORRUPT")
        return rationale.unpack(row["payload"]), head["version"]

    def catalog(self, context):
        if self.ai_case_policy:
            from oa.sanity_ai_cases import CaseRetriever
            retriever = CaseRetriever(self.db, self.ws)
            selected = retriever.select(context, stage="CLUSTER")["cases"]
            current, _ = self.current(context["report"]["id"])
            # A still-valid cited group must remain editable/confirmable even
            # when unrelated cases move it outside the ranked selection window.
            pinned = retriever.resolve_references(context, current.get("references", []) if current else [],
                                                  stage="CLUSTER")["cases"]
            return list({case["id"]: case for case in [*selected, *pinned]}.values())
        source = "FROM {oa} h " \
            "JOIN {oa} e ON e.record_id=h.workspace_pipeline_event_ref "
        source += (
            "JOIN {oa} s ON s.record_id=h.workspace_pipeline_report_ref "
            "WHERE h.record_type='WS_CLUSTER' AND h.workspace_block_ref=%s AND e.payload->'scope'=%s "
            "AND e.payload->>'report_id'<>%s AND e.payload->>'confirmed'='true' "
            "AND (e.payload->>'version_id'=%s OR e.payload->>'compatible_versions'='true') ")
        parameters = (record_id("WS_BLOCK", context["report"]["block_id"]), json_value(context["scope"]),
                      context["report"]["id"], context["report"]["version_id"])
        budget = self.db.execute("SELECT count(*) AS cases,COALESCE(sum(COALESCE(jsonb_array_length(s.payload->'records'),"
                                 "(s.payload->>'record_count')::bigint)),0) AS records,"
                                 "COALESCE(sum(octet_length(s.payload::text)+octet_length(e.payload::text)"
                                 "+COALESCE((s.payload->>'source_bytes')::bigint,0)"
                                 "+COALESCE((e.payload->>'rationale_bytes')::bigint,0)),0) AS bytes "
                                 + source, parameters).fetchone()
        check(budget["cases"] <= MAX_CASES and budget["records"] <= MAX_CASE_RECORDS and budget["bytes"] <= MAX_CASE_BYTES, "CASE_LIMIT")
        rows = self.db.execute("SELECT e.payload,h.workspace_pipeline_report_ref AS report_ref " + source +
                              "ORDER BY e.created_at DESC,e.workspace_key", parameters).fetchall()
        result, records_total = [], 0
        for row in rows:
            event = rationale.unpack(row["payload"])
            if event["version_id"] != context["report"]["version_id"] and not event["compatible_versions"]:
                continue
            if not self.references_current(event, exclude_report=context["report"]["id"], context=context):
                continue
            # Reject stale/self-dependent examples before materializing their
            # potentially 100,000-row source. The preceding byte guard is kept.
            source_row = self.reviews.row("WS_REPORT", event["report_id"])
            check(source_row and source_row["record_id"] == row["report_ref"], "STATE_CORRUPT")
            source = source_row["payload"]
            check(event["source_sha256"] == source_digest(source), "STATE_CORRUPT")
            if source.get('source_storage') == 'PARTS_V2':
                from oa.sanity_workspace_sources import hydrate
                source = hydrate(self.db,source)
            records = evidence({"report": source})
            partition(event["groups"], records)
            records_total += len(records)
            check(records_total <= MAX_CASE_RECORDS, "CASE_LIMIT")
            result.append({**event, "records": records, "report_rule": source.get("rule")})
        return result

    def catalog_hash(self, context):
        return digest([case["id"] for case in self.catalog(context)])

    def references_current(self, event, exclude_report=None, *, context=None):
        head = self.reviews.row("WS_CLUSTER", event["report_id"])
        if ai_job_active(head) or head and head["state"].get("task", {}).get("status") in ("PENDING", "RUNNING", "FAILED", "STALE"):
            return False
        if event.get("case_policy") == "OA_RETRIEVAL_V1":
            from oa.sanity_ai_cases import CaseRetriever
            if any(reference["report_id"] == exclude_report for reference in event["references"]):
                return False
            reference_context = {"scope": event["scope"], "report": {"id": exclude_report or event["report_id"],
                                                          "version_id": event["version_id"]}}
            if context and context.get("_actor_user_id"):
                reference_context["_actor_user_id"] = context["_actor_user_id"]
            resolved = CaseRetriever(self.db, self.ws).resolve_references(reference_context, event["references"], stage="CLUSTER")
            return not resolved["stale_ids"] and not resolved["over_budget_ids"]
        pending, visited = list(event["references"]), set()
        while pending:
            reference = pending.pop()
            if reference["report_id"] == exclude_report:
                return False
            if reference["id"] in visited:
                continue
            visited.add(reference["id"])
            check(len(visited) <= MAX_CASES, "CASE_LIMIT")
            current, _ = self.current(reference["report_id"])
            if not current or not current["confirmed"] or current["id"] != reference["id"]:
                return False
            head = self.reviews.row("WS_CLUSTER", reference["report_id"])
            if ai_job_active(head) or head["state"].get("task", {}).get("status") in ("PENDING", "RUNNING", "FAILED", "STALE"):
                return False
            source = self.reviews.row("WS_REPORT", reference["report_id"])
            check(source and source_digest(source["payload"]) == current["source_sha256"], "STATE_CORRUPT")
            pending.extend(current["references"])
        return True

    def stale(self, event, context):
        return (event["source_sha256"] != context["source_sha256"] or not self.references_current(event, context=context)
                or not event["confirmed"] and bool(event["references"])
                and event.get("case_policy") != "OA_RETRIEVAL_V1"
                and event["case_catalog_sha256"] != self.catalog_hash(context))

    def view(self, user_id, block_id, version_id, report_id):
        context = self.context(user_id, block_id, version_id, report_id)
        current, revision = self.current(report_id)
        if current:
            check(current["source_sha256"] == context["source_sha256"], "STATE_CORRUPT")
            partition(current["groups"], evidence(context))
        cases = self.catalog(context)
        history_bytes = self.db.execute("SELECT COALESCE(sum(octet_length(h.payload::text)"
            "+COALESCE((h.payload->>'rationale_bytes')::bigint,0)),0) AS bytes FROM "
            "(SELECT payload FROM {oa} WHERE record_type='WS_CLUSTER_EVENT' AND workspace_pipeline_report_ref=%s "
            "ORDER BY created_at DESC,workspace_key LIMIT 30) h", (record_id("WS_REPORT", report_id),)).fetchone()["bytes"]
        check(history_bytes <= MAX_CASE_BYTES, "CASE_LIMIT", "Use paged history for large classification revisions")
        history = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CLUSTER_EVENT' "
            "AND workspace_pipeline_report_ref=%s ORDER BY created_at DESC,workspace_key LIMIT 30",
            (record_id("WS_REPORT", report_id),)).fetchall()
        summaries = pipeline_summaries(self.db, [report_id], ws=self.ws, user_id=user_id).get(report_id)
        return shared_case_views({"current": current, "revision": revision, "ready": context["ready"], "can_edit": context["can_review"],
                "stale": bool(current and self.stale(current, context)), "history": [rationale.unpack(row["payload"]) for row in history],
                "case_count": len(cases), "reusable_count": sum(case["reusable"] for case in cases),
                "summary": summaries, "corporate_ai_connected": False, "record_count": len(evidence(context))})

    def apply_ai(self, user_id, block_id, version_id, report_id, *, expected_revision,
                 request_id, advice, cases, metadata, retrieval, identity_fields=None):
        """Publish an already obtained model receipt, never call a model or confirm it.

        The background worker owns config/input/criterion pinning. This method
        reauthorizes and checks the source, partition revision and supplied case
        revisions under the same lock used by human edits and confirmations.
        """
        from oa.sanity_ai_cases import CaseRetriever
        check(self.ai_case_policy, "AI_NOT_CONFIGURED")
        check(type(expected_revision) is int and expected_revision >= 0, "INVALID_REQUEST")
        rid = str(uuid_value(request_id))
        validate_ai_receipt(metadata, retrieval, cases)
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "action": "PROPOSE", "mode": "OA_AI", "expected_revision": expected_revision,
                   "identity_fields": identity_fields, "receipt_sha256": digest({"advice": advice,
                       "cases": cases, "metadata": metadata, "retrieval": retrieval})}
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        context = self.context(user_id, block_id, version_id, report_id, write=True)
        from oa.sanity_preserved_text import enrich
        from common.sanity_preserved_ai import receipt
        receipt(metadata, advice, enrich(self.db, context))
        previous = self.reviews.row("WS_CLUSTER_EVENT", rid)
        if previous:
            check(previous["payload"]["command"] == command, "REQUEST_CONFLICT")
            return rationale.unpack(previous["payload"])
        old, revision = self.current(report_id)
        check(revision == expected_revision, "REVISION_CONFLICT")
        check(context["ready"] and context["can_review"], "CLUSTER_BLOCKED")
        check(not old or old["source_sha256"] == context["source_sha256"], "STATE_CORRUPT")
        head = self.reviews.row("WS_CLUSTER", report_id)
        check(not ai_job_active(head) and (not head or head["state"].get("task", {}).get("status")
              not in ("PENDING", "RUNNING", "FAILED", "STALE")), "CLUSTER_TASK_ACTIVE")
        check(CaseRetriever(self.db, self.ws).revalidate(context, cases, stage="CLUSTER"), "PROPOSAL_STALE")
        fields = identity_fields if identity_fields is not None else old["identity_fields"] if old else ["object", "check_point", "mode"]
        fields = identity_fields_valid(fields, evidence(context))
        value = validate_clustering_advice(advice, context, cases, require_rationales=True)
        groups = value["groups"]
        references = [{key: case[key] for key in ("id", "report_id", "revision", "criteria", "source_sha256", "scope", "source_version")
                       if key in case}
                      for case in cases if case["id"] in value["reference_ids"]]
        event = {**WorkspaceReviewStore.metadata(context), "id": rid, "revision": revision + 1, "command": command,
                 "previous_event_id": old["id"] if old else None, "at": utcnow().isoformat(), "actor": self.ws.user(user_id),
                 "action": "PROPOSE", "groups": groups, "identity_fields": fields, "reason": value["reason"].strip(),
                 "confirmed": False, "reusable": False, "compatible_versions": False, "criteria": "",
                 "references": references, "advisor": "OA_OPENAI_COMPATIBLE", "model_called": True,
                 "case_policy": "OA_RETRIEVAL_V1", "case_catalog_sha256": digest([case["id"] for case in cases]),
                 "input_cases": [{"id": case["id"], "revision": case["revision"],
                                  "source_sha256": case["source_sha256"], "sha256": digest(case)} for case in cases],
                 "retrieval": retrieval, "model_metadata": metadata, "input_sha256": metadata["input_sha256"],
                 "initial_ai_event_id": rid, "rationale_schema": 2,
                 "membership_sha256": digest([{key: group[key] for key in ("id", "record_ids")} for group in groups])}
        proposed = rationale.validate_group_rationales(value["group_rationales"], groups, cases, value["reference_ids"])
        event["rationales"] = rationale.carry(None, event, proposed)
        self.ws.put("WS_CLUSTER_EVENT", rid, rationale.pack(event), project=event["project_id"], block=block_id)
        if head is None:
            self.ws.put("WS_CLUSTER", report_id, WorkspaceReviewStore.metadata(context), project=event["project_id"], block=block_id)
        self.db.execute("UPDATE {oa} SET state=%s,version=%s,updated_at=clock_timestamp() "
                        "WHERE record_type='WS_CLUSTER' AND workspace_key=%s",
                        (json_value({**(head["state"] if head else {}), "event_id": rid,
                                     "event_record_id": str(record_id("WS_CLUSTER_EVENT", rid))}), revision + 1, report_id))
        return event

    def change(self, user_id, block_id, version_id, report_id, *, action, expected_revision, request_id,
               reason="", groups=None, identity_fields=None, reusable=False, compatible_versions=False,
               criteria="", mode="CRITERIA", prepared=None, task=None, operation=None, mock_receipt=None,
               rationale_operation=None, _task_context=None):
        if _task_context is not None:
            # advance_task already authorized and pinned the complete source in
            # this transaction/lock. Avoid decoding another 100,000-row copy.
            check(action == "PROPOSE" and prepared is not None and task is not None
                  and (_task_context["report"]["id"], _task_context["report"]["block_id"],
                       _task_context["report"]["version_id"]) == (report_id, block_id, version_id), "INVALID_REQUEST")
        context = _task_context if _task_context is not None else self.context(user_id, block_id, version_id, report_id, write=True)
        check(action in ("PROPOSE", "EDIT", "CONFIRM", "WITHDRAW"), "INVALID_REQUEST")
        check(mode in ("CRITERIA", "CORPORATE", "MOCK_CODEX"), "INVALID_REQUEST")
        if mode == "CORPORATE":
            check(False, "AI_NOT_CONFIGURED", "Corporate proposals require a validated OA background job")
        if mode == "MOCK_CODEX":
            check(action == "PROPOSE" and isinstance(mock_receipt, dict) and prepared is None
                  and task is None and operation is None, "AI_NOT_CONFIGURED")
        else:
            check(mock_receipt is None, "INVALID_REQUEST")
        check(type(expected_revision) is int and expected_revision >= 0 and type(reusable) is bool
              and type(compatible_versions) is bool and isinstance(reason, str) and len(reason) <= 2000
              and isinstance(criteria, str) and len(criteria) <= 2000, "INVALID_REQUEST")
        check(action in ("PROPOSE", "CONFIRM") or bool(reason.strip()), "INVALID_REQUEST")
        rid = str(uuid_value(request_id))
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "action": action, "expected_revision": expected_revision, "reason": reason.strip(), "groups": groups,
                   "identity_fields": identity_fields, "reusable": reusable, "compatible_versions": compatible_versions,
                   "criteria": criteria.strip(), "mode": mode}
        if operation is not None:
            check(action == "EDIT" and groups is None, "INVALID_REQUEST")
            command["operation"] = operation
        if rationale_operation is not None:
            check(action == "EDIT" and groups is None and operation is None, "INVALID_REQUEST")
            command["rationale_operation"] = rationale_operation
        if mode == "MOCK_CODEX":
            # Bind UUID retries to the exact externally obtained result, never another call.
            command["mock_receipt_sha256"] = digest(mock_receipt)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        if self.ai_case_policy:
            context = self.context(user_id, block_id, version_id, report_id, write=True)
        previous = self.reviews.row("WS_CLUSTER_EVENT", rid)
        if previous:
            check(previous["payload"]["command"] == command, "REQUEST_CONFLICT")
            return rationale.unpack(previous["payload"])
        old, revision = self.current(report_id)
        check(revision == expected_revision, "REVISION_CONFLICT")
        head = self.reviews.row("WS_CLUSTER", report_id)
        check(not ai_job_active(head) or mode == "MOCK_CODEX" and head["state"].get("codex_job_id") == rid
              and head["state"]["codex_jobs"][rid]["status"] == "RESULT_READY", "CLUSTER_TASK_ACTIVE")
        check(task is not None or not head or head["state"].get("task", {}).get("status") not in
              ("PENDING", "RUNNING", "FAILED", "STALE"), "CLUSTER_TASK_ACTIVE")
        check(not old or old["source_sha256"] == context["source_sha256"], "STATE_CORRUPT")
        cases = self.catalog(context)
        references, advisor = [], "HUMAN_EDIT"
        fields = old["identity_fields"] if old else ["object", "check_point", "mode"]
        if action == "PROPOSE":
            fields = identity_fields_valid(identity_fields if identity_fields is not None else fields, evidence(context))
            if mode == "MOCK_CODEX":
                from devtools.sanity_codex_mock import validate_receipt
                value = validate_receipt(mock_receipt, context, cases, fields)
            else:
                value = validate_clustering_advice(prepared if prepared is not None else
                    ConfirmedPartitionAdvisor().propose(context, cases, fields), context, cases)
            groups, reason = value["groups"], value["reason"]
            references = [{key: case[key] for key in ("id", "report_id", "revision", "criteria", "source_sha256")}
                          for case in cases if case["id"] in value["reference_ids"]]
            advisor = "CODEX_SYNTHETIC_FEEDBACK_V1" if mode == "MOCK_CODEX" else "CONFIRMED_PARTITION_V1"
        else:
            check(old is not None, "CLUSTER_NOT_READY")
            if action != "EDIT":
                check(groups is None and identity_fields is None, "INVALID_REQUEST")
                groups = old["groups"]
                references = old["references"]
                advisor = old["advisor"]
            else:
                check(identity_fields is None, "INVALID_REQUEST")
                if rationale_operation is not None:
                    groups = old["groups"]
                if operation is not None:
                    from oa.sanity_workspace_pipeline import apply_operation
                    groups = apply_operation(old["groups"], operation)
        groups = partition(groups, evidence(context))
        if action == "EDIT" and operation is not None and groups == old["groups"]:
            return old
        confirmed = action == "CONFIRM"
        if confirmed:
            check(context["ready"], "CLUSTER_BLOCKED")
            check(not self.stale(old, context), "PROPOSAL_STALE")
            check(not reusable or bool(criteria.strip()), "INVALID_CRITERIA")
            check(reusable or not compatible_versions and not criteria.strip(), "INVALID_CRITERIA")
        else:
            check(not reusable and not compatible_versions and not criteria.strip(), "INVALID_CRITERIA")
        event = {**WorkspaceReviewStore.metadata(context), "id": rid, "revision": revision + 1, "command": command,
                 "previous_event_id": old["id"] if old else None, "at": utcnow().isoformat(), "actor": self.ws.user(user_id),
                 "action": action, "groups": groups, "identity_fields": fields, "reason": reason.strip(),
                 "confirmed": confirmed, "reusable": reusable, "compatible_versions": compatible_versions,
                 "criteria": criteria.strip(), "references": references, "advisor": advisor, "model_called": mode == "MOCK_CODEX",
                 "case_catalog_sha256": digest([case["id"] for case in cases]),
                 "membership_sha256": digest([{key: group[key] for key in ("id", "record_ids")} for group in groups])}
        proposed = (rationale.validate_group_rationales(value["group_rationales"], groups, cases, value["reference_ids"])
                    if action == "PROPOSE" and "group_rationales" in value else None)
        event["rationale_schema"] = 2
        event["rationales"] = rationale.carry(old if action != "PROPOSE" else None, event, proposed)
        if rationale_operation is not None:
            op = rationale_operation
            check(isinstance(op, dict) and op.get("kind") in ("SAVE", "ACCEPT")
                  and set(op) <= {"kind", "group_id", "content", "reviewed"}, "INVALID_RATIONALE")
            group = next((g for g in groups if g["id"] == op.get("group_id")), None)
            check(group is not None, "NOT_FOUND")
            key = group["id"]
            if op["kind"] == "SAVE":
                check(type(op.get("reviewed", False)) is bool, "INVALID_RATIONALE")
                content = rationale.validate_content(op.get("content"), group, cases)
                row = event["rationales"].get(key, {})
                if row.get("proposal") and "content" not in row["proposal"]:
                    row["proposal"] = dict(row["proposal"])
                    row["proposal"]["content"] = row["content"]
                row.update(content=content, source="HUMAN", status="DRAFT", binding=rationale.binding(event, group),
                           change_reason=reason.strip(), reviewed_by=None, reviewed_at=None,
                           previous_event_id=old["id"], edited_by=event["actor"], edited_at=event["at"])
                event["rationales"][key] = row
                if op.get("reviewed"):
                    rationale.adopt(row, event, group)
            else:
                check(set(op) == {"kind", "group_id"}, "INVALID_RATIONALE")
                row = event["rationales"].get(key)
                check(row is not None, "RATIONALE_REQUIRED")
                rationale.validate_content(row["content"], group, cases)
                row["change_reason"] = reason.strip()
                rationale.adopt(row, event, group)
        if action != "PROPOSE":
            # References are dependencies of the actual group explanations. A human
            # may explicitly replace an expired example; do not keep phantom refs.
            ids = {ref["case_id"] for row in event["rationales"].values()
                   for ref in row["content"]["reference_groups"]}
            snapshots = {ref["id"]: ref for ref in old["references"]}
            snapshots.update({case["id"]: {k: case[k] for k in
                             ("id", "report_id", "revision", "criteria", "source_sha256")} for case in cases})
            if old.get("rationale_schema") == 2 or event["rationales"]:
                check(ids <= set(snapshots), "INVALID_RATIONALE")
                event["references"] = [snapshots[key] for key in sorted(ids)]
        if confirmed:
            valid_ids = {case["id"] for case in cases}
            for group in groups:
                status = rationale.state(event, group, valid_ids)
                check(status in ("DRAFT", "CONFIRMED"), "RATIONALE_REQUIRED" if status == "MISSING" else "RATIONALE_STALE")
                row = event["rationales"][group["id"]]
                rationale.validate_content(row["content"], group, cases)
                # CONFIRM is the explicit, revision-pinned bulk adoption action.
                if status == "DRAFT":
                    rationale.adopt(row, event, group)
        if task is not None:
            event["task"] = task
        if mode == "MOCK_CODEX":
            event.update(model_metadata=mock_receipt["metadata"], initial_ai_event_id=rid)
        elif action != "PROPOSE" and old and old.get("model_metadata"):
            # A human edit/confirmation keeps the original proposal's provenance.
            event.update(model_metadata=old["model_metadata"], initial_ai_event_id=old["initial_ai_event_id"])
            for key in ("case_policy", "retrieval", "input_cases", "input_sha256"):
                if key in old:
                    event[key] = old[key]
        self.ws.put("WS_CLUSTER_EVENT", rid, rationale.pack(event), project=event["project_id"], block=block_id)
        if head is None:
            self.ws.put("WS_CLUSTER", report_id, WorkspaceReviewStore.metadata(context), project=event["project_id"], block=block_id)
        self.db.execute("UPDATE {oa} SET state=%s,version=%s,updated_at=clock_timestamp() "
                        "WHERE record_type='WS_CLUSTER' AND workspace_key=%s",
                        (json_value({**(head["state"] if head else {}), "event_id": rid, "event_record_id": str(record_id("WS_CLUSTER_EVENT", rid)),
                                     **({"task": task} if task is not None else {})}), revision + 1, report_id))
        return event


def pipeline_summaries(db, report_ids, compact=False, *, ws=None, user_id=None):
    if not report_ids:
        return {}
    clusters = db.execute("SELECT h.workspace_key,e.payload FROM {oa} h JOIN {oa} e ON e.record_id=h.workspace_pipeline_event_ref "
                          "WHERE h.record_type='WS_CLUSTER' AND h.workspace_key=ANY(%s)", (report_ids,)).fetchall()
    reviews = db.execute("SELECT e.payload FROM {oa} h JOIN {oa} e ON e.record_id=h.workspace_pipeline_event_ref "
                         "WHERE h.record_type='WS_GROUP_REVIEW' AND h.payload->>'report_id'=ANY(%s)", (report_ids,)).fetchall()
    decisions = {(row["payload"]["report_id"], row["payload"]["group_id"]): row["payload"] for row in reviews}
    result = {}
    for row in clusters:
        cluster, groups = rationale.unpack(row["payload"]), {}
        context = {"_actor_user_id": user_id} if user_id else None
        confirmed = cluster["confirmed"] and rationale.complete(cluster) and WorkspaceClusteringStore(db, ws=ws).references_current(cluster, context=context)
        for group in cluster["groups"]:
            event = decisions.get((row["workspace_key"], group["id"]))
            stale = bool(event and (not confirmed or event["cluster_event_id"] != cluster["id"]))
            groups[group["id"]] = {"conclusion": event["conclusion"] if event else None, "stale": stale,
                                    "revision": event["revision"] if event else 0}
        removed = [{"id": key[1], "label": value["group_snapshot"]["label"], "conclusion": value["conclusion"]}
                   for key, value in decisions.items() if key[0] == row["workspace_key"] and key[1] not in groups]
        result[row["workspace_key"]] = {"cluster_revision": cluster["revision"], "confirmed": confirmed,
            "total": len(groups), "reviewed": sum(value["conclusion"] in ("PASS", "FAIL") and not value["stale"] for value in groups.values()),
            "stale": sum(value["stale"] for value in groups.values()), "groups": groups, "removed": removed}
        if compact:
            result[row["workspace_key"]].update(groups={}, removed=[])
    return result
