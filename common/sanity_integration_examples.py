"""Small, synthetic adapter packets. No SDK, DB, environment or company imports.

Historical approvals below are fixture data, never real review events. Each call
returns independent objects. These packets are not registered parsers, production
job envelopes, a model benchmark, or a substitute for company report validation.
Only decision inputs and selected audit fields are illustrated.
"""
from copy import deepcopy

from common.sanity_common import digest
from common.sanity_integration_contracts import CollectionPlan
from common.sanity_review_advice import report_facts


def collection_plan_example() -> CollectionPlan:
    return {
        "schema_version": 1,
        "run": {"run_id": "synthetic-run", "project": "synthetic-project",
                "design": "synthetic-block", "stage": "sanity",
                "tool": "synthetic", "tool_version": "1"},
        "inventory": {
            "confirmed": False, "source": "synthetic-inventory-source", "version": "1",
            "units": [{"review_unit_id": "synthetic-unit", "item_id": "synthetic_item",
                       "report": "reports/check.txt", "parser": "synthetic_text_v1",
                       "rule_version": "1", "condition": {"mode": "M1"},
                       "identity_fields": ["object", "check_point", "mode"],
                       "execution_status": "COMPLETED", "applicable": True, "exclusion": None}],
        },
    }


def _context(prefix):
    messages = ["Clock waveform is undefined at synthetic/clock/A.",
                "Clock waveform is undefined at synthetic/clock/B.",
                "Data width mismatch at synthetic/data/C: expected 16, observed 8."]
    records = [{"id": f"{prefix}-record-{index}", "line_start": index, "line_end": index,
                "raw": message, "check_status": "FAIL", "fields": {
                    "message": message, "object": f"synthetic/object/{index}",
                    "check_point": "constraint", "mode": "M1", "severity": "WARNING",
                    "code": "SYNTHETIC-CLOCK" if index < 3 else "SYNTHETIC-WIDTH"}}
               for index, message in enumerate(messages, 1)]
    report = {"id": f"{prefix}-report", "project_id": "synthetic-project",
              "block_id": "synthetic-block", "version_id": "synthetic-version",
              "version_record_id": "00000000-0000-4000-8000-000000000001",
              "item_id": "synthetic_item", "family": "GCA", "parser": "synthetic_text_v1",
              "rule_version": "1", "eco": "chip_finish", "rule": "SYNTHETIC-RULE",
              "analysis_ready": True, "parse_status": "COMPLETE", "check_status": "FAIL",
              "record_count": len(records), "raw_report": "\n".join(messages) + "\n", "records": records}
    scope = {key: report[key] for key in ("project_id", "block_id", "item_id", "family", "parser", "rule_version", "eco")}
    scope["layout"] = "POST"
    return {"report": report, "version": {"id": "synthetic-version", "net_ver": "NET_SYNTHETIC",
                "revision": "SYNTHETIC_REVISION", "layout": "POST", "eco": "chip_finish"},
            "scope": scope, "facts": report_facts(report),
            "source_sha256": digest(report), "ready": True, "can_review": True}


def clustering_example(*, with_history=False):
    context = _context("current")
    ids = [record["id"] for record in context["report"]["records"]]
    reply = {"groups": [
        {"id": "clock", "label": "클록 정의 누락", "record_ids": ids[:2]},
        {"id": "width", "label": "데이터 폭 불일치", "record_ids": ids[2:]},
    ], "reason": "합성 로그 전체를 메시지 패턴에 따라 두 그룹으로 분류했습니다.", "reference_ids": [],
        "group_rationales": [
            {"group_id": "clock", "reason": "클록 정의 누락 패턴입니다. 폭 불일치와 구분하며 동일 원인은 미확인입니다.",
             "evidence_ids": ids[:2], "reference_groups": []},
            {"group_id": "width", "reason": "16비트 기대값과 8비트 관측값의 차이를 보고합니다. 클록 누락과 별도 검토합니다.",
             "evidence_ids": ids[2:], "reference_groups": []},
        ]}
    cases = []
    if with_history:
        old = _context("prior")
        case_id = "00000000-0000-4000-8000-000000000002"
        groups = [{"id": "prior-clock", "label": "담당자 확인 클록 그룹", "record_ids": ["prior-record-1", "prior-record-2"]},
                  {"id": "prior-width", "label": "담당자 확인 폭 그룹", "record_ids": ["prior-record-3"]}]
        rows = {}
        for group, original in zip(groups, reply["group_rationales"]):
            content = {"reason": "합성 검토자가 원본 패턴과 예외를 대조해 확정한 근거입니다.",
                       "evidence_ids": list(group["record_ids"]), "reference_groups": []}
            rows[group["id"]] = {"status": "CONFIRMED", "source": "HUMAN", "content": content,
                "proposal": {"source": "AI", "event_id": "00000000-0000-4000-8000-000000000003",
                    "content": {"reason": original["reason"], "evidence_ids": list(group["record_ids"]), "reference_groups": []}},
                "binding": {"source_sha256": old["source_sha256"], "cluster_event_id": case_id,
                    "cluster_revision": 2, "group_id": group["id"], "label": group["label"],
                    "membership_sha256": digest(sorted(group["record_ids"]))},
                "reviewed_by": {"id": "synthetic-reviewer", "name": "합성 검토자", "username": "synthetic-user", "role": "블록 담당자"},
                "reviewed_at": "2026-10-16T00:00:00+00:00", "change_reason": "합성 정정 사례"}
        cases.append({"id": case_id, "report_id": old["report"]["id"], "version_id": "synthetic-version",
            "revision": 2, "groups": groups, "identity_fields": ["object", "check_point", "mode"],
            "reason": "합성 사람 확정 사례", "reusable": False, "criteria": "", "compatible_versions": False,
            "source_sha256": old["source_sha256"], "records": old["report"]["records"],
            "report_rule": "SYNTHETIC-RULE", "rationales": rows})
        reply["reference_ids"] = [case_id]
        for row, group in zip(reply["group_rationales"], groups):
            row["reference_groups"] = [{"case_id": case_id, "group_id": group["id"],
                "reason": "같은 모드의 합성 확정 패턴과 사용자 정정 근거를 참고했습니다."}]
    return {"context": context, "cases": cases, "identity_fields": ["object", "check_point", "mode"], "reply": reply}


def group_review_example(*, with_history=False):
    packet = clustering_example(with_history=with_history)
    context, group = packet["context"], packet["reply"]["groups"][0]
    context["scope"]["target_type"] = "GROUP"
    context.update(group_id=group["id"], group_snapshot=deepcopy(group),
        group_evidence=deepcopy(context["report"]["records"][:2]),
        cluster_event_id="00000000-0000-4000-8000-000000000004", cluster_revision=3,
        membership_sha256=digest([{key: row[key] for key in ("id", "record_ids")} for row in packet["reply"]["groups"]]),
        blocked_reason=None)
    context["facts"] = report_facts({**context["report"], "records": context["group_evidence"]})
    rationale = packet["reply"]["group_rationales"][0]
    context["clustering_rationale"] = {"status": "CONFIRMED", "source": "AI",
        "reviewed_by": {"id": "synthetic-reviewer", "name": "합성 검토자", "username": "synthetic-user", "role": "블록 담당자"},
        "reviewed_at": "2026-10-16T00:00:00+00:00",
        "content": {key: deepcopy(rationale[key]) for key in ("reason", "evidence_ids", "reference_groups")},
        "binding": {"source_sha256": context["source_sha256"], "cluster_event_id": context["cluster_event_id"],
            "cluster_revision": 3, "group_id": group["id"], "label": group["label"],
            "membership_sha256": digest(sorted(group["record_ids"]))}}
    cases = []
    reply = {"conclusion": "DEFER", "reason": "현재 계약에 적용할 사람 확정 사례가 없어 담당자의 판정이 필요합니다.", "reference_ids": []}
    if with_history:
        old = packet["cases"][0]
        case_id = "00000000-0000-4000-8000-000000000005"
        cases.append({"id": case_id, "report_id": old["report_id"], "version_id": "synthetic-version",
            "conclusion": "PASS", "reason": "합성 검사 조건에서 담당자가 확인한 허용 예외입니다.",
            "criteria": "", "conditions": {}, "reusable": False, "compatible_versions": False,
            "revision": 1, "actor": {"id": "synthetic-reviewer", "name": "합성 검토자", "username": "synthetic-user", "role": "블록 담당자"},
            "at": "2026-10-16T00:00:00+00:00", "source_sha256": old["source_sha256"],
            "source_version": deepcopy(context["version"]), "scope": deepcopy(context["scope"]),
            "group_id": "prior-clock", "group_snapshot": deepcopy(old["groups"][0]),
            "cluster_event_id": old["id"], "cluster_revision": old["revision"],
            "membership_sha256": digest([{key: row[key] for key in ("id", "record_ids")} for row in old["groups"]]),
            "clustering_rationale": deepcopy(old["rationales"]["prior-clock"])})
        reply = {"conclusion": "PASS", "reason": "같은 합성 모드에서 사람이 확인한 허용 예외를 참고한 제안입니다. 사용자 확정은 별도입니다.",
                 "reference_ids": [case_id]}
    return {"context": context, "cases": cases, "reply": reply}
