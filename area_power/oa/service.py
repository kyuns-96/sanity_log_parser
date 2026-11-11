"""Transactional OA operations. All cross-server work is represented by DB jobs."""
from __future__ import annotations

from datetime import datetime, timezone
from email.headerregistry import Address
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, field_validator

from area_power.common.errors import DomainError
from area_power.common.jsonutil import digest
from area_power.common.models import ComparisonRequest, MappingPayload

CONFIG_TYPES = {"project", "version", "block", "target", "mapping", "change"}
SCOPE_FIELDS = ("project_id", "version_id", "block_id", "snapshot_id")


def reject(message: str, code: str = "validation_error", status: int = 422):
    raise DomainError(message, code=code, status_code=status)


def addresses(values: list[str] | tuple[str, ...]) -> list[str]:
    result = []
    for value in values:
        try:
            if not isinstance(value, str) or any(c in value for c in "\r\n"):
                raise ValueError
            address = Address(addr_spec=value)
            if not address.username or not address.domain or address.addr_spec != value:
                raise ValueError
        except (ValueError, TypeError, IndexError):
            reject("Recipients must be plain email addresses.", "invalid_recipient")
        if value not in result:
            result.append(value)
    return result


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigInput(Input):
    record_type: Literal["target", "mapping", "change"]
    entity_id: UUID | None = None
    expected_row_version: int | None = Field(default=None, ge=1)
    project_id: UUID | None = None
    version_id: UUID | None = None
    block_id: UUID | None = None
    snapshot_id: UUID | None = None
    payload: dict[str, Any]


class Binding(Input):
    snapshot_id: UUID
    paths: list[str] = Field(min_length=1)


class TargetPayload(Input):
    name: str = Field(min_length=1, max_length=200)
    paths: list[str] = Field(default_factory=list)
    snapshot_id: UUID | None = None
    scope: Literal["node", "subtree"] = "node"
    metrics: list[str] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)

    @field_validator("paths", "metrics")
    @classmethod
    def unique_nonempty(cls, values):
        if any(not value.strip() for value in values) or len(values) != len(set(values)):
            raise ValueError("Values must be nonempty and unique")
        return values


class ChangePayload(Input):
    text: str = Field(min_length=1)
    applies_from_version_id: UUID | None = None
    applies_through_version_id: UUID | None = None
    baseline_project_id: UUID | None = None
    target_ids: list[UUID] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    expected: list[dict[str, Any]] = Field(default_factory=list)
    extraction_evidence: list[dict[str, Any]] = Field(default_factory=list)


class SelectionInput(Input):
    upload_id: UUID
    selection_scope: Literal["shared"] = "shared"
    expected_row_version: int | None = Field(default=None, ge=1)


class RejudgeInput(Input):
    change_ids: list[UUID] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)


class AlertStateInput(Input):
    action: Literal["read", "acknowledge", "resolve"]
    expected_row_version: int = Field(ge=1)
    note: str | None = None


class RerunInput(Input):
    expected_row_version: int | None = Field(default=None, ge=1)


class Service:
    def __init__(self, db, access=None):
        self.access = access
        if access is not None:
            from .access import AuthorizedDatabase
            db = AuthorizedDatabase(db, access)
        self.db = db

    def authorize(self, conn, row):
        return self.db.authorize(conn, row) if self.access is not None else row

    def lock(self, conn, identity):
        # Transaction-scoped lock also covers entities that do not have a row yet.
        key = int.from_bytes(digest(identity)[:8], "big", signed=True)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

    def scope(self, row):
        return {key: row.get(key) for key in SCOPE_FIELDS}

    def validate_scope(self, conn, values, *, require_project=True):
        project_id = values.get("project_id")
        if require_project and not project_id:
            reject("A project is required.")
        if project_id:
            self.db.record(conn, project_id, kind="project")
        for key, kind in (("version_id", "version"), ("block_id", "block"), ("snapshot_id", "snapshot")):
            if values.get(key):
                row = self.db.record(conn, values[key], kind=kind)
                if row.get("project_id") != project_id:
                    reject(f"{kind} does not belong to the chosen project.", "scope_mismatch")
                if kind == "snapshot":
                    for scope_key in ("version_id", "block_id"):
                        if values.get(scope_key) and row.get(scope_key) != values[scope_key]:
                            reject("Snapshot does not belong to the chosen scope.", "scope_mismatch")
        return values

    def lineage(self, conn, version_id):
        ids = []
        while version_id:
            version_id = UUID(str(version_id))
            if version_id in ids:
                reject("Version lineage contains a cycle.", "version_cycle")
            row = self.db.record(conn, version_id, kind="version")
            ids.append(version_id)
            previous = row["payload"].get("previous_version_id")
            if previous:
                parent = self.db.record(conn, UUID(str(previous)), kind="version")
                if parent.get("project_id") != row.get("project_id"):
                    reject("Version lineage crosses projects.", "scope_mismatch")
            version_id = previous
        return ids

    def version_range(self, conn, start, end, project_id):
        for value in (start, end):
            if value:
                row = self.db.record(conn, UUID(str(value)), kind="version")
                if row.get("project_id") != project_id:
                    reject("Version interval belongs to another project.", "scope_mismatch")
        if start and end and UUID(str(start)) not in self.lineage(conn, end):
            reject("Version interval is not an explicit lineage path.", "invalid_version_interval")

    def entity(self, conn, value, kind):
        row = self.db.record(conn, value, kind=kind)
        return self.db.latest_record(conn, kind, row["entity_id"])

    def config(self, body, actor):
        with self.db.connect() as conn:
            scope = {key: getattr(body, key) for key in SCOPE_FIELDS}
            self.validate_scope(conn, scope)
            previous = None
            if body.entity_id:
                self.lock(conn, ["config", body.record_type, body.entity_id])
                previous = self.db.latest_record(conn, body.record_type, body.entity_id, lock=True)
                if body.expected_row_version != previous["row_version"]:
                    reject("Configuration was changed; reload before editing.", "conflict", 409)
                if any(previous.get(key) != value for key, value in scope.items()):
                    reject("An existing configuration cannot change its scope.", "scope_mismatch")
            elif body.expected_row_version is not None:
                reject("Expected row version requires an existing entity.")
            if body.record_type == "mapping":
                raw = dict(body.payload)
                raw.pop("path_validation", None)
                raw.pop("state", None)
                payload = MappingPayload.model_validate(raw).model_dump(mode="python")
                for side in ("baseline", "current"):
                    self.validate_scope(conn, {key: payload[f"{side}_{key}"] for key in ("project_id", "version_id", "block_id")})
                if payload["current_project_id"] != scope["project_id"]:
                    reject("Mapping current side must match its owning project.", "scope_mismatch")
                for key in ("version_id", "block_id"):
                    if scope[key] and scope[key] != payload[f"current_{key}"]:
                        reject("Mapping current side must match its stored scope.", "scope_mismatch")
                if scope["snapshot_id"]:
                    snapshot = self.db.record(conn, scope["snapshot_id"], kind="snapshot")
                    if any(snapshot.get(key) != payload[f"current_{key}"] for key in ("version_id", "block_id")):
                        reject("Mapping snapshot differs from its current side.", "scope_mismatch")
                self.version_range(conn, payload.get("valid_from_version_id"), payload.get("valid_through_version_id"), scope["project_id"])
                for rule in payload["rules"]:
                    if rule.get("target_id"):
                        target = self.entity(conn, rule["target_id"], "target")
                        if target.get("project_id") != scope["project_id"] or target.get("block_id") not in (None, payload["current_block_id"]):
                            reject("Mapping target belongs to another project or block.", "scope_mismatch")
                payload.update(state="pending", path_validation="pending_worker_validation")
            elif body.record_type == "target":
                raw = dict(body.payload)
                raw.pop("path_validation", None)
                payload = TargetPayload.model_validate(raw).model_dump(mode="python")
                if not payload["paths"] and not payload["bindings"]:
                    reject("A target needs paths or snapshot bindings.")
                if payload.get("snapshot_id") and scope["snapshot_id"] and payload["snapshot_id"] != scope["snapshot_id"]:
                    reject("Target snapshot differs from its scope.", "scope_mismatch")
                snapshots = [binding["snapshot_id"] for binding in payload["bindings"]]
                if len(snapshots) != len(set(snapshots)):
                    reject("Target snapshot bindings must be unique.")
                if payload.get("snapshot_id"):
                    snapshots.append(payload["snapshot_id"])
                for snapshot_id in snapshots:
                    snapshot = self.db.record(conn, snapshot_id, kind="snapshot")
                    if snapshot.get("project_id") != scope["project_id"] or (scope["block_id"] and snapshot.get("block_id") != scope["block_id"]):
                        reject("Target binding belongs to another project or block.", "scope_mismatch")
                    if snapshot_id == payload.get("snapshot_id") and scope["version_id"] and snapshot.get("version_id") != scope["version_id"]:
                        reject("Target snapshot belongs to another version.", "scope_mismatch")
                for binding in payload["bindings"]:
                    if len(binding["paths"]) != len(set(binding["paths"])) or any(not path.strip() for path in binding["paths"]):
                        reject("Binding paths must be unique and nonempty.")
                payload["path_validation"] = "pending_worker_validation"
            else:
                payload = ChangePayload.model_validate(body.payload).model_dump(mode="python")
                self.version_range(conn, payload.get("applies_from_version_id"), payload.get("applies_through_version_id"), scope["project_id"])
                if payload.get("baseline_project_id"):
                    self.db.record(conn, payload["baseline_project_id"], kind="project")
                for target_id in payload["target_ids"]:
                    target = self.entity(conn, target_id, "target")
                    if target.get("project_id") != scope["project_id"] or (scope["block_id"] and target.get("block_id") not in (None, scope["block_id"])):
                        reject("Change target belongs to another scope.", "scope_mismatch")
            fields = dict(scope)
            if previous:
                fields.update(entity_id=previous["entity_id"], revision=previous["revision"] + 1, row_version=previous["row_version"] + 1, previous_id=previous["id"])
            return self.db.insert_record(conn, body.record_type, payload, actor, **fields)

    def selection(self, body, actor):
        with self.db.connect() as conn:
            upload = self.ready_upload(conn, body.upload_id)
            if not upload.get("snapshot_id") or not upload.get("condition_key"):
                reject("Upload is missing snapshot or condition identity.")
            self.lock(conn, ["selection", body.selection_scope, upload["snapshot_id"], upload["condition_key"]])
            row = conn.execute(sql.SQL("SELECT * FROM {} WHERE record_type='selection' AND selection_scope=%s AND snapshot_id=%s AND condition_key=%s FOR UPDATE").format(self.db.table("tracking_record")), (body.selection_scope, upload["snapshot_id"], upload["condition_key"])).fetchone()
            fields = {**self.scope(upload), "upload_id": upload["id"], "condition_key": upload["condition_key"], "selection_scope": body.selection_scope}
            if row:
                if body.expected_row_version is None:
                    reject("Expected row version is required to change a selection.", "conflict", 409)
                return self.db.update_record(conn, row["id"], actor, expected_row_version=body.expected_row_version, **fields)
            if body.expected_row_version is not None:
                reject("Selection no longer exists.", "conflict", 409)
            return self.db.insert_record(conn, "selection", {}, actor, **fields)

    def ready_upload(self, conn, identifier):
        upload = self.db.upload(conn, identifier)
        if upload["state"] != "ready":
            reject("Only ready uploads may be selected or compared.", "upload_not_ready", 409)
        self.validate_scope(conn, upload)
        return upload

    def changes(self, conn, ids, current, baseline):
        if len(ids) != len(set(ids)):
            reject("Change revisions must be unique.")
        changes = []
        for identifier in ids:
            row = self.db.record(conn, identifier, kind="change")
            if row.get("project_id") != current["project_id"] or (row.get("block_id") and row["block_id"] != current["block_id"]):
                reject("Change belongs to another comparison scope.", "scope_mismatch")
            bridge = row["payload"].get("baseline_project_id")
            if bridge and (not baseline or UUID(str(bridge)) != baseline["project_id"]):
                reject("Change project bridge differs from the selected baseline.", "scope_mismatch")
            # Exact revisions are frozen. Applicable intervals are evaluated by analysis,
            # including ambiguous natural-language intervals, without dropping text.
            changes.append(row)
        return changes

    def mapping_for(self, conn, identifier, current, baseline):
        if not identifier:
            return None
        if not baseline:
            reject("A mapping requires an explicit baseline upload.")
        row = self.db.record(conn, identifier, kind="mapping")
        payload = row["payload"]
        for side, upload in (("current", current), ("baseline", baseline)):
            for key in ("project_id", "block_id"):
                if UUID(str(payload[f"{side}_{key}"])) != upload[key]:
                    reject("Mapping side differs from selected upload.", "scope_mismatch")
        if UUID(str(payload["baseline_version_id"])) != baseline["version_id"]:
            reject("Mapping baseline version differs from selected upload.", "scope_mismatch")
        mapped_version = UUID(str(payload["current_version_id"]))
        if mapped_version != current["version_id"]:
            start, end = payload.get("valid_from_version_id"), payload.get("valid_through_version_id")
            if not start or not end:
                reject("Mapping does not cover the selected version pair.", "scope_mismatch")
            lineage = self.lineage(conn, end)
            start = UUID(str(start))
            if start not in lineage or current["version_id"] not in lineage[:lineage.index(start) + 1]:
                reject("Mapping interval does not include current version.", "scope_mismatch")
        return row

    def explanation_targets(self, conn, scope_targets, changes, current):
        targets = {str(target["entity_id"]): target for target in scope_targets}
        for change in changes:
            for identifier in change["payload"].get("target_ids", []):
                target = self.entity(conn, UUID(str(identifier)), "target")
                if target.get("project_id") != current["project_id"] or target.get("block_id") not in (None, current["block_id"]):
                    reject("Explanation target belongs to another comparison scope.", "scope_mismatch")
                targets[str(target["entity_id"])] = target
        texts = [change["payload"].get("text", "") for change in changes]
        if texts:
            registered = self.all_records(conn, "target", project_id=current["project_id"], latest=True)
            for target in registered:
                name = target["payload"].get("name")
                if target.get("block_id") not in (None, current["block_id"]):
                    continue
                if isinstance(name, str) and name.strip() and any(name in text for text in texts):
                    # Preserve every exact-name match; duplicated display names
                    # must remain ambiguous rather than choosing one entity.
                    targets[str(target["entity_id"])] = target
        return list(targets.values())

    def new_run(self, conn, comparison, changes, recipients, actor, *, fresh=False):
        frozen = comparison["payload"].get("frozen", {})
        scope_targets = frozen.get("scope_targets", frozen.get("targets", []))
        targets = self.explanation_targets(conn, scope_targets, changes, comparison)
        key = digest(["ai_run", comparison["id"], sorted(str(row["id"]) for row in changes), sorted(str(target["id"]) for target in targets), sorted(recipients), str(uuid4()) if fresh else None])
        self.lock(conn, ["ai_run", key])
        old = conn.execute(sql.SQL("SELECT * FROM {} WHERE record_type='ai_run' AND dedupe_key=%s").format(self.db.table("tracking_record")), (key,)).fetchone()
        if old:
            old = self.authorize(conn, old)
            jobs = self.db.records(conn, "job", parent_id=old["id"], limit=1)
            return old, jobs[0] if jobs else None
        run = self.db.insert_record(conn, "ai_run", {"change_ids": [row["id"] for row in changes], "changes": changes, "targets": targets, "recipients": recipients, "coverage": {}}, actor, state="queued", parent_id=comparison["id"], upload_id=comparison["upload_id"], baseline_upload_id=comparison.get("baseline_upload_id"), dedupe_key=key, **self.scope(comparison))
        job = self.db.enqueue(conn, "judge", actor=actor, parent_id=run["id"], payload={}, **self.scope(comparison))
        return run, job

    def compare(self, body: ComparisonRequest, actor):
        recipients = addresses(body.recipients)
        with self.db.connect() as conn:
            current = self.ready_upload(conn, body.current_upload_id)
            baseline = self.ready_upload(conn, body.baseline_upload_id) if body.baseline_upload_id else None
            if baseline:
                if baseline["id"] == current["id"]:
                    reject("Baseline and current must be different uploads.")
                if body.comparison_kind == "previous_version":
                    if baseline["project_id"] != current["project_id"] or baseline["block_id"] != current["block_id"]:
                        reject("Previous-version comparison must stay in the same project and block.", "scope_mismatch")
                    if baseline["version_id"] == current["version_id"] or baseline["version_id"] not in self.lineage(conn, current["version_id"]):
                        reject("Baseline version is not an explicit predecessor.", "invalid_baseline")
                elif baseline["project_id"] == current["project_id"]:
                    reject("Previous-project comparison requires another project.", "invalid_baseline")
            mapping = self.mapping_for(conn, body.mapping_id, current, baseline)
            targets = []
            for identifier in body.scope.target_ids:
                target = self.entity(conn, identifier, "target")
                if target.get("project_id") != current["project_id"] or (target.get("block_id") and target["block_id"] != current["block_id"]):
                    reject("Target belongs to another comparison scope.", "scope_mismatch")
                targets.append(target)
            changes = self.changes(conn, body.change_ids, current, baseline)
            power_uploads = []
            for pair in body.power_pairs:
                if not pair.get("current_upload_id"):
                    reject("Power pairs need an explicit current upload.")
                pair_current = self.ready_upload(conn, UUID(str(pair["current_upload_id"])))
                if pair_current["snapshot_id"] != current["snapshot_id"]:
                    reject("Current power upload belongs to another snapshot.", "scope_mismatch")
                pair_baseline = None
                if pair.get("baseline_upload_id"):
                    pair_baseline = self.ready_upload(conn, UUID(str(pair["baseline_upload_id"])))
                    if not baseline or pair_baseline["snapshot_id"] != baseline["snapshot_id"]:
                        reject("Baseline power upload belongs to another snapshot.", "scope_mismatch")
                power_uploads.append({"current_upload": pair_current, "baseline_upload": pair_baseline})
            request = body.model_dump(mode="python")
            request["recipients"] = recipients
            numeric = {key: value for key, value in request.items() if key not in ("change_ids", "recipients")}
            numeric["target_revisions"] = sorted(str(target["id"]) for target in targets)
            key = digest(numeric)
            self.lock(conn, ["comparison", key])
            old = conn.execute(sql.SQL("SELECT * FROM {} WHERE record_type='comparison' AND dedupe_key=%s FOR UPDATE").format(self.db.table("tracking_record")), (key,)).fetchone()
            if old:
                old = self.authorize(conn, old)
                original = old["payload"]["request"]
                explanation_changed = set(map(str, original.get("change_ids", []))) != set(map(str, body.change_ids)) or set(original.get("recipients", [])) != set(recipients)
                job = None
                if explanation_changed:
                    _, job = self.new_run(conn, old, changes, recipients, actor)
                return {"comparison": old, "job": job, "reused": True}
            explanation_targets = self.explanation_targets(conn, targets, changes, current)
            payload = {"request": request, "frozen": {"current_upload": current, "baseline_upload": baseline, "mapping": mapping, "scope_targets": targets, "targets": explanation_targets, "changes": changes, "power_uploads": power_uploads}, "coverage": {}, "policy_unconfigured": not bool(body.policy.metrics or body.policy.operating_limits), "ai_state": "pending"}
            comparison = self.db.insert_record(conn, "comparison", payload, actor, state="queued", upload_id=current["id"], baseline_upload_id=baseline["id"] if baseline else None, dedupe_key=key, **self.scope(current))
            job = self.db.enqueue(conn, "compare", actor=actor, parent_id=comparison["id"], payload={}, **self.scope(current))
            return {"comparison": comparison, "job": job, "reused": False}

    def rejudge(self, identifier, body, actor):
        with self.db.connect() as conn:
            comparison = self.db.record(conn, identifier, kind="comparison", lock=True)
            if comparison["state"] != "completed":
                reject("Only completed numerical comparisons can be explicitly rejudged.", "comparison_not_ready", 409)
            current = self.db.upload(conn, comparison["upload_id"])
            baseline = self.db.upload(conn, comparison["baseline_upload_id"]) if comparison.get("baseline_upload_id") else None
            changes = self.changes(conn, body.change_ids, current, baseline)
            run, job = self.new_run(conn, comparison, changes, addresses(body.recipients), actor, fresh=True)
            return {"ai_run": run, "job": job}

    def alert_state(self, identifier, body, actor):
        with self.db.connect() as conn:
            row = self.db.record(conn, identifier, kind="alert", lock=True)
            fields = {}
            now = datetime.now(timezone.utc)
            if body.action == "read":
                fields.update(read_at=row.get("read_at") or now, read_by=row.get("read_by") or actor)
            elif body.action == "acknowledge":
                fields.update(acknowledged_at=row.get("acknowledged_at") or now, acknowledged_by=row.get("acknowledged_by") or actor)
            else:
                fields.update(resolved_at=row.get("resolved_at") or now, resolved_by=row.get("resolved_by") or actor, resolution_note=body.note, state="resolved")
            updated = self.db.update_record(conn, identifier, actor, expected_row_version=body.expected_row_version, **fields)
            self.db.insert_record(conn, "alert_event", {"action": body.action, "note": body.note, "alert_row_version": updated["row_version"]}, actor, parent_id=identifier, **self.scope(row))
            return updated

    def rerun(self, identifier, body, actor):
        with self.db.connect() as conn:
            job = self.db.record(conn, identifier, kind="job", lock=True)
            if job["state"] != "failed" or not job.get("finished_at"):
                reject("Only failed terminal jobs can be explicitly rerun.", "invalid_job_state", 409)
            if body.expected_row_version is not None and body.expected_row_version != job["row_version"]:
                reject("Job changed; reload before rerunning.", "conflict", 409)
            self.lock(conn, ["rerun", identifier])
            existing = conn.execute(sql.SQL("SELECT id FROM {} WHERE record_type='job' AND previous_id=%s AND state IN ('pending','running')").format(self.db.table("tracking_record")), (identifier,)).fetchone()
            if existing:
                reject("A rerun of this job is already pending or running.", "conflict", 409)
            parent = None
            if job.get("parent_id"):
                parent = self.db.record(conn, job["parent_id"], lock=True)
            subject_context = {}
            if job["job_kind"] in ("compare", "judge", "extract"):
                expected_kind = {"compare": "comparison", "judge": "ai_run", "extract": "change"}[job["job_kind"]]
                if not parent or parent["record_type"] != expected_kind:
                    reject("Job subject does not match its kind.", "invalid_job_subject")
                if expected_kind in ("comparison", "ai_run"):
                    if parent["state"] != "failed":
                        reject("Subject is not failed; it cannot safely be reset.", "invalid_subject_state", 409)
                    active = conn.execute(sql.SQL("SELECT id FROM {} WHERE record_type='job' AND parent_id=%s AND id<>%s AND state IN ('pending','running')").format(self.db.table("tracking_record")), (parent["id"], identifier)).fetchone()
                    if active:
                        reject("Another job already owns this subject.", "conflict", 409)
                    payload = dict(parent["payload"])
                    history = list(payload.get("failure_history", []))
                    history.append({"job_id": identifier, "error_detail": job.get("error_detail"), "subject_error": parent.get("error_detail"), "failed_at": job.get("finished_at"), "coverage": payload.get("coverage")})
                    payload["failure_history"] = history
                    self.db.update_record(conn, parent["id"], actor, expected_row_version=parent["row_version"], state="queued", payload=payload, error_detail=None)
            elif job["job_kind"] == "ingest":
                upload = self.db.upload(conn, job["upload_id"], lock=True)
                if upload["state"] != "failed":
                    reject("Upload is not failed; it cannot safely be reset.", "invalid_subject_state", 409)
                active = conn.execute(sql.SQL("SELECT id FROM {} WHERE record_type='job' AND upload_id=%s AND job_kind='ingest' AND state IN ('pending','running')").format(self.db.table("tracking_record")), (upload["id"],)).fetchone()
                if active:
                    reject("Another ingest job already owns this upload.", "conflict", 409)
                subject_context = {"upload_error": upload.get("error_detail"), "validation_report": upload.get("validation_report")}
                conn.execute(sql.SQL("UPDATE {} SET state='pending',error_detail=NULL,validated_at=NULL WHERE id=%s AND state='failed'").format(self.db.table("di_area_power_csv")), (upload["id"],))
            elif job["job_kind"] != "mail":
                reject("Unsupported job kind.", "invalid_job_kind")
            payload = dict(job["payload"])
            payload["rerun_context"] = {"previous_job_id": identifier, "previous_error": job.get("error_detail"), "requested_by": actor, **subject_context}
            return self.db.enqueue(conn, job["job_kind"], actor=actor, parent_id=job.get("parent_id"), upload_id=job.get("upload_id"), payload=payload, dedupe_key=digest(["explicit_rerun", uuid4()]), previous_id=identifier, **self.scope(job))

    def all_records(self, conn, kind, **filters):
        rows = []
        offset = 0
        while True:
            page = self.db.records(conn, kind, limit=500, offset=offset, **filters)
            rows.extend(page)
            if len(page) < 500:
                return rows
            offset += len(page)

    def comparison_detail(self, identifier):
        with self.db.connect() as conn:
            comparison = self.db.record(conn, identifier, kind="comparison")
            results = self.all_records(conn, "metric_result", parent_id=identifier)
            runs = self.all_records(conn, "ai_run", parent_id=identifier)
            judgements = []
            for run in runs:
                judgements.extend(self.all_records(conn, "judgement", parent_id=run["id"]))
            return {"comparison": comparison, "results": results, "judgements": judgements, "ai_runs": runs}

    def dashboard(self, filters, limit=100, offset=0):
        with self.db.connect() as conn:
            uploads = self.db.uploads(conn, limit=limit, offset=offset, **filters)
            records = self.db.records(conn, limit=limit, offset=offset, latest=True, **filters)
            jobs = self.db.records(conn, "job", limit=limit, offset=offset, **filters)
            alerts = self.db.records(conn, "alert", limit=limit, offset=offset, **filters)
            comparisons = self.db.records(conn, "comparison", limit=limit, offset=offset, **filters)
            # Counts must use the same complete reference authorization as rows.
            # A current-project grant cannot reveal a denied baseline via totals.
            total_uploads, upload_offset = 0, 0
            while True:
                page = self.db.uploads(conn, limit=500, offset=upload_offset, **filters)
                total_uploads += len(page)
                if len(page) < 500:
                    break
                upload_offset += len(page)
            all_comparisons = self.all_records(conn, "comparison", **filters)
            all_alerts = self.all_records(conn, "alert", **filters)
            all_jobs = self.all_records(conn, "job", **filters)
            counts_row = {
                "comparisons": len(all_comparisons),
                "open_alerts": sum(row.get("resolved_at") is None for row in all_alerts),
                "pending_jobs": sum(row["state"] in {"pending", "running"} for row in all_jobs),
            }
            # Root metrics follow explicit selection only, never upload recency.
            selections = self.db.records(conn, "selection", limit=2, **filters)
            root = None
            if len(selections) == 1:
                root = self.db.upload(conn, selections[0]["upload_id"]).get("root_summary")
            return {"uploads": uploads, "records": records, "jobs": jobs, "alerts": alerts, "comparisons": comparisons, "counts": {"uploads": total_uploads, **counts_row}, "root_metrics": root}
