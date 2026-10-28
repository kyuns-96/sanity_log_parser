"""Version 2 source/submission contracts. Pure stdlib; no DB or company SDK."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import re
import uuid

from common.sanity_common import ReviewError, canonical_json, digest

PROTOCOL = "source-collection-v2"
PART_BYTES = 4 * 1024 * 1024
PART_RECORDS = 1000
MAX_UNITS = 4096
MAX_PARTS = 100000
MAX_METADATA = 64 * 1024 * 1024
SOURCE_TYPES = ("SOURCE_MANIFEST", "SOURCE_PART", "SOURCE_COMMIT")
DELIVERY_STATES = ("LOCAL_QUEUED", "VWP_ACCEPTED", "PARSING", "DB_QUEUED", "OA_STORED", "REJECTED")
COLLECTION_KEYS = {"source_namespace", "project_key", "block_key", "net_ver", "submission_revision",
                   "layout", "target_eco", "pre_revision", "stage", "profile_id", "profile_version"}
SOURCE_KEYS = {"net_ver", "revision", "layout", "eco", "mode", "execution_ref", "execution_order",
               "source_stage", "tool", "tool_version"}
UNIT_KEYS = {"review_unit_id", "item_id", "report", "parser", "rule_version", "condition", "identity_fields",
             "execution_status", "applicable", "exclusion", "evaluation", "family", "source"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")


def check(value, code="INVALID_SUBMISSION", message="Invalid submission contract"):
    if not value:
        raise ReviewError(code, message)


def text(value, maximum=1000):
    return (isinstance(value, str) and 1 <= len(value) <= maximum and bool(value.strip())
            and re.search(r'[\x00-\x1f\x7f]',value) is None)


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


def actor_value(value):
    check(isinstance(value, dict) and set(value) == {"issuer", "subject", "user_id"})
    check(text(value["issuer"]) and text(value["subject"]))
    identifier(value["user_id"])
    return value


@dataclass(frozen=True)
class VerifiedRemoteSubmitter:
    """Identity verified by the bridge's company authentication service."""
    issuer: str
    subject: str
    user_id: str

    def __post_init__(self):
        actor_value(self.actor)

    @property
    def actor(self):
        return {"issuer": self.issuer, "subject": self.subject, "user_id": self.user_id}

    @property
    def key(self):
        return digest([self.issuer, self.subject])


@dataclass(frozen=True)
class VerifiedSubmitter(VerifiedRemoteSubmitter):
    """A verified local caller / authenticated mount owner, including OS UID."""
    os_uid: int

    def __post_init__(self):
        super().__post_init__()
        check(type(self.os_uid) is int and self.os_uid >= 0)


@dataclass(frozen=True)
class SubmissionInput:
    plan: dict
    # Canonical reports/... -> company-local original. Includes unexpected reports.
    reports: dict[str, Path]


class SubmissionInputProvider(ABC):
    @abstractmethod
    def prepare(self, target: dict) -> SubmissionInput:
        """Read source facts; do not write files, run tools, approve or access app DB."""


class SubmissionAuthority(ABC):
    def authenticate_bearer(self, token: str) -> VerifiedRemoteSubmitter:
        """Verify a submission credential, including issuer/audience/expiry/revocation.

        Never decode an unsigned JWT or trust actor/forwarded identity headers.
        This is a submission credential, not the OA model API JWT.
        """
        raise ReviewError('SUBMISSION_AUTH_NOT_CONFIGURED', 'HTTPS submission authentication is not connected')

    def resolve_actor(self, actor: dict) -> VerifiedRemoteSubmitter:
        """Revalidate a service-stored identity against the current company directory."""
        raise ReviewError('SUBMISSION_AUTH_NOT_CONFIGURED', 'HTTPS submitter lookup is not connected')

    @abstractmethod
    def current(self) -> VerifiedSubmitter:
        """Authenticate the actual caller. Never use an input username as proof."""

    @abstractmethod
    def inbox_owner(self, directory: Path) -> VerifiedSubmitter:
        """Verify authenticated mount/account binding; a directory name is not proof."""

    @abstractmethod
    def authorize(self, actor: VerifiedSubmitter, collection: dict) -> None:
        """Raise unless the verified active account currently owns this block."""

    @abstractmethod
    def approval_valid(self, plan: dict) -> bool:
        """Verify approval reference, approver authority, exact digest and revision."""


class SourceIdentityResolver(ABC):
    @abstractmethod
    def resolve(self, actor: dict) -> str:
        """OA: map a trusted VWP assertion's issuer/subject to an active local ID."""


def submission_receipt(row):
    """Project the durable submission state without relying on a receipt file."""
    submission, state = row['payload'], row['state']
    actor = submission['actor']
    return {'schema_version':1,'submission_id':submission['submission_id'],
            'actor_key':digest([actor['issuer'],actor['subject']]),
            'delivery_state':row['status'],'state_revision':row['version'],
            'updated_at':row['updated_at'].isoformat(),'verified_oa_state':bool(state.get('oa_receipt')),
            'review_readiness':state.get('oa_receipt',{}).get('review_readiness','BLOCKED'),
            'blockers':state.get('oa_receipt',{}).get('blockers',state.get('blockers',[])),
            'workspace_links':state.get('oa_receipt',{}).get('workspace_links',[])}


def source_version(collection, source):
    return "sv-" + digest([collection["source_namespace"], collection["project_key"], collection["block_key"],
                            source["net_ver"], source["revision"], source["layout"]])


def source_unit(collection, unit):
    source = unit["source"]
    return "su-" + digest([source_version(collection, source), source["eco"], unit["item_id"], source["mode"]])


def source_execution(collection, unit):
    return "sx-" + digest([source_unit(collection, unit), unit["source"]["execution_ref"]])


def scope_digest(plan):
    c = plan["collection"]
    # File presence, source executions, upload actor and times are not scope approval.
    scope = {key: c[key] for key in COLLECTION_KEYS - {"target_eco"}}
    value = {"scope": scope, "inventory_source": plan["inventory"]["source"],
                   "inventory_version": plan["inventory"]["version"],
                   "units": sorted(({"unit_key":source_unit(c,u),"applicable":u['applicable'],
                                     "exclusion":u.get('exclusion')}
                                    for u in plan["inventory"]["units"]),key=lambda u:u['unit_key'])}
    if plan['schema_version'] >= 3:
        from common.sanity_preserved_sources import scope_declarations
        value['preserved_files'] = scope_declarations(plan)
    return digest(value)


def validate_collection(c):
    check(isinstance(c, dict) and set(c) == COLLECTION_KEYS)
    for key in COLLECTION_KEYS - {"target_eco", "pre_revision", "layout"}:
        check(text(c[key]))
    identifier(c["project_key"]); identifier(c["block_key"])
    check(c["layout"] in ("PRE", "POST"))
    check((c["layout"] == "PRE" and c["target_eco"] is None and c["pre_revision"] is None)
          or (c["layout"] == "POST" and text(c["target_eco"], 100)
              and (c["pre_revision"] is None or text(c["pre_revision"]))))
    return c


def validate_plan(plan):
    check(isinstance(plan, dict) and set(plan) == {"schema_version", "collection", "inventory", "approval"})
    check(type(plan["schema_version"]) is int and plan["schema_version"] in (2, 3, 4), "UNSUPPORTED_SOURCE_SCHEMA")
    c = validate_collection(plan['collection'])
    inv = plan["inventory"]
    keys = {"confirmed", "source", "version", "units"}
    if plan['schema_version'] >= 3:
        keys.add('preserved_files')
    check(isinstance(inv, dict) and set(inv) == keys)
    check(type(inv["confirmed"]) is bool and text(inv["source"]) and text(inv["version"]))
    check(isinstance(inv["units"], list) and 1 <= len(inv["units"]) <= MAX_UNITS)
    ids, paths, units = set(), set(), set()
    for unit in inv["units"]:
        check(isinstance(unit, dict) and set(unit) <= UNIT_KEYS
              and UNIT_KEYS - {"evaluation", "exclusion"} <= set(unit))
        uid = identifier(unit["review_unit_id"]); identifier(unit["item_id"])
        path = relative_report(unit["report"])
        check(uid not in ids and path not in paths)
        ids.add(uid); paths.add(path)
        check(text(unit["parser"], 100) and text(unit["rule_version"], 100))
        check(unit["family"] in ("GCA", "VCLP", "SPYGLASS"))
        check(type(unit["applicable"]) is bool and unit["execution_status"] in ("COMPLETED", "FAILED", "UNKNOWN", "NOT_RUN"))
        check(isinstance(unit["condition"], dict))
        fields = unit["identity_fields"]
        check(isinstance(fields, list) and all(text(f, 100) for f in fields) and len(set(fields)) == len(fields))
        s = unit["source"]
        check(isinstance(s, dict) and set(s) == SOURCE_KEYS)
        for key in SOURCE_KEYS - {"eco", "layout", "execution_order"}:
            check(text(s[key]))
        check(type(s["execution_order"]) is int and s["execution_order"] >= 0)
        check(s["layout"] in ("PRE", "POST") and s["net_ver"] == c["net_ver"])
        check(unit["condition"].get("mode") == s["mode"])
        check((s["layout"] == "PRE" and s["eco"] is None
               and s["revision"] == (c["submission_revision"] if c["layout"] == "PRE" else c["pre_revision"]))
              or (s["layout"] == "POST" and c["layout"] == "POST" and text(s["eco"], 100)
                  and s["revision"] == c["submission_revision"]))
        key = source_unit(c, unit)
        check(key not in units)
        units.add(key)
    if c["layout"] == "POST":
        check(any(u["source"]["layout"] == "POST" and u["source"]["eco"] == c["target_eco"] for u in inv["units"]))
        check(c['pre_revision'] is None or any(u['source']['layout']=='PRE' for u in inv['units']))
    if plan['schema_version'] >= 3:
        from common.sanity_preserved_sources import validate_declarations
        validate_declarations(plan)
    approval = plan["approval"]
    if approval is not None:
        check(isinstance(approval, dict) and set(approval) == {"scope_sha256", "reference", "actor", "approved_at"})
        check(isinstance(approval["scope_sha256"], str) and HEX.fullmatch(approval["scope_sha256"]))
        check(text(approval["reference"]))
        actor_value(approval["actor"])
        try:
            check(datetime.fromisoformat(approval["approved_at"]).utcoffset() is not None)
        except (TypeError, ValueError):
            raise ReviewError("INVALID_SUBMISSION", "Invalid approval timestamp") from None
    check(len(canonical_json(plan)) <= MAX_METADATA, "SUBMISSION_LIMIT")
    return plan


def approved(plan, authority):
    value = plan["approval"]
    # Merely claiming confirmed=true cannot grant review permission.
    return bool(plan["inventory"]["confirmed"] and value and value["scope_sha256"] == scope_digest(plan)
                and authority.approval_valid(plan) is True)


def validate_target(target, plan):
    validate_plan(plan)
    c = plan["collection"]
    check(all(target[key] == c[dest] for key, dest in {
        "project": "project_key", "block": "block_key", "net_ver": "net_ver", "revision": "submission_revision",
        "layout": "layout", "eco": "target_eco", "profile": "profile_id"}.items()), "SUBMISSION_SCOPE_MISMATCH")


def packet_base(submission, collection_id):
    return {"schema_version": 2, "protocol": PROTOCOL, "submission_id": submission["submission_id"],
            "content_sha256": submission["content_sha256"], "collection_id": collection_id,
            "actor": submission["actor"], "scope": submission["plan"]["collection"]}


def validate_packet(packet):
    check(isinstance(packet, dict) and set(packet) == {
        "schema_version", "protocol", "submission_id", "content_sha256", "collection_id", "actor", "scope", "body"})
    check(type(packet["schema_version"]) is int and packet["schema_version"] == 2 and packet["protocol"] == PROTOCOL)
    submission_uuid(packet["submission_id"])
    check(isinstance(packet["content_sha256"], str) and HEX.fullmatch(packet["content_sha256"]))
    check(isinstance(packet["collection_id"], str) and re.fullmatch(r"sc-[0-9a-f]{64}", packet["collection_id"]))
    actor_value(packet["actor"])
    validate_collection(packet['scope'])
    check(isinstance(packet["body"], dict) and len(canonical_json(packet)) <= PART_BYTES, "SOURCE_MESSAGE_LIMIT")
    return packet


def message_id(kind, packet):
    check(kind in SOURCE_TYPES + ("SOURCE_RECEIPT",))
    return "s-" + digest([kind, packet])


def source_digest(report):
    """Page data are protected by the immutable header's ordered part hashes."""
    if report.get("source_storage") == "PARTS_V2":
        return digest({key: value for key, value in report.items()
                       if key not in ("records", "raw_report", "delivery_ready", "delivery_blockers")})
    return digest(report)
