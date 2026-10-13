"""Shared, offline protocol helpers for the sanity-review prototype (stdlib only)."""

from __future__ import annotations

import gzip
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import zlib


class ReviewError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_json(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReviewError("INVALID_JSON", "Cannot serialize protocol data") from exc


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError("INVALID_JSON", "Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ReviewError("INVALID_JSON", "Non-finite JSON number")


def _finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ReviewError("INVALID_JSON", "Non-finite JSON number")
    return number


def _decode_json(data):
    try:
        result = json.loads(data, object_pairs_hook=_object_pairs,
                            parse_constant=_constant, parse_float=_finite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReviewError("INVALID_JSON", "Malformed UTF-8 JSON") from exc
    if not isinstance(result, dict):
        raise ReviewError("INVALID_JSON", "Expected JSON object")
    return result


def read_json(path) -> dict:
    try:
        return _decode_json(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReviewError("READ_ERROR", "Cannot read JSON file") from exc
    except UnicodeError as exc:
        raise ReviewError("INVALID_JSON", "Expected UTF-8 JSON") from exc


def _atomic_bytes(path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ReviewError("UNSAFE_PATH", "Output must not be a symlink")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sanity-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_json(path, value) -> None:
    _atomic_bytes(path, canonical_json(value) + b"\n")


def atomic_text(path, text: str) -> None:
    _atomic_bytes(path, text.encode("utf-8"))


def safe_path(root, relative) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ReviewError("UNSAFE_PATH", "Expected relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in relative.split("/")):
        raise ReviewError("UNSAFE_PATH", "Path traversal is not allowed")
    base = Path(root).resolve()
    resolved = (base / candidate).resolve()
    if not resolved.is_relative_to(base):
        raise ReviewError("UNSAFE_PATH", "Path escapes input directory")
    return resolved


def require_id(value, field="id") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
        raise ReviewError("INVALID_ID", f"Invalid {field}")
    return value


def _require(condition, message):
    if not condition:
        raise ReviewError("INVALID_SCHEMA", message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _count(value):
    return type(value) is int and value >= 0


def _strings(value):
    return isinstance(value, list) and all(_text(item) for item in value)


def _counts(value):
    return (isinstance(value, dict) and set(value) == {"PASS", "FAIL", "UNKNOWN"}
            and all(_count(number) for number in value.values()))


def identity_value_valid(field, value):
    """Prototype identity fields are typed scalars, never opaque containers."""
    textual = {"object", "check_point", "mode", "design", "project", "stage", "tool",
               "tool_version", "run_id", "code", "severity"}
    if field in textual or isinstance(value, str):
        return _text(value)
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _exclusion_valid(unit):
    exclusion = unit.get("exclusion")
    return isinstance(exclusion, dict) and all(
        _text(exclusion.get(key)) for key in ("reason", "approved_by", "reference"))


def validate_payload(payload) -> None:
    _require(isinstance(payload, dict), "Payload must be an object")
    _require(type(payload.get("schema_version")) is int and payload["schema_version"] == 1,
             "Unsupported schema version")
    require_id(payload.get("bundle_id"), "bundle_id")
    run = payload.get("run")
    _require(isinstance(run, dict), "Missing run")
    require_id(run.get("run_id"), "run_id")
    _require(all(_text(run.get(key)) for key in
                 ("project", "design", "stage", "tool", "tool_version")), "Incomplete run metadata")
    inventory = payload.get("inventory")
    _require(isinstance(inventory, dict), "Missing inventory")
    _require(type(inventory.get("confirmed")) is bool, "Invalid inventory confirmation")
    _require(all(_text(inventory.get(key)) for key in ("source", "version", "sha256")),
             "Incomplete inventory provenance")
    ids = inventory.get("expected_unit_ids")
    _require(isinstance(ids, list), "Expected unit IDs must be a list")
    for unit_id in ids:
        require_id(unit_id, "review_unit_id")
    _require(len(ids) == len(set(ids)), "Duplicate expected units")
    _require(_strings(payload.get("global_blockers")), "Invalid global blockers")
    _require(isinstance(payload.get("producer"), dict) and
             _text(payload["producer"].get("version")), "Missing producer version")
    units = payload.get("review_units")
    _require(isinstance(units, list), "Missing review units")
    by_id = {}
    for unit in units:
        _require(isinstance(unit, dict), "Invalid review unit")
        unit_id = require_id(unit.get("review_unit_id"), "review_unit_id")
        _require(unit_id not in by_id, "Duplicate review unit")
        by_id[unit_id] = unit
        _require(_text(unit.get("item_id")) and _text(unit.get("report")), "Missing item/report")
        _require(type(unit.get("applicable")) is bool, "Invalid applicability")
        _require(_text(unit.get("execution_status")), "Missing execution status")
        _require(unit.get("parse_status") in
                 ("COMPLETE", "PARTIAL", "MISSING", "UNSUPPORTED", "NOT_APPLICABLE"),
                 "Invalid parse status")
        _require(unit.get("check_status") in ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"),
                 "Invalid check status")
        _require(_strings(unit.get("blockers")), "Invalid unit blockers")
        _require(_counts(unit.get("status_counts")), "Invalid status counts")
        for key in ("record_count", "evaluated_count", "unknown_count"):
            _require(_count(unit.get(key)), "Invalid record count")
        counts = unit["status_counts"]
        _require(unit["record_count"] == sum(counts.values()), "Record count mismatch")
        _require(unit["evaluated_count"] == counts["PASS"] + counts["FAIL"], "Evaluation count mismatch")
        _require(unit["unknown_count"] == counts["UNKNOWN"], "Unknown count mismatch")
        _require(_text(unit.get("rule_version")), "Missing rule version")
        if unit["check_status"] == "PASS":
            _require(not counts["FAIL"] and not counts["UNKNOWN"], "PASS conflicts with records")
        if unit["check_status"] == "FAIL":
            _require(counts["FAIL"] > 0, "FAIL requires failing records")
    _require(set(by_id) == set(ids), "Expected inventory and review units differ")
    _require(len({unit["report"] for unit in units}) == len(units), "Duplicate report paths")
    files = payload.get("source_files")
    _require(isinstance(files, list), "Missing source files")
    source_ids, source_paths = set(), set()
    source_by_id = {}
    for source in files:
        _require(isinstance(source, dict), "Invalid source file")
        file_id = require_id(source.get("file_id"), "file_id")
        _require(file_id not in source_ids, "Duplicate source ID")
        source_ids.add(file_id)
        source_by_id[file_id] = source
        path = source.get("path")
        _require(_text(path) and path not in source_paths, "Invalid source path")
        source_paths.add(path)
        _require(_count(source.get("size_bytes")), "Invalid source size")
        _require(isinstance(source.get("sha256"), str) and
                 re.fullmatch(r"[0-9a-f]{64}", source["sha256"]), "Invalid source digest")
    groups = payload.get("finding_groups")
    _require(isinstance(groups, list), "Missing finding groups")
    group_ids, example_ids = set(), set()
    grouped = {key: {"FAIL": 0, "UNKNOWN": 0} for key in by_id}
    for group in groups:
        _require(isinstance(group, dict), "Invalid finding group")
        group_id = require_id(group.get("finding_group_id"), "finding_group_id")
        _require(group_id not in group_ids, "Duplicate finding group")
        group_ids.add(group_id)
        unit_id = group.get("review_unit_id")
        _require(isinstance(unit_id, str) and unit_id in by_id, "Orphan finding group")
        _require(group.get("item_id") == by_id[unit_id]["item_id"], "Group item mismatch")
        _require(group.get("identity_status") in ("SAME_FINDING", "UNDECIDED"), "Invalid identity status")
        _require(isinstance(group.get("identity"), dict), "Missing identity fields")
        _require(_text(group.get("message_code")) and _text(group.get("severity")), "Missing group type")
        _require(_text(group.get("rule_version")), "Missing group rule version")
        _require(group["rule_version"] == by_id[unit_id]["rule_version"], "Group rule version mismatch")
        if group["identity_status"] == "SAME_FINDING":
            declared = by_id[unit_id].get("identity_fields")
            _require(_strings(declared) and bool(declared), "Missing identity rule fields")
            _require(len(declared) == len(set(declared)), "Duplicate identity rule fields")
            _require(set(group["identity"]) == set(declared), "Identity fields differ from declared rule")
            _require(all(identity_value_valid(field, value)
                         for field, value in group["identity"].items()), "Invalid confirmed identity")
        _require(_count(group.get("occurrence_count")) and group["occurrence_count"] > 0,
                 "Invalid group count")
        if group["identity_status"] == "UNDECIDED":
            _require(group["occurrence_count"] == 1, "Undecided identities must remain separate")
        _require(_counts(group.get("status_counts")), "Invalid group status counts")
        counts = group["status_counts"]
        _require(sum(counts.values()) == group["occurrence_count"] and counts["PASS"] == 0,
                 "Group occurrence mismatch")
        for status in grouped[unit_id]:
            grouped[unit_id][status] += counts[status]
        examples = group.get("examples")
        _require(isinstance(examples, list) and 0 < len(examples) <= 3, "Invalid examples")
        _require(_count(group.get("examples_omitted")) and
                 len(examples) + group["examples_omitted"] == group["occurrence_count"],
                 "Example count mismatch")
        for example in examples:
            _require(isinstance(example, dict), "Invalid example")
            record_id = require_id(example.get("record_id"), "record_id")
            _require(record_id not in example_ids, "Duplicate example record")
            example_ids.add(record_id)
            _require(isinstance(example.get("file_id"), str) and example["file_id"] in source_ids,
                     "Unknown example source")
            _require(source_by_id[example["file_id"]]["path"] == by_id[unit_id]["report"],
                     "Evidence source does not belong to the review unit")
            _require(_count(example.get("line_start")) and example["line_start"] >= 1 and
                     _count(example.get("line_end")) and example["line_end"] >= example["line_start"],
                     "Invalid evidence location")
            _require(isinstance(example.get("raw"), str), "Missing raw example")
    for unit_id, counts in grouped.items():
        _require(all(counts[status] == by_id[unit_id]["status_counts"][status] for status in counts),
                 "Finding groups do not cover all FAIL/UNKNOWN records")


def _unit_blockers(unit):
    reasons = list(unit["blockers"])
    if not unit["applicable"]:
        if not _exclusion_valid(unit):
            reasons.append("EXCLUSION_UNVERIFIED")
        if unit["execution_status"] == "FAILED":
            reasons.append("FAILED_EXECUTION_EXCLUDED")
        if unit["parse_status"] != "NOT_APPLICABLE" or unit["check_status"] != "NOT_APPLICABLE":
            reasons.append("EXCLUSION_STATUS_MISMATCH")
        if unit["record_count"]:
            reasons.append("EXCLUDED_WITH_RECORDS")
        return sorted(set(reasons))
    if unit["execution_status"] != "COMPLETED":
        reasons.append("EXECUTION_INCOMPLETE")
    if unit["parse_status"] != "COMPLETE":
        reasons.append("REPORT_" + unit["parse_status"])
    if not unit["record_count"]:
        reasons.append("NO_RECORDS")
    if unit["unknown_count"] or unit["evaluated_count"] != unit["record_count"]:
        reasons.append("UNEVALUATED_RECORDS")
    if unit["check_status"] not in ("PASS", "FAIL"):
        reasons.append("CHECK_UNKNOWN")
    return sorted(set(reasons))


def coverage(payload, decisions=None) -> dict:
    validate_payload(payload)
    decisions = decisions or {}
    global_blockers = list(payload["global_blockers"])
    if not payload["inventory"]["confirmed"]:
        global_blockers.append("INVENTORY_UNCONFIRMED")
    if not payload["review_units"]:
        global_blockers.append("EMPTY_INVENTORY")
    source_paths = {source["path"] for source in payload["source_files"]}
    expected_paths = {unit["report"] for unit in payload["review_units"]}
    if source_paths - expected_paths:
        global_blockers.append("UNEXPECTED_REPORTS")
    result = {"expected": len(payload["review_units"]), "reviewed": 0, "approved_na": 0,
              "unresolved": 0, "missing": 0, "global_blockers": sorted(set(global_blockers)), "units": []}
    for unit in payload["review_units"]:
        reasons = _unit_blockers(unit)
        if unit["applicable"] and unit["report"] not in source_paths:
            reasons.append("SOURCE_REPORT_MISSING")
        ready = not reasons
        decision = decisions.get(unit["review_unit_id"])
        decision_valid = (isinstance(decision, dict) and decision.get("bundle_id") == payload["bundle_id"]
                          and all(_text(decision.get(k)) for k in ("reviewer", "reason"))
                          and decision.get("conclusion") in ("PASS", "FAIL")
                          and not (unit["check_status"] == "FAIL" and decision["conclusion"] == "PASS"))
        if not unit["applicable"] and ready:
            state = "APPROVED_NA"
            result["approved_na"] += 1
        elif ready and decision_valid:
            state = "REVIEWED"
            result["reviewed"] += 1
        else:
            state = "PENDING"
            result["unresolved"] += 1
            if unit["applicable"] and not decision_valid:
                reasons.append("HUMAN_REVIEW_PENDING")
        if unit["parse_status"] == "MISSING":
            result["missing"] += 1
        result["units"].append({"review_unit_id": unit["review_unit_id"], "item_id": unit["item_id"],
                                "check_status": unit["check_status"], "review_status": state,
                                "blockers": sorted(set(reasons)), "analysis_ready": ready,
                                "decision": decision if decision_valid else None})
    result["review_status"] = "COMPLETE" if not result["unresolved"] and not result["global_blockers"] else "INCOMPLETE"
    if any(unit["check_status"] == "FAIL" for unit in payload["review_units"]) or any(
            u["decision"] and u["decision"]["conclusion"] == "FAIL" for u in result["units"]):
        result["check_status"] = "FAIL"
    elif result["global_blockers"] or any(not unit["analysis_ready"] for unit in result["units"]):
        result["check_status"] = "UNKNOWN"
    elif result["approved_na"] == result["expected"]:
        result["check_status"] = "NOT_APPLICABLE"
    else:
        result["check_status"] = "PASS"
    return result


def _immutable_bytes(path, data):
    path = Path(path)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != data:
            raise ReviewError("BUNDLE_CONFLICT", "Existing bundle has different contents")
        return
    _atomic_bytes(path, data)


def pack_json(value, max_bytes=67108864):
    """Byte contract used by the shared DB, independent of local files."""
    raw = canonical_json(value)
    if len(raw) > max_bytes:
        raise ReviewError("SIZE_LIMIT", "Expanded payload exceeds limit")
    compressed = gzip.compress(raw, mtime=0)
    if len(compressed) > max_bytes:
        raise ReviewError("SIZE_LIMIT", "Compressed payload exceeds limit")
    return {"payload_gzip": compressed, "payload_sha256": hashlib.sha256(compressed).hexdigest(),
            "uncompressed_bytes": len(raw)}


def unpack_json(message, max_bytes=67108864):
    try:
        compressed = message.get("payload_gzip")
        size = message.get("uncompressed_bytes")
        if not isinstance(compressed, bytes) or not 0 < len(compressed) <= max_bytes:
            raise ReviewError("SIZE_LIMIT", "Invalid compressed payload size")
        if type(size) is not int or not 0 < size <= max_bytes:
            raise ReviewError("SIZE_LIMIT", "Invalid declared expansion size")
        if hashlib.sha256(compressed).hexdigest() != message.get("payload_sha256"):
            raise ReviewError("INTEGRITY_ERROR", "Compressed payload hash mismatch")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ReviewError("SIZE_LIMIT", "Expanded payload exceeds limit")
        if len(raw) != size:
            raise ReviewError("INTEGRITY_ERROR", "Expanded payload size mismatch")
        return _decode_json(raw.decode("utf-8"))
    except ReviewError:
        raise
    except (OSError, EOFError, ValueError, UnicodeError, zlib.error) as exc:
        raise ReviewError("BUNDLE_READ_ERROR", "Cannot decode complete compressed payload") from exc


def validate_content_id(payload):
    validate_payload(payload)
    expected = "b-" + digest({key: value for key, value in payload.items() if key != "bundle_id"})
    if payload["bundle_id"] != expected:
        raise ReviewError("INTEGRITY_ERROR", "Bundle content ID mismatch")


def unpack_bundle(message):
    payload = unpack_json(message)
    validate_content_id(payload)
    if (payload["bundle_id"] != message.get("bundle_id") or payload["run"]["project"] != message.get("project")
            or payload["run"]["run_id"] != message.get("run_id")):
        raise ReviewError("INTEGRITY_ERROR", "Database message scope differs from bundle")
    metadata = message.get("payload", {})
    if (type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1
            or metadata.get("inventory_sha256") != payload["inventory"]["sha256"]
            or metadata.get("producer_version") != payload["producer"]["version"]):
        raise ReviewError("INTEGRITY_ERROR", "Database message manifest differs from bundle")
    return payload


def write_bundle(payload, out_dir) -> Path:
    validate_payload(payload)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bundle_id = payload["bundle_id"]
    raw = canonical_json(payload)
    if len(raw) > 67108864:
        raise ReviewError("SIZE_LIMIT", "Payload exceeds prototype 64 MiB transport limit")
    compressed = gzip.compress(raw, mtime=0)
    filename = f"{bundle_id}.json.gz"
    manifest = {"schema_version": 1, "bundle_id": bundle_id, "run_id": payload["run"]["run_id"],
                "payload_file": filename, "size_bytes": len(compressed),
                "sha256": hashlib.sha256(compressed).hexdigest(), "uncompressed_bytes": len(raw)}
    _immutable_bytes(out / filename, compressed)
    path = out / f"{bundle_id}.manifest.json"
    _immutable_bytes(path, canonical_json(manifest) + b"\n")
    return path


def read_bundle(manifest_path, max_bytes=67108864) -> dict:
    path = Path(manifest_path)
    try:
        if path.stat().st_size > 65536:
            raise ReviewError("SIZE_LIMIT", "Manifest too large")
        manifest = read_json(path)
        _require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
                 "Unsupported manifest schema")
        bundle_id = require_id(manifest.get("bundle_id"), "bundle_id")
        require_id(manifest.get("run_id"), "run_id")
        _require(manifest.get("payload_file") == f"{bundle_id}.json.gz", "Unexpected payload filename")
        for key in ("size_bytes", "uncompressed_bytes"):
            _require(_count(manifest.get(key)), "Invalid declared size")
            if manifest[key] > max_bytes:
                raise ReviewError("SIZE_LIMIT", "Bundle exceeds size limit")
        compressed_path = safe_path(path.parent, manifest["payload_file"])
        if compressed_path.stat().st_size != manifest["size_bytes"]:
            raise ReviewError("INTEGRITY_ERROR", "Compressed size mismatch")
        with compressed_path.open("rb") as stream:
            compressed = stream.read(max_bytes + 1)
        if len(compressed) > max_bytes:
            raise ReviewError("SIZE_LIMIT", "Compressed payload too large")
        if hashlib.sha256(compressed).hexdigest() != manifest.get("sha256"):
            raise ReviewError("INTEGRITY_ERROR", "Payload checksum mismatch")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ReviewError("SIZE_LIMIT", "Expanded payload too large")
        if len(raw) != manifest["uncompressed_bytes"]:
            raise ReviewError("INTEGRITY_ERROR", "Expanded size mismatch")
        payload = _decode_json(raw.decode("utf-8"))
        validate_payload(payload)
        if payload["bundle_id"] != bundle_id or payload["run"]["run_id"] != manifest["run_id"]:
            raise ReviewError("INTEGRITY_ERROR", "Manifest identity mismatch")
        return payload
    except ReviewError:
        raise
    except (OSError, EOFError, ValueError, UnicodeError, zlib.error) as exc:
        raise ReviewError("BUNDLE_READ_ERROR", "Cannot read complete bundle") from exc


def _cell(value):
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _code(value):
    text = str(value)
    fence = "`" * max(3, 1 + max((len(s) for s in re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}"


def render_report(payload, decisions=None, context=None) -> str:
    state = coverage(payload, decisions)
    context = context or {}
    dashboard_mode = context.get("interface") == "dashboard-v1"
    jira = context.get("jira", {"status": "NOT_CONFIGURED", "candidates": []})
    ai = context.get("ai", {"status": "NOT_CONFIGURED"})
    run = payload["run"]
    unit_details = {unit["review_unit_id"]: unit for unit in payload["review_units"]}
    source_details = {source["file_id"]: source for source in payload["source_files"]}
    lines = ["# Sanity 검토 보고서", "",
             "OA 대시보드 검토 보고서 — 최종 판정은 사람이 수행합니다." if dashboard_mode else
             "PostgreSQL 프로토타입 — 실제 Jira 게시·AI 검토가 아닙니다.", "",
             f"- 실행: {_cell(run['run_id'])} / {_cell(run['project'])} / {_cell(run['design'])}",
             f"- 결과 묶음: `{payload['bundle_id']}`",
             f"- 전체 검토: **{state['review_status']}** / sanity 결과: **{state['check_status']}**",
             f"- 예상 {state['expected']} / 검토 완료 {state['reviewed']} / 적용 제외 {state['approved_na']} / "
             f"미완료 {state['unresolved']} / report 누락 {state['missing']}",
             (f"- AI: {_cell(ai.get('status'))}" if dashboard_mode else
              f"- Jira 맥락: {_cell(jira.get('status'))} / AI: {_cell(ai.get('status'))}"),
             f"- 전체 미완료 사유: {_cell(', '.join(state['global_blockers']) or '없음')}",
             "", "## 전체 item 검토 대장", "",
             "| 검토 단위 | item | 판정 | PASS / FAIL / UNKNOWN | 사람 검토 | 미완료 사유 |",
             "|---|---|---|---|---|---|"]
    for unit in state["units"]:
        counts = unit_details[unit["review_unit_id"]]["status_counts"]
        lines.append("| " + " | ".join(_cell(v) for v in (unit["review_unit_id"], unit["item_id"],
                     unit["check_status"], f"{counts['PASS']} / {counts['FAIL']} / {counts['UNKNOWN']}",
                     unit["review_status"], ", ".join(unit["blockers"]) or "없음")) + " |")
    lines += ["", "## report와 적용 규칙", ""]
    for unit in payload["review_units"]:
        rule = unit.get("evaluation")
        rule_text = json.dumps(rule, ensure_ascii=False, sort_keys=True) if rule else "명시 결과 또는 적용 제외"
        lines += [f"- `{unit['review_unit_id']}`: {_cell(unit['report'])}; "
                  f"실행 {_cell(unit['execution_status'])}; 파싱 {_cell(unit['parse_status'])}; "
                  f"규칙 버전 {_cell(unit['rule_version'])}; {_cell(rule_text)}"]
        if not unit["applicable"]:
            lines += [f"  적용 제외 근거: {_cell(json.dumps(unit.get('exclusion'), ensure_ascii=False, sort_keys=True))}"]
    for unit in state["units"]:
        decision = unit["decision"]
        if decision:
            lines += ["", f"판정 `{unit['review_unit_id']}`: {_cell(decision['conclusion'])}, "
                      f"검토자 {_cell(decision['reviewer'])}, 근거 {_cell(decision['reason'])}"]
    lines += ["", "## 문제 그룹과 원문 근거", "",
              "대표 예시는 전체 원문을 대체하지 않습니다. 전체 개별 판정과 근거는 VWP에 보존합니다."]
    if not payload["finding_groups"]:
        lines += ["", "문제 그룹 없음. 이 사실만으로 누락 없는 검토 완료를 의미하지 않습니다."]
    for group in payload["finding_groups"]:
        lines += ["", f"### {group['finding_group_id']}", "",
                  f"- 단위: `{group['review_unit_id']}` / 코드: {_cell(group.get('message_code', 'UNKNOWN'))}",
                  f"- 동일성: {_cell(group['identity_status'])} / 발생: {group['occurrence_count']} / "
                  f"예시 생략: {group['examples_omitted']}",
                  f"- 식별 필드: {_cell(json.dumps(group['identity'], ensure_ascii=False, sort_keys=True))}"]
        for example in group["examples"]:
            source = source_details[example["file_id"]]
            lines += ["", f"근거 `{example['record_id']}`: {_cell(source['path'])} "
                      f"{example['line_start']}–{example['line_end']}행 "
                      f"(file_id: `{example['file_id']}`, SHA-256: `{source['sha256']}`)"]
            if "json_pointer" in example:
                lines += [f"JSON 위치: {_cell(example['json_pointer'])}"]
            if "observed" in example or "expected" in example:
                lines += [f"측정값: {_cell(example.get('observed'))}; 임계값: "
                          f"{_cell(example.get('expected'))}; 단위: {_cell(example.get('unit'))}"]
            if example.get("raw_omitted_chars"):
                lines += [f"원문 예시 생략: {_cell(example['raw_omitted_chars'])}문자 (전체 근거는 VWP 보존)"]
            lines += ["", _code(example["raw"])]
    # Preserve historical stored report bytes; new dashboard reports have no Jira section.
    if not dashboard_mode:
        lines += ["", "## 관련 Jira 후보", "", "후보 연결은 동일 원인 확정이나 자동 승인이 아닙니다."]
        if "total_matches" in jira:
            limits = jira.get("limits", {})
            lines += ["", f"검색 일치: {_cell(jira['total_matches'])}; 표시: "
                      f"{_cell(jira.get('returned_count', len(jira.get('candidates', []))))}; "
                      f"생략: {_cell(jira.get('omitted_count', 0))}; "
                      f"상한: 그룹당 {_cell(limits.get('per_group'))}, 전체 {_cell(limits.get('total'))}"]
            if jira.get("truncated"):
                lines += ["", "후보 상한으로 일부 결과를 생략했습니다. 표시되지 않은 후보가 없거나 무관하다는 뜻은 아닙니다."]
                omitted_groups = [group for group in jira.get("groups", []) if group.get("omitted_count", 0)]
                if omitted_groups:
                    lines += ["", "| 문제 그룹 | 검색 일치 | 표시 | 생략 |", "|---|---|---|---|"]
                    for group in omitted_groups:
                        lines.append("| " + " | ".join(_cell(group.get(key)) for key in
                                     ("finding_group_id", "total_matches", "returned_count", "omitted_count")) + " |")
        for candidate in jira.get("candidates", []):
            lines += ["", f"- {_cell(candidate.get('key'))}: {_cell(candidate.get('summary'))} "
                      f"(그룹 {_cell(candidate.get('finding_group_id'))}, CANDIDATE)",
                      f"  상태: {_cell(candidate.get('status'))}; 종료/해결: "
                      f"{_cell(candidate.get('closed_or_resolved', False))}; 조건 차이: "
                      f"{_cell(json.dumps(candidate.get('condition_differences', []), ensure_ascii=False, sort_keys=True))}"]
    lines += ["", "## 보증 범위", "",
              "검토 완료는 확정된 예상 목록, 지원 report 계약, 모든 개별 판정과 현재 결과에 대한 사람의 확인을 기준으로 합니다. "
              "실제 현장 형식·업무 규칙과 판정 정확성은 별도 검증이 필요합니다.", ""]
    return "\n".join(lines)
