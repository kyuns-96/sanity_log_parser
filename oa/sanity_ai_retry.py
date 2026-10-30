"""Bounded model retries require a proven failed outcome, never uncertainty."""
from datetime import timedelta

from common.sanity_common import ReviewError

MAX_RETRIES = 5
MAX_RESUMES = 5
HTTP_RETRYABLE = frozenset((429, 500, 502, 503, 504))
INVALID_REPLIES = frozenset(("INVALID_JSON", "INVALID_AI_RESPONSE", "AI_VWP_PROTOCOL", "INVALID_PARTITION", "INVALID_ADVICE",
    "INVALID_RATIONALE", "INVALID_REFERENCE", "AI_BATCH_COVERAGE", "AI_BATCH_TEXT", "AI_BATCH_CITATION"))


def annotate(error, attempt):
    error.model_called = attempt["model_called"]
    error.response_complete = attempt.get("response_complete", False)
    if "http_status" in attempt:
        error.http_status = attempt["http_status"]
    if "retry_after_seconds" in attempt:
        error.retry_after_seconds = attempt["retry_after_seconds"]


def response_headers(attempt, response):
    attempt["http_status"] = response.status_code
    raw = response.headers.get("Retry-After", "")
    if len(raw) <= 10 and raw.isascii() and raw.isdecimal():
        attempt["retry_after_seconds"] = min(300, int(raw))


def receipt(error):
    if not isinstance(error, ReviewError):
        return None
    code, called = error.code, getattr(error, "model_called", None)
    if code == "AI_CONNECT_FAILED" and called is False:
        return {"kind":"CONNECT", "error":code, "model_called":False}
    status = getattr(error, "http_status", None)
    if code == "AI_CALL_FAILED" and called is True and type(status) is int and status in HTTP_RETRYABLE:
        return {"kind":"HTTP", "error":code, "model_called":True, "http_status":status}
    if code in INVALID_REPLIES and called is True and getattr(error, "response_complete", False) is True:
        return {"kind":"INVALID_RESPONSE", "error":code, "model_called":True, "response_complete":True}
    return None


def valid_receipt(value):
    if not isinstance(value, dict) or not isinstance(value.get("error"),str):
        return False
    error = ReviewError(value.get("error"), "")
    for key in ("model_called", "http_status", "response_complete"):
        if key in value:
            setattr(error, key, value[key])
    return receipt(error) == value and receipt(error) is not None


def retry_at(now, number, error=None):
    seconds = min(60, 5 * 2 ** (number - 1))
    supplied = getattr(error, "retry_after_seconds", 0)
    if type(supplied) is int:
        seconds = max(seconds, min(300, max(0, supplied)))
    return (now + timedelta(seconds=seconds)).isoformat()


def resumable(event, *, checkpoint_ready=False):
    data = event["data"]
    if (not data.get("fail_safe") or data.get("resume_count",0) >= MAX_RESUMES
            or event["status"] not in ("FAILED", "CANCELLED") or "result" in data or "result_ref" in data
            or data.get("error") == "AI_BATCH_LIMIT"):
        return False
    if event["status"] == "FAILED" and data.get("error") != "AI_BATCH_DEADLINE":
        proof = data.get("retry_failure")
        if not valid_receipt(proof) or proof["error"] != data.get("error"):
            return False
    if "batch" in data:
        return checkpoint_ready and (event["status"] == "CANCELLED" or data.get("error") == "AI_BATCH_DEADLINE"
                                     or valid_receipt(data.get("retry_failure")))
    return ("call_token" not in data or event["status"] == "FAILED") and (
        valid_receipt(data.get("retry_failure")) or event["status"] == "CANCELLED" and data.get("model_attempted",False) is False)
