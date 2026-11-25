"""Leaf submission validation and identity primitives. Pure stdlib; no DB or company SDK."""
from __future__ import annotations

from pathlib import PurePosixPath
import re
import uuid

from common.sanity_common import ReviewError, digest

COLLECTION_KEYS = {
    "source_namespace", "project_key", "block_key", "net_ver", "submission_revision",
    "layout", "target_eco", "pre_revision", "stage", "profile_id", "profile_version",
}
SOURCE_KEYS = {
    "net_ver", "revision", "layout", "eco", "mode", "execution_ref", "execution_order",
    "source_stage", "tool", "tool_version",
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")


def check(value, code="INVALID_SUBMISSION", message="Invalid submission contract"):
    if not value:
        raise ReviewError(code, message)


def text(value, maximum=1000):
    return (isinstance(value, str) and 1 <= len(value) <= maximum and bool(value.strip())
            and re.search(r"[\x00-\x1f\x7f]", value) is None)


def identifier(value):
    check(isinstance(value, str) and ID.fullmatch(value))
    return value


def submission_uuid(value):
    try:
        result = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReviewError("INVALID_SUBMISSION", "A submission UUID is required") from exc
    check(str(value) == result)
    return result


def relative_report(value):
    check(text(value) and value.startswith("reports/") and "\\" not in value)
    path = PurePosixPath(value)
    check(not path.is_absolute() and len(path.parts) >= 2 and str(path) == value
          and not any(p in (".", "..") for p in path.parts))
    return value


def source_version(collection, source):
    eco = source.get("eco_num") if "eco_num" in source else source.get("eco")
    parts = [
        collection["source_namespace"], collection["project_key"], collection["block_key"],
        source["net_ver"], source["revision"], source["layout"],
    ]
    if eco is not None:
        parts.append(eco)
    return "sv-" + digest(parts)


def source_unit(collection, unit):
    source = unit["source"]
    return "su-" + digest([source_version(collection, source), source["eco"], unit["item_id"], source["mode"]])


def source_execution(collection, unit, submission_id=None):
    identity = [source_unit(collection, unit), unit["source"]["execution_ref"]]
    if submission_id is not None:
        identity.append(submission_uuid(submission_id))
    return "sx-" + digest(identity)
