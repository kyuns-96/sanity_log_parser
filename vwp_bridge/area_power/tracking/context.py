"""Version/baseline resolution, mapping ambiguity check, and comparison setup for NET_VER tracking."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from common.area_power.analysis import (
    advisory_lock,
    build_comparison_dedupe_and_payload,
    comparison_coverage,
    existing_dedupe,
)
from common.area_power.atlas_bindings import (
    BINDING_PREFIX,
    collect_applicable_triage_inputs,
    resolve_area_version_info,
)
from common.area_power.errors import DomainError
from common.area_power.layered_csv import load_base_upload, read_layered_hierarchy
from common.area_power.mapping_suggestions import (
    Tree,
    build_index,
    candidates,
    rename_patterns,
    unmatched,
)
from common.area_power.models import ComparisonRequest
from common.area_power.tracking import CornerSpec
from ..ai_runtime import ACTOR


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
    return resolve_area_version_info(db, conn, version_id, block_id, host_db=host_db, auto_open_host=True)


def applicable_notes_and_watch(db, conn, current_upload: dict, baseline_upload: dict, kind: str, current_net: str, *, host_db=None):
    collected = collect_applicable_triage_inputs(
        db, conn, current_upload, baseline_upload, kind,
        host_db=host_db, current_net_override=current_net, auto_open_host=True,
    )
    return {k: v for k, v in collected.items() if k != "comparison_kind"}


def check_mapping_for_uploads(
    db, settings, current_upload: dict, baseline_upload: dict, mapping_row: dict | None
) -> dict[str, Any]:
    """Validate existing approved mapping + relative correspondence; flag ambiguous renames/splits/merges."""
    with db.connect() as conn:
        cu_blob = db.upload(conn, current_upload["id"], include_blob=True)
        bu_blob = db.upload(conn, baseline_upload["id"], include_blob=True)
        cu_base = load_base_upload(db, conn, cu_blob)
        bu_base = load_base_upload(db, conn, bu_blob)
    cm, c_items = read_layered_hierarchy(cu_blob, settings.max_uncompressed_bytes, base_upload=cu_base)
    bm, b_items = read_layered_hierarchy(bu_blob, settings.max_uncompressed_bytes, base_upload=bu_base)
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
    designation = None
    if comparison_kind == "previous_project":
        with db.connect() as conn:
            proj = db.record(conn, current_upload["project_id"], kind="project")
        designation = {
            "project_id": proj["id"],
            "baseline_project_id": baseline_upload["project_id"],
            "row_version": proj["row_version"],
        }
    key, payload = build_comparison_dedupe_and_payload(
        request_data, current_upload, baseline_upload, designation=designation, policy_unconfigured=True
    )

    with db.connect() as conn:
        advisory_lock(conn, key)
        old = existing_dedupe(db, conn, "comparison", key, lock=True)
        if old is not None and old["state"] == "completed":
            return old
        if old is None:
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
