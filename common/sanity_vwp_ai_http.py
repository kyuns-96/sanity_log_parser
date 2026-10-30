"""Bounded OpenAI-compatible HTTP on VWP, using only local profile credentials."""
import logging
import ssl
import time
from urllib.parse import urlsplit

import httpx

from common.sanity_common import ReviewError
from common.sanity_privacy import SecretGuard
from common.sanity_vwp_ai import require, strict_json
from common.sanity_ai_reasoning import validate_wire_reasoning
from common.sanity_ai_cache import validate_wire_cache


def _unknown(reason):
    error = ReviewError("AI_OUTCOME_UNKNOWN", "VWP model completion is unknown")
    error.unknown_reason = reason
    return error


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


def _invoke(raw, profile, *, timeout, max_output_bytes, transport,observer,attempt):
    validate_body(raw, profile)
    headers = {"Content-Type":"application/json", "Accept":"application/json", "Accept-Encoding":"identity"}
    if profile["auth_mode"] == "x_client_api_key":
        headers["x-client-api-key"] = profile["token"]
    else:
        headers["Authorization"] = "Bearer " + profile["token"]
        if profile["auth_mode"] == "bearer_x_client":
            headers["x-client-api-key"] = profile["client_api_key"]
    try:
        uses_tls = any(urlsplit(endpoint).scheme == "https" for endpoint in
                       (profile["endpoint"], profile["fallback_endpoint"]) if endpoint)
        tls = ssl.create_default_context(cafile=profile["ca_path"] or None) if uses_tls else True
    except (ValueError, OSError, ssl.SSLError):
        raise ReviewError("AI_VWP_PROFILE_CONFIG", "Cannot load VWP model TLS trust") from None
    for name in ("httpx", "httpcore", *list(logging.Logger.manager.loggerDict)):
        if name in ("httpx", "httpcore") or name.startswith(("httpx.", "httpcore.")):
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    limit = min(profile["max_output_bytes"], max_output_bytes)
    timeout = min(profile["timeout"], timeout)
    started = time.monotonic()
    endpoints = [("primary", profile["endpoint"])]
    if profile["fallback_endpoint"]:
        endpoints.append(("fallback", profile["fallback_endpoint"]))
    attempted = []
    for role, endpoint in endpoints:
        remaining = timeout - (time.monotonic() - started)
        require(remaining > 0, "AI_CONNECT_FAILED")
        attempted.append(role)
        response_started = False
        try:
            budget = httpx.Timeout(remaining, connect=remaining / 2) if len(endpoints) == 2 and role == "primary" else remaining
            with httpx.Client(timeout=budget, trust_env=False, follow_redirects=False, verify=tls, transport=transport) as client:
                if observer:observer("SENDING")
                attempt["model_called"]=None
                with client.stream("POST", endpoint, headers=headers, content=raw) as response:
                    response_started = True
                    attempt["model_called"]=True
                    attempt["http_status"]=response.status_code
                    retry_after=response.headers.get("Retry-After","")
                    if len(retry_after)<=10 and retry_after.isascii() and retry_after.isdecimal():
                        attempt["retry_after_seconds"]=min(300,int(retry_after))
                    if observer:observer("SERVER_RESPONDED")
                    require(response.status_code == 200, "AI_AUTH_FAILED" if response.status_code == 401 else "AI_CALL_FAILED")
                    require(response.headers.get("content-encoding", "identity").lower() == "identity", "INVALID_AI_RESPONSE")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        require(len(body) + len(chunk) <= limit, "AI_OUTPUT_LIMIT")
                        if time.monotonic() - started > timeout:raise _unknown("RESPONSE_DEADLINE")
                        body.extend(chunk)
                    if time.monotonic() - started > timeout:raise _unknown("RESPONSE_DEADLINE")
                    attempt["response_complete"]=True
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if response_started:
                raise _unknown("TRANSPORT_ERROR") from None
            attempt["model_called"]=False
            if role == endpoints[-1][0]:
                raise ReviewError("AI_CONNECT_FAILED", "Cannot connect to the configured VWP model") from None
            continue
        except httpx.ReadTimeout:
            attempt["model_called"]=None
            raise _unknown("READ_TIMEOUT") from None
        except httpx.WriteTimeout:
            attempt["model_called"]=None
            raise _unknown("WRITE_TIMEOUT") from None
        except (httpx.HTTPError, ValueError, OSError):
            attempt["model_called"]=None
            raise _unknown("TRANSPORT_ERROR") from None
        break
    if observer:observer("RESPONSE_RECEIVED")
    SecretGuard({"VWP_MODEL_SECRET":profile["token"], "VWP_CLIENT_SECRET":profile["client_api_key"],
                 "VWP_ENDPOINT_SECRET":profile["endpoint"], "VWP_BACKUP_SECRET":profile["fallback_endpoint"]}).ensure_public(
                     strict_json(bytes(body), limit))
    return bytes(body), {"endpoint":role, "attempted_endpoints":attempted}
