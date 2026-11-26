"""Shared OpenAI-compatible HTTP endpoint validation, TLS setup, and bounded streaming POST.

Used by both ``oa.sanity_ai_model`` and ``common.sanity_vwp_ai_http`` so pre-send
connect failover, identity encoding enforcement, output byte caps, and response
deadlines behave identically across execution sites without leaking URLs or tokens.
"""
from __future__ import annotations

import logging
import ssl
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from common.sanity_ai_retry import response_headers
from common.sanity_common import ReviewError


def validate_http_endpoint(value, *, require_path: bool = True) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return False
    if "\\" in value or any(ch.isspace() or ord(ch) < 33 for ch in value):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.port == 0:
            return False
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if require_path and parsed.path in ("", "/"):
            return False
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def build_tls_context(endpoints: tuple[str, ...], ca_path: str, *, error_code: str, message: str):
    try:
        uses_tls = any(urlsplit(endpoint).scheme == "https" for endpoint in endpoints if endpoint)
        return ssl.create_default_context(cafile=ca_path or None) if uses_tls else True
    except (OSError, ValueError, ssl.SSLError):
        raise ReviewError(error_code, message) from None


def build_auth_headers(auth_mode: str, token: str, client_api_key: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    if auth_mode == "x_client_api_key":
        headers["x-client-api-key"] = token
    else:
        headers["Authorization"] = "Bearer " + token
        if client_api_key:
            headers["x-client-api-key"] = client_api_key
    return headers


def silence_http_debug_loggers() -> None:
    """Prevent httpx/httpcore debug loggers from recording private URLs or headers."""
    for name in ("httpx", "httpcore", *list(logging.Logger.manager.loggerDict)):
        if name in ("httpx", "httpcore") or name.startswith(("httpx.", "httpcore.")):
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)


def unknown_outcome_error(reason: str, message: str) -> ReviewError:
    error = ReviewError("AI_OUTCOME_UNKNOWN", message)
    error.unknown_reason = reason
    return error


def stream_openai_post(
    data: bytes,
    headers: dict[str, str],
    *,
    endpoint: str,
    fallback_endpoint: str = "",
    timeout: int | float,
    max_output_bytes: int,
    tls,
    transport=None,
    observer: Callable[[str], None] | None = None,
    attempt: dict,
    auth_401: bool = True,
    sending_model_called: bool | None = True,
    unknown_model_called: bool | None = True,
    label: str = "AI",
) -> tuple[bytes, dict[str, object]]:
    """Execute a bounded non-redirected POST with connect-only failover."""
    silence_http_debug_loggers()
    started = time.monotonic()
    endpoints = [("primary", endpoint)]
    if fallback_endpoint:
        endpoints.append(("fallback", fallback_endpoint))
    attempted: list[str] = []
    role = "primary"
    raw = bytearray()

    for role, target_url in endpoints:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise ReviewError("AI_CONNECT_FAILED", f"{label} connection deadline exceeded before dispatch")
        response_started = False
        attempted.append(role)
        # Reserve half the remaining connect budget for the fallback when configured.
        client_timeout = (
            httpx.Timeout(remaining, connect=remaining / 2)
            if role == "primary" and fallback_endpoint
            else remaining
        )
        try:
            with httpx.Client(
                timeout=client_timeout,
                trust_env=False,
                follow_redirects=False,
                verify=tls,
                transport=transport,
            ) as client:
                attempt["model_called"] = sending_model_called
                if observer is not None:
                    observer("SENDING")
                with client.stream("POST", target_url, content=data, headers=headers) as response:
                    response_started = True
                    attempt["model_called"] = True
                    response_headers(attempt, response)
                    if observer is not None:
                        observer("SERVER_RESPONDED")
                    if auth_401 and response.status_code == 401:
                        raise ReviewError("AI_AUTH_FAILED", "Check personal token and gateway authentication")
                    if response.status_code != 200:
                        raise ReviewError("AI_CALL_FAILED", f"{label} call did not succeed")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ReviewError("INVALID_AI_RESPONSE", f"Compressed {label} responses are not supported")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw) + len(chunk) > max_output_bytes:
                            raise ReviewError("AI_OUTPUT_LIMIT", f"{label} response exceeds its configured bound")
                        if time.monotonic() - started > timeout:
                            raise unknown_outcome_error("RESPONSE_DEADLINE", f"{label} call completion is unknown")
                        raw.extend(chunk)
                    if time.monotonic() - started > timeout:
                        raise unknown_outcome_error("RESPONSE_DEADLINE", f"{label} call completion is unknown")
                    attempt["response_complete"] = True
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if response_started:
                raise unknown_outcome_error("TRANSPORT_ERROR", f"{label} call completion is unknown") from None
            attempt["model_called"] = False
            if role == endpoints[-1][0]:
                raise ReviewError("AI_CONNECT_FAILED", f"Cannot connect to an approved {label} endpoint") from None
            continue
        except httpx.ReadTimeout:
            attempt["model_called"] = unknown_model_called
            raise unknown_outcome_error("READ_TIMEOUT", f"{label} call completion is unknown") from None
        except httpx.WriteTimeout:
            attempt["model_called"] = unknown_model_called
            raise unknown_outcome_error("WRITE_TIMEOUT", f"{label} call completion is unknown") from None
        except ReviewError:
            raise
        except (httpx.HTTPError, ValueError, OSError):
            attempt["model_called"] = unknown_model_called
            raise unknown_outcome_error("TRANSPORT_ERROR", f"{label} call completion is unknown") from None
        break

    if observer is not None:
        observer("RESPONSE_RECEIVED")
    return bytes(raw), {"endpoint": role, "attempted_endpoints": attempted}
