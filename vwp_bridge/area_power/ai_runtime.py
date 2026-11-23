"""Central VWP AI runtime over the logical OA DB and user-pinned AI provider.

Only nonsecret configuration/credential pins cross the Area DB queue. As in
``oa.ai_worker --site vwp``, execution uses the logical OA Database on VWP.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

from psycopg import sql

from common.area_power.errors import DomainError
from common.area_power.jsonutil import dumps, loads
from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError, digest
from common.sanity_config import load_environment
from common.sanity_db import Database
from common.sanity_vwp_ai import require
from oa.area_power.access import AreaAccess
from oa.area_power.ai_context import token_store
from oa.sanity_access import AccessStore, access_lock, administrator_id
from oa.sanity_ai_connections import ConnectionStore


@dataclass
class Runtime:
    db: object
    subject: dict
    context: dict
    receipts: list = field(default_factory=list)

    def wait_for_retry(self, seconds):
        deadline = monotonic() + seconds
        while True:
            with self.db.connect() as conn:
                if not conn.execute(sql.SQL("SELECT 1 FROM {} WHERE id=%s").format(
                        self.db.table("di_area_power_tracking_record")), (self.subject["id"],)).fetchone():
                    raise DomainError("Analysis removed.", "analysis_deleted", 410)
            remaining = deadline - monotonic()
            if remaining <= 0:
                return
            sleep(min(1, remaining))

    def _resolve(self, host_db, task, *, token=False):
        pin = self.context.get(task, {})
        require(isinstance(pin, dict), "AI_CONFIG_CHANGED")
        if pin.get("error"):
            raise ReviewError(pin["error"], "The selected AI connection is unavailable")
        require(set(pin) == {"user_id", "connection_id", "model", "config_sha256", "credential_pin"}, "AI_CONFIG_CHANGED")
        access_lock(host_db)
        access = AccessStore(host_db, administrator_id(load_environment()))
        access.require_ready()
        actor = SimpleNamespace(user_id=pin["user_id"], auth_method="sso", access=access)
        permission = AreaAccess(actor)
        with self.db.connect() as conn:
            current_subject = self.db.record(conn, self.subject["id"])
            permission.row(self.db, conn, current_subject, write=True)
        store = ConnectionStore(host_db)
        if pin["connection_id"] is not None:
            store.authorize_user(pin["connection_id"], pin["user_id"])
            advisor = store.advisor(pin["connection_id"], pin["model"])
        else:
            advisor = store.default_advisor(pin["model"])
            require(advisor is not None, "AI_CONFIG_CHANGED")
        require(advisor.describe()["config_sha256"] == pin["config_sha256"], "AI_CONFIG_CHANGED")
        tokens = token_store(store, pin["connection_id"], advisor)
        if tokens is not None:
            credential, revision = tokens.get_token(pin["user_id"])
            require({"revision": revision, "scope": tokens.credential_scope} == pin["credential_pin"], "AI_CREDENTIAL_CHANGED")
            if token:
                return advisor.with_user_token(credential)
        else:
            require(pin["credential_pin"] is None, "AI_CREDENTIAL_CHANGED")
        return advisor if token else None

    def validate(self, tasks=("CLUSTER", "REVIEW")):
        """Check pinned authority and credentials before processing observations."""
        for task in tasks:
            pin = self.context.get(task, {})
            require(isinstance(pin, dict), "AI_CONFIG_CHANGED")
            if pin.get("error"):
                raise ReviewError(pin["error"], "The selected AI connection is unavailable")
            require(bool(pin), "AI_NOT_CONFIGURED")
        with executing_on("vwp"):
            with Database("oa") as host_db, host_db.transaction():
                for task in tasks:
                    self._resolve(host_db, task)

    def infer(self, task, context, instruction, prompt_version):
        pin = self.context.get(task, {})
        if pin.get("error"):
            raise ReviewError(pin["error"], "The selected AI connection is unavailable")
        require(bool(pin), "AI_NOT_CONFIGURED")
        with executing_on("vwp"):
            with Database("oa") as host_db, host_db.transaction():
                advisor = self._resolve(host_db, task, token=True)
                advisor = advisor.bind_execution(
                    call_id=str(uuid4()),
                    job_id=str(self.subject["id"]),
                    actor_id=pin["user_id"],
                    scope_sha256=digest(dumps(
                        {key: self.subject.get(key) for key in ("project_id", "version_id", "block_id", "snapshot_id")},
                        canonical=True,
                    )),
                )
            value, metadata = advisor.infer_json(
                {"context_json": dumps(context, canonical=True)},
                instruction,
                prompt_version,
                decoder=loads,
            )
            with Database("oa") as host_db, host_db.transaction():
                self._resolve(host_db, task)
            self.receipts.append(metadata)
            return value


def runtime_for(db, subject, comparison=None):
    context = subject["payload"].get("ai_context")
    if context is None and comparison is not None:
        context = comparison["payload"].get("ai_context")
    return Runtime(db, subject, context or {})
