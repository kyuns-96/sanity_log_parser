#!/usr/bin/env python3
"""Loopback-only OA dashboard API. No bridge-to-bridge HTTP or automatic writes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import secrets
import sys
from typing import Annotated, Literal
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import Depends, FastAPI, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sanity_common import ReviewError, coverage
from sanity_config import access_mode, load_environment
from sanity_db import Database, Queue
from sanity_exchange import request_evidence, receive_evidence, _validate_response
from sanity_oa_store import OAStore

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
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > 65536:
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


def authenticated(authorization: Annotated[str | None, Header()] = None):
    settings = load_environment()
    token = settings.get("SANITY_DASHBOARD_TOKEN", "") or ""
    reviewer = (settings.get("SANITY_DASHBOARD_REVIEWER", "") or "").strip()
    if len(token) < 32 or not reviewer or len(reviewer) > 100:
        raise ReviewError("DASHBOARD_NOT_CONFIGURED", "Configure a token and a fixed reviewer")
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not secrets.compare_digest(supplied.encode(), token.encode()):
        raise ReviewError("UNAUTHORIZED", "Authentication required")
    return {"reviewer": reviewer, "access_mode": access_mode(settings)}


def summary_only(value):
    return {key: val for key, val in value.items() if key != "units"}


def create_app():
    app = FastAPI(title="OA Sanity Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestBoundary)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.exception_handler(ReviewError)
    async def review_error(request, exc):
        code = exc.code
        status = (401 if code == "UNAUTHORIZED" else 404 if code.endswith("NOT_FOUND") else
                  503 if code.startswith(("DATABASE_", "DBAAS_", "DASHBOARD_")) else
                  409 if code in {"REVISION_CONFLICT", "REQUEST_CONFLICT", "REVIEW_BLOCKED", "FAIL_CANNOT_PASS",
                                 "STATE_CORRUPT", "STATE_CONFLICT", "EVIDENCE_NOT_RECEIVED", "LEASE_LOST"} else 400)
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

    @app.get("/api/session")
    def session(actor=Depends(authenticated)):
        direct = actor["access_mode"] == "postgresql"
        return {**actor, "writable": direct,
                "notice": "직접 PostgreSQL 검증 모드 · 접속/권한은 데이터 요청 시 검증" if direct else
                          "현재 DBaaS 방식은 임시 읽기 전용입니다. 정식 전체 접근 방식이 필요합니다."}

    @app.get("/api/bundles")
    def bundles(offset: Offset = 0, limit: Limit = 20, actor=Depends(authenticated)):
        with Database("oa") as db, db.transaction(readonly=True):
            total = db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='RECEIVED_BUNDLE'").fetchone()["n"]
            rows = db.execute("SELECT bundle_id,created_at FROM {oa} WHERE record_type='RECEIVED_BUNDLE' "
                              "ORDER BY created_at DESC,record_id DESC LIMIT %s OFFSET %s", (limit, offset)).fetchall()
            store, items = OAStore(db), []
            for row in rows:
                payload, decisions, _, revision = store.load(row["bundle_id"])
                store.report(row["bundle_id"])
                items.append({**row, "project": payload["run"]["project"], "run_id": payload["run"]["run_id"],
                              "review_revision": revision, "coverage": summary_only(coverage(payload, decisions))})
            return {"items": items, "total": total, "offset": offset, "limit": limit}

    @app.post("/api/receive")
    def receive(body: Command, actor=Depends(authenticated)):
        from argparse import Namespace
        result = commands.receive(Namespace(message_id=None, lease_seconds=300))
        if "coverage" in result:
            result["coverage"] = summary_only(result["coverage"])
        return result

    @app.get("/api/bundles/{bundle_id}")
    def bundle(bundle_id: BundleID, unit_offset: Offset = 0, unit_limit: Limit = 50, actor=Depends(authenticated)):
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
                    "context": {"ai": context.get("ai", {"status": "NOT_CONFIGURED"})}}

    @app.get("/api/bundles/{bundle_id}/units/{unit_id}/groups")
    def groups(bundle_id: BundleID, unit_id: ID, offset: Offset = 0, limit: Limit = 20, actor=Depends(authenticated)):
        with Database("oa") as db, db.transaction(readonly=True):
            payload, _, _, _ = OAStore(db).load(bundle_id)
            if unit_id not in {u["review_unit_id"] for u in payload["review_units"]}:
                raise ReviewError("UNIT_NOT_FOUND", "Unknown unit")
            values = [g for g in payload["finding_groups"] if g["review_unit_id"] == unit_id]
            return {"items": values[offset:offset + limit], "total": len(values), "offset": offset, "limit": limit}

    @app.post("/api/bundles/{bundle_id}/reviews")
    def review(bundle_id: BundleID, body: ReviewCommand, actor=Depends(authenticated)):
        with Database("oa") as db, db.transaction():
            result = OAStore(db).review(bundle_id, body.unit_id, actor["reviewer"], body.conclusion,
                                       body.reason, body.request_id, body.expected_revision)
            result["coverage"] = summary_only(result["coverage"])
            return result

    @app.get("/api/bundles/{bundle_id}/report")
    def report(bundle_id: BundleID, actor=Depends(authenticated)):
        with Database("oa") as db, db.transaction(readonly=True):
            body = OAStore(db).report(bundle_id)
        return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{bundle_id}.md"'})

    @app.post("/api/bundles/{bundle_id}/evidence")
    def evidence(bundle_id: BundleID, body: EvidenceCommand, actor=Depends(authenticated)):
        return request_evidence(bundle_id, body.unit_id, body.record_ids, body.reason, body.request_id, body.ttl_seconds)

    @app.get("/api/bundles/{bundle_id}/requests")
    def requests(bundle_id: BundleID, offset: Offset = 0, limit: Limit = 20, actor=Depends(authenticated)):
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
    def response_receive(request_id: UUID, body: Command, actor=Depends(authenticated)):
        return receive_evidence(request_id)

    @app.get("/api/evidence/{request_id}")
    def response_preview(request_id: UUID, actor=Depends(authenticated)):
        with Database("oa") as db, db.transaction(readonly=True):
            response = Queue(db, "vwp_to_oa").get("response-" + str(request_id))
            if response is None:
                raise ReviewError("EVIDENCE_NOT_FOUND", "No response")
            if response["status"] != "RECEIVED":
                raise ReviewError("EVIDENCE_NOT_RECEIVED", "Validate and receive the response first")
            data = _validate_response(db, response)
            records = data["records"] if data else []
            return {"result": response["payload"]["result"], "record_count": len(records),
                    "records": [{"record_id": r["record_id"], "file_id": r["file_id"], "raw": r["raw"][:4000],
                                 "raw_truncated": len(r["raw"]) > 4000} for r in records]}

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def unknown_api(path: str):
        return JSONResponse({"code": "NOT_FOUND"}, 404)

    dist = Path(__file__).with_name("dashboard") / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:
        @app.get("/")
        def unbuilt():
            return PlainTextResponse("Build oa-bridge/dashboard first: npm ci && npm run build", 503)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    # A single-user prototype; do not expose it beyond loopback or trust forwarded headers.
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False, proxy_headers=False, log_level="critical")
