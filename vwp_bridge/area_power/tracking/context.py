"""Version/baseline resolution, mapping ambiguity check, and comparison setup for NET_VER tracking."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from common.area_power.analysis import (
    advisory_lock,
    comparison_coverage,
    existing_dedupe,
    version_chain,
)
from common.area_power.csv_input import read_hierarchy
from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest
from common.area_power.mapping_suggestions import (
    Tree,
    build_index,
    candidates,
    rename_patterns,
    unmatched,
)
from common.area_power.models import (
    CALCULATION_CONTRACT,
    COMPARISON_FORMAT,
    CORRESPONDENCE_POLICY,
    ComparisonRequest,
)
from common.area_power.tracking import CornerSpec
from common.sanity_db import Database as HostDatabase
from ..ai_models import ACTOR

BINDING_PREFIX = "design-atlas-v1.binding."


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now().isoformat()


def parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def latest_one(db, conn, record_type: str, **filters) -> dict[str, Any] | None:
    rows = db.records(conn, record_type, latest=True, limit=1, **filters)
    return rows[0] if rows else None


def resolve_version_info(db, conn, version_id: UUID, block_id: UUID | None = None, *, host_db=None) -> dict[str, str | None]:
    """Resolve explicit (net_ver, revision, layout, eco_num) from version payload or host OA WS_VERSION."""
    version = db.record(conn, version_id, kind="version")
    payload = version.get("payload") or {}
    if payload.get("net_ver"):
        return {
            "net_ver": str(payload["net_ver"]),
            "revision": str(payload.get("revision") or ""),
            "layout": str(payload.get("layout") or ""),
            "eco_num": str(payload.get("eco_num") or ""),
        }
    close_host = False
    if host_db is None:
        try:
            host_db = HostDatabase("oa")
            host_db.__enter__()
            close_host = True
        except Exception:
            host_db = None
    if host_db is None:
        return {"net_ver": None, "revision": None, "layout": None, "eco_num": None}
    try:
        bindings = [
            row["payload"]
            for row in host_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                (BINDING_PREFIX + "%",),
            ).fetchall()
        ]
        natives = {
            b.get("version_id")
            for b in bindings
            if b.get("area_version_id") == str(version_id)
            and (block_id is None or b.get("area_block_id") == str(block_id))
        }
        direct = payload.get("sanity_version_id")
        if not natives and isinstance(direct, str):
            natives = {direct}
        found = []
        for native in natives:
            row = host_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s",
                (native,),
            ).fetchone()
            if row and row["payload"].get("net_ver"):
                found.append(row["payload"])
        net_set = {item.get("net_ver") for item in found if item.get("net_ver")}
        if len(net_set) == 1:
            sample = found[0]
            return {
                "net_ver": sample.get("net_ver"),
                "revision": sample.get("revision"),
                "layout": sample.get("layout"),
                "eco_num": sample.get("eco_num") or "",
            }
        return {"net_ver": None, "revision": None, "layout": None, "eco_num": None}
    except Exception:
        return {"net_ver": None, "revision": None, "layout": None, "eco_num": None}
    finally:
        if close_host and host_db is not None:
            try:
                host_db.__exit__(None, None, None)
            except Exception:
                pass


def applicable_notes_and_watch(db, conn, current_upload: dict, baseline_upload: dict, kind: str, current_net: str, *, host_db=None):
    chain = version_chain(db, conn, current_upload["version_id"])
    interval = chain
    if kind == "previous_version":
        bvid = str(baseline_upload["version_id"])
        interval = chain[: chain.index(bvid)] if bvid in chain else chain
    wanted = list(dict.fromkeys([*interval, *([str(baseline_upload["version_id"])] if kind == "previous_version" else [])]))
    labels = {}
    for vid in wanted:
        info = resolve_version_info(db, conn, UUID(vid), current_upload["block_id"], host_db=host_db)
        labels[vid] = info.get("net_ver")
    if current_net:
        labels[str(current_upload["version_id"])] = current_net
    baseline_net = labels.get(str(baseline_upload["version_id"])) if kind == "previous_version" else None
    applicable = [
        label
        for label in dict.fromkeys(labels.get(vid) for vid in reversed(interval))
        if label is not None and label != baseline_net
    ]
    if current_net and current_net != baseline_net and current_net not in applicable:
        applicable.append(current_net)
    warnings = []
    unresolved = sum(labels.get(vid) is None for vid in interval)
    if unresolved:
        warnings.append({"code": "net_ver_unresolved", "count": unresolved})
    if kind == "previous_version" and baseline_net is None:
        warnings.append({"code": "baseline_net_ver_unresolved", "count": 1})
    same_rtl = kind == "previous_version" and current_net is not None and current_net == baseline_net

    notes = []
    for row in db.records(conn, "change", project_id=current_upload["project_id"], block_id=current_upload["block_id"], latest=True, limit=500):
        payload = row["payload"]
        if payload.get("archived"):
            continue
        if payload.get("note_kind") == "net_ver":
            applies = payload.get("net_ver") in applicable
        elif payload.get("note_kind") == "project_change":
            applies = kind == "previous_project" and str(payload.get("baseline_project_id")) == str(baseline_upload["project_id"])
        else:
            applies = False
        if applies:
            notes.append({
                "id": str(row["id"]),
                "entity_id": str(row["entity_id"]),
                "revision": row["revision"],
                "kind": payload["note_kind"],
                "net_ver": payload.get("net_ver"),
                "text": payload["text"],
                "updated_by": row.get("updated_by"),
                "created_at": row.get("created_at"),
            })
    order = {label: pos for pos, label in enumerate(applicable)}
    notes.sort(key=lambda note: (note["kind"] != "project_change", order.get(note["net_ver"], -1), note["id"]))

    watched = []
    for row in db.records(conn, "target", project_id=current_upload["project_id"], block_id=current_upload["block_id"], latest=True, limit=500):
        if row["payload"].get("watch"):
            watched.extend(row["payload"].get("paths", []))

    expectations = []
    for row in db.records(conn, "tracking_expectation", project_id=current_upload["project_id"], block_id=current_upload["block_id"], latest=True, limit=500):
        p = row["payload"]
        if p.get("archived") or row.get("state") == "archived":
            continue
        eff = p.get("effective_net_ver")
        if eff is None or eff in applicable or eff == current_net:
            expectations.append({"id": str(row["id"]), **p})

    process = {
        "current": db.record(conn, current_upload["project_id"], kind="project")["payload"].get("process"),
        "baseline": db.record(conn, baseline_upload["project_id"], kind="project")["payload"].get("process"),
    }
    return {
        "notes": notes,
        "note_warnings": warnings,
        "same_rtl": same_rtl,
        "applicable_net_vers": applicable,
        "watched_paths": list(dict.fromkeys(watched)),
        "approved_expectations": expectations,
        "process": process,
    }


def check_mapping_for_uploads(
    db, settings, current_upload: dict, baseline_upload: dict, mapping_row: dict | None
) -> dict[str, Any]:
    """Validate existing approved mapping + relative correspondence; flag ambiguous renames/splits/merges."""
    with db.connect() as conn:
        cu_blob = db.upload(conn, current_upload["id"], include_blob=True)
        bu_blob = db.upload(conn, baseline_upload["id"], include_blob=True)
    cm, c_items = read_hierarchy(cu_blob, settings.max_uncompressed_bytes)
    bm, b_items = read_hierarchy(bu_blob, settings.max_uncompressed_bytes)
    current_tree = Tree(c_items, cm)
    before_tree = Tree(b_items, bm)

    rules = list(mapping_row["payload"].get("rules", [])) if mapping_row else []
    try:
        protected, free_b, free_c, anchors = unmatched(before_tree, current_tree, rules)
    except DomainError as exc:
        return {"ok": False, "reason": exc.code, "message": str(exc), "ambiguous_count": 0, "baseline_unmatched": 0, "current_unmatched": 0}

    if not free_b or not free_c:
        return {"ok": True, "reason": None, "ambiguous_count": 0, "baseline_unmatched": len(free_b), "current_unmatched": len(free_c)}

    index = build_index(current_tree, free_c)
    renames = rename_patterns(before_tree, current_tree, anchors)
    cases, held = candidates(before_tree, current_tree, sorted(free_b), anchors, renames, index, set())
    ambiguous = [item for item in held if item.get("reason") in ("ambiguous", "possible_split", "possible_merge")]
    if cases or ambiguous:
        return {
            "ok": False,
            "reason": "mapping_ambiguous",
            "message": f"확인이 필요한 경로 후보 {len(cases) + len(ambiguous)}건이 있습니다.",
            "ambiguous_count": len(cases) + len(ambiguous),
            "candidate_count": len(cases),
            "held_count": len(ambiguous),
            "baseline_unmatched": len(free_b),
            "current_unmatched": len(free_c),
        }
    return {"ok": True, "reason": None, "ambiguous_count": 0, "baseline_unmatched": len(free_b), "current_unmatched": len(free_c)}


def ensure_comparison(db, settings, current_upload: dict, baseline_upload: dict, comparison_kind: str, mapping_id: UUID | None, actor: str) -> dict:
    """Create or reuse an immutable hierarchy-reference-v1 comparison for automatic tracking."""
    body = ComparisonRequest(
        current_upload_id=current_upload["id"],
        baseline_upload_id=baseline_upload["id"],
        comparison_kind=comparison_kind,
        mapping_id=mapping_id,
    )
    request_data = body.model_dump(mode="python")
    numeric = {key: value for key, value in request_data.items() if key not in ("change_ids", "recipients")}
    numeric["result_format"] = COMPARISON_FORMAT
    numeric["calculation_contract"] = CALCULATION_CONTRACT
    numeric["target_revisions"] = []
    designation = None
    if comparison_kind == "previous_project":
        with db.connect() as conn:
            proj = db.record(conn, current_upload["project_id"], kind="project")
        designation = {
            "project_id": proj["id"],
            "baseline_project_id": baseline_upload["project_id"],
            "row_version": proj["row_version"],
        }
        numeric["baseline_project_id"] = baseline_upload["project_id"]
    correspondence = CORRESPONDENCE_POLICY if baseline_upload["project_id"] != current_upload["project_id"] else None
    if correspondence:
        numeric["correspondence"] = correspondence
    key = digest(numeric)

    with db.connect() as conn:
        advisory_lock(conn, key)
        old = existing_dedupe(db, conn, "comparison", key, lock=True)
        if old is not None and old["state"] == "completed":
            return old
        if old is None:
            def _ref(upload):
                fields = ("id", "project_id", "block_id", "version_id", "snapshot_id", "condition_key", "structure_digest", "original_sha256")
                return {**{f: upload[f] for f in fields}, "input_contract_sha256": digest(upload["input_contract"]).hex()}

            payload = {
                "request": request_data,
                "result_format": COMPARISON_FORMAT,
                "calculation_contract": CALCULATION_CONTRACT,
                "frozen": {
                    "current_upload": _ref(current_upload),
                    "baseline_upload": _ref(baseline_upload),
                    "scope_target_ids": [],
                    "power_uploads": [],
                    **({"baseline_designation": designation} if designation is not None else {}),
                },
                "coverage": {},
                **({"correspondence": correspondence} if correspondence else {}),
                "policy_unconfigured": True,
                "ai_state": "not_requested",
            }
            scope = {k: current_upload[k] for k in ("project_id", "version_id", "block_id", "snapshot_id") if current_upload.get(k) is not None}
            old = db.insert_record(
                conn, "comparison", payload, actor, state="queued",
                upload_id=current_upload["id"], baseline_upload_id=baseline_upload["id"],
                dedupe_key=key, **scope,
            )

    coverage = comparison_coverage(db, settings, old)
    with db.connect() as conn:
        locked = db.record(conn, old["id"], kind="comparison", lock=True)
        if locked["state"] == "completed":
            return locked
        return db.update_record(
            conn, locked["id"], ACTOR, state="completed",
            payload={**locked["payload"], "coverage": coverage}, error_detail=None,
        )


def resolve_baseline_upload(db, conn, block_cfg: dict, corner: CornerSpec) -> dict | None:
    """Resolve the pinned baseline upload for a corner: explicit approved_baselines first,
    then automatic corner match inside the block's pinned baseline_version_id."""
    cid = corner.corner_id()
    ref = next((r for r in (block_cfg.get("approved_baselines") or []) if r.get("corner_id") == cid), None)
    if ref and ref.get("upload_id"):
        return db.upload(conn, UUID(str(ref["upload_id"])))
    bvid = block_cfg.get("baseline_version_id")
    bpid = block_cfg.get("baseline_project_id")
    bbid = block_cfg.get("baseline_block_id")
    if bvid and bpid and bbid:
        rows = db.uploads(
            conn,
            project_id=UUID(str(bpid)),
            block_id=UUID(str(bbid)),
            version_id=UUID(str(bvid)),
            state="ready",
            limit=50,
        )
        matched = [u for u in rows if corner.matches_upload(u)]
        if matched:
            matched.sort(key=lambda u: str(u.get("created_at") or ""), reverse=True)
            return matched[0]
    return None
