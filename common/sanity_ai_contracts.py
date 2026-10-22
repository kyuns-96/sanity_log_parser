"""Direct-evidence judgment contract for the OA AI decision cycle.

These pure checks do not authorize DB reads or establish that a criterion is
authoritative. The caller supplies the current, authorized group and approved
criteria/cases, then rechecks their revisions before saving a proposal. The
legacy human-case-only review contract remains unchanged.
"""
from common.sanity_clustering import check


REVIEW_V2_KEYS = {
    "schema_version", "conclusion", "reason", "evidence_ids", "reference_ids", "criterion_ids",
}


def _ids(values, *, maximum=100):
    check(isinstance(values, list) and len(values) <= maximum
          and all(isinstance(value, str) and 1 <= len(value) <= 200 for value in values),
          "INVALID_ADVICE", "Invalid evidence or reference list")
    check(len(set(values)) == len(values), "INVALID_ADVICE", "Duplicate reference")
    return set(values)


def review_evidence(context):
    """Return the complete selected group; reject ambiguous or partial membership."""
    check(isinstance(context, dict), "INVALID_AI_INPUT")
    records = context.get("group_evidence")
    check(isinstance(records, list) and records
          and all(isinstance(row, dict) for row in records), "INVALID_AI_INPUT")
    keys = [row.get("id") for row in records]
    check(all(isinstance(key, str) and 1 <= len(key) <= 128 for key in keys)
          and len(set(keys)) == len(keys), "INVALID_AI_INPUT")
    group = context.get("group_snapshot")
    if group is not None:
        check(isinstance(group, dict) and isinstance(group.get("record_ids"), list)
              and all(isinstance(key, str) for key in group["record_ids"])
              and len(group["record_ids"]) == len(keys)
              and set(group["record_ids"]) == set(keys), "INVALID_AI_INPUT",
              "The complete current group must be supplied")
    return records


def validate_criteria(criteria, scope):
    """Validate the supplied criterion catalog and its applicability scope."""
    check(isinstance(criteria, list) and len(criteria) <= 100 and isinstance(scope, dict),
          "INVALID_AI_CRITERIA")
    ids = set()
    for criterion in criteria:
        check(isinstance(criterion, dict) and set(criterion) == {"id", "version", "text", "scope"},
              "INVALID_AI_CRITERIA")
        key, version, text = criterion["id"], criterion["version"], criterion["text"]
        check(isinstance(key, str) and 1 <= len(key.strip()) <= 200 and key == key.strip()
              and key not in ids and isinstance(version, str) and 1 <= len(version.strip()) <= 200
              and isinstance(text, str) and 1 <= len(text.strip()) <= 20000,
              "INVALID_AI_CRITERIA")
        applies = criterion["scope"]
        check(isinstance(applies, dict) and applies
              and all(isinstance(name, str) and isinstance(value, str) and value
                      and scope.get(name) == value for name, value in applies.items()),
              "INVALID_AI_CRITERIA", "Criterion scope must match the current target")
        ids.add(key)
    return ids


def validate_review_v2(value, context, cases, criteria):
    """Validate a proposal grounded in current evidence and criteria or human cases.

    A first judgment needs no historical case when an approved criterion is
    cited. Both PASS and FAIL require current group evidence. DEFER may have no
    citations, but supplied citations must still be valid. Nothing here records
    human approval or changes the immutable tool result.
    """
    records = review_evidence(context)
    criterion_ids = validate_criteria(criteria, context.get("scope", {}))
    check(isinstance(cases, list) and all(isinstance(case, dict)
          and isinstance(case.get("id"), str) for case in cases), "INVALID_AI_INPUT")
    case_ids = {case["id"] for case in cases}
    check(len(case_ids) == len(cases), "INVALID_AI_INPUT")
    check(isinstance(value, dict) and set(value) == REVIEW_V2_KEYS
          and type(value["schema_version"]) is int and value["schema_version"] == 2
          and value["conclusion"] in ("PASS", "FAIL", "DEFER")
          and isinstance(value["reason"], str) and 1 <= len(value["reason"].strip()) <= 2000,
          "INVALID_ADVICE", "Invalid direct-evidence judgment proposal")
    evidence = _ids(value["evidence_ids"])
    references = _ids(value["reference_ids"])
    applied = _ids(value["criterion_ids"])
    check(evidence <= {record["id"] for record in records}
          and references <= case_ids and applied <= criterion_ids,
          "INVALID_ADVICE", "Only supplied target evidence, cases and criteria may be cited")
    if value["conclusion"] != "DEFER":
        check(evidence and (references or applied), "INVALID_ADVICE",
              "A judgment needs direct evidence and an approved criterion or confirmed case")
        check(context.get("ready", True) is True and context.get("can_review", True) is True,
              "INVALID_ADVICE", "Incomplete or unconfirmed input requires deferral")
    return {**value, "reason": value["reason"].strip(),
            "evidence_ids": list(value["evidence_ids"]),
            "reference_ids": list(value["reference_ids"]), "criterion_ids": list(value["criterion_ids"])}
