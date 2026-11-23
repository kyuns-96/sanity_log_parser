"""OA-side AI connection and credential pinning for Area/Power requests."""
from __future__ import annotations

from common.sanity_common import ReviewError
from common.sanity_vwp_ai import require
from oa.sanity_ai_connections import ConnectionStore
from oa.sanity_ai_task_defaults import task_selection
from oa.sanity_ai_tokens import UserTokenStore


def token_store(store, connection_id, advisor):
    if connection_id is not None:
        return store.tokens(store.get(connection_id))
    return UserTokenStore(store.db, store.settings) if advisor.settings.token_source == "user" else None


def capture_context(access, *, tasks=("CLUSTER", "REVIEW"), defaults=None, vwp_only=False):
    """Capture the same initial choices as log classification and judgment."""
    actor = getattr(access, "actor", None)
    if getattr(actor, "auth_method", None) != "sso" or getattr(actor, "access", None) is None:
        return {task: {"error": "AI_AUTHENTICATED_REQUEST_REQUIRED"} for task in tasks}
    store = ConnectionStore(actor.access.db)
    result = {}
    for task in tasks:
        try:
            _, selected, task_default = task_selection(store, actor.user_id, task, defaults=defaults)
            if task_default is not None:
                require(task_default.get("applied"), task_default.get("reason") or "AI_CONNECTION_UNAVAILABLE")
            if vwp_only:
                require(selected and selected.get("execution_site") == "vwp", "AI_VWP_CONNECTION_REQUIRED")
            if selected:
                require(selected.get("credential_ready"), selected.get("credential_error") or "AI_USER_TOKEN_REQUIRED")
                connection_id = selected["id"]
                store.authorize_user(connection_id, actor.user_id)
                advisor = store.advisor(connection_id, selected["model"])
            else:
                require(
                    task_default is None,
                    task_default.get("reason", "AI_CONNECTION_UNAVAILABLE") if task_default else "AI_CONNECTION_UNAVAILABLE",
                )
                connection_id, advisor = None, store.default_advisor()
                require(advisor is not None, "AI_NOT_CONFIGURED")
            tokens = token_store(store, connection_id, advisor)
            credential_pin = None
            if tokens is not None:
                _, revision = tokens.get_token(actor.user_id)
                credential_pin = {"revision": revision, "scope": tokens.credential_scope}
            description = advisor.describe()
            result[task] = {
                "user_id": actor.user_id,
                "connection_id": connection_id,
                "model": description["model"],
                "config_sha256": description["config_sha256"],
                "credential_pin": credential_pin,
            }
        except ReviewError as error:
            # Missing AI must not prevent creation of an exact numerical comparison.
            result[task] = {"user_id": actor.user_id, "error": error.code}
    return result
