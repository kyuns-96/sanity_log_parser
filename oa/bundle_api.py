"""Bundle review, evidence exchange and legacy bundle clustering routes for the OA dashboard."""
from __future__ import annotations

from argparse import Namespace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from common.sanity_common import ReviewError, coverage
from common.sanity_db import Database, Queue
from oa.main import receive as receive_bundle
from oa.sanity_cluster_store import ClusterStore, receive_cluster
from oa.sanity_exchange import receive_evidence, request_evidence, validate_response
from oa.sanity_oa_store import OAStore

ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
BundleID = Annotated[str, Field(pattern=r"^b-[0-9a-f]{64}$")]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
Limit = Annotated[int, Query(ge=1, le=100)]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReviewCommand(Command):
    unit_id: ID
    conclusion: Literal["PASS", "FAIL"]
    reason: str = Field(min_length=1, max_length=2000)
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)


class EvidenceCommand(Command):
    unit_id: ID
    record_ids: list[ID] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)
    request_id: UUID
    ttl_seconds: int = Field(default=3600, ge=1, le=86400, strict=True)


class ClusterCommand(Command):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    instruction: str = Field(default="", max_length=4000)
    rules: list[dict] | None = Field(default=None, max_length=20)
    mode: Literal["AUTO", "AI", "RULES"] = "AUTO"


class ClusterAction(Command):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    action: Literal["APPLY", "CONFIRM", "SAVE_RULES", "CANDIDATE", "DEFER"]
    job_id: ID
    reason: str = Field(min_length=1, max_length=2000)
    record_id: ID | None = None
    group_id: ID | None = None
    decision: Literal["DIFFERENT", "CONFIRM_MATCH", "DEFER"] | None = None


def summary_only(value):
    return {key: val for key, val in value.items() if key != "units"}


def create_bundle_router(authorized_actor, *, scoped_actor_type):
    router = APIRouter()

    def require_bundle(actor, bundle_id, *, write=False):
        if isinstance(actor, scoped_actor_type):
            return actor.access.require_bundle(actor.user_id, bundle_id, write=write)
        return None

    def audit_reviewer(actor):
        # Stable SSO identity is retained in all new review/clustering events.
        return actor.user_id if isinstance(actor, scoped_actor_type) else actor.reviewer

    def require_evidence(actor, db, request_id, *, write=False):
        if not isinstance(actor, scoped_actor_type):
            return
        request = Queue(db, "oa_to_vwp").get("request-" + str(request_id))
        if request is None or request["message_type"] != "EVIDENCE_REQUEST":
            raise ReviewError("RESOURCE_NOT_FOUND", "Resource is not available")
        require_bundle(actor, request["bundle_id"], write=write)

    @router.get("/api/bundles")
    def bundles(offset: Offset = 0, limit: Limit = 20, actor=Depends(authorized_actor)):
        predicate, params = actor.access.read_predicate(actor.user_id, alias="r") if isinstance(actor, scoped_actor_type) else ("TRUE", ())
        with Database("oa") as db, db.transaction(readonly=True):
            total = db.execute("SELECT count(*) AS n FROM {oa} r WHERE r.record_type='RECEIVED_BUNDLE' AND " + predicate,
                               params).fetchone()["n"]
            rows = db.execute("SELECT r.bundle_id,r.created_at FROM {oa} r WHERE r.record_type='RECEIVED_BUNDLE' AND " + predicate +
                              " ORDER BY r.created_at DESC,r.record_id DESC LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
            store, items = OAStore(db), []
            for row in rows:
                payload, decisions, _, revision = store.load(row["bundle_id"])
                store.report(row["bundle_id"])
                items.append({**row, "project": payload["run"]["project"], "run_id": payload["run"]["run_id"],
                              "review_revision": revision, "coverage": summary_only(coverage(payload, decisions))})
            return {"items": items, "total": total, "offset": offset, "limit": limit}

    @router.post("/api/receive")
    def receive(body: Command, actor=Depends(authorized_actor)):
        if isinstance(actor, scoped_actor_type):
            raise ReviewError("FORBIDDEN", "Global queue ingestion is a trusted worker responsibility")
        result = receive_bundle(Namespace(message_id=None, lease_seconds=300))
        if "coverage" in result:
            result["coverage"] = summary_only(result["coverage"])
        return result

    @router.get("/api/bundles/{bundle_id}")
    def bundle(bundle_id: BundleID, unit_offset: Offset = 0, unit_limit: Limit = 50, actor=Depends(authorized_actor)):
        canonical_scope = require_bundle(actor, bundle_id)
        can_write = True
        if isinstance(actor, scoped_actor_type):
            try:
                require_bundle(actor, bundle_id, write=True)
            except ReviewError as exc:
                if exc.code != "FORBIDDEN":
                    raise
                can_write = False
        with Database("oa") as db, db.transaction(readonly=True):
            store = OAStore(db)
            payload, decisions, context, revision = store.load(bundle_id)
            store.report(bundle_id)
            cov = coverage(payload, decisions)
            readiness = {u["review_unit_id"]: u for u in cov["units"]}
            units = [{**unit, **readiness[unit["review_unit_id"]], "decision": decisions.get(unit["review_unit_id"])}
                     for unit in payload["review_units"][unit_offset:unit_offset + unit_limit]]
            events = db.execute("SELECT review_revision,payload FROM {oa} WHERE record_type='REVIEW_EVENT' "
                                "AND bundle_id=%s ORDER BY review_revision DESC LIMIT 50", (bundle_id,)).fetchall()
            history = [{"revision": row["review_revision"], "actor":
                        row["payload"]["decisions"][0]["reviewer"] if row["payload"]["decisions"] else "INITIAL",
                        "decisions": row["payload"]["decisions"]} for row in events]
            return {"bundle_id": bundle_id, "run": payload["run"], "review_revision": revision,
                    "coverage": summary_only(cov), "units": units, "unit_total": len(payload["review_units"]),
                    "unit_offset": unit_offset, "unit_limit": unit_limit, "history": history, "history_total": revision + 1,
                    "scope": canonical_scope,
                    "permissions": {key: can_write for key in ("can_review", "can_cluster", "can_request_evidence", "can_receive_evidence")},
                    "context": {"ai": context.get("ai", {"status": "NOT_CONFIGURED"})}}

    @router.get("/api/bundles/{bundle_id}/units/{unit_id}/groups")
    def groups(bundle_id: BundleID, unit_id: ID, offset: Offset = 0, limit: Limit = 20, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            payload, _, _, _ = OAStore(db).load(bundle_id)
            if unit_id not in {u["review_unit_id"] for u in payload["review_units"]}:
                raise ReviewError("UNIT_NOT_FOUND", "Unknown unit")
            values = [g for g in payload["finding_groups"] if g["review_unit_id"] == unit_id]
            return {"items": values[offset:offset + limit], "total": len(values), "offset": offset, "limit": limit}

    @router.post("/api/bundles/{bundle_id}/reviews")
    def review(bundle_id: BundleID, body: ReviewCommand, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        with Database("oa") as db, db.transaction():
            result = OAStore(db).review(bundle_id, body.unit_id, audit_reviewer(actor), body.conclusion,
                                       body.reason, body.request_id, body.expected_revision)
            result["coverage"] = summary_only(result["coverage"])
            return result

    @router.get("/api/bundles/{bundle_id}/report")
    def report(bundle_id: BundleID, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            body = OAStore(db).report(bundle_id)
        return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{bundle_id}.md"'})

    @router.post("/api/bundles/{bundle_id}/evidence")
    def evidence(bundle_id: BundleID, body: EvidenceCommand, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        return request_evidence(bundle_id, body.unit_id, body.record_ids, body.reason, body.request_id, body.ttl_seconds)

    @router.get("/api/bundles/{bundle_id}/requests")
    def requests(bundle_id: BundleID, offset: Offset = 0, limit: Limit = 20, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            OAStore(db).root(bundle_id)
            total = db.execute("SELECT count(*) AS n FROM {oa_to_vwp} WHERE bundle_id=%s "
                               "AND message_type='EVIDENCE_REQUEST'", (bundle_id,)).fetchone()["n"]
            rows = db.execute("SELECT q.message_id,q.payload,q.status,q.created_at,q.expires_at, "
                              "r.status AS response_status,r.payload->>'result' AS result "
                              "FROM {oa_to_vwp} q LEFT JOIN {vwp_to_oa} r ON r.correlation_id=q.message_id "
                              "AND r.message_type='EVIDENCE_RESPONSE' WHERE q.bundle_id=%s AND q.message_type='EVIDENCE_REQUEST' "
                              "ORDER BY q.created_at DESC,q.message_id DESC LIMIT %s OFFSET %s", (bundle_id, limit, offset)).fetchall()
            return {"items": [{"request_id": r["message_id"].removeprefix("request-"),
                               **{k: r["payload"].get(k) for k in ("review_unit_id", "record_ids", "reason")},
                               **{k: r[k] for k in ("status", "created_at", "expires_at", "response_status", "result")}}
                              for r in rows], "total": total, "offset": offset, "limit": limit}

    @router.post("/api/evidence/{request_id}/receive")
    def response_receive(request_id: UUID, body: Command, actor=Depends(authorized_actor)):
        if isinstance(actor, scoped_actor_type):
            require_evidence(actor, actor.access.db, request_id, write=True)
        return receive_evidence(request_id)

    @router.get("/api/evidence/{request_id}")
    def response_preview(request_id: UUID, actor=Depends(authorized_actor)):
        with Database("oa") as db, db.transaction(readonly=True):
            require_evidence(actor, db, request_id)
            response = Queue(db, "vwp_to_oa").get("response-" + str(request_id))
            if response is None:
                raise ReviewError("EVIDENCE_NOT_FOUND", "No response")
            if response["status"] != "RECEIVED":
                raise ReviewError("EVIDENCE_NOT_RECEIVED", "Validate and receive the response first")
            data = validate_response(db, response)
            records = data["records"] if data else []
            return {"result": response["payload"]["result"], "record_count": len(records),
                    "records": [{"record_id": r["record_id"], "file_id": r["file_id"], "raw": r["raw"][:4000],
                                 "raw_truncated": len(r["raw"]) > 4000, "redacted": r.get("redacted", False)} for r in records]}

    @router.get("/api/evidence/{request_id}/records/{record_id}")
    def full_record(request_id: UUID, record_id: ID, start: Annotated[int, Query(ge=0)] = 0,
                    length: Annotated[int, Query(ge=1, le=16000)] = 8000, actor=Depends(authorized_actor)):
        with Database("oa") as db, db.transaction(readonly=True):
            require_evidence(actor, db, request_id)
            response = Queue(db, "vwp_to_oa").get("response-" + str(request_id))
            if response is None or response["status"] != "RECEIVED":
                raise ReviewError("EVIDENCE_NOT_RECEIVED", "Receive the evidence first")
            data = validate_response(db, response)
            record = next((r for r in data["records"] if r["record_id"] == record_id), None) if data else None
            if record is None:
                raise ReviewError("EVIDENCE_NOT_FOUND", "Record is not part of this response")
            return {"record_id": record_id, "raw": record["raw"][start:start + length], "start": start,
                    "total_chars": len(record["raw"]), "redacted": record.get("redacted", False),
                    "source_raw_sha256": record.get("source_raw_sha256", record["raw_sha256"]), "raw_sha256": record["raw_sha256"]}

    @router.get("/api/bundles/{bundle_id}/units/{unit_id}/clustering")
    def cluster_view(bundle_id: BundleID, unit_id: ID, offset: Offset = 0, limit: Limit = 20, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            return ClusterStore(db, bundle_id, unit_id).view(offset, limit)

    @router.post("/api/bundles/{bundle_id}/units/{unit_id}/clustering")
    def cluster_request(bundle_id: BundleID, unit_id: ID, body: ClusterCommand, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        with Database("oa") as db, db.transaction():
            return ClusterStore(db, bundle_id, unit_id, lock=True).request(**body.model_dump(), reviewer=audit_reviewer(actor))

    @router.post("/api/bundles/{bundle_id}/units/{unit_id}/clustering/actions")
    def cluster_action(bundle_id: BundleID, unit_id: ID, body: ClusterAction, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        values = body.model_dump()
        values["jid"] = values.pop("job_id")
        with Database("oa") as db, db.transaction():
            return ClusterStore(db, bundle_id, unit_id, lock=True).action(**values, reviewer=audit_reviewer(actor))

    @router.get("/api/bundles/{bundle_id}/units/{unit_id}/clustering/{jid}/members")
    def cluster_members(bundle_id: BundleID, unit_id: ID, jid: ID, offset: Offset = 0, limit: Limit = 20,
                        filter: Literal["all", "unresolved", "candidates"] = "all", group_id: ID | None = None,
                        actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            return ClusterStore(db, bundle_id, unit_id).members(jid, offset, limit, filter, group_id)

    @router.post("/api/bundles/{bundle_id}/units/{unit_id}/clustering/{jid}/receive")
    def cluster_receive(bundle_id: BundleID, unit_id: ID, jid: ID, body: Command, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        with Database("oa") as db, db.transaction(readonly=True):
            job = ClusterStore(db, bundle_id, unit_id).get("CLUSTER_JOB", jid)
            if job is None or job["bundle_id"] != bundle_id or job["review_unit_id"] != unit_id:
                raise ReviewError("CLUSTER_NOT_FOUND", "Unknown classification job")
        count = 0
        for _ in range(20):
            if receive_cluster(jid)["status"] == "NO_MESSAGE":
                break
            count += 1
        return {"status": "RECEIVED" if count else "NO_MESSAGE", "messages": count}

    return router
