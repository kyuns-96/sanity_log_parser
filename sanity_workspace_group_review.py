"""Human judgments are bound to a confirmed partition, group and source digest."""
from sanity_clustering import check
from sanity_common import digest
from sanity_review_advice import report_facts
from sanity_workspace import record_id
from sanity_workspace_clustering import WorkspaceClusteringStore
from sanity_workspace_review import WorkspaceReviewStore
from sanity_workspace_rationale import complete


class WorkspaceGroupReviewStore(WorkspaceReviewStore):
    max_cases = 100000
    head_kind, event_kind, proposal_kind = "WS_GROUP_REVIEW", "WS_GROUP_REVIEW_EVENT", "WS_GROUP_REVIEW_PROPOSAL"
    report_ref, event_ref = "workspace_pipeline_report_ref", "workspace_pipeline_event_ref"

    def __init__(self, db, group_id, expected_cluster_revision=None):
        super().__init__(db)
        self.group_id, self.expected_cluster_revision = group_id, expected_cluster_revision
        self.clustering = WorkspaceClusteringStore(db)

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
        result, valid_sources = [], {}
        for case in super().cases(context):
            if case["report_id"] not in valid_sources:
                cluster, _ = self.clustering.current(case["report_id"])
                valid_sources[case["report_id"]] = (cluster["id"] if cluster and cluster["confirmed"] and complete(cluster)
                    and self.clustering.references_current(cluster, exclude_report=context["report"]["id"]) else None)
            if valid_sources[case["report_id"]] == case["cluster_event_id"]:
                result.append(case)
        return result
