"""Area/Power routes for the authenticated Sanity host; no standalone app/login."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import logging
import traceback
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError

from area_power.common.config import Settings
from area_power.common.db import Database
from area_power.common.errors import ConfigurationError, DomainError
from area_power.common.jsonutil import public
from area_power.common.models import ComparisonRequest
from oa.sanity_auth import AuthError
from .access import AreaAccess
from .service import (
    AlertStateInput, BaselineInput, ConfigInput, RejudgeInput, RerunInput,
    SelectionInput, Service, CONFIG_TYPES,
)

LOG = logging.getLogger('area_power.oa')


class SafeAreaRoute(APIRoute):
    """Use the host's fixed-code error contract without exposing domain inputs."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except ConfigurationError as exc:
                cause = exc.__cause__ or exc.__context__ or exc
                state = getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)
                if not isinstance(state, str) or len(state) != 5 or any(char not in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ' for char in state):
                    state = None
                frames = [f'{frame.filename.rsplit("/", 1)[-1]}:{frame.name}:{frame.lineno}'
                          for frame in traceback.extract_tb(cause.__traceback__)]
                LOG.error('Area API unavailable: code=AREA_POWER_UNAVAILABLE type=%s sqlstate=%s at=%s',
                          type(cause).__name__, state, '; '.join(frames))
                return JSONResponse({"code": "AREA_POWER_UNAVAILABLE"}, 503)
            except DomainError as exc:
                return JSONResponse({"code": exc.code.upper()}, exc.status_code)
            except ValidationError:
                return JSONResponse({"code": "INVALID_REQUEST"}, 400)
            except Exception as exc:
                # Diagnostics contain code locations only, never exception
                # messages, request values, SQL, credentials or CSV contents.
                frames = [f'{frame.filename.rsplit("/", 1)[-1]}:{frame.name}:{frame.lineno}'
                          for frame in traceback.extract_tb(exc.__traceback__)]
                missing = exc.name if isinstance(exc, ModuleNotFoundError) else None
                if (not isinstance(missing, str) or len(missing) > 200
                        or not all(part.isascii() and part.isidentifier() for part in missing.split('.'))):
                    missing = None
                LOG.error('Unhandled Area API error: %s module=%s (%s)',
                          type(exc).__name__, missing, '; '.join(frames))
                raise
        return safe


def create_area_power_router(authorized_dependency, authenticated_dependency):
    router = APIRouter(prefix="/api/area-power", route_class=SafeAreaRoute)
    authorization_scope = contextmanager(authorized_dependency)

    def json(value, status_code=200):
        return JSONResponse(public(value), status_code=status_code)

    def reauthenticated(request, actor):
        current = authenticated_dependency(request)
        if (current.user_id, current.auth_method) != (actor.user_id, actor.auth_method):
            raise AuthError('UNAUTHORIZED')
        return current

    def access(request: Request, identity=Depends(authorized_dependency, scope="function")):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            from oa.sanity_access import require_mutation_request
            require_mutation_request(request)
        return AreaAccess(identity)

    def service(permission=Depends(access)):
        # Runtime settings/connections are independent from Sanity's OA DB. No
        # eager DB connection and no HTTP forwarding to a numerical worker.
        return Service(Database(Settings.from_env()), permission)

    def filters(project_id: UUID | None = None, version_id: UUID | None = None,
                block_id: UUID | None = None, snapshot_id: UUID | None = None):
        return {key: value for key, value in {
            "project_id": project_id, "version_id": version_id,
            "block_id": block_id, "snapshot_id": snapshot_id,
        }.items() if value is not None}

    @router.get("/access")
    def permissions(permission=Depends(access)):
        return json(permission.public())

    @router.get("/health")
    def health(context=Depends(service)):
        context.db.raw.check_schema()
        return json({"ready": True})

    @router.get("/projects/{identifier}/baseline")
    def project_baseline(identifier: UUID, context=Depends(service)):
        return json(context.project_baseline(identifier))

    @router.post("/projects/{identifier}/baseline")
    def set_project_baseline(identifier: UUID, body: BaselineInput, context=Depends(service)):
        return json(context.project_baseline(identifier, body, context.access.actor.user_id))

    @router.get("/dashboard")
    def dashboard(scope=Depends(filters), context=Depends(service),
                  limit: int = Query(default=100, ge=1, le=500),
                  offset: int = Query(default=0, ge=0)):
        return json(context.dashboard(scope, limit, offset))

    @router.get("/uploads")
    def uploads(scope=Depends(filters), context=Depends(service), state: str | None = None,
                limit: int = Query(default=100, ge=1, le=500),
                offset: int = Query(default=0, ge=0)):
        with context.db.connect() as conn:
            return json({"items": context.db.uploads(conn, **scope, state=state,
                                                     limit=limit, offset=offset)})

    @router.get("/uploads/{identifier}")
    def upload(identifier: UUID, context=Depends(service)):
        with context.db.connect() as conn:
            return json(context.db.upload(conn, identifier))

    @router.get("/uploads/{identifier}/hierarchy")
    def hierarchy(identifier: UUID, request: Request, actor=Depends(authenticated_dependency)):
        from area_power.common.analysis import _validate_frozen_upload

        raw = Database(Settings.from_env())
        with authorization_scope(request, actor) as identity:
            context = Service(raw, AreaAccess(identity))
            with context.db.connect() as conn:
                selected = context.ready_upload(conn, identifier)

        # The host's permission connection must not remain idle while reading
        # the compressed original, parsing CSV or serializing the response.
        with raw.connect() as conn:
            upload = raw.upload(conn, identifier, include_blob=True)
        _validate_frozen_upload(selected, upload)
        response = json(Service(raw).upload_hierarchy(upload))

        current_actor = reauthenticated(request, actor)
        with authorization_scope(request, current_actor) as identity:
            context = Service(raw, AreaAccess(identity))
            with context.db.connect() as conn:
                current = context.ready_upload(conn, identifier)
                _validate_frozen_upload(upload, current)
            return response

    @router.get("/uploads/{identifier}/download")
    def download(identifier: UUID, context=Depends(service)):
        with context.db.connect() as conn:
            row = context.db.upload(conn, identifier, include_blob=True)
            blob = bytes(row["csv_gzip"])
        return Response(blob, media_type="application/gzip", headers={
            "Content-Disposition": f'attachment; filename="{identifier}.csv.gz"',
            "X-Content-Type-Options": "nosniff",
        })

    @router.get("/records")
    def records(scope=Depends(filters), context=Depends(service),
                record_type: str | None = None, parent_id: UUID | None = None,
                state: str | None = None, latest: bool | None = None,
                limit: int = Query(default=100, ge=1, le=500),
                offset: int = Query(default=0, ge=0)):
        with context.db.connect() as conn:
            return json({"items": context.db.records(
                conn, record_type, **scope, parent_id=parent_id, state=state,
                latest=(record_type is None or record_type in CONFIG_TYPES) if latest is None else latest,
                limit=limit, offset=offset,
            )})

    @router.get("/records/{identifier}")
    def record(identifier: UUID, context=Depends(service)):
        with context.db.connect() as conn:
            return json(context.db.record(conn, identifier))

    @router.post("/config")
    def config(body: ConfigInput, context=Depends(service)):
        return json(context.config(body, context.access.actor.user_id), 201)

    @router.post("/selections")
    def selection(body: SelectionInput, context=Depends(service)):
        return json(context.selection(body, context.access.actor.user_id))

    @router.post("/comparisons")
    def compare(body: ComparisonRequest, request: Request, actor=Depends(authenticated_dependency)):
        from area_power.common.analysis import comparison_coverage
        raw = Database(Settings.from_env())
        with authorization_scope(request, actor) as identity:
            result, designation = Service(raw, AreaAccess(identity)).prepare_comparison(body, identity.user_id)
        selected = result['comparison']
        coverage = None
        if selected['state'] != 'completed':
            try:
                coverage = comparison_coverage(raw, raw.settings, selected)
            except Exception as exc:
                error = {'code': exc.code if isinstance(exc, DomainError) else 'comparison_preparation_failed'}
                with authorization_scope(request, reauthenticated(request, actor)) as identity:
                    Service(raw, AreaAccess(identity)).finish_comparison(selected, None, identity.user_id,
                        error=error, baseline_designation=designation)
                raise
        with authorization_scope(request, reauthenticated(request, actor)) as identity:
            context = Service(raw, AreaAccess(identity))
            result['comparison'] = context.finish_comparison(selected, coverage, identity.user_id, baseline_designation=designation)
            return json(result)

    @router.get("/comparisons/{identifier}")
    def comparison(identifier: UUID, request: Request, actor=Depends(authenticated_dependency), view: Literal["full", "summary", "hierarchy"] = "full",
                   offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500),
                   search: str = Query(default="", max_length=1000),
                   parent: str | None = Query(default=None, max_length=64, pattern="^[0-9a-f]*$"),
                   metrics: str = Query(default="", max_length=2000, pattern="^[a-z0-9_,]*$"),
                   min_area: Decimal | None = Query(default=None, ge=0)):
        observed = [name for name in metrics.split(",") if name] or None
        from area_power.common.models import COMPARISON_FORMAT
        raw = Database(Settings.from_env())
        with authorization_scope(request, actor) as identity:
            context = Service(raw, AreaAccess(identity))
            with context.db.connect() as conn:
                selected = context.db.record(conn, identifier, kind='comparison')
            if view == 'summary' or selected['payload'].get('result_format') != COMPARISON_FORMAT:
                return json(context.comparison_detail(identifier, view=view, offset=offset, limit=limit, search=search, parent=parent, metrics=observed, min_area=min_area))
        value = Service(raw).comparison_detail(identifier, view=view, offset=offset, limit=limit, search=search, parent=parent, metrics=observed, min_area=min_area)
        response = json(value)
        with authorization_scope(request, reauthenticated(request, actor)) as identity:
            context = Service(raw, AreaAccess(identity))
            with context.db.connect() as conn:
                context.validate_comparison(conn, value['comparison'])
                # Observations use the frozen uploads checked above. AI records
                # are separate persisted inputs and need their own scope check.
                context.db.references(conn, {'ai_runs': value['ai_runs'], 'judgements': value['judgements']})
            return response

    @router.post("/comparisons/{identifier}/rejudge")
    def rejudge(identifier: UUID, body: RejudgeInput, context=Depends(service)):
        return json(context.rejudge(identifier, body, context.access.actor.user_id))

    @router.post("/alerts/{identifier}/state")
    def alert_state(identifier: UUID, body: AlertStateInput, context=Depends(service)):
        return json(context.alert_state(identifier, body, context.access.actor.user_id))

    @router.post("/jobs/{identifier}/rerun")
    def rerun(identifier: UUID, body: RerunInput, context=Depends(service)):
        return json(context.rerun(identifier, body, context.access.actor.user_id), 201)

    return router
