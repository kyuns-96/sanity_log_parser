"""Judgment suggestions are separate from immutable tool results and human approval.

The reference advisor executes explicitly confirmed conditions; it is not an LLM.
Corporate implementations of ReviewAdvisor run on VWP, never as an OA fallback.
"""
from abc import ABC, abstractmethod

from common.sanity_common import ReviewError
from common.sanity_integration_contracts import AdvisorContext, ReviewCase, ReviewProposal

FACT_FIELDS = ("mode", "object", "check_point", "severity", "code", "design")


def normalize_review_exception(value, *, code="INVALID_EXCEPTION", canonical=False):
    """Validate human context without interpreting rule names or conditions.

    New writes trim text. Stored events must already be canonical, and are
    never modified or retrospectively annotated by the reader.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"rule", "reason", "conditions"}:
        raise ReviewError(code, "Exception requires rule, reason and conditions")
    normalized = {}
    for key, maximum in (("rule", 500), ("reason", 2000), ("conditions", 2000)):
        text = value[key]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= maximum:
            raise ReviewError(code, "Exception fields must contain bounded text")
        normalized[key] = text.strip()
    if canonical and value != normalized:
        raise ReviewError(code, "Stored exception is not canonical")
    return normalized


def validate_stored_review_exception(event):
    if "exception" not in event:
        return
    value = normalize_review_exception(event["exception"], code="STATE_CORRUPT", canonical=True)
    if value is not None and (event.get("conclusion") not in ("PASS", "FAIL")
                              or event.get("reusable") is not False):
        raise ReviewError("STATE_CORRUPT", "Stored exception conflicts with decision semantics")


def report_facts(report):
    facts = {"rule": str(report["rule"])} if report.get("rule") else {}
    records = report.get("records", [])
    for key in FACT_FIELDS:
        values = [record.get("fields", {}).get(key) for record in records]
        if values and all(isinstance(value, (str, int, float, bool)) for value in values):
            normalized = {str(value) for value in values}
            if len(normalized) == 1:
                value = normalized.pop()
                if value and len(value) <= 500:
                    facts[key] = value
    return facts


class ReviewAdvisor(ABC):
    @abstractmethod
    def propose(self, context: AdvisorContext, cases: list[ReviewCase]) -> ReviewProposal:
        """Return conclusion/reason/reference_ids, without changing any source or decision.

        context contains the scoped report, immutable source hash and normalized facts.
        cases are current, human-confirmed examples with explicit scope and conditions.
        The caller validates references and records advisor identity and input provenance.
        For group review, only group_evidence is the decision target. The current
        runtime validator requires a supplied human-case reference for PASS/FAIL;
        no cases means DEFER. Types do not activate the corporate execution path.
        """


class ApprovedCriteriaAdvisor(ReviewAdvisor):
    def propose(self, context, cases):
        matching = [case for case in cases if case.get("exception") is None
                    and case["reusable"] and case["conditions"]
                    and all(context["facts"].get(key) == value for key, value in case["conditions"].items())]
        conclusions = {case["conclusion"] for case in matching}
        if len(matching) > 100:
            return {"conclusion": "DEFER", "reason": "적용 사례가 100건을 넘어 기준 범위를 좁혀야 합니다.", "reference_ids": []}
        if not matching:
            return {"conclusion": "DEFER", "reason": "현재 조건에 적용할 확정 기준이 없습니다. 담당자의 판정과 기준을 먼저 기록해 주세요.", "reference_ids": []}
        return {"conclusion": next(iter(conclusions)) if len(conclusions) == 1 else "DEFER",
                "reason": f"조건이 일치하는 사람의 확정 기준 {len(matching)}건을 적용했습니다." if len(conclusions) == 1
                          else "같은 조건에 PASS와 FAIL 기준이 충돌합니다. 담당자의 확인이 필요합니다.",
                "reference_ids": [case["id"] for case in matching]}


def validate_advice(value, cases):
    if (not isinstance(value, dict) or set(value) != {"conclusion", "reason", "reference_ids"}
            or value["conclusion"] not in ("PASS", "FAIL", "DEFER")
            or not isinstance(value["reason"], str) or not 1 <= len(value["reason"].strip()) <= 2000
            or not isinstance(value["reference_ids"], list) or len(value["reference_ids"]) > 100
            or any(not isinstance(key, str) for key in value["reference_ids"])):
        raise ReviewError("INVALID_ADVICE", "Review advisor returned an invalid proposal")
    ids = value["reference_ids"]
    if len(set(ids)) != len(ids) or not set(ids) <= {case["id"] for case in cases}:
        raise ReviewError("INVALID_ADVICE", "Review proposal references unknown or duplicate cases")
    if value["conclusion"] != "DEFER" and not ids:
        raise ReviewError("INVALID_ADVICE", "A final suggestion needs human-confirmed reference cases")
    return value
