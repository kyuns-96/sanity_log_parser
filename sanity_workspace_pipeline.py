"""Paged sequential review and resumable, non-model classification on WS_CLUSTER.

The mutable head owns a task; only a fully validated partition becomes an
immutable classification event. No company SDK or cross-bridge transport runs
here. Corporate mode remains explicitly unavailable.
"""
import copy

from sanity_clustering import check
from sanity_common import ReviewError, digest
from sanity_db import json_value, lock_key, uuid_value
from sanity_workspace import record_id, utcnow
from sanity_workspace_clustering import (WorkspaceClusteringStore, ConfirmedPartitionAdvisor,
    evidence, identity_fields_valid, partition, validate_clustering_advice, ai_job_active)
from sanity_workspace_clustering import reference_rationales
import sanity_workspace_rationale as rationale

ACTIVE = ("PENDING", "RUNNING", "FAILED", "STALE")
CHUNK_SIZE = 500


def apply_operation(groups, operation):
    """Resolve explicit ID selection against the full, pinned server partition."""
    check(isinstance(operation, dict) and operation.get("kind") in ("MOVE", "MERGE", "RENAME"), "INVALID_REQUEST")
    allowed = {"kind", "record_ids", "group_ids", "destination_id", "new_group_id", "label"}
    check(set(operation) <= allowed, "INVALID_REQUEST")
    result = copy.deepcopy(groups)
    index = {group["id"]: group for group in result}
    destination = operation.get("destination_id")
    label = operation.get("label", "")
    if operation["kind"] == "RENAME":
        check(set(operation) == {"kind", "destination_id", "label"} and destination in index, "INVALID_REQUEST")
        index[destination]["label"] = label
        return result
    ids, selected_groups = operation.get("record_ids", []), operation.get("group_ids", [])
    check(isinstance(ids, list) and isinstance(selected_groups, list)
          and all(isinstance(key, str) for key in ids + selected_groups)
          and len(ids) == len(set(ids)) and len(selected_groups) == len(set(selected_groups))
          and set(selected_groups) <= set(index), "INVALID_PARTITION")
    if operation["kind"] == "MERGE":
        check(not ids and len(selected_groups) >= 2, "INVALID_REQUEST")
    else:
        check(bool(ids) != bool(selected_groups), "INVALID_REQUEST")
    selected = set(ids)
    for key in selected_groups:
        selected.update(index[key]["record_ids"])
    existing = {key for group in result for key in group["record_ids"]}
    check(selected and selected <= existing, "INVALID_PARTITION")
    new_id = operation.get("new_group_id")
    check(bool(destination) != bool(new_id), "INVALID_REQUEST")
    if new_id:
        check(new_id not in index and bool(label.strip()), "INVALID_REQUEST")
        target = {"id": new_id, "label": label, "record_ids": []}
        result.append(target)
    else:
        check(destination in index, "INVALID_REQUEST")
        target = index[destination]
    for group in result:
        group["record_ids"] = [key for key in group["record_ids"] if key not in selected]
    target["record_ids"] = sorted(set(target["record_ids"]) | selected)
    return [group for group in result if group["record_ids"]]


def event_brief(event):
    if not event:
        return None
    return {**{key: value for key, value in event.items() if key not in ("groups", "command", "task", "rationales")},
            "rationale_counts": rationale.counts(event),
            "group_count": len(event["groups"]), "record_count": sum(len(group["record_ids"]) for group in event["groups"])}


def task_brief(task):
    return {key: value for key, value in task.items() if key not in ("groups", "command")}


def compact_review(value):
    """Keep audit facts; member IDs are retrieved through a revision-pinned page."""
    if isinstance(value, list):
        return [compact_review(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: compact_review(item) for key, item in value.items() if key != "command"}
    if "group_snapshot" in result:
        snapshot = result["group_snapshot"]
        snapshot["record_count"] = len(snapshot.pop("record_ids", []))
    return result


def tool_status(records):
    values = {record["check_status"] for record in records}
    return "FAIL" if "FAIL" in values else "PASS" if values == {"PASS"} else "UNKNOWN"


class WorkspacePipelineStore(WorkspaceClusteringStore):
    def judgments(self, report_id):
        rows = self.db.execute("SELECT e.payload->>'group_id' AS id,e.payload->>'conclusion' AS conclusion,"
            "(e.payload->>'revision')::int AS revision,e.payload->>'cluster_event_id' AS cluster_id,"
            "e.payload->'group_snapshot'->>'label' AS label FROM {oa} h JOIN {oa} e "
            "ON e.record_id=h.workspace_pipeline_event_ref WHERE h.record_type='WS_GROUP_REVIEW' "
            "AND h.workspace_pipeline_report_ref=%s", (record_id("WS_REPORT", report_id),)).fetchall()
        return {row["id"]: row for row in rows}

    def summary(self, context, current, decisions, case_ids=None):
        valid = bool(current and current["confirmed"] and rationale.complete(current)
                     and context["ready"] and not self.stale(current, context))
        counts = {"PASS": 0, "FAIL": 0, "DEFER": 0, "PENDING": 0, "STALE": 0}
        for group in current["groups"] if current else []:
            decision = decisions.get(group["id"])
            state = ("STALE" if decision and (not valid or decision["cluster_id"] != current["id"])
                     else decision["conclusion"] if decision else "PENDING")
            counts[state] += 1
        total = sum(counts.values())
        reviewed = counts["PASS"] + counts["FAIL"]
        return {"confirmed": valid, "total": total, "reviewed": reviewed, "pending": total-reviewed,
                "rationale_counts": rationale.counts(current, case_ids),
                "counts": counts, "complete": bool(valid and total > 0 and reviewed == total)}

    def view_paged(self, user_id, block_id, version_id, report_id):
        context = self.context(user_id, block_id, version_id, report_id)
        current, revision = self.current(report_id)
        if current:
            check(current["source_sha256"] == context["source_sha256"], "STATE_CORRUPT")
            partition(current["groups"], evidence(context))
        cases = self.catalog(context)
        head = self.reviews.row("WS_CLUSTER", report_id)
        task = head["state"].get("task") if head else None
        decisions = self.judgments(report_id)
        report = context["report"]
        case_ids = {case["id"] for case in cases}
        brief = event_brief(current)
        if brief:
            brief["rationale_counts"] = rationale.counts(current, case_ids)
        return {"report": {key: value for key, value in report.items() if key not in ("records", "raw_report")},
                "version": context["version"], "source_sha256": context["source_sha256"],
                "raw_line_count": len((report.get("raw_report") or "").splitlines()),
                "current": brief, "revision": revision, "ready": context["ready"],
                "can_edit": context["can_review"], "stale": bool(current and self.stale(current, context)),
                "task": task_brief(task) if task else None, "summary": self.summary(context, current, decisions, case_ids),
                "case_count": len(cases), "reusable_count": sum(case["reusable"] for case in cases),
                "corporate_ai_connected": False, "record_count": len(evidence(context)),
                "removed_count": len(set(decisions) - {group["id"] for group in current["groups"]}) if current else 0}

    def selected_event(self, context, expected_revision, event_id=None):
        if event_id:
            row = self.reviews.row("WS_CLUSTER_EVENT", str(uuid_value(event_id)))
            check(row and row["payload"]["report_id"] == context["report"]["id"], "NOT_FOUND")
            event = rationale.unpack(row["payload"])
        else:
            event, revision = self.current(context["report"]["id"])
            check(revision == expected_revision, "REVISION_CONFLICT")
        if event:
            check(event["source_sha256"] == context["source_sha256"], "STATE_CORRUPT")
        return event

    def groups_page(self, user_id, block_id, version_id, report_id, *, expected_revision, offset=0, limit=25,
                    search="", status="", event_id=None, rationale_status=""):
        context = self.context(user_id, block_id, version_id, report_id)
        event = self.selected_event(context, expected_revision, event_id)
        decisions = self.judgments(report_id)
        valid = bool(event and event["confirmed"] and rationale.complete(event) and not event_id and not self.stale(event, context))
        case_ids = {case["id"] for case in self.catalog(context)}
        records = {record["id"]: record for record in evidence(context)}
        items, needle = [], search.casefold()
        for group in event["groups"] if event else []:
            group_rationale = rationale.brief(event, group, case_ids)
            if rationale_status and (group_rationale["status"] == "CONFIRMED" if rationale_status == "UNRESOLVED"
                                     else group_rationale["status"] != rationale_status):
                continue
            decision = decisions.get(group["id"])
            state = ("STALE" if decision and (not valid or decision["cluster_id"] != event["id"])
                     else decision["conclusion"] if decision else "PENDING")
            first = records[group["record_ids"][0]]
            if status and (state not in ("PENDING", "STALE", "DEFER") if status == "UNREVIEWED" else state != status):
                continue
            if needle and not (needle in (group["id"]+" "+group["label"]+" "+str(first["fields"])).casefold()
                               or any(needle in key.casefold() for key in group["record_ids"])):
                continue
            items.append({"id": group["id"], "label": group["label"], "record_count": len(group["record_ids"]),
                          "status": state, "revision": decision["revision"] if decision else 0,
                          "rationale": group_rationale,
                          "example": {key: first["fields"][key] for key in ("code", "object", "mode", "severity") if key in first["fields"]}})
        return {"items": items[offset:offset+limit], "total": len(items), "offset": offset, "limit": limit,
                "cluster_revision": event["revision"] if event else 0, "event_id": event["id"] if event else None}

    def records_page(self, user_id, block_id, version_id, report_id, *, expected_revision, offset=0, limit=50,
                     search="", group_id=None, event_id=None):
        context = self.context(user_id, block_id, version_id, report_id)
        event = self.selected_event(context, expected_revision, event_id)
        group = next((group for group in event["groups"] if group["id"] == group_id), None) if event and group_id else None
        check(not group_id or group is not None, "NOT_FOUND")
        ids = set(group["record_ids"]) if group else None
        needle = search.casefold()
        records = [record for record in evidence(context) if (ids is None or record["id"] in ids)
                   and (not needle or needle in (record["id"]+" "+record.get("raw", "")+" "+str(record["fields"])).casefold())]
        page = [{**record, "raw": record.get("raw", "")[:4096],
                 "raw_display_truncated": len(record.get("raw", "")) > 4096} for record in records[offset:offset+limit]]
        return {"items": page, "total": len(records), "offset": offset, "limit": limit,
                "group": {"id": group["id"], "label": group["label"], "record_count": len(group["record_ids"]),
                    "check_status": tool_status([record for record in evidence(context) if record["id"] in ids])} if group else None,
                "cluster_revision": event["revision"] if event else 0}

    def history_page(self, user_id, block_id, version_id, report_id, *, offset=0, limit=25, removed=False):
        self.context(user_id, block_id, version_id, report_id)
        if removed:
            current, _ = self.current(report_id)
            ids = {group["id"] for group in current["groups"]} if current else set()
            items = [{"id": key, "label": value["label"], "conclusion": value["conclusion"]}
                     for key, value in self.judgments(report_id).items() if key not in ids]
            return {"items": items[offset:offset+limit], "total": len(items), "offset": offset, "limit": limit}
        count = self.db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_CLUSTER_EVENT' "
            "AND workspace_pipeline_report_ref=%s", (record_id("WS_REPORT", report_id),)).fetchone()["n"]
        rows = self.db.execute("SELECT payload-'groups'-'command'-'task'-'rationales'-'rationale_chunks' AS event,"
            "jsonb_array_length(payload->'groups') AS group_count FROM {oa} WHERE record_type='WS_CLUSTER_EVENT' "
            "AND workspace_pipeline_report_ref=%s ORDER BY created_at DESC,workspace_key LIMIT %s OFFSET %s",
            (record_id("WS_REPORT", report_id), limit, offset)).fetchall()
        return {"items": [{**row["event"], "group_count": row["group_count"]} for row in rows],
                "total": count, "offset": offset, "limit": limit}

    def rationale_detail(self, user_id, block_id, version_id, report_id, group_id, *, expected_revision,
                         event_id=None, offset=0, limit=10):
        context = self.context(user_id, block_id, version_id, report_id)
        event = self.selected_event(context, expected_revision, event_id)
        group = next((g for g in event["groups"] if g["id"] == group_id), None) if event else None
        check(group is not None, "NOT_FOUND")
        cases = self.catalog(context)
        row = event.get("rationales", {}).get(group_id)
        ids = set(row["content"]["evidence_ids"]) & set(group["record_ids"]) if row else set()
        samples = [{**r, "raw": r.get("raw", "")[:4096], "raw_display_truncated": len(r.get("raw", "")) > 4096}
                   for r in evidence(context) if r["id"] in ids]
        # Only this group's rationale is materialized, including its deletion
        # history. Never transfer all partitions/rationales for the history panel.
        parameters = (record_id("WS_REPORT", report_id), group_id)
        condition = "record_type='WS_CLUSTER_EVENT' AND workspace_pipeline_report_ref=%s AND payload->'rationales' ? %s"
        total = self.db.execute("SELECT count(*) AS n FROM {oa} WHERE " + condition, parameters).fetchone()["n"]
        history = self.db.execute("SELECT payload->>'id' AS id,(payload->>'revision')::int AS revision,"
            "payload->>'at' AS at,payload->'actor' AS actor,payload->'rationales'->%s AS rationale,"
            "payload->'rationale_chunks'->(payload->'rationales'->>%s) AS packed_rationale FROM {oa} WHERE "
            + condition + " ORDER BY created_at DESC,workspace_key LIMIT %s OFFSET %s",
            (group_id, group_id, *parameters, limit, offset)).fetchall()
        for item in history:
            packed = item.pop("packed_rationale")
            if isinstance(item["rationale"], str):
                item["rationale"] = rationale.unpack_group(group_id, item["rationale"], packed)
        return {"group_id": group_id, "cluster_revision": event["revision"], "event_id": event["id"],
                "status": rationale.state(event, group, {case["id"] for case in cases}), "rationale": rationale.materialized(row),
                "samples": samples, "history": {"items": history, "total": total, "offset": offset, "limit": limit},
                "references": [{"id": case["id"], "report_id": case["report_id"], "revision": case["revision"],
                    "group": {"id": g["id"], "label": g["label"]},
                    "rationale": case.get("rationales", {}).get(g["id"])}
                    for case in cases for g in case["groups"]
                    if row and any(ref["case_id"] == case["id"] and ref["group_id"] == g["id"]
                                   for ref in row["content"]["reference_groups"])]}

    def write_task(self, report_id, task, history=None):
        head = self.reviews.row("WS_CLUSTER", report_id)
        state = {**head["state"], "task": task}
        if history is not None:
            state["task_history"] = history
        self.db.execute("UPDATE {oa} SET state=%s,updated_at=clock_timestamp() WHERE record_type='WS_CLUSTER' "
                        "AND workspace_key=%s", (json_value(state), report_id))

    def start_task(self, user_id, block_id, version_id, report_id, *, expected_revision, request_id,
                   identity_fields=None, mode="CRITERIA"):
        context = self.context(user_id, block_id, version_id, report_id, write=True)
        check(mode == "CRITERIA", "AI_NOT_CONFIGURED")
        check(context["ready"], "CLUSTER_BLOCKED")
        rid = str(uuid_value(request_id))
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "action": "PROPOSE", "expected_revision": expected_revision, "reason": "", "groups": None,
                   "identity_fields": identity_fields, "reusable": False, "compatible_versions": False,
                   "criteria": "", "mode": mode}
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        previous = self.reviews.row("WS_CLUSTER_EVENT", rid)
        if previous:
            check(previous["payload"]["command"] == command and "task" in previous["payload"], "REQUEST_CONFLICT")
            return previous["payload"]["task"]
        old, revision = self.current(report_id)
        head = self.reviews.row("WS_CLUSTER", report_id)
        prior_task = head["state"].get("task") if head else None
        check(not ai_job_active(head), "CLUSTER_TASK_ACTIVE")
        if prior_task and prior_task["id"] == rid:
            check(prior_task["command"] == command, "REQUEST_CONFLICT")
            return task_brief(prior_task)
        check(revision == expected_revision, "REVISION_CONFLICT")
        check(not old or old["source_sha256"] == context["source_sha256"], "STATE_CORRUPT")
        check(not prior_task or prior_task["status"] not in ACTIVE, "CLUSTER_TASK_ACTIVE")
        fields = identity_fields_valid(identity_fields if identity_fields is not None else
            old["identity_fields"] if old else ["object", "check_point", "mode"], evidence(context))
        cases = self.catalog(context)
        task = {"id": rid, "command": command, "status": "PENDING", "expected_revision": revision,
                "source_sha256": context["source_sha256"], "case_catalog_sha256": digest([case["id"] for case in cases]),
                "identity_fields": fields, "record_count": len(evidence(context)), "processed": 0, "next_chunk": 0,
                "chunks": [], "groups": {}, "reference_ids": [], "at": utcnow().isoformat(),
                "advisor": "CONFIRMED_PARTITION_V1", "model_called": False, "error": None}
        # Keep at most about 20 reference-engine ranges at larger scales. This
        # is not a model token budget; corporate execution has a separate budget.
        task["chunk_size"] = max(CHUNK_SIZE, (len(evidence(context))+19)//20)
        history = head["state"].get("task_history", []) if head else []
        if prior_task:
            check(len(history) < 1000, "CASE_LIMIT")
            history = [*history, task_brief(prior_task)]
        if not head:
            self.ws.put("WS_CLUSTER", report_id, self.reviews.metadata(context), project=context["report"]["project_id"], block=block_id)
        self.write_task(report_id, task, history)
        return task_brief(task)

    def advance_task(self, user_id, block_id, version_id, report_id, *, task_id, expected_chunk, action="ADVANCE"):
        context = self.context(user_id, block_id, version_id, report_id, write=True)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))
        head = self.reviews.row("WS_CLUSTER", report_id)
        task = head["state"].get("task") if head else None
        check(task and task["id"] == str(uuid_value(task_id)), "NOT_FOUND")
        check(task.get("command", {}).get("user_id", user_id) == user_id, "FORBIDDEN")
        if task["status"] == "COMPLETED":
            return task_brief(task)
        check(action in ("ADVANCE", "RESUME", "CANCEL"), "INVALID_REQUEST")
        if action == "CANCEL":
            task.update(status="CANCELLED", groups={})
            self.write_task(report_id, task)
            return task_brief(task)
        if expected_chunk < task["next_chunk"]:
            return task_brief(task)
        check(expected_chunk == task["next_chunk"], "REVISION_CONFLICT")
        check(task["status"] in ("PENDING", "RUNNING") or action == "RESUME" and task["status"] == "FAILED", "CLUSTER_TASK_ACTIVE")
        cases = self.catalog(context)
        if (context["source_sha256"] != task["source_sha256"] or self.current(report_id)[1] != task["expected_revision"]
                or digest([case["id"] for case in cases]) != task["case_catalog_sha256"]):
            task.update(status="STALE", error="PROPOSAL_STALE")
            self.write_task(report_id, task)
            return task_brief(task)
        records = evidence(context)
        start, stop = task["processed"], min(task["processed"]+task.get("chunk_size", CHUNK_SIZE), len(records))
        subset = {**context, "report": {**context["report"], "records": records[start:stop], "record_count": stop-start}}
        try:
            advice = validate_clustering_advice(ConfirmedPartitionAdvisor().propose(subset, cases, task["identity_fields"]), subset, cases)
            for group in advice["groups"]:
                task["groups"].setdefault(group["id"], []).extend(group["record_ids"])
            task["reference_ids"] = sorted(set(task["reference_ids"]) | set(advice["reference_ids"]))
            task["chunks"].append({"index": task["next_chunk"], "start": start, "stop": stop,
                "record_count": stop-start, "input_sha256": digest(records[start:stop]), "status": "COMPLETED"})
            task.update(processed=stop, next_chunk=task["next_chunk"]+1, status="RUNNING", error=None)
            if stop == len(records):
                groups = [{"id": key, "label": f"그룹 {index+1}", "record_ids": ids}
                          for index, (key, ids) in enumerate(sorted(task["groups"].items()))]
                value = {"groups": groups, "reference_ids": task["reference_ids"],
                    "reason": f"전체 로그 {len(records):,}건을 {len(task['chunks'])}개 작업으로 나누어 원본 필드와 확정 분류 사례를 대조한 초안입니다. 실제 AI 호출은 없습니다."}
                value["group_rationales"] = reference_rationales(groups, records, cases, task["identity_fields"],
                                                               context["scope"], context["report"].get("rule"))
                value["reference_ids"] = sorted({ref["case_id"] for row in value["group_rationales"]
                                                 for ref in row["reference_groups"]})
                validate_clustering_advice(value, context, cases)
                task["status"] = "COMPLETED"
                event = self.change(user_id, block_id, version_id, report_id, action="PROPOSE",
                    expected_revision=task["expected_revision"], request_id=task["id"],
                    identity_fields=task["command"]["identity_fields"], prepared=value, task=task_brief(task),
                    _task_context=context)
                return event["task"]
        except ReviewError as error:
            task.update(status="FAILED", error=error.code)
            # Failed output is never committed as a partial classification.
            self.write_task(report_id, task)
            return task_brief(task)
        self.write_task(report_id, task)
        return task_brief(task)
