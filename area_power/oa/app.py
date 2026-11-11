"""Area/Power routes for the authenticated Sanity host; no standalone app/login."""
from __future__ import annotations

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
from .access import AreaAccess
from .service import (
    AlertStateInput, ConfigInput, RejudgeInput, RerunInput,
    SelectionInput, Service, CONFIG_TYPES,
)


class SafeAreaRoute(APIRoute):
    """Use the host's fixed-code error contract without exposing domain inputs."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except ConfigurationError:
                return JSONResponse({"code": "AREA_POWER_UNAVAILABLE"}, 503)
            except DomainError as exc:
                return JSONResponse({"code": exc.code.upper()}, exc.status_code)
            except ValidationError:
                return JSONResponse({"code": "INVALID_REQUEST"}, 400)
        return safe


def create_area_power_router(authorized_dependency):
    router = APIRouter(prefix="/api/area-power", route_class=SafeAreaRoute)

    def json(value, status_code=200):
        return JSONResponse(public(value), status_code=status_code)

    def access(request: Request, identity=Depends(authorized_dependency)):
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
    def compare(body: ComparisonRequest, context=Depends(service)):
        return json(context.compare(body, context.access.actor.user_id))

    @router.get("/comparisons/{identifier}")
    def comparison(identifier: UUID, context=Depends(service)):
        return json(context.comparison_detail(identifier))

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
