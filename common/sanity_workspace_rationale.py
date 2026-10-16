"""Versioned, group-specific classification evidence; no database or model calls."""
import copy
import base64
import hashlib
import json
import zlib

from common.sanity_clustering import check
from common.sanity_common import canonical_json, digest, ReviewError

STATES = ("MISSING", "DRAFT", "CONFIRMED", "STALE")
CONTENT_KEYS = {"reason", "evidence_ids", "reference_groups"}
MAX_RATIONALE_BYTES = 128 * 1024 * 1024
PACK_THRESHOLD = 1000
PACKED_KEYS = {"rationale_encoding", "rationale_chunks", "rationale_bytes"}


def bucket(key):
    return hashlib.sha256(key.encode()).hexdigest()[:2]


def pack(event):
    """Bound JSONB construction memory for many groups without new tables.

    The index retains exact group-key lookup in SQL; each of 256 independently
    bounded chunks contains the original JSON objects, never truncated summaries.
    API/advisor callers always receive the ordinary logical event.
    """
    rows = event.get("rationales", {})
    if len(rows) < PACK_THRESHOLD:
        return event
    chunks, index, size = {}, {}, 0
    for key, row in rows.items():
        name = bucket(key)
        chunks.setdefault(name, {})[key] = row
        index[key] = name
    encoded = {}
    for name, values in chunks.items():
        raw = canonical_json(values)
        size += len(raw)
        check(size <= MAX_RATIONALE_BYTES, "CLUSTER_LIMIT", "Explicit rationale byte budget exceeded")
        encoded[name] = base64.b64encode(zlib.compress(raw, level=3)).decode("ascii")
    return {**event, "rationales": index, "rationale_encoding": "bucket-zlib-v1",
            "rationale_chunks": encoded, "rationale_bytes": size}


def unpack_chunk(value):
    try:
        check(isinstance(value, str) and len(value) <= MAX_RATIONALE_BYTES, "STATE_CORRUPT")
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(value, validate=True), MAX_RATIONALE_BYTES+1)
        check(len(raw) <= MAX_RATIONALE_BYTES and decoder.eof
              and not decoder.unused_data and not decoder.unconsumed_tail, "STATE_CORRUPT")
        rows = json.loads(raw)
        check(isinstance(rows, dict), "STATE_CORRUPT")
        return rows, len(raw)
    except (ValueError, TypeError, zlib.error, UnicodeError):
        raise ReviewError("STATE_CORRUPT", "Invalid stored rationale chunk") from None


def unpack_group(key, name, value):
    check(isinstance(name, str) and name == bucket(key), "STATE_CORRUPT")
    rows, _ = unpack_chunk(value)
    check(key in rows and isinstance(rows[key], dict), "STATE_CORRUPT")
    return rows[key]


def unpack(event):
    if "rationale_encoding" not in event:
        return event
    check(event["rationale_encoding"] == "bucket-zlib-v1", "STATE_CORRUPT")
    index, chunks = event.get("rationales"), event.get("rationale_chunks")
    check(isinstance(index, dict) and isinstance(chunks, dict) and len(chunks) <= 256
          and type(event.get("rationale_bytes")) is int
          and 0 <= event["rationale_bytes"] <= MAX_RATIONALE_BYTES, "STATE_CORRUPT")
    rows, size = {}, 0
    for name, value in chunks.items():
        values, length = unpack_chunk(value)
        size += length
        check(size <= MAX_RATIONALE_BYTES and all(isinstance(key, str) and bucket(key) == name
              and index.get(key) == name and isinstance(row, dict) for key, row in values.items()), "STATE_CORRUPT")
        rows.update(values)
    check(size == event["rationale_bytes"] and set(rows) == set(index), "STATE_CORRUPT")
    return {**{key: value for key, value in event.items() if key not in PACKED_KEYS}, "rationales": rows}


def validate_content(value, group, cases):
    check(isinstance(value, dict) and set(value) == CONTENT_KEYS, "INVALID_RATIONALE")
    reason, ids, refs = value["reason"], value["evidence_ids"], value["reference_groups"]
    check(isinstance(reason, str) and 1 <= len(reason.strip()) <= 2000, "INVALID_RATIONALE")
    members = set(group["record_ids"])
    check(isinstance(ids, list) and 1 <= len(ids) <= 20 and all(isinstance(key, str) for key in ids)
          and len(set(ids)) == len(ids) and set(ids) <= members, "INVALID_RATIONALE",
          "Representative IDs must belong to this group")
    check(isinstance(refs, list) and len(refs) <= 20, "INVALID_RATIONALE")
    catalog = {case["id"]: case for case in cases}
    seen = set()
    for ref in refs:
        check(isinstance(ref, dict) and set(ref) == {"case_id", "group_id", "reason"}, "INVALID_RATIONALE")
        check(isinstance(ref["case_id"], str) and isinstance(ref["group_id"], str)
              and isinstance(ref["reason"], str) and 1 <= len(ref["reason"].strip()) <= 500, "INVALID_RATIONALE")
        pair = (ref["case_id"], ref["group_id"])
        case = catalog.get(ref["case_id"])
        check(case is not None and any(g["id"] == ref["group_id"] for g in case["groups"])
              and pair not in seen, "INVALID_RATIONALE", "Historical group must be an available confirmed example")
        seen.add(pair)
    return {"reason": reason.strip(), "evidence_ids": list(ids),
            "reference_groups": [{**ref, "reason": ref["reason"].strip()} for ref in refs]}


def validate_group_rationales(values, groups, cases, reference_ids):
    check(isinstance(values, list) and len(values) == len(groups), "INVALID_RATIONALE")
    index, result = {group["id"]: group for group in groups}, {}
    used = set()
    for value in values:
        check(isinstance(value, dict) and set(value) == CONTENT_KEYS | {"group_id"}, "INVALID_RATIONALE")
        key = value["group_id"]
        check(isinstance(key, str) and key in index and key not in result, "INVALID_RATIONALE")
        content = validate_content({k: value[k] for k in CONTENT_KEYS}, index[key], cases)
        used.update(ref["case_id"] for ref in content["reference_groups"])
        result[key] = content
    check(used == set(reference_ids), "INVALID_RATIONALE", "Global references must equal the per-group references")
    return result


def binding(event, group):
    return {"source_sha256": event["source_sha256"], "cluster_event_id": event["id"],
            "cluster_revision": event["revision"], "group_id": group["id"], "label": group["label"],
            "membership_sha256": digest(sorted(group["record_ids"]))}


def state(event, group, valid_case_ids=None):
    row = event.get("rationales", {}).get(group["id"])
    if not row or not row.get("content"):
        return "MISSING"
    bound = row.get("binding", {})
    current = binding(event, group)
    if (row.get("status") == "STALE" or bound != current
            or not set(row["content"]["evidence_ids"]) <= set(group["record_ids"])
            or valid_case_ids is not None and any(ref["case_id"] not in valid_case_ids
                                                 for ref in row["content"]["reference_groups"])):
        return "STALE"
    return "CONFIRMED" if row.get("status") == "CONFIRMED" else "DRAFT"


def counts(event, valid_case_ids=None):
    result = dict.fromkeys(STATES, 0)
    for group in event["groups"] if event else []:
        result[state(event, group, valid_case_ids)] += 1
    return result


def complete(event):
    return bool(event and event["groups"] and counts(event)["CONFIRMED"] == len(event["groups"]))


def brief(event, group, valid_case_ids=None):
    row = event.get("rationales", {}).get(group["id"], {})
    reason = row.get("content", {}).get("reason", "")
    return {"status": state(event, group, valid_case_ids), "reason": reason[:160],
            "reason_truncated": len(reason) > 160, "reason_omitted_chars": max(0, len(reason)-160), "source": row.get("source"),
            "evidence_count": len(row.get("content", {}).get("evidence_ids", []))}


def carry(old, event, proposed=None):
    """An unchanged group carries its review with an explicit lineage link."""
    result = {}
    for group in event["groups"]:
        key = group["id"]
        if proposed is not None:
            content = proposed[key]
            source = "AI" if event["model_called"] else "REFERENCE_ENGINE"
            # Until a human changes the content, the immutable proposal event
            # already owns this exact content. Do not duplicate it 100,000 times.
            result[key] = {"content": content, "proposal": {
                "source": source, "event_id": event["id"]}, "source": source, "status": "DRAFT",
                "binding": binding(event, group)}
        elif old and key in old.get("rationales", {}):
            # Content and proposal are immutable values. Copy only the envelope;
            # a SAVE creates a new content object and copies proposal before edit.
            row = dict(old["rationales"][key])
            before = row.get("binding", {})
            after = binding(event, group)
            same = all(before.get(k) == after[k] for k in
                       ("source_sha256", "group_id", "label", "membership_sha256"))
            row["previous_event_id"] = old["id"]
            if same and row["status"] != "STALE":
                row["binding"] = after
            else:
                row["status"] = "STALE"
            result[key] = row
    return result


def adopt(row, event, group):
    row.update(status="CONFIRMED", binding=binding(event, group),
               reviewed_by=event["actor"], reviewed_at=event["at"])


def materialized(row):
    if row is None:
        return None
    result = copy.deepcopy(row)
    for key, default in (("reviewed_by", None), ("reviewed_at", None),
                         ("change_reason", ""), ("previous_event_id", None)):
        result.setdefault(key, default)
    if result.get("proposal") and "content" not in result["proposal"]:
        result["proposal"]["content"] = copy.deepcopy(result["content"])
    return result


def examples(case):
    """Do not fabricate rationale/approval for archived, pre-V2 examples."""
    result = []
    for group in case["groups"]:
        row = materialized(case.get("rationales", {}).get(group["id"]))
        # Model input needs the grouping feedback, not synthetic account names or
        # import-time clocks. Full actor/time audit remains in immutable events.
        if row:
            row = {key: value for key, value in row.items() if key not in
                   ("reviewed_by", "reviewed_at", "edited_by", "edited_at")}
        result.append({"group_id": group["id"], "status": state(case, group), "rationale": row})
    return result
