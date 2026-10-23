"""Shared confirmed knowledge without historical report/source evidence.

Callers authorize the current target and validate historical heads, digests and
partitions before projecting a case. This module grants no access and does not
turn a draft into a confirmed example. Human-written reasons and conditions are
shared knowledge; original logs, source fields and evidence membership are not.
"""
from copy import deepcopy


EVIDENCE_MODE = "SHARED_CONFIRMED_KNOWLEDGE"
_SCOPE = ("project_id", "block_id", "item_id", "family", "parser", "rule_version",
          "layout", "eco", "target_type")
_IDENTITY_TEXT = ("id", "report_id", "version_id", "project_id", "block_id", "eco",
                  "source_sha256", "sha256", "membership_sha256", "cluster_event_id",
                  "group_id", "report_rule", "reason", "criteria", "at", "previous_event_id")
_IDENTITY_INTS = ("revision", "cluster_revision", "record_count", "source_record_count")
_IDENTITY_BOOLS = ("confirmed", "reusable", "compatible_versions")
_PIN_TEXT = ("id", "report_id", "group_id", "source_sha256", "sha256")
_COMPARISON_KEYS = (*_SCOPE, "net_ver", "revision", "version_id")
_PRIVATE_KEYS = frozenset(("records", "raw", "fields", "raw_report", "record_ids", "evidence_ids",
                           "command", "event", "proposal", "group_evidence", "source_parts"))


def _strings(value, keys, *, nullable=()):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value
            and (isinstance(value[key], str) or key in nullable and value[key] is None)}


def _integers(value, keys):
    return {key: value[key] for key in keys
            if type(value.get(key)) is int and value[key] >= 0}


def _actor(value):
    return _strings(value, ("id", "name", "username", "role"))


def _group(value):
    if not isinstance(value, dict):
        return {}
    result = {**_strings(value, ("id", "label")), **_integers(value, ("record_count",))}
    if "record_count" not in result and isinstance(value.get("record_ids"), list):
        result["record_count"] = len(value["record_ids"])
    return result


def _reference_groups(values):
    if not isinstance(values, list):
        return []
    return [_strings(value, ("case_id", "group_id", "reason"))
            for value in values if isinstance(value, dict)]


def _rationale(value):
    if not isinstance(value, dict):
        return None
    result = _strings(value, ("status", "source", "reviewed_at", "edited_at", "change_reason",
                              "previous_event_id"), nullable=("reviewed_at", "previous_event_id"))
    for key in ("reviewed_by", "edited_by"):
        if isinstance(value.get(key), dict):
            result[key] = _actor(value[key])
        elif key in value and value[key] is None:
            result[key] = None
    content = value.get("content")
    if isinstance(content, dict):
        result["content"] = _strings(content, ("reason",))
        if isinstance(content.get("reference_groups"), list):
            result["content"]["reference_groups"] = _reference_groups(content["reference_groups"])
    return result


def _version(value):
    result = _strings(value, ("id", "project_id", "block_id", "net_ver", "revision", "layout", "source_pre_id"),
                      nullable=("source_pre_id",))
    if isinstance(value, dict) and isinstance(value.get("ecos"), list):
        result["ecos"] = [eco for eco in value["ecos"] if eco is None or isinstance(eco, str)]
    return result


def _comparison(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    attributes = value.get("attributes")
    if isinstance(attributes, dict):
        result["attributes"] = {key: _strings(attributes[key], ("current", "case"), nullable=("current", "case"))
                                for key in _COMPARISON_KEYS if isinstance(attributes.get(key), dict)}
    if isinstance(value.get("different_attributes"), list):
        result["different_attributes"] = [key for key in value["different_attributes"]
                                          if isinstance(key, str) and key in _COMPARISON_KEYS]
    for key in ("eco", "version"):
        if isinstance(value.get(key), dict):
            result[key] = _strings(value[key], ("current", "case"), nullable=("current", "case"))
    for key in ("compatible_versions_explicit", "automatic_rule_reuse_approved"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    return result


def _pin(value):
    if not isinstance(value, dict):
        return {}
    return {**_strings(value, _PIN_TEXT), **_integers(value, ("revision",))}


def shared_case(case):
    """Return an immutable, idempotent allowlist view of historical knowledge.

    Counts describe the verified source; they do not claim that its original
    records were supplied to the model. No case identity or digest is invented
    or recalculated. Partial historical reference projections remain partial.
    """
    if not isinstance(case, dict):
        raise TypeError("A shared case must be an object")
    result = {**_strings(case, _IDENTITY_TEXT, nullable=("eco", "previous_event_id")),
              **_integers(case, _IDENTITY_INTS)}
    for key in _IDENTITY_BOOLS:
        if type(case.get(key)) is bool:
            result[key] = case[key]
    if case.get("conclusion") in ("PASS", "FAIL", "DEFER"):
        result["conclusion"] = case["conclusion"]
    if "proposal_conclusion" in case and case["proposal_conclusion"] in ("PASS", "FAIL", "DEFER", None):
        result["proposal_conclusion"] = case["proposal_conclusion"]
    if "proposal_agreed" in case and (type(case["proposal_agreed"]) is bool or case["proposal_agreed"] is None):
        result["proposal_agreed"] = case["proposal_agreed"]
    if isinstance(case.get("ai_proposal"), dict):
        proposal = case["ai_proposal"]
        result["ai_proposal"] = _strings(proposal, ("id",))
        if proposal.get("conclusion") in ("PASS", "FAIL", "DEFER"):
            result["ai_proposal"]["conclusion"] = proposal["conclusion"]
    if isinstance(case.get("scope"), dict):
        result["scope"] = _strings(case["scope"], _SCOPE, nullable=("eco",))
    if isinstance(case.get("actor"), dict):
        result["actor"] = _actor(case["actor"])
    if isinstance(case.get("source_version"), dict):
        result["source_version"] = _version(case["source_version"])
    if isinstance(case.get("conditions"), dict):
        result["conditions"] = {key: value for key, value in case["conditions"].items()
                                if isinstance(key, str) and key not in _PRIVATE_KEYS and isinstance(value, str)}
    if "exception" in case:
        if case["exception"] is None:
            result["exception"] = None
        elif isinstance(case["exception"], dict):
            result["exception"] = _strings(case["exception"], ("rule", "reason", "conditions"))
    if isinstance(case.get("groups"), list):
        result["groups"] = [_group(group) for group in case["groups"] if isinstance(group, dict)]
    for key in ("group", "group_snapshot"):
        if isinstance(case.get(key), dict):
            result[key] = _group(case[key])
    if isinstance(case.get("rationales"), dict):
        result["rationales"] = {key: _rationale(value) for key, value in case["rationales"].items()
                                if isinstance(key, str) and isinstance(value, dict)}
    for key in ("rationale", "clustering_rationale"):
        if isinstance(case.get(key), dict):
            result[key] = _rationale(case[key])
        elif key in case and case[key] is None:
            result[key] = None
    if isinstance(case.get("references"), list):
        result["references"] = [_pin(value) for value in case["references"] if isinstance(value, dict)]
    if isinstance(case.get("reference_ids"), list):
        result["reference_ids"] = [value for value in case["reference_ids"] if isinstance(value, str)]
    if isinstance(case.get("retrieval_comparison"), dict):
        result["retrieval_comparison"] = _comparison(case["retrieval_comparison"])
    if "source_record_count" not in result and isinstance(case.get("records"), list):
        result["source_record_count"] = len(case["records"])
    result.update(evidence_mode=EVIDENCE_MODE, example_complete=False)
    return result


def shared_case_views(value):
    """Copy an API payload while projecting historical case collections only.

    Current target records, group membership, original evidence and editable
    rationales are preserved. Historical cases nested in saved proposals and
    audit history receive the same allowlist used for fresh model inputs.
    """
    if isinstance(value, list):
        return [shared_case_views(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        if key in ("cases", "references") and isinstance(item, list):
            result[key] = [shared_case(case) if isinstance(case, dict) else deepcopy(case) for case in item]
        elif key == "input_cases" and isinstance(item, list):
            result[key] = [_pin(case) if isinstance(case, dict) else deepcopy(case) for case in item]
        else:
            result[key] = shared_case_views(item)
    return result
