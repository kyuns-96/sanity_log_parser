"""Pure clustering SDK contract and validation, shared without OA stores or DB."""
from abc import ABC, abstractmethod
import re

from common.sanity_clustering import assignment, check, validate_rules
from common.sanity_common import digest
from common.sanity_integration_contracts import AdvisorContext, ClusteringCase, ClusteringProposalV2
import common.sanity_workspace_rationale as rationale

# Resource guards, not the expected/business report size.
MAX_RECORDS, MAX_GROUPS = 1000000, 1000000


def evidence(context):
    records = context["report"].get("records", [])
    check(len(records) <= MAX_RECORDS, "CLUSTER_LIMIT", "Explicitly extend the complete-input limit")
    check(len(records) == context["report"].get("record_count"), "STATE_CORRUPT")
    ids = [record.get("id") for record in records]
    check(all(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", key) for key in ids)
          and len(set(ids)) == len(ids), "STATE_CORRUPT", "Evidence IDs must be unique and original")
    return records


def partition(groups, records):
    check(isinstance(groups, list) and 1 <= len(groups) <= MAX_GROUPS, "INVALID_PARTITION")
    expected, seen, result, names = {record["id"] for record in records}, [], [], set()
    for group in groups:
        check(isinstance(group, dict) and set(group) == {"id", "label", "record_ids"}, "INVALID_PARTITION")
        key, label, ids = group["id"], group["label"], group["record_ids"]
        check(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", key)
              and key not in names and isinstance(label, str) and 1 <= len(label.strip()) <= 120
              and isinstance(ids, list) and ids and all(isinstance(rid, str) for rid in ids), "INVALID_PARTITION")
        names.add(key); seen.extend(ids)
        result.append({"id": key, "label": label.strip(), "record_ids": sorted(ids)})
    check(len(seen) == len(set(seen)) and set(seen) == expected, "INVALID_PARTITION",
          "Every original evidence must occur in exactly one group")
    return sorted(result, key=lambda group: group["id"])


def identity_fields_valid(fields, records):
    check(records, "CLUSTER_BLOCKED", "No received evidence to group")
    validate_rules([{"identity_fields": fields, "where": {}, "reason": "Workspace grouping identity",
                     "evidence_ids": [records[0]["id"]]}])
    check(not any(record["fields"].get("mode") for record in records) or "mode" in fields,
          "INVALID_CLUSTER", "Known modes must remain part of clustering identity")
    return sorted(fields)


def signature(record, fields, scope, report_rule):
    rule = {"identity_fields": fields, "where": {}, "reason": "Confirmed identity", "evidence_ids": [record["id"]]}
    value = assignment(record["fields"], scope, [rule])
    return digest([report_rule, value["identity"]]) if value["group_id"] else None


class ClusteringAdvisor(ABC):
    @abstractmethod
    def propose(self, context: AdvisorContext, cases: list[ClusteringCase],
                identity_fields: list[str]) -> ClusteringProposalV2:
        """Return a V2 proposal without changing inputs or recording approval.

        No prior case is required. Assign every original record exactly once and
        supply its group's rationale. The caller validates new company outputs
        with validate_clustering_advice(..., require_rationales=True). This hook
        does not connect a company SDK to the OA workspace or a VWP worker.
        """


def validate_clustering_advice(value, context, cases, *, require_rationales=False):
    keys = {"groups", "reason", "reference_ids"}
    check(isinstance(value, dict) and set(value) in (keys, keys | {"group_rationales"}), "INVALID_ADVICE")
    check(not require_rationales or "group_rationales" in value, "INVALID_RATIONALE")
    check(isinstance(value["reason"], str) and 1 <= len(value["reason"].strip()) <= 2000, "INVALID_ADVICE")
    refs = value["reference_ids"]
    check(isinstance(refs, list) and all(isinstance(key, str) for key in refs)
          and len(set(refs)) == len(refs) and set(refs) <= {case["id"] for case in cases}, "INVALID_ADVICE")
    check(len(refs) <= 100, "CASE_LIMIT")
    groups = partition(value["groups"], evidence(context))
    if "group_rationales" in value:
        rationale.validate_group_rationales(value["group_rationales"], groups, cases, refs)
    return {**value, "groups": groups}
