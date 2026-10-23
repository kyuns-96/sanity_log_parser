"""Bounded lexical retrieval policy, shared independently of DB/model clients.

Similarity selects examples for an AI to compare; it never decides a group or
verdict. Human confirmation makes an example eligible. ``reusable`` continues
to mean approval of automatic rule reuse, not permission to read an example.
"""
from collections import Counter
import math
import re

from common.sanity_clustering import check
from common.sanity_ai_knowledge import shared_case
from common.sanity_common import canonical_json, digest

METHOD = "lexical-shared-confirmed-knowledge-v3"
SCOPE_KEYS = ("project_id", "block_id", "item_id", "family", "parser", "rule_version", "layout")
_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9_.:/-]*|[가-힣]+")


def words(value):
    """Words plus Hangul bigrams/trigrams allow Korean suffix variation."""
    for token in _WORDS.findall(str(value).lower()):
        if len(token) < 2:
            continue
        yield token
        if re.fullmatch(r"[가-힣]+", token):
            for length in (2, 3):
                if len(token) > length:
                    yield from (token[index:index + length] for index in range(len(token) - length + 1))


def text_values(value):
    if isinstance(value, dict):
        for part in value.values():
            yield from text_values(part)
    elif isinstance(value, (list, tuple)):
        for part in value:
            yield from text_values(part)
    elif isinstance(value, (str, int, float)):
        yield str(value)


def query_profile(context, *, max_terms=128):
    check(type(max_terms) is int and 1 <= max_terms <= 512, "INVALID_REQUEST")
    counts = Counter()
    records = context.get("group_evidence", context.get("report", {}).get("records", []))
    for record in records:
        for text in text_values({"raw": record.get("raw", ""), "fields": record.get("fields", {})}):
            counts.update(set(words(text)))
    for text in text_values({key: context.get(key) for key in ("facts", "clustering_rationale", "criteria")}):
        counts.update(set(words(text)))
    # Frequency is useful for a representative query, never a semantic decision.
    ranked = sorted(counts, key=lambda key: (-counts[key], -len(key), key))
    terms = ranked[:max_terms]
    return {"terms": terms, "weights": {key: 1 + math.log1p(counts[key]) for key in terms},
            "feature_count": len(counts), "omitted_query_features": max(0, len(counts) - len(terms)),
            "source_record_count": len(records), "query_sha256": digest(terms)}


def compatible(context, case, *, stage):
    check(stage in ("CLUSTER", "REVIEW"), "INVALID_REQUEST")
    scope, other = context.get("scope", {}), case.get("scope", {})
    # These attributes describe the source, not permission to use its example.
    # The DB retriever independently validates registered source scope and
    # shared-knowledge eligibility. Original-source access keeps its own RBAC.
    if any(key not in scope or key not in other for key in SCOPE_KEYS):
        return False
    if any(not scope.get(key) or not other.get(key) for key in ("project_id", "block_id", "item_id")):
        return False
    if case.get("report_id") == context["report"]["id"]:
        return False
    if stage == "CLUSTER":
        return case.get("confirmed") is True
    return (case.get("conclusion") in ("PASS", "FAIL")
            and bool(case.get("group_id")) == bool(context.get("group_id")))


def comparison(context, case):
    attributes = (*SCOPE_KEYS, "eco")
    differences = {key: {"current": context["scope"].get(key), "case": case["scope"].get(key)}
                   for key in attributes}
    current_version, source_version = context.get("version", {}), case.get("source_version", {})
    differences.update({key: {"current": current_version.get(key), "case": source_version.get(key)}
                        for key in ("net_ver", "revision")})
    return {"attributes": differences,
            "different_attributes": [key for key, values in differences.items() if values["current"] != values["case"]],
            "eco": {"current": context["scope"].get("eco"), "case": case["scope"].get("eco")},
            "version": {"current": context["report"]["version_id"], "case": case["version_id"]},
            "compatible_versions_explicit": case.get("compatible_versions") is True,
            "automatic_rule_reuse_approved": case.get("reusable") is True}


def rank_case(profile, case, *, matched_terms=None):
    if matched_terms is None:
        knowledge = shared_case(case)
        matched_terms = set(words(" ".join(text_values({key: knowledge.get(key) for key in
            ("reason", "criteria", "conditions", "exception", "groups", "group_snapshot", "rationales", "clustering_rationale", "scope", "report_rule")}))))
    weights = profile["weights"]
    total = sum(weights.values())
    return sum(weights[term] for term in set(matched_terms) if term in weights) / total if total else 0.0


def selection_key(candidate):
    """Stable relevance-first order; IDs only resolve otherwise equal scores."""
    return (-candidate["score"], str(candidate["case"]["id"]))


def select_ranked(candidates, *, limit, max_bytes):
    """Select full examples, retaining relevant opposing verdicts when possible."""
    check(type(limit) is int and 1 <= limit <= 100 and type(max_bytes) is int and max_bytes > 0, "INVALID_REQUEST")
    ordered = sorted((row for row in candidates if row["score"] > 0), key=selection_key)
    selected, seen, used = [], set(), 0

    def take(row):
        nonlocal used
        key = row["case"]["id"]
        size = row.get("bytes", len(canonical_json(row["case"])))
        if key in seen or len(selected) >= limit or used + size > max_bytes:
            return False
        selected.append(row)
        seen.add(key)
        used += size
        return True

    if ordered:
        take(ordered[0])
    # Opposing human outcomes are evidence to compare, never a voting rule.
    if limit >= 2 and selected and selected[0]["case"].get("conclusion") in ("PASS", "FAIL"):
        opposite = "FAIL" if selected[0]["case"]["conclusion"] == "PASS" else "PASS"
        for row in ordered:
            if row["case"].get("conclusion") == opposite and take(row):
                break
    for row in ordered:
        take(row)
    return selected
