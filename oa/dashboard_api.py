#!/usr/bin/env python3
"""OA dashboard API with local defaults and explicit SSO container hosts."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from common.sanity_common import ReviewError
from common.sanity_config import access_mode, load_environment
from common.sanity_db import Database
from oa.access_api import create_access_router, require_mutation_request
from oa.ai_connections_api import create_ai_connections_router
from oa.ai_tokens_api import create_ai_credentials_router
from oa.ai_workbench_api import create_workbench_router
from oa.atlas_api import create_atlas_router
from oa.bundle_api import (
    BundleID, ClusterAction, ClusterCommand, Command, EvidenceCommand, ID, Limit, Offset,
    ReviewCommand, create_bundle_router, summary_only,
)
from oa.sanity_access import AccessStore, access_lock, administrator_id
from oa.sanity_auth import (
    Actor, AuthMode, AuthRequest, AuthenticationService, SSOIdentityProvider, UserDirectory,
    require_business_access,
)
from oa.sanity_hosts import allowed_hosts as validate_hosts
from oa.workspace_schemas import workbench_body_ceiling

LOG = logging.getLogger('oa.dashboard')


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
                ceiling = workbench_body_ceiling(scope["path"], default_ceiling=65536)
            elif scope["path"].startswith("/api/floorplan/"):
                ceiling = 25 * 1024 * 1024 if scope["path"].endswith("/attachments") else 1024 * 1024
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
        exclusive_atlas = request.method == "POST" and (
            request.url.path.endswith(("/api/atlas/projects", "/api/atlas/blocks"))
            or (request.url.path.startswith("/api/atlas/projects/") and request.url.path.endswith("/delete"))
        )
        access_lock(db, exclusive=exclusive_atlas)
        store = AccessStore(db, admin)
        store.require_ready()
        store.require_actor(actor)
        yield ScopedActor(actor, store)


def _review_error_status(code: str) -> int:
    if code == "UNAUTHORIZED":
        return 401
    if code in {"ACCOUNT_NOT_LINKED", "ACCOUNT_DISABLED", "FORBIDDEN", "FORCE_DELETE_DISABLED"}:
        return 403
    if code.endswith("NOT_FOUND"):
        return 404
    if code in {"AI_INPUT_LIMIT", "AI_OUTPUT_LIMIT", "CLUSTER_LIMIT", "REPORT_LIMIT", "SOURCE_CONTEXT_LIMIT"}:
        return 413
    if code in {
        "AI_NOT_CONFIGURED", "AI_CONFIG", "INVALID_AI_CRITERIA", "WORKSPACE_NOT_CONFIGURED",
        "AI_TOKEN_STORE_NOT_CONFIGURED", "AI_TOKEN_UNAVAILABLE", "AI_JOB_DELETE_NOT_CONFIGURED",
        "DATA_DELETE_NOT_CONFIGURED", "AI_SCHEDULER_CONFIG", "AI_SCHEDULER_CORRUPT", "AI_EVIDENCE_CORRUPT",
        "AREA_POWER_UNAVAILABLE",
    } or code.startswith(("DATABASE_", "DBAAS_", "DASHBOARD_", "AUTH_", "AUTHORIZATION_")):
        return 503
    if code in {
        "PROCESS_AREA_CONFLICT", "REVISION_CONFLICT", "REQUEST_CONFLICT", "REVIEW_BLOCKED", "FAIL_CANNOT_PASS",
        "STATE_CORRUPT", "STATE_CONFLICT", "EVIDENCE_NOT_RECEIVED", "LEASE_LOST",
        "CLUSTER_NOT_READY", "CLUSTER_BLOCKED", "STALE_REFERENCE", "CANDIDATES_UNRESOLVED",
        "POR_UNCHANGED", "PROPOSAL_STALE", "CLUSTER_NOT_CONFIRMED", "CLUSTER_TASK_ACTIVE",
        "RATIONALE_REQUIRED", "RATIONALE_STALE", "SOURCE_FILE_ON_VWP", "AI_BUSY",
        "AI_JOB_ACTIVE", "AI_JOB_DELETED", "AI_INPUT_STALE", "AI_CASES_STALE", "AI_CRITERIA_STALE", "AI_CONFIG_CHANGED",
        "REMOVAL_CHANGED", "POR_VERSION_IN_USE", "VERSION_REFERENCED", "SUBMISSION_BUSY", "SOURCE_REMOVED",
        "PROJECT_HAS_DATA", "BLOCK_HAS_DATA", "CONFIRMATION_MISMATCH",
        "AI_EVIDENCE_CHANGED", "AI_EVIDENCE_NOT_READY",
        "SOURCE_INTAKE_NOT_READY", "SOURCE_SCOPE_CONFIRMATION_REQUIRED",
        "CONFLUENCE_PAT_REQUIRED",
        "AREA_BINDING_CONFLICT", "AREA_PREVIOUS_NOT_PREPARED", "AREA_LINEAGE_REQUIRED",
    }:
        return 409
    return 400


def create_app(*, auth_mode: AuthMode | None = None,
               identity_provider: SSOIdentityProvider | None = None,
               user_directory: UserDirectory | None = None,
               auth_router: APIRouter | None = None,
               allowed_hosts: str | None = None):
    area_enabled = os.environ.get("AREA_POWER_ENABLED", "").strip().lower() == "true"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if area_enabled and access_mode(load_environment()) == "postgresql":
            try:
                from oa.atlas_area_sync import sync_all_area_project_blocks
                synced = sync_all_area_project_blocks(actor_id="oa-startup")
                if synced["projects_created"] or synced["blocks_created"]:
                    LOG.info(
                        "Area/Power startup scope sync created %s project(s) and %s block(s)",
                        synced["projects_created"], synced["blocks_created"],
                    )
            except Exception as exc:
                LOG.warning("Area/Power startup scope sync skipped (%s)", type(exc).__name__)
        yield

    app = FastAPI(title="Design Atlas", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.authentication = AuthenticationService(mode=auth_mode, identity_provider=identity_provider,
                                                     user_directory=user_directory)
    app.add_middleware(RequestBoundary)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=validate_hosts(allowed_hosts), www_redirect=False)

    @app.exception_handler(ReviewError)
    async def review_error(request, exc):
        code = exc.code
        status = _review_error_status(code)
        if status >= 500:
            cause = exc.__cause__ or exc.__context__ or exc
            state = getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)
            if not isinstance(state, str) or len(state) != 5 or any(char not in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ' for char in state):
                state = None
            frames = [f'{frame.filename.rsplit("/", 1)[-1]}:{frame.name}:{frame.lineno}'
                      for frame in traceback.extract_tb(cause.__traceback__)]
            LOG.error('Dashboard API unavailable: code=%s type=%s sqlstate=%s at=%s',
                      code, type(cause).__name__, state, '; '.join(frames))
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
                          "DBaaS HTTP 저장 연결은 지원하지 않습니다. PostgreSQL 연결 설정을 확인해 주세요."}

    app.include_router(create_bundle_router(authorized_actor, scoped_actor_type=ScopedActor))
    app.include_router(create_access_router(authenticated))
    app.include_router(create_workbench_router(authorized_actor))
    app.include_router(create_ai_credentials_router(authorized_actor))
    app.include_router(create_ai_connections_router(authorized_actor))

    # Area is opt-in and uses this host's identity, locked grants, safe errors,
    # request boundary and mutation-origin checks. Disabled hosts do not import
    # Area's optional runtime/dependencies or attempt its independent DB.
    app.state.area_power_enabled = area_enabled
    app.include_router(create_atlas_router(authorized_actor))
    if area_enabled:
        from oa.area_power.app import create_area_power_router
        from oa.floorplan.app import create_floorplan_router
        app.include_router(create_area_power_router(authorized_actor, authenticated))
        app.include_router(create_floorplan_router(authorized_actor))
    else:
        @app.api_route("/api/area-power", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        @app.api_route("/api/area-power/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        @app.api_route("/api/floorplan", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        @app.api_route("/api/floorplan/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        def area_unavailable(path: str = "", actor=Depends(authenticated)):
            return JSONResponse({"code": "AREA_POWER_UNAVAILABLE"}, 503)

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
