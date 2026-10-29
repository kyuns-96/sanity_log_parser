#!/usr/bin/env python3
"""OA dashboard API with local defaults and explicit SSO container hosts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Annotated, Literal
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from oa.sanity_auth import Actor, AuthRequest, AuthenticationService, AuthMode, SSOIdentityProvider, UserDirectory, require_business_access
from oa.sanity_hosts import allowed_hosts as validate_hosts
from common.sanity_common import ReviewError, coverage
from common.sanity_config import access_mode, load_environment
from common.sanity_db import Database, Queue
from oa.sanity_exchange import request_evidence, receive_evidence, _validate_response
from oa.sanity_oa_store import OAStore
from oa.sanity_cluster_store import ClusterStore, receive_cluster
from oa.sanity_access import AccessStore, access_lock, administrator_id
from oa.access_api import create_access_router, require_mutation_request

spec = importlib.util.spec_from_file_location("dashboard_oa_commands", Path(__file__).with_name("main.py"))
commands = importlib.util.module_from_spec(spec)
spec.loader.exec_module(commands)

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


class RequestBoundary:
    """Bound even chunked request bodies; do not echo inputs or log credentials."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        if scope["path"].startswith("/api/") and scope["method"] == "POST":
            if headers.get(b"content-type", b"").split(b";", 1)[0].strip() != b"application/json":
                return await JSONResponse({"code": "JSON_REQUIRED"}, 415)(scope, receive, send)
            # Large partition edits still have explicit bounds. Other business
            # APIs retain their original small request limit.
            ceiling = 65536
            if scope["path"].startswith("/api/workbench/"):
                ceiling = (16777216 if scope["path"].endswith("/clustering/operations") else
                           4194304 if "/clustering" in scope["path"] else
                           262144 if scope["path"].endswith("/rationale") else 65536)
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > ceiling:
                    return await JSONResponse({"code": "REQUEST_TOO_LARGE"}, 413)(scope, receive, send)
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            consumed = False

            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()
            incoming = bounded_receive
        else:
            incoming = receive

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")]
            await send(message)
        await self.app(scope, incoming, secured_send)


def authenticated(request: Request) -> Actor:
    context = AuthRequest(method=request.method, path=request.url.path, headers=dict(request.headers),
                          cookies=request.cookies, client_host=request.client.host if request.client else None)
    return request.app.state.authentication.authenticate(context, load_environment())


class ScopedActor(Actor):
    def __init__(self, actor, access):
        super().__init__(actor.user_id, actor.reviewer, actor.auth_method)
        object.__setattr__(self, "access", access)


def authorized_actor(request: Request, actor: Actor = Depends(authenticated)):
    """Keep current grants locked until every route/helper has completed.

    The ordinary READ COMMITTED transaction deliberately takes its snapshot
    after waiting for a concurrent revoke. Helper connections remain protected
    by this connection's shared transaction lock.
    """
    if actor.auth_method == "local_token":
        yield require_business_access(actor)
        return
    admin = administrator_id(load_environment())
    if admin is None:
        require_business_access(actor)  # Preserve the unconfigured SSO barrier.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_mutation_request(request)
    with Database("oa") as db, db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        access_lock(db)
        store = AccessStore(db, admin)
        store.require_ready()
        store.require_actor(actor)
        yield ScopedActor(actor, store)


def require_bundle(actor, bundle_id, *, write=False):
    if isinstance(actor, ScopedActor):
        return actor.access.require_bundle(actor.user_id, bundle_id, write=write)
    return None


def audit_reviewer(actor):
    # Stable SSO identity is retained in all new review/clustering events.
    return actor.user_id if isinstance(actor, ScopedActor) else actor.reviewer


def require_evidence(actor, db, request_id, *, write=False):
    if not isinstance(actor, ScopedActor):
        return
    request = Queue(db, "oa_to_vwp").get("request-" + str(request_id))
    if request is None or request["message_type"] != "EVIDENCE_REQUEST":
        raise ReviewError("RESOURCE_NOT_FOUND", "Resource is not available")
    require_bundle(actor, request["bundle_id"], write=write)


def summary_only(value):
    return {key: val for key, val in value.items() if key != "units"}


def create_app(*, auth_mode: AuthMode | None = None,
               identity_provider: SSOIdentityProvider | None = None,
               user_directory: UserDirectory | None = None,
               auth_router: APIRouter | None = None,
               allowed_hosts: str | None = None):
    app = FastAPI(title="OA Sanity Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.authentication = AuthenticationService(mode=auth_mode, identity_provider=identity_provider,
                                                     user_directory=user_directory)
    app.add_middleware(RequestBoundary)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=validate_hosts(allowed_hosts), www_redirect=False)

    @app.exception_handler(ReviewError)
    async def review_error(request, exc):
        code = exc.code
        status = (401 if code == "UNAUTHORIZED" else
                  403 if code in {"ACCOUNT_NOT_LINKED", "ACCOUNT_DISABLED", "FORBIDDEN"} else
                  404 if code.endswith("NOT_FOUND") else
                  413 if code in {"AI_INPUT_LIMIT", "AI_OUTPUT_LIMIT", "CLUSTER_LIMIT", "REPORT_LIMIT", "SOURCE_CONTEXT_LIMIT"} else
                  503 if code in {"AI_NOT_CONFIGURED", "AI_CONFIG", "INVALID_AI_CRITERIA", "WORKSPACE_NOT_CONFIGURED",
                                  "AI_TOKEN_STORE_NOT_CONFIGURED", "AI_TOKEN_UNAVAILABLE"} else
                  503 if code.startswith(("DATABASE_", "DBAAS_", "DASHBOARD_", "AUTH_", "AUTHORIZATION_")) else
                  409 if code in {"REVISION_CONFLICT", "REQUEST_CONFLICT", "REVIEW_BLOCKED", "FAIL_CANNOT_PASS",
                                 "STATE_CORRUPT", "STATE_CONFLICT", "EVIDENCE_NOT_RECEIVED", "LEASE_LOST",
                                 "CLUSTER_NOT_READY", "CLUSTER_BLOCKED", "STALE_REFERENCE", "CANDIDATES_UNRESOLVED",
                                 "POR_UNCHANGED", "PROPOSAL_STALE", "CLUSTER_NOT_CONFIRMED", "CLUSTER_TASK_ACTIVE",
                                 "RATIONALE_REQUIRED", "RATIONALE_STALE", "SOURCE_FILE_ON_VWP", "AI_BUSY",
                                 "AI_JOB_ACTIVE", "AI_INPUT_STALE", "AI_CASES_STALE", "AI_CRITERIA_STALE", "AI_CONFIG_CHANGED",
                                 "SOURCE_INTAKE_NOT_READY", "SOURCE_SCOPE_CONFIRMATION_REQUIRED"} else 400)
        return JSONResponse({"code": code}, status, headers={"Cache-Control": "no-store"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"code": "INVALID_REQUEST"}, 400)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        # No driver error strings, request bodies, DSNs, tokens or SQL in responses.
        return JSONResponse({"code": "INTERNAL_ERROR"}, 500, headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        return {"status": "OK", "interface": "OA_DASHBOARD", "scope": "LOCAL_PROTOTYPE"}

    @app.get("/api/auth-config")
    def auth_config():
        # Public presentation metadata only: no SDK/DB call, identity or secret.
        mode = app.state.authentication.effective_mode(load_environment())
        routes = {(route.path, method) for route in auth_router.routes
                  for method in (getattr(route, "methods", None) or ())} if auth_router is not None else set()
        login = mode == "sso" and ("/login", "GET") in routes
        logout = mode == "sso" and ("/logout", "POST") in routes
        return {"mode": mode, "login_url": "/auth/login" if login else None,
                "logout_url": "/auth/logout" if logout else None,
                "logout_method": "POST" if logout else None}

    @app.get("/api/session")
    def session(actor=Depends(authenticated)):
        settings = load_environment()
        mode = access_mode(settings)
        direct = mode == "postgresql"
        authorization_ready = actor.auth_method == "local_token"
        admin, readable, writable = False, authorization_ready, direct and authorization_ready
        if actor.auth_method == "sso" and administrator_id(settings) is not None:
            with Database("oa") as db, db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
                access_lock(db)
                store = AccessStore(db, administrator_id(settings))
                store.require_ready()
                store.require_actor(actor)
                authorization_ready, admin = True, store.is_admin(actor.user_id)
                readable = store.can_read_business(actor.user_id)
                writable = direct and store.can_write_business(actor.user_id)
        return {"user_id": actor.user_id, "reviewer": actor.reviewer, "auth_method": actor.auth_method,
                "access_mode": mode, "writable": writable,
                "authorization_ready": authorization_ready,
                "is_admin": admin, "can_read_business": readable,
                "can_receive": actor.auth_method == "local_token" and direct,
                "notice": "SSO 사용자 연결 확인 · 프로젝트·블록 권한 연동은 아직 준비되지 않았습니다." if not authorization_ready else
                          "프로젝트·블록 권한을 요청하고 승인 상태를 확인하세요." if actor.auth_method == "sso" and not readable else
                          "직접 PostgreSQL 검증 모드 · 접속/권한은 데이터 요청 시 검증" if direct else
                          "현재 DBaaS 방식은 임시 읽기 전용입니다. 정식 전체 접근 방식이 필요합니다."}

    @app.get("/api/bundles")
    def bundles(offset: Offset = 0, limit: Limit = 20, actor=Depends(authorized_actor)):
        predicate, params = actor.access.read_predicate(actor.user_id, alias="r") if isinstance(actor, ScopedActor) else ("TRUE", ())
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

    @app.post("/api/receive")
    def receive(body: Command, actor=Depends(authorized_actor)):
        if isinstance(actor, ScopedActor):
            raise ReviewError("FORBIDDEN", "Global queue ingestion is a trusted worker responsibility")
        from argparse import Namespace
        result = commands.receive(Namespace(message_id=None, lease_seconds=300))
        if "coverage" in result:
            result["coverage"] = summary_only(result["coverage"])
        return result

    @app.get("/api/bundles/{bundle_id}")
    def bundle(bundle_id: BundleID, unit_offset: Offset = 0, unit_limit: Limit = 50, actor=Depends(authorized_actor)):
        canonical_scope = require_bundle(actor, bundle_id)
        can_write = True
        if isinstance(actor, ScopedActor):
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

    @app.get("/api/bundles/{bundle_id}/units/{unit_id}/groups")
    def groups(bundle_id: BundleID, unit_id: ID, offset: Offset = 0, limit: Limit = 20, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            payload, _, _, _ = OAStore(db).load(bundle_id)
            if unit_id not in {u["review_unit_id"] for u in payload["review_units"]}:
                raise ReviewError("UNIT_NOT_FOUND", "Unknown unit")
            values = [g for g in payload["finding_groups"] if g["review_unit_id"] == unit_id]
            return {"items": values[offset:offset + limit], "total": len(values), "offset": offset, "limit": limit}

    @app.post("/api/bundles/{bundle_id}/reviews")
    def review(bundle_id: BundleID, body: ReviewCommand, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        with Database("oa") as db, db.transaction():
            result = OAStore(db).review(bundle_id, body.unit_id, audit_reviewer(actor), body.conclusion,
                                       body.reason, body.request_id, body.expected_revision)
            result["coverage"] = summary_only(result["coverage"])
            return result

    @app.get("/api/bundles/{bundle_id}/report")
    def report(bundle_id: BundleID, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            body = OAStore(db).report(bundle_id)
        return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{bundle_id}.md"'})

    @app.post("/api/bundles/{bundle_id}/evidence")
    def evidence(bundle_id: BundleID, body: EvidenceCommand, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        return request_evidence(bundle_id, body.unit_id, body.record_ids, body.reason, body.request_id, body.ttl_seconds)

    @app.get("/api/bundles/{bundle_id}/requests")
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

    @app.post("/api/evidence/{request_id}/receive")
    def response_receive(request_id: UUID, body: Command, actor=Depends(authorized_actor)):
        if isinstance(actor, ScopedActor):
            require_evidence(actor, actor.access.db, request_id, write=True)
        return receive_evidence(request_id)

    @app.get("/api/evidence/{request_id}")
    def response_preview(request_id: UUID, actor=Depends(authorized_actor)):
        with Database("oa") as db, db.transaction(readonly=True):
            require_evidence(actor, db, request_id)
            response = Queue(db, "vwp_to_oa").get("response-" + str(request_id))
            if response is None:
                raise ReviewError("EVIDENCE_NOT_FOUND", "No response")
            if response["status"] != "RECEIVED":
                raise ReviewError("EVIDENCE_NOT_RECEIVED", "Validate and receive the response first")
            data = _validate_response(db, response)
            records = data["records"] if data else []
            return {"result": response["payload"]["result"], "record_count": len(records),
                    "records": [{"record_id": r["record_id"], "file_id": r["file_id"], "raw": r["raw"][:4000],
                                 "raw_truncated": len(r["raw"]) > 4000, "redacted": r.get("redacted", False)} for r in records]}

    @app.get("/api/evidence/{request_id}/records/{record_id}")
    def full_record(request_id: UUID, record_id: ID, start: Annotated[int, Query(ge=0)] = 0,
                    length: Annotated[int, Query(ge=1, le=16000)] = 8000, actor=Depends(authorized_actor)):
        with Database("oa") as db, db.transaction(readonly=True):
            require_evidence(actor, db, request_id)
            response = Queue(db, "vwp_to_oa").get("response-" + str(request_id))
            if response is None or response["status"] != "RECEIVED":
                raise ReviewError("EVIDENCE_NOT_RECEIVED", "Receive the evidence first")
            data = _validate_response(db, response)
            record = next((r for r in data["records"] if r["record_id"] == record_id), None) if data else None
            if record is None:
                raise ReviewError("EVIDENCE_NOT_FOUND", "Record is not part of this response")
            return {"record_id": record_id, "raw": record["raw"][start:start + length], "start": start,
                    "total_chars": len(record["raw"]), "redacted": record.get("redacted", False),
                    "source_raw_sha256": record.get("source_raw_sha256", record["raw_sha256"]), "raw_sha256": record["raw_sha256"]}

    @app.get("/api/bundles/{bundle_id}/units/{unit_id}/clustering")
    def cluster_view(bundle_id: BundleID, unit_id: ID, offset: Offset = 0, limit: Limit = 20, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            return ClusterStore(db, bundle_id, unit_id).view(offset, limit)

    @app.post("/api/bundles/{bundle_id}/units/{unit_id}/clustering")
    def cluster_request(bundle_id: BundleID, unit_id: ID, body: ClusterCommand, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        with Database("oa") as db, db.transaction():
            return ClusterStore(db, bundle_id, unit_id, lock=True).request(**body.model_dump(), reviewer=audit_reviewer(actor))

    @app.post("/api/bundles/{bundle_id}/units/{unit_id}/clustering/actions")
    def cluster_action(bundle_id: BundleID, unit_id: ID, body: ClusterAction, actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id, write=True)
        values = body.model_dump()
        values["jid"] = values.pop("job_id")
        with Database("oa") as db, db.transaction():
            return ClusterStore(db, bundle_id, unit_id, lock=True).action(**values, reviewer=audit_reviewer(actor))

    @app.get("/api/bundles/{bundle_id}/units/{unit_id}/clustering/{jid}/members")
    def cluster_members(bundle_id: BundleID, unit_id: ID, jid: ID, offset: Offset = 0, limit: Limit = 20,
                        filter: Literal["all", "unresolved", "candidates"] = "all", group_id: ID | None = None,
                        actor=Depends(authorized_actor)):
        require_bundle(actor, bundle_id)
        with Database("oa") as db, db.transaction(readonly=True):
            return ClusterStore(db, bundle_id, unit_id).members(jid, offset, limit, filter, group_id)

    @app.post("/api/bundles/{bundle_id}/units/{unit_id}/clustering/{jid}/receive")
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

    app.include_router(create_access_router(authenticated))
    from oa.ai_workbench_api import create_workbench_router
    app.include_router(create_workbench_router(authorized_actor))
    from oa.ai_tokens_api import create_ai_credentials_router
    app.include_router(create_ai_credentials_router(authorized_actor))
    from oa.ai_connections_api import create_ai_connections_router
    app.include_router(create_ai_connections_router(authorized_actor))

    # Company login/callback/logout routes must precede the root static mount.
    if auth_router is not None:
        app.include_router(auth_router, prefix="/auth")

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def unknown_api(path: str):
        return JSONResponse({"code": "NOT_FOUND"}, 404)

    dist = Path(__file__).with_name("dashboard") / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:
        @app.get("/")
        def unbuilt():
            return PlainTextResponse("Build oa/dashboard first: npm ci && npm run build", 503)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    # A single-user prototype; do not expose it beyond loopback or trust forwarded headers.
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False, proxy_headers=False, log_level="critical")
