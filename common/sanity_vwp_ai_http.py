"""Bounded OpenAI-compatible HTTP on VWP, using only local profile credentials."""
from common.sanity_ai_cache import validate_wire_cache
from common.sanity_ai_http import (
    build_auth_headers,
    build_tls_context,
    stream_openai_post,
    unknown_outcome_error,
)
from common.sanity_ai_reasoning import validate_wire_reasoning
from common.sanity_common import ReviewError
from common.sanity_privacy import SecretGuard
from common.sanity_vwp_ai import require, strict_json


def _unknown(reason):
    return unknown_outcome_error(reason, "VWP model completion is unknown")


def validate_body(raw, profile):
    require(len(raw) <= profile["max_input_bytes"], "AI_INPUT_LIMIT")
    body = strict_json(raw, profile["max_input_bytes"])
    request_key = "messages" if profile["api"] == "chat_completions" else "input"
    optional = {"response_format", "max_tokens", "max_completion_tokens", "reasoning_effort", "chat_template_kwargs"} if request_key == "messages" else {"text", "max_output_tokens", "store", "reasoning"}
    optional.add("cache")
    if profile["auth_mode"] == "bearer_x_client":
        optional.add("extraData")
    require({"model", "stream", request_key} <= set(body) <= {"model", "stream", request_key} | optional)
    validate_wire_reasoning(body, profile["api"])
    validate_wire_cache(body)
    require(body["model"] in profile["models"] and body["stream"] is False and
            isinstance(body[request_key], list) and len(body[request_key]) == 2)
    require([m.get("role") if isinstance(m, dict) else None for m in body[request_key]] == ["system", "user"])
    require(all(set(m) == {"role", "content"} and isinstance(m["content"], str) for m in body[request_key]))
    if request_key == "input":
        require(body.get("store") is False)
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        if key in body:
            require(type(body[key]) is int and 1 <= body[key] <= 1000000)
    if "response_format" in body:
        require(body["response_format"] == {"type":"json_object"})
    if "text" in body:
        require(body["text"] == {"format":{"type":"json_object"}})
    if profile["auth_mode"] == "bearer_x_client":
        extra = body.get("extraData")
        require(isinstance(extra, dict) and set(extra) == {"clientName", "clientVersion", "sessionId"}
                and extra["clientName"] == profile["client_name"] and extra["clientVersion"] == profile["client_version"])
        if profile["session_mode"] == "fixed":
            require(extra["sessionId"] == profile["session_id"])
        else:
            from common.sanity_db import uuid_value
            require(str(uuid_value(extra["sessionId"])) == extra["sessionId"])
    return body


def invoke(raw, profile, *, timeout, max_output_bytes, transport=None,observer=None):
    attempt={"model_called":False}
    try:
        return _invoke(raw,profile,timeout=timeout,max_output_bytes=max_output_bytes,
                       transport=transport,observer=observer,attempt=attempt)
    except ReviewError as error:
        error.model_called=attempt["model_called"]
        error.response_complete=attempt.get("response_complete",False)
        for key in ("http_status","retry_after_seconds"):
            if key in attempt:setattr(error,key,attempt[key])
        raise


def _invoke(raw, profile, *, timeout, max_output_bytes, transport, observer, attempt):
    validate_body(raw, profile)
    headers = build_auth_headers(
        profile["auth_mode"],
        profile["token"],
        profile["client_api_key"] if profile["auth_mode"] == "bearer_x_client" else "",
    )
    tls = build_tls_context(
        (profile["endpoint"], profile["fallback_endpoint"]),
        profile["ca_path"],
        error_code="AI_VWP_PROFILE_CONFIG",
        message="Cannot load VWP model TLS trust",
    )
    limit = min(profile["max_output_bytes"], max_output_bytes)
    timeout = min(profile["timeout"], timeout)
    body, routing = stream_openai_post(
        raw,
        headers,
        endpoint=profile["endpoint"],
        fallback_endpoint=profile["fallback_endpoint"],
        timeout=timeout,
        max_output_bytes=limit,
        tls=tls,
        transport=transport,
        observer=observer,
        attempt=attempt,
        auth_401=True,
        sending_model_called=None,
        unknown_model_called=None,
        label="VWP model",
    )
    SecretGuard({
        "VWP_MODEL_SECRET": profile["token"],
        "VWP_CLIENT_SECRET": profile["client_api_key"],
        "VWP_ENDPOINT_SECRET": profile["endpoint"],
        "VWP_BACKUP_SECRET": profile["fallback_endpoint"],
    }).ensure_public(strict_json(body, limit))
    return body, routing
