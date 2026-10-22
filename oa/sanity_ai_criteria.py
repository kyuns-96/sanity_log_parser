"""Load operator-approved checking criteria from an explicitly configured private file.

The model and browser cannot register criteria. This catalog is versioned input,
not a learned decision, a historical case, or an automatic PASS rule. Workers pin
its selected contents and recheck them before publishing an AI suggestion.
"""
import json
from pathlib import Path

from common.sanity_ai_contracts import validate_criteria
from common.sanity_clustering import check
from common.sanity_common import ReviewError
from common.sanity_config import load_environment

MAX_CATALOG_BYTES = 4 * 1024 * 1024
REQUIRED_SCOPE = frozenset(("project_id", "block_id", "item_id", "family", "parser", "rule_version", "layout"))
OPTIONAL_SCOPE = frozenset(("eco", "target_type"))


def _object(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, "INVALID_AI_CRITERIA")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ReviewError("INVALID_AI_CRITERIA", "Criteria must be strict JSON")


def load_criteria(context, settings=None):
    """Return complete applicable criteria; no file means no authoritative criteria.

    A malformed/unreadable configured catalog fails closed, including malformed
    entries for other scopes. Nothing is silently repaired, sampled, or inferred
    from the report's existing PASS/FAIL status.
    """
    settings = load_environment() if settings is None else settings
    path = settings.get("SANITY_OA_AI_CRITERIA_FILE", "")
    if not path:
        return []
    check(isinstance(path, str) and "\x00" not in path and Path(path).is_absolute(), "AI_CONFIG")
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        check(len(raw) <= MAX_CATALOG_BYTES, "INVALID_AI_CRITERIA")
        catalog = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise ReviewError("INVALID_AI_CRITERIA", "Cannot read the configured criteria catalog") from None
    check(isinstance(catalog, dict) and set(catalog) == {"schema_version", "criteria"}
          and type(catalog["schema_version"]) is int and catalog["schema_version"] == 1
          and isinstance(catalog["criteria"], list) and len(catalog["criteria"]) <= 10000,
          "INVALID_AI_CRITERIA")
    scope = context.get("scope", {})
    check(isinstance(scope, dict), "INVALID_AI_INPUT")
    selected, identities = [], set()
    for criterion in catalog["criteria"]:
        check(isinstance(criterion, dict) and isinstance(criterion.get("scope"), dict), "INVALID_AI_CRITERIA")
        applies = criterion["scope"]
        check(REQUIRED_SCOPE <= set(applies) <= REQUIRED_SCOPE | OPTIONAL_SCOPE, "INVALID_AI_CRITERIA")
        validate_criteria([criterion], applies)
        identity = (criterion["id"], tuple(sorted(applies.items())))
        check(identity not in identities, "INVALID_AI_CRITERIA", "Criteria revisions must not overlap")
        identities.add(identity)
        if all(scope.get(key) == value for key, value in applies.items()):
            selected.append(criterion)
    validate_criteria(selected, scope)
    return selected
