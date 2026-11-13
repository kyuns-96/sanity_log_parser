"""VWP upload HTTP boundary; OA and VWP exchange work only through the DB.

Run: uvicorn area_power.vwp_server.app:create_app --factory
Configuration uses AREA_POWER_ environment variables.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from area_power.common.config import Settings
from area_power.common.db import Database
from area_power.common.errors import ConfigurationError, DomainError
from area_power.common.jsonutil import loads, public
from area_power.common.models import UploadMetadata
from area_power.vwp_server.ingest import ingest_upload

logger = logging.getLogger(__name__)
_METADATA_LIMIT = 65536


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)


class _SafeErrors:
    """Do not let ASGI server traceback logging reveal DB exception contents."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False
        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        try:
            await self.app(scope, receive, safe_send)
        except Exception as exc:
            logger.error("VWP request failed (%s)", type(exc).__name__)
            if not started:
                await _error("internal_error", "Request could not be completed.", 500)(scope, receive, send)


class _BoundedBody:
    """Enforce the wire-size limit as the ASGI stream is read, before spooling."""
    def __init__(self, app, maximum: int):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    await _error("invalid_request", "Invalid Content-Length.", 400)(scope, receive, send)
                    return
                if length < 0 or length > self.maximum:
                    await _error("file_too_large", "Upload exceeds the configured size limit.", 413)(scope, receive, send)
                    return
        total = 0
        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.maximum:
                    scope["upload_too_large"] = True
                    # MultipartParser closes partial spooled files on this exception.
                    raise MultiPartException("Upload exceeds the configured size limit.")
            return message
        await self.app(scope, bounded_receive, send)


def create_app(settings=None, db=None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await run_in_threadpool(db.check_schema)
        except Exception as exc:
            logger.error("VWP readiness unavailable (%s)", type(exc).__name__)
            raise ConfigurationError("Service is not ready.") from None
        yield

    app = FastAPI(title="Design Atlas CSV ingestion", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.db = db
    app.add_middleware(_BoundedBody, maximum=settings.max_upload_bytes + _METADATA_LIMIT + 65536)
    app.add_middleware(_SafeErrors)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return _error(exc.code, str(exc), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request, exc):
        return _error("invalid_request", "Request fields are invalid.", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        if request.scope.get("upload_too_large"):
            return _error("file_too_large", "Upload exceeds the configured size limit.", 413)
        messages = {400: "Malformed request.", 404: "Not found.", 405: "Method not allowed.", 413: "Upload exceeds the configured size limit."}
        return _error("http_error", messages.get(exc.status_code, "Request could not be processed."), exc.status_code)

    @app.exception_handler(ConfigurationError)
    async def configuration_error(request, exc):
        return _error("service_unavailable", "Service is not configured or ready.", 503)


    @app.get("/health")
    async def health():
        try:
            await run_in_threadpool(db.check_schema)
        except Exception as exc:
            logger.warning("VWP readiness unavailable (%s)", type(exc).__name__)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.post("/api/area-power/uploads", status_code=201)
    async def upload(request: Request):
        async with request.form(max_files=1, max_fields=1, max_part_size=_METADATA_LIMIT) as form:
            if len(form.multi_items()) != 2 or set(form.keys()) != {"metadata", "file"}:
                raise DomainError("Provide exactly one metadata field and one file.", code="invalid_request")
            metadata_json = form["metadata"]
            file = form["file"]
            if not isinstance(metadata_json, str) or not isinstance(file, UploadFile):
                raise DomainError("metadata must be JSON text and file must be an uploaded file.", code="invalid_request")
            if len(metadata_json.encode("utf-8")) > _METADATA_LIMIT:
                raise DomainError("Metadata exceeds the configured size limit.", code="file_too_large", status_code=413)
            try:
                metadata = UploadMetadata.model_validate(loads(metadata_json))
            except (ValueError, TypeError, ValidationError) as exc:
                raise DomainError("Upload metadata is invalid.", code="invalid_metadata") from exc
            filename = file.filename
            if not filename or not filename.strip() or len(filename) > 1024 or any(ord(char) < 32 for char in filename):
                raise DomainError("A valid original filename is required.", code="invalid_request")
            chunks = []
            length = 0
            while True:
                chunk = await file.read(min(65536, settings.max_upload_bytes - length + 1))
                if not chunk:
                    break
                length += len(chunk)
                if length > settings.max_upload_bytes:
                    raise DomainError("Upload exceeds the configured size limit.", code="file_too_large", status_code=413)
                chunks.append(chunk)
            result = await run_in_threadpool(ingest_upload, db, settings, b"".join(chunks), filename, metadata, "vwp_server")
        return JSONResponse(public(result), status_code=201)

    @app.get("/api/area-power/uploads/{upload_id}")
    async def upload_metadata(upload_id: UUID):
        def read_upload():
            with db.connect() as conn:
                return db.upload(conn, upload_id)
        return JSONResponse(public(await run_in_threadpool(read_upload)))

    return app
