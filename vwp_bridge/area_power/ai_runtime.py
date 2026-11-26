"""Central VWP AI runtime, retry/backoff loop, and attempt lifecycle helpers.

Only nonsecret configuration/credential pins cross the Area DB queue. As in
``oa.ai_worker --site vwp``, execution uses the logical OA Database on VWP, with
all ``oa.*`` imports deferred to execution time so ``vwp_bridge`` HTTP/ingest
services start cleanly without ``oa/`` installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

from psycopg import sql

from common.area_power.analysis import advisory_lock, existing_dedupe
from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest as area_digest, dumps, loads
from common.area_power.triage import RUN_KEYS as TRIAGE_RUN_KEYS
from common.sanity_ai_execution import executing_on
from common.sanity_ai_retry import receipt, retry_delay_seconds
from common.sanity_common import ReviewError, digest
from common.sanity_config import load_environment
from common.sanity_db import Database
from common.sanity_vwp_ai import require

ACTOR = "vwp-worker"
LEGACY_PROMPT_VERSION = "hierarchy-evidence-2"
AI_CONTEXT_ERRORS = frozenset({
    "AI_AUTHENTICATED_REQUEST_REQUIRED", "AI_NOT_CONFIGURED", "AI_CONFIG", "AI_BATCH_CONFIG", "AI_CONFIG_CHANGED",
    "AI_CONNECTION_CONFIG", "AI_CONNECTION_CORRUPT", "AI_CONNECTION_UNAVAILABLE",
    "AI_MODEL_NOT_ALLOWED", "AI_CREDENTIAL_CHANGED", "AI_CREDENTIAL_SCOPE_CHANGED",
    "AI_USER_TOKEN_REQUIRED", "AI_USER_TOKEN_EXPIRED", "AI_TOKEN_STORE_NOT_CONFIGURED",
    "AI_TOKEN_UNAVAILABLE", "AI_AUTH_FAILED", "AI_VWP_PROFILE_UNAVAILABLE",
    "FORBIDDEN", "RESOURCE_NOT_FOUND",
})


class AIError(Exception):
    """Safe failure code; response/provider bodies are deliberately not retained."""


class RetryableValidationError(Exception):
    """Raised by a response parser when output is malformed and eligible for retry."""

    def __init__(self, code: str, message: str = "", *, fallback: Any = None, has_fallback: bool = False):
        super().__init__(message or code)
        self.code = code
        self.fallback = fallback
        self.has_fallback = has_fallback


def aborts_ai_context(code: str) -> bool:
    return code in AI_CONTEXT_ERRORS or code.startswith(("AUTHORIZATION_", "DATABASE_", "DBAAS_"))


def http_status_of(error: Exception) -> int | None:
    status = getattr(error, "http_status", None)
    return status if isinstance(status, int) and 100 <= status <= 599 else None


def failure_detail(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, (ReviewError, DomainError)):
        code = exc.code
    elif isinstance(exc, AIError):
        code = str(exc)
    else:
        code = "analysis_failure"
    return {"code": code, "error_class": type(exc).__name__}


def new_attempt(db, comparison: dict, original: dict, job_id, *, prompt_version: str = LEGACY_PROMPT_VERSION) -> dict:
    key = area_digest({"explicit_job_attempt": job_id, "previous_ai_run": original["id"]})
    payload = {
        k: original["payload"][k]
        for k in ("change_ids", "changes", "recipients", "targets", "ai_context", *TRIAGE_RUN_KEYS)
        if k in original["payload"]
    }
    payload.update({"coverage": {}, "prompt_version": prompt_version, "explicit_attempt_job_id": job_id})
    with db.connect() as conn:
        advisory_lock(conn, key)
        old = existing_dedupe(db, conn, "ai_run", key)
        return old or db.insert_record(
            conn, "ai_run", payload, ACTOR,
            parent_id=comparison["id"], previous_id=original["id"],
            upload_id=comparison["upload_id"], baseline_upload_id=comparison.get("baseline_upload_id"),
            state="queued", dedupe_key=key,
            **{k: comparison[k] for k in ("project_id", "version_id", "block_id", "snapshot_id", "condition_key") if comparison.get(k) is not None},
        )


def mirror_attempt(db, original_id, attempt_id, error=None) -> None:
    with db.connect() as conn:
        original = db.record(conn, original_id, lock=True, kind="ai_run")
        attempt = db.record(conn, attempt_id, lock=True, kind="ai_run")
        if error and attempt["state"] not in ("completed", "failed"):
            attempt = db.update_record(conn, attempt_id, ACTOR, state="failed", error_detail=error)
        payload = dict(original["payload"])
        payload.update({"rerun_ai_run_id": attempt_id, "coverage": attempt["payload"].get("coverage", {})})
        db.update_record(conn, original_id, ACTOR, state=attempt["state"], error_detail=attempt.get("error_detail"), payload=payload)


@dataclass
class Runtime:
    db: Any
    subject: dict
    context: dict
    receipts: list = field(default_factory=list)
    timeout_seconds: int | None = None

    def wait_for_retry(self, seconds: float) -> None:
        deadline = monotonic() + seconds
        while True:
            with self.db.connect() as conn:
                if not conn.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE id=%s").format(self.db.table("di_area_power_tracking_record")),
                    (self.subject["id"],),
                ).fetchone():
                    raise DomainError("Analysis removed.", "analysis_deleted", 410)
            remaining = deadline - monotonic()
            if remaining <= 0:
                return
            sleep(min(1, remaining))

    def _resolve(self, host_db, task: str, *, token: bool = False):
        from oa.area_power.access import AreaAccess
        from oa.area_power.ai_context import token_store
        from oa.sanity_access import AccessStore, access_lock, administrator_id
        from oa.sanity_ai_connections import ConnectionStore

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
        timeout = getattr(getattr(advisor, "settings", None), "timeout", None)
        if isinstance(timeout, int) and timeout > 0:
            self.timeout_seconds = timeout
        tokens = token_store(store, pin["connection_id"], advisor)
        if tokens is not None:
            credential, revision = tokens.get_token(pin["user_id"])
            require({"revision": revision, "scope": tokens.credential_scope} == pin["credential_pin"], "AI_CREDENTIAL_CHANGED")
            if token:
                return advisor.with_user_token(credential)
        else:
            require(pin["credential_pin"] is None, "AI_CREDENTIAL_CHANGED")
        return advisor if token else None

    def validate(self, tasks=("CLUSTER", "REVIEW")) -> None:
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

    def infer(self, task: str, context: Any, instruction: str, prompt_version: str, *, observer=None):
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
            if observer is not None and hasattr(advisor, "with_observer"):
                advisor = advisor.with_observer(observer)
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

    def infer_with_retry(
        self,
        task: str,
        evidence: Any,
        instructions: str,
        prompt_version: str,
        *,
        retry_limit: int = 0,
        parse: Callable[[Any], Any] | None = None,
        observer=None,
        on_attempt: Callable[[int, int], None] | None = None,
        on_retry: Callable[[int, int, str, int | None, float], None] | None = None,
        on_retry_waited: Callable[[], None] | None = None,
    ):
        """Invoke ``infer`` with bounded retries on confirmed retryable provider or validation errors."""
        max_attempts = retry_limit + 1
        for attempt in range(1, max_attempts + 1):
            if on_attempt is not None:
                on_attempt(attempt, max_attempts)
            retry_error = None
            last_error = None
            last_status = None
            try:
                raw = self.infer(task, evidence, instructions, prompt_version, observer=observer)
                return parse(raw) if parse is not None else raw
            except RetryableValidationError as vexc:
                if attempt >= max_attempts:
                    if vexc.has_fallback:
                        return vexc.fallback
                    raise ReviewError(vexc.code, str(vexc)) from None
                last_error = vexc.code
            except ReviewError as exc:
                if aborts_ai_context(exc.code) or exc.code == "AI_INPUT_LIMIT":
                    raise
                if attempt >= max_attempts or receipt(exc) is None:
                    raise
                retry_error = exc
                last_error = exc.code
                last_status = http_status_of(exc)
            delay = retry_delay_seconds(attempt, retry_error)
            if on_retry is not None:
                on_retry(attempt, max_attempts, last_error or "ai_invalid_response", last_status, delay)
            self.wait_for_retry(delay)
            if on_retry_waited is not None:
                on_retry_waited()


def runtime_for(db, subject: dict, comparison: dict | None = None) -> Runtime:
    context = subject["payload"].get("ai_context")
    if context is None and comparison is not None:
        context = comparison["payload"].get("ai_context")
    return Runtime(db, subject, context or {})
