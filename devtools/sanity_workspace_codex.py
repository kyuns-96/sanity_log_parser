"""Explicit UI requests for the exact synthetic Codex fixtures, never company AI.

Reservations and receipts live in WS_CLUSTER.state. No database transaction is
held across inference, and an uncertain call is never repeated automatically.
"""
import uuid

from common.sanity_clustering import check
from devtools.sanity_codex_mock import CodexMockClusteringAdvisor, MODEL, REPORT_IDS, assert_synthetic_context, fixture_report, request_packet
from common.sanity_common import ReviewError, digest
from common.sanity_db import Database, json_value, lock_key, uuid_value
from oa.sanity_workspace import utcnow
from oa.sanity_workspace_clustering import WorkspaceClusteringStore, ai_job_active

FIELDS = ["check_point", "mode", "object"]
MAX_JOBS = 20  # Retain every UUID for this bounded public mock, without eviction.
ACTIVE = ("RUNNING", "RESULT_READY")


class WorkspaceCodexService:
    def __init__(self, *, enabled=False, advisor_factory=None):
        self.enabled = enabled is True
        self.owner = str(uuid.uuid4())
        self.advisor_factory = advisor_factory or (lambda: CodexMockClusteringAdvisor(enabled=True))

    @staticmethod
    def locked(db):
        db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-review-write"),))

    @staticmethod
    def write(db, report_id, state):
        db.execute("UPDATE {oa} SET state=%s,updated_at=clock_timestamp() WHERE record_type='WS_CLUSTER' AND workspace_key=%s",
                   (json_value(state), report_id))

    @staticmethod
    def brief(job):
        return {key: job[key] for key in ("id", "status", "error", "model", "created_at", "finished_at",
            "expected_revision", "input_case_count", "event_id") if key in job} if job else None

    def recover(self):
        if not self.enabled:
            return
        with Database("oa") as db, db.transaction():
            self.locked(db)
            for head in self.heads(db):
                state = head["state"]
                changed = False
                for job in state.get("codex_jobs", {}).values():
                    if job["status"] in ACTIVE and job["owner"] != self.owner:
                        job.update(status="SAVE_FAILED" if job.get("receipt") else "UNKNOWN",
                                   error="SAVE_NOT_COMPLETED" if job.get("receipt") else "AI_OUTCOME_UNKNOWN",
                                   finished_at=utcnow().isoformat())
                        changed = True
                if changed:
                    self.write(db, head["workspace_key"], state)

    @staticmethod
    def heads(db):
        return db.execute("SELECT workspace_key,state FROM {oa} WHERE record_type='WS_CLUSTER' AND workspace_key=ANY(%s)",
                          (list(REPORT_IDS),)).fetchall()

    def view(self, db, user_id, block_id, version_id, report_id):
        store = WorkspaceClusteringStore(db)
        # Authorize even unsupported/disabled reports, without inferring scope from an ID.
        context = store.context(user_id, block_id, version_id, report_id)
        supported = report_id in REPORT_IDS and context["report"] == fixture_report(report_id)
        head = store.reviews.row("WS_CLUSTER", report_id) if supported else None
        state = head["state"] if head else {}
        jobs = state.get("codex_jobs", {})
        return {"available": self.enabled and supported and context["can_review"] and len(jobs) < MAX_JOBS,
                "supported": supported, "enabled": self.enabled, "model": MODEL,
                "remaining": MAX_JOBS-len(jobs), "job": self.brief(jobs.get(state.get("codex_job_id")))}

    def reserve(self, user_id, block_id, version_id, report_id, *, expected_revision, request_id):
        check(self.enabled, "AI_NOT_CONFIGURED")
        rid = str(uuid_value(request_id))
        command = dict(user_id=user_id, block_id=block_id, version_id=version_id,
                       report_id=report_id, expected_revision=expected_revision)
        with Database("oa") as db, db.transaction():
            self.locked(db)
            store = WorkspaceClusteringStore(db)
            store.ws.check_workspace()
            context = store.context(user_id, block_id, version_id, report_id, write=True)
            assert_synthetic_context(context, [])
            head = store.reviews.row("WS_CLUSTER", report_id)
            state = head["state"] if head else {}
            jobs = state.get("codex_jobs", {})
            for other in self.heads(db):
                previous = other["state"].get("codex_jobs", {}).get(rid)
                if previous:
                    check(previous["command"] == command, "REQUEST_CONFLICT")
                    if previous["status"] == "SAVE_FAILED" and previous.get("receipt"):
                        check(not any(ai_job_active(value) for value in self.heads(db)), "AI_BUSY")
                        previous.update(status="RESULT_READY", owner=self.owner, error=None)
                        state["codex_jobs"][rid] = previous
                        self.write(db, report_id, state)
                        return previous, True
                    return previous, False
            check(store.reviews.row("WS_CLUSTER_EVENT", rid) is None, "REQUEST_CONFLICT")
            check(not any(ai_job_active(value) for value in self.heads(db)), "AI_BUSY")
            check(len(jobs) < MAX_JOBS, "AI_MOCK_LIMIT")
            check(store.current(report_id)[1] == expected_revision, "REVISION_CONFLICT")
            check(not state.get("task") or state["task"]["status"] not in ("PENDING", "RUNNING", "FAILED", "STALE"), "CLUSTER_TASK_ACTIVE")
            check(context["ready"], "CLUSTER_BLOCKED")
            cases = store.catalog(context)
            packet = request_packet(context, cases, FIELDS)
            job = {"id": rid, "command": command, "owner": self.owner, "status": "RUNNING", "error": None,
                   "model": MODEL, "created_at": utcnow().isoformat(), "expected_revision": expected_revision,
                   "source_sha256": context["source_sha256"], "input_sha256": digest(packet),
                   "case_catalog_sha256": digest([case["id"] for case in cases]), "input_case_count": len(cases)}
            if not head:
                store.ws.put("WS_CLUSTER", report_id, store.reviews.metadata(context),
                             project=context["report"]["project_id"], block=block_id)
            self.write(db, report_id, {**state, "codex_job_id": rid, "codex_jobs": {**jobs, rid: job}})
            return job, True

    def execute(self, job):
        command = job["command"]
        rid, report_id = job["id"], command["report_id"]
        receipt = job.get("receipt")
        try:
            if receipt is None:
                with Database("oa") as db, db.transaction(readonly=True):
                    store = WorkspaceClusteringStore(db)
                    context = store.context(command["user_id"], command["block_id"], command["version_id"], report_id, write=True)
                    cases = store.catalog(context)
                    check(store.current(report_id)[1] == job["expected_revision"]
                          and digest(request_packet(context, cases, FIELDS)) == job["input_sha256"], "PROPOSAL_STALE")
                advisor = self.advisor_factory()
                advisor.propose(context, cases, FIELDS)
                receipt = advisor.receipt
                # Persist a known result before attempting the business write.
                with Database("oa") as db, db.transaction():
                    self.locked(db)
                    store = WorkspaceClusteringStore(db)
                    head = store.reviews.row("WS_CLUSTER", report_id)
                    saved = head["state"]["codex_jobs"][rid]
                    check(saved["owner"] == self.owner and saved["status"] == "RUNNING", "PROPOSAL_STALE")
                    saved.update(receipt=receipt, status="RESULT_READY")
                    self.write(db, report_id, head["state"])
            with Database("oa") as db, db.transaction():
                self.locked(db)
                store = WorkspaceClusteringStore(db)
                head = store.reviews.row("WS_CLUSTER", report_id)
                saved = head["state"]["codex_jobs"][rid]
                check(saved["owner"] == self.owner and saved["status"] == "RESULT_READY", "PROPOSAL_STALE")
                event = store.change(**command, action="PROPOSE", request_id=rid,
                                     identity_fields=FIELDS, mode="MOCK_CODEX", mock_receipt=receipt)
                head = store.reviews.row("WS_CLUSTER", report_id)
                saved = head["state"]["codex_jobs"][rid]
                saved.update(status="COMPLETED", event_id=event["id"], finished_at=utcnow().isoformat())
                self.write(db, report_id, head["state"])
                return self.brief(saved)
        except Exception as error:
            code = error.code if isinstance(error, ReviewError) else "INTERNAL_ERROR"
            status = ("STALE" if code in ("PROPOSAL_STALE", "REVISION_CONFLICT", "FORBIDDEN") else
                      "UNKNOWN" if code == "AI_OUTCOME_UNKNOWN" else
                      "SAVE_FAILED" if receipt is not None and code in ("DATABASE_ERROR", "DATABASE_UNAVAILABLE") else "FAILED")
            with Database("oa") as db, db.transaction():
                self.locked(db)
                store = WorkspaceClusteringStore(db)
                head = store.reviews.row("WS_CLUSTER", report_id)
                saved = head["state"]["codex_jobs"][rid]
                if saved["owner"] == self.owner and saved["status"] in ACTIVE:
                    saved.update(status=status, error=code, finished_at=utcnow().isoformat())
                    if receipt is not None:
                        saved["receipt"] = receipt
                    self.write(db, report_id, head["state"])
                return self.brief(saved)

    def run(self, **command):
        job, execute = self.reserve(**command)
        return self.execute(job) if execute else self.brief(job)
