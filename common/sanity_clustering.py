"""Versioned, declarative classification. No executable model output or sanity verdict changes."""
from __future__ import annotations

from common.sanity_common import ReviewError, canonical_json, digest, identity_value_valid, require_id

SCHEMA = 1
MAX_RULES = 20
MAX_GROUPS = 100
PART_ROWS = 128
PART_BYTES = 512 * 1024
SAMPLE_ROWS = 64
SAMPLE_BYTES = 128 * 1024
SCOPE_KEYS = ("project", "design", "stage", "tool", "tool_version", "item_id", "parser", "rule_version", "condition")
FIELDS = {"object", "check_point", "mode", "code", "severity", "observed", "expected", "unit"}


def check(ok, code="INVALID_CLUSTER", message="Invalid classification contract"):
    if not ok:
        raise ReviewError(code, message)


def scope_for(run, unit):
    return {**{k: run[k] for k in SCOPE_KEYS[:5]},
            **{k: unit.get(k) for k in SCOPE_KEYS[5:]}}


def validate_rules(rules, evidence_ids=None):
    check(isinstance(rules, list) and len(rules) <= MAX_RULES)
    for rule in rules:
        check(isinstance(rule, dict) and set(rule) == {"identity_fields", "where", "reason", "evidence_ids"})
        fields = rule["identity_fields"]
        check(isinstance(fields, list) and 1 <= len(fields) <= len(FIELDS)
              and all(isinstance(f, str) and f in FIELDS for f in fields) and len(set(fields)) == len(fields))
        check(bool(set(fields) - {"code", "severity"}), "INVALID_CLUSTER", "Code alone cannot define issue identity")
        check(isinstance(rule["where"], dict) and set(rule["where"]) <= FIELDS
              and all(identity_value_valid(k, v) for k, v in rule["where"].items()))
        check(isinstance(rule["reason"], str) and 1 <= len(rule["reason"].strip()) <= 2000)
        refs = rule["evidence_ids"]
        check(isinstance(refs, list) and 1 <= len(refs) <= SAMPLE_ROWS)
        for rid in refs:
            require_id(rid, "rule evidence")
        check(len(set(refs)) == len(refs))
        if evidence_ids is not None:
            check(set(refs) <= set(evidence_ids), "INVALID_AI_RESPONSE", "AI cited evidence outside its input")
    check(len(canonical_json(rules)) <= 48 * 1024, "SIZE_LIMIT")
    return rules


def rule_key(scope, rule):
    # Explanation and example IDs do not change the meaning of an approved rule.
    return "rule-" + digest([scope, sorted(rule["identity_fields"]), rule["where"]])


def assignment(fields, scope, rules):
    matches, incomplete = {}, False
    for rule in rules:
        if any(fields.get(k) != v for k, v in rule["where"].items()):
            continue
        names = sorted(set(rule["identity_fields"]) | {"code", "severity"})
        identity = {k: fields.get(k) for k in names}
        if not all(identity_value_valid(k, v) for k, v in identity.items()):
            incomplete = True
            continue
        rk = rule_key(scope, rule)
        gid = "pg-" + digest([rk, identity])
        matches[gid] = {"group_id": gid, "rule_id": rk, "identity": identity, "method": "RULE"}
    if len(matches) == 1 and not incomplete:
        return {**next(iter(matches.values())), "reason": "IDENTITY_MATCH"}
    return {"group_id": None, "rule_id": None, "identity": {}, "method": "UNDECIDED",
            "reason": "RULE_CONFLICT" if len(matches) > 1 or matches and incomplete else
                      "MISSING_IDENTITY" if incomplete else "NO_APPLICABLE_RULE"}


def fail_candidates(fields, known_groups):
    """Conservative exact-field retrieval; never a probability or root-cause conclusion."""
    values = []
    for group in known_groups:
        identity = group["identity"]
        matched, missing, different = [], [], []
        for name, value in identity.items():
            if not identity_value_valid(name, fields.get(name)):
                missing.append(name)
            elif fields[name] == value:
                matched.append(name)
            else:
                different.append(name)
        # Code alone is not sufficient even to report a positive identity candidate.
        if different or not set(matched) - {"code", "severity"}:
            continue
        values.append({"group_id": group["group_id"], "group_revision": group["group_revision"],
                       "source_bundle": group["source_bundle"], "source_unit": group["source_unit"],
                       "source_job": group["source_job"], "fail_evidence_ids": group["fail_evidence_ids"], "identity": identity,
                       "source": "EXACT_FIELDS", "matches": matched, "differences": different,
                       "missing": missing, "state": "NEEDS_EVIDENCE" if missing else "POSSIBLE_FAIL_GROUP"})
    return values


def classify_record(evidence, scope, rules, known_groups):
    fields = evidence["fields"]
    result = assignment(fields, scope, rules) if evidence["check_status"] != "PASS" else {
        "group_id": None, "rule_id": None, "identity": {}, "method": "NOT_TARGET", "reason": "PASS_NOT_TARGET"}
    return {"record_id": evidence["record_id"], "file_id": evidence["file_id"],
            "record_index": evidence["record_index"],
            "review_unit_id": evidence["review_unit_id"], "check_status": evidence["check_status"],
            "fields": fields, "location": {k: evidence[k] for k in ("line_start", "line_end", "json_pointer") if k in evidence},
            **result, "candidates": fail_candidates(fields, known_groups),
            "comparison": {"status": "COMPLETE", "method": "EXACT_FIELDS_ONLY", "groups_compared": len(known_groups)}}


def validate_ai_response(response, sample):
    check(isinstance(response, dict) and set(response) == {"schema_version", "rules", "explanation", "needs_clarification"},
          "INVALID_AI_RESPONSE")
    check(type(response["schema_version"]) is int and response["schema_version"] == SCHEMA
          and type(response["needs_clarification"]) is bool, "INVALID_AI_RESPONSE")
    check(isinstance(response["explanation"], str) and 1 <= len(response["explanation"].strip()) <= 4000,
          "INVALID_AI_RESPONSE")
    validate_rules(response["rules"], [r["record_id"] for r in sample])
    check(not response["needs_clarification"] or not response["rules"], "INVALID_AI_RESPONSE")
    return response


def validate_known_groups(groups):
    check(isinstance(groups, list) and len(groups) <= MAX_GROUPS, "CATALOG_LIMIT")
    for group in groups:
        check(isinstance(group, dict) and set(group) == {"group_id", "group_revision", "source_bundle", "source_unit",
              "source_job", "identity", "fail_evidence_ids"})
        for key in ("group_id", "source_bundle", "source_unit", "source_job"):
            require_id(group[key], key)
        check(type(group["group_revision"]) is int and group["group_revision"] > 0)
        check(isinstance(group["identity"], dict) and bool(group["identity"])
              and set(group["identity"]) <= FIELDS
              and all(identity_value_valid(k, v) for k, v in group["identity"].items()))
        check(isinstance(group["fail_evidence_ids"], list) and 1 <= len(group["fail_evidence_ids"]) <= 3)
        for rid in group["fail_evidence_ids"]:
            require_id(rid, "FAIL evidence")
    return groups
