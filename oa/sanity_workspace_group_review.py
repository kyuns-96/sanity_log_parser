"""Human judgments are bound to a confirmed partition, group and source digest."""
from common.sanity_clustering import check
from common.sanity_ai_contracts import validate_review_v2
from common.sanity_common import digest
from common.sanity_db import lock_key, uuid_value
from common.sanity_review_advice import report_facts
from oa.sanity_workspace import record_id, utcnow
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_review import WorkspaceReviewStore, validate_ai_receipt
from common.sanity_workspace_rationale import complete


class WorkspaceGroupReviewStore(WorkspaceReviewStore):
    max_cases = 100000
    head_kind, event_kind, proposal_kind = "WS_GROUP_REVIEW", "WS_GROUP_REVIEW_EVENT", "WS_GROUP_REVIEW_PROPOSAL"
    report_ref, event_ref = "workspace_pipeline_report_ref", "workspace_pipeline_event_ref"

    def __init__(self, db, group_id, expected_cluster_revision=None, ws=None, criteria_provider=None):
        super().__init__(db, ws=ws)
        self.group_id, self.expected_cluster_revision = group_id, expected_cluster_revision
        self.clustering = WorkspaceClusteringStore(db, ws=self.ws)
        self.criteria_provider = criteria_provider

    def current_criteria(self, context):
        if self.criteria_provider is not None:
            return self.criteria_provider(context)
        from oa.sanity_ai_criteria import load_criteria
        return load_criteria(context)

    def head_key(self, report_id, group_id=None):
        return "group-" + digest([report_id, group_id or self.group_id])

    def current(self, report_id, group_id=None):
        event, revision = super().current(report_id, group_id)
        check(not event or event["group_id"] == (group_id or self.group_id), "STATE_CORRUPT")
        return event, revision

    def context(self, user_id, block_id, version_id, report_id, write=False):
        context = super().context(user_id, block_id, version_id, report_id, write)
        cluster, revision = self.clustering.current(report_id)
        check(cluster is not None, "CLUSTER_NOT_READY")
        check(cluster["source_sha256"] == context["source_sha256"], "STATE_CORRUPT")
        group = next((value for value in cluster["groups"] if value["id"] == self.group_id), None)
        removed = group is None
        if removed:
            old, _ = self.current(report_id)
            check(old is not None, "NOT_FOUND")
            group = old["group_snapshot"]
        member_ids = set(group["record_ids"])
        records = [value for value in context["report"]["records"] if value["id"] in member_ids]
        check(len(records) == len(group["record_ids"]), "STATE_CORRUPT")
        valid = cluster["confirmed"] and complete(cluster) and not removed and not self.clustering.stale(cluster, context)
        context.update(group_id=self.group_id, group_snapshot=group, cluster_revision=revision,
                       cluster_event_id=cluster["id"], membership_sha256=cluster["membership_sha256"],
                       clustering_rationale=cluster.get("rationales", {}).get(self.group_id),
                       ready=context["ready"] and valid, can_review=context["can_review"] and valid,
                       blocked_reason="GROUP_REMOVED" if removed else "CLUSTER_NOT_CONFIRMED" if not valid else None)
        context["scope"] = {**context["scope"], "target_type": "GROUP"}
        context["facts"] = report_facts({**context["report"], "records": records})
        context["group_evidence"] = records
        return context

    @staticmethod
    def metadata(context):
        return {**WorkspaceReviewStore.metadata(context), **{key: context[key] for key in (
            "group_id", "group_snapshot", "cluster_revision", "cluster_event_id", "membership_sha256")},
            "clustering_rationale": context["clustering_rationale"],
            "cluster_event_record_id": str(record_id("WS_CLUSTER_EVENT", context["cluster_event_id"]))}

    def locked_context(self, context, user_id, block_id, version_id, report_id):
        latest = self.context(user_id, block_id, version_id, report_id, write=True)
        check(latest["blocked_reason"] is None, "CLUSTER_NOT_CONFIRMED")
        check(type(self.expected_cluster_revision) is int and self.expected_cluster_revision == latest["cluster_revision"],
              "REVISION_CONFLICT", "Reload the confirmed partition before judging a group")
        return latest

    def decision_stale(self, event, context):
        return (bool(context.get("blocked_reason")) or event["cluster_event_id"] != context["cluster_event_id"]
                or event["membership_sha256"] != context["membership_sha256"])

    def cases(self, context):
        if self.ai_case_policy:
            return super().cases(context)
        result, valid_sources = [], {}
        for case in super().cases(context):
            if case["report_id"] not in valid_sources:
                cluster, _ = self.clustering.current(case["report_id"])
                valid_sources[case["report_id"]] = (cluster["id"] if cluster and cluster["confirmed"] and complete(cluster)
                    and self.clustering.references_current(cluster, exclude_report=context["report"]["id"], context=context) else None)
            if valid_sources[case["report_id"]] == case["cluster_event_id"]:
                result.append(case)
        return result

    def apply_ai(self, user_id, block_id, version_id, report_id, *, expected_revision,
                 request_id, advice, cases, metadata, retrieval, criteria):
        """Save a validated direct-evidence proposal tied to the confirmed group."""
        from oa.sanity_ai_cases import CaseRetriever
        check(self.ai_case_policy, "AI_NOT_CONFIGURED")
        check(type(expected_revision) is int and expected_revision >= 0, "INVALID_REQUEST")
        rid = str(uuid_value(request_id))
        validate_ai_receipt(metadata, retrieval, cases)
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id,
                   "report_id": report_id, "group_id": self.group_id, "expected_revision": expected_revision,
                   "cluster_revision": self.expected_cluster_revision, "mode": "OA_AI",
                   "receipt_sha256": digest({"advice": advice, "cases": cases, "criteria": criteria,
                                             "metadata": metadata, "retrieval": retrieval})}
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        context = self.context(user_id, block_id, version_id, report_id, write=True)
        previous = self.row(self.proposal_kind, rid)
        if previous:
            check(previous["payload"]["command"] == command, "REQUEST_CONFLICT")
            return previous["payload"]
        context = self.locked_context(context, user_id, block_id, version_id, report_id)
        _, revision = self.checked_current(context)
        check(revision == expected_revision, "REVISION_CONFLICT")
        check(context["can_review"] and context["ready"], "REVIEW_BLOCKED")
        check(CaseRetriever(self.db, self.ws).revalidate(context, cases, stage="REVIEW"), "PROPOSAL_STALE")
        value = validate_review_v2(advice, context, cases, criteria)
        references = [case for case in cases if case["id"] in value["reference_ids"]]
        proposal = {**self.metadata(context), **value, "id": rid, "command": command,
                    "review_revision": revision, "at": utcnow().isoformat(), "actor": self.ws.user(user_id),
                    "references": references, "advisor": "OA_OPENAI_COMPATIBLE", "model_called": True,
                    "case_policy": "OA_RETRIEVAL_V1", "case_catalog_sha256": digest([case["id"] for case in cases]),
                    "input_case_count": len(cases), "input_cases": [{"id": case["id"], "revision": case["revision"],
                        "source_sha256": case["source_sha256"], "sha256": digest(case)} for case in cases],
                    "criteria_snapshot": criteria, "criteria_sha256": digest(criteria), "retrieval": retrieval,
                    "model_metadata": metadata, "input_sha256": metadata["input_sha256"]}
        self.ws.put(self.proposal_kind, rid, proposal, project=context["report"]["project_id"], block=block_id)
        return proposal
