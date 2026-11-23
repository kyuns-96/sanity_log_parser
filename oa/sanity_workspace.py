"""Persistent OA workspace and scoped PoR changes, independent of legacy SSO routes.

WS_* are explicit management records with no fabricated run/bundle identifiers.
The local deployment is an explicitly isolated synthetic workspace, not SSO fallback.
"""
from __future__ import annotations

import secrets

from common.sanity_common import ReviewError, digest
from common.sanity_submission import check
from common.sanity_submission_intake import unverified
from oa.sanity_workspace_clustering import pipeline_summaries
from oa.sanity_workspace_core import (
    CONFIG_KEY, MAX_REPORT_JSON_BYTES, NAMES, SYNTHETIC_PASSWORD, WorkspaceCoreStore,
    catalog_name_id, password_hash, record_id, token_hash, utcnow,
)
from oa.sanity_workspace_review import review_summaries
from oa.sanity_workspace_sources import CURRENT_SOURCES_SQL, delivery_status


class WorkspaceStore(WorkspaceCoreStore):
    def reports(self, user_id, block_id, version_id, *, eco=None, item_id=None, family=None, status=None, search="", offset=0, limit=25, include_history=False):
        version = self.version(user_id, block_id, version_id)
        if eco not in version["payload"]["ecos"]:
            raise ReviewError("INVALID_ECO", "Select an ECO from this version")
        predicates, parameters = ["record_type='WS_REPORT'", "workspace_version_ref=%s", "payload->>'eco' IS NOT DISTINCT FROM %s"], [version["record_id"], eco]
        if not include_history:
            predicates.append(CURRENT_SOURCES_SQL)
        base = " AND ".join(predicates)
        counts = self.db.execute("SELECT payload->>'check_status' AS status,count(*) AS count FROM {oa} WHERE " + base +
                                 " GROUP BY payload->>'check_status'", parameters).fetchall()
        grouped = self.db.execute(
            "SELECT COALESCE(payload->>'item_id','') AS item_id,payload->>'family' AS family,"
            "payload->>'check_status' AS status,payload->>'parse_status' AS parse_status,count(*) AS count "
            "FROM {oa} WHERE " + base + " GROUP BY 1,2,3,4 ORDER BY 1,2,3,4", parameters).fetchall()
        groups = {}
        for row in grouped:
            group = groups.setdefault(row["item_id"], {"item_id": row["item_id"], "total": 0,
                                                      "counts": {}, "parse_counts": {}, "families": []})
            group["total"] += row["count"]
            for key, value in (("counts", row["status"]), ("parse_counts", row["parse_status"])):
                group[key][value] = group[key].get(value, 0) + row["count"]
            if row["family"] not in group["families"]:
                group["families"].append(row["family"])
        if item_id is not None:
            predicates.append("COALESCE(payload->>'item_id','')=%s"); parameters.append(item_id)
        if family:
            predicates.append("payload->>'family'=%s"); parameters.append(family)
        if status:
            predicates.append("payload->>'parse_status'=%s" if status == "MISSING" else "payload->>'check_status'=%s"); parameters.append(status)
        if search:
            predicates.append("(strpos(lower(payload->>'id'),lower(%s))>0 OR strpos(lower(payload->>'rule'),lower(%s))>0)")
            parameters.extend((search, search))
        where = " AND ".join(predicates)
        total = self.db.execute("SELECT count(*) AS n FROM {oa} WHERE " + where, parameters).fetchone()["n"]
        rows = self.db.execute("SELECT payload - 'raw_report' - 'records' AS item," +
                               CURRENT_SOURCES_SQL + " AS is_current FROM {oa} WHERE " + where +
                               " ORDER BY COALESCE(payload->>'submitted_at',payload->>'analysis_created_at') DESC NULLS LAST,"
                               "workspace_key LIMIT %s OFFSET %s", (*parameters, limit, offset)).fetchall()
        reviews = review_summaries(self.db, [row["item"]["id"] for row in rows])
        pipelines = pipeline_summaries(self.db, [row["item"]["id"] for row in rows], compact=True, ws=self, user_id=user_id)
        for row in rows:
            row['item']['is_current'] = row['is_current']
            if row['item'].get('source_storage') == 'PARTS_V2':
                row['item'].update(delivery_status(self.db, row['item']))
            row["item"]["human_review"] = reviews.get(row["item"]["id"])
            row["item"]["group_review_summary"] = pipelines.get(row["item"]["id"])
        return {"items": [row["item"] for row in rows], "total": total, "offset": offset, "limit": limit,
                "counts": {row["status"]: row["count"] for row in counts}, "groups": list(groups.values()),
                "version": version["payload"]}


def submissions(db, user_id, block_id, *, offset=0, limit=25, workspace=None):
    workspace = WorkspaceStore(db) if workspace is None else workspace
    check(workspace.db is db, 'AUTHORIZATION_CONFIG')
    workspace.access(user_id, block_id)
    check(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100, 'INVALID_REQUEST')
    total = db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_block_ref=%s",
                       (record_id('WS_BLOCK', block_id),)).fetchone()['n']
    rows = db.execute("SELECT workspace_key,payload->'scope' AS scope,payload->'actor' AS actor,"
        "state->>'delivery_state' AS delivery_state,state->'receipt' AS receipt,created_at,updated_at "
        "FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_block_ref=%s "
        "ORDER BY created_at DESC,workspace_key LIMIT %s OFFSET %s", (record_id('WS_BLOCK', block_id), limit, offset)).fetchall()
    return {'items': [{'id': r['workspace_key'], 'scope': r['scope'],
        'submitted_by': None if unverified(r['actor']) else r['actor']['user_id'],
        'submitter_verified': not unverified(r['actor']),
        'reported_by': r['actor']['subject'] if unverified(r['actor']) else None,
        'delivery_state': r['delivery_state'], 'review_readiness': (r['receipt'] or {}).get('review_readiness', 'BLOCKED'),
        'blockers': (r['receipt'] or {}).get('blockers', []), 'workspace_links': (r['receipt'] or {}).get('workspace_links', []),
        'created_at': r['created_at'].isoformat(), 'updated_at': r['updated_at'].isoformat()} for r in rows],
        'total': total, 'offset': offset, 'limit': limit}


def seed_workspace(db, data):
    """Import only the generated, integrity-checked public fixture; never company data."""
    expected = data.get("dataset_sha256")
    content = {key: value for key, value in data.items() if key != "dataset_sha256"}
    if (digest(content) != expected or data.get("schema_version") != 1
            or data.get("summary", {}).get("origin") != "SYNTHETIC_PUBLIC_REFERENCE"
            or data["summary"].get("actual_tools_run") is not False or data["summary"].get("real_ai_called") is not False
            or data["summary"].get("db_ingestion_verified") is not True
            or len(data["users"]) != 10 or len(data["projects"]) != 3 or len(data["blocks"]) != 9):
        raise ReviewError("INVALID_WORKSPACE_SEED", "Verified synthetic fixture is required")
    store = WorkspaceStore(db)
    existing = db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (CONFIG_KEY,)).fetchone()
    if existing:
        if existing["payload"].get("dataset_sha256") != expected:
            raise ReviewError("WORKSPACE_SEED_CONFLICT", "Existing workspace must not be reset or overwritten")
        return {"status": "ALREADY_INITIALIZED"}
    for i, source in enumerate(data["users"]):
        salt = secrets.token_hex(16)
        store.put("WS_USER", source["id"], {"id": source["id"], "name": NAMES[i], "username": f"user{i + 1:02d}",
                  "role": source["role"], "salt": salt, "password_hash": password_hash(SYNTHETIC_PASSWORD, salt)}, state={"active": True})
    for source in data["projects"]:
        store.put("WS_PROJECT", source["id"], {"id": source["id"], "project_id": source["id"],
                  "name": source["name"], "tops": [source["top_user"]], "description": f"{source['name']} SoC 검증 프로젝트"})
    latest = {}
    for source in data["blocks"]:
        pid, bid = source["project_id"], source["id"]
        store.put("WS_BLOCK", bid, {"id": bid, "project_id": pid, "name": source["name"], "owners": [source["owner"]]}, project=pid)
        for index in (0, 1):
            pre_id, post_id = f"{bid}-pre-{index}", f"{bid}-post-{index}"
            pre = {"id": pre_id, "project_id": pid, "block_id": bid, "net_ver": f"NET_{index:03d}",
                   "revision": f"SYNTHETIC_{pid}_{source['name']}_PRE_R{index:02d}", "layout": "PRE", "ecos": [None], "source_pre_id": None}
            post = {"id": post_id, "project_id": pid, "block_id": bid, "net_ver": f"NET_{index:03d}",
                    "revision": source["revision"] if index else f"SYNTHETIC_{pid}_{source['name']}_POST_R00",
                    "layout": "POST", "ecos": ["chip_finish", "eco01"], "source_pre_id": pre_id}
            store.put("WS_VERSION", pre_id, pre, project=pid, block=bid)
            store.put("WS_VERSION", post_id, post, project=pid, block=bid)
            latest[bid] = post_id
        store.put("WS_POR", bid, {"block_id": bid}, project=pid, block=bid)
    for source in data["items"]:
        bid = source["block_id"]
        item = {**source, "version_id": latest[bid], "version_record_id": str(record_id("WS_VERSION", latest[bid])), "eco": "chip_finish"}
        store.put("WS_REPORT", source["id"], item, project=source["project_id"], block=bid)
    store.put("WS_CONFIG", CONFIG_KEY, {"environment": "SYNTHETIC_WORKSPACE", "schema_version": 1,
              "dataset_sha256": expected, "item_count": len(data["items"]), "initialized_at": utcnow().isoformat()})
    return {"status": "INITIALIZED", "items": len(data["items"])}
