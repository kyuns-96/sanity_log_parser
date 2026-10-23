"""Persistent OA workspace and scoped PoR changes, independent of legacy SSO routes.

WS_* are explicit management records with no fabricated run/bundle identifiers.
The local deployment is an explicitly isolated synthetic workspace, not SSO fallback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from common.sanity_common import ReviewError, digest
from common.sanity_db import json_value, stable_uuid, uuid_value, lock_key

SYNTHETIC_PASSWORD = "sanity-workspace"  # Public test fixture, never a company credential.
CONFIG_KEY = "workspace-v1"
MAX_REPORT_JSON_BYTES = 128 * 1024 * 1024
NAMES = ("이서연", "박지훈", "김도윤", "정민수", "최유진", "한지민", "오서준", "윤지호", "임수빈", "강현우")


def utcnow():
    return datetime.now(timezone.utc)


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1,
                          maxmem=64 * 1024 * 1024, dklen=32).hex()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def record_id(kind, key):
    return stable_uuid("workspace-v1", kind, key)


class WorkspaceStore:
    def __init__(self, db):
        self.db = db

    def get(self, kind, key, lock=False):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type=%s AND workspace_key=%s" +
                              (" FOR UPDATE" if lock else ""), (kind, key)).fetchone()
        if row is None:
            raise ReviewError("NOT_FOUND", "Workspace resource not found")
        return row

    def rows(self, kind):
        return self.db.execute("SELECT * FROM {oa} WHERE record_type=%s ORDER BY workspace_key", (kind,)).fetchall()

    def put(self, kind, key, payload, *, project=None, block=None, state=None):
        project_ref = record_id("WS_PROJECT", project) if project else None
        block_ref = record_id("WS_BLOCK", block) if block else None
        scope = payload.get("project_id") if kind == "WS_PROJECT" else project
        self.db.execute("INSERT INTO {oa} (record_id,record_type,workspace_key,project,payload,state,"
                        "workspace_project_ref,workspace_block_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (record_id(kind, key), kind, key, scope, json_value(payload), json_value(state or {}),
                         project_ref, block_ref))

    def check_workspace(self):
        config = self.get("WS_CONFIG", CONFIG_KEY)["payload"]
        if config.get("environment") != "SYNTHETIC_WORKSPACE" or config.get("schema_version") != 1:
            raise ReviewError("WORKSPACE_NOT_CONFIGURED", "Explicit workspace configuration is required")
        return config

    def login(self, username, password):
        self.check_workspace()
        user = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_USER' AND payload->>'username'=%s",
                               (username,)).fetchone()
        # Identical public error and scrypt work for unknown, wrong or inactive accounts.
        salt = user["payload"]["salt"] if user else "00" * 16
        actual = password_hash(password, salt)
        expected = user["payload"]["password_hash"] if user else "00" * 32
        valid = secrets.compare_digest(actual, expected)
        if not user or not valid or not user["state"].get("active"):
            raise ReviewError("UNAUTHORIZED", "Account credentials are invalid")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.put("WS_SESSION", token_hash(token), {"user_id": user["workspace_key"], "csrf": csrf,
                 "expires_at": (utcnow() + timedelta(hours=8)).isoformat()}, state={"revoked": False})
        return token, self.session(token)

    def session(self, token):
        if not token or len(token) > 128:
            raise ReviewError("UNAUTHORIZED", "Login is required")
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_SESSION' AND workspace_key=%s",
                               (token_hash(token),)).fetchall()
        if not rows or rows[0]["state"].get("revoked") or datetime.fromisoformat(rows[0]["payload"]["expires_at"]) <= utcnow():
            raise ReviewError("UNAUTHORIZED", "Session has expired")
        row = rows[0]
        user = self.get("WS_USER", row["payload"]["user_id"])
        if not user["state"].get("active"):
            raise ReviewError("UNAUTHORIZED", "Account is inactive")
        return {"user": self.public_user(user), "csrf": row["payload"]["csrf"], "expires_at": row["payload"]["expires_at"],
                "environment": "SYNTHETIC_WORKSPACE"}

    def logout(self, token):
        self.db.execute("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() "
                        "WHERE record_type='WS_SESSION' AND workspace_key=%s", (json_value({"revoked": True}), token_hash(token)))

    @staticmethod
    def public_user(row):
        return {key: row["payload"][key] for key in ("id", "name", "username", "role")}

    def user(self, user_id):
        row = self.get("WS_USER", user_id)
        if not row["state"].get("active"):
            raise ReviewError("UNAUTHORIZED", "Account is inactive")
        return self.public_user(row)

    def access(self, user_id, block_id, *, write=False):
        self.user(user_id)
        block = self.get("WS_BLOCK", block_id)
        project = self.get("WS_PROJECT", block["payload"]["project_id"])
        if user_id not in block["payload"]["owners"] and (write or user_id not in project["payload"]["tops"]):
            raise ReviewError("FORBIDDEN", "Account is not assigned to this block")
        if block["workspace_project_ref"] != project["record_id"]:
            raise ReviewError("STATE_CORRUPT", "Workspace scope mismatch")
        return block, project

    def case_reference_scopes(self, user_id):
        """Shared confirmed knowledge catalog; this grants no source access."""
        self.user(user_id)
        return self.registered_case_scopes()

    def registered_case_scopes(self):
        projects = {row["workspace_key"]: row for row in self.rows("WS_PROJECT")}
        result = []
        for block in self.rows("WS_BLOCK"):
            project = projects.get(block["payload"].get("project_id"))
            if project is None or block["workspace_project_ref"] != project["record_id"]:
                raise ReviewError("STATE_CORRUPT", "Workspace scope mismatch")
            result.append({"project_id": project["workspace_key"], "block_id": block["workspace_key"]})
        return result

    def can_write(self, user_id, block):
        self.access(user_id, block["workspace_key"])
        return user_id in block["payload"]["owners"]

    def version(self, user_id, block_id, version_id):
        block, project = self.access(user_id, block_id)
        version = self.get("WS_VERSION", version_id)
        if (version["workspace_project_ref"] != project["record_id"]
                or version["workspace_block_ref"] != block["record_id"]
                or version["payload"]["block_id"] != block_id):
            raise ReviewError("NOT_FOUND", "Version is outside this block")
        return {**version, "payload": self.version_ecos(version)}

    def version_ecos(self, row):
        """New ECOs are received facts; the original version row stays immutable."""
        value = row['payload']
        if value.get('source_namespace') is None:
            return value
        ecos = self.db.execute("SELECT DISTINCT payload->>'eco' AS eco FROM {oa} WHERE record_type='WS_REPORT' "
                              "AND workspace_version_ref=%s ORDER BY 1",(row['record_id'],)).fetchall()
        return {**value,'ecos':[r['eco'] for r in ecos] if value['layout']=='POST' else [None]}

    def por(self, block_id):
        row = self.get("WS_POR", block_id)
        state = row["state"]
        version = None
        if state.get("version_id"):
            version = self.get("WS_VERSION", state["version_id"])
            if (version["record_id"] != row["workspace_version_ref"] or version["workspace_block_ref"] != row["workspace_block_ref"]
                    or version["workspace_project_ref"] != row["workspace_project_ref"]):
                raise ReviewError("STATE_CORRUPT", "PoR version scope mismatch")
        return {"revision": row["version"], "version": version["payload"] if version else None,
                "assigned_by": state.get("assigned_by"), "assigned_at": state.get("assigned_at")}

    def overview(self, user_id):
        from oa.sanity_workspace_sources import CURRENT_SOURCES_SQL
        user = self.user(user_id)
        people = {row["workspace_key"]: self.public_user(row) for row in self.rows("WS_USER")}
        projects = []
        for project in self.rows("WS_PROJECT"):
            data = project["payload"]
            blocks = []
            for block in self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_BLOCK' AND workspace_project_ref=%s ORDER BY workspace_key",
                                         (project["record_id"],)).fetchall():
                value = block["payload"]
                if user_id not in value["owners"] and user_id not in data["tops"]:
                    continue
                por = self.por(value["id"])
                report_version = por["version"]
                if report_version is None:
                    received = self.db.execute("SELECT v.payload FROM {oa} v WHERE v.record_type='WS_VERSION' "
                        "AND v.workspace_block_ref=%s AND EXISTS (SELECT 1 FROM {oa} r WHERE r.record_type='WS_REPORT' "
                        "AND r.workspace_version_ref=v.record_id) ORDER BY v.payload->>'net_ver' DESC,v.created_at DESC LIMIT 1",
                        (block["record_id"],)).fetchone()
                    report_version = received["payload"] if received else None
                reports = self.db.execute("SELECT payload->>'check_status' AS check_status,count(*) AS count FROM {oa} "
                    "WHERE record_type='WS_REPORT' AND workspace_block_ref=%s AND workspace_version_ref=%s "
                    "AND " + CURRENT_SOURCES_SQL + " GROUP BY payload->>'check_status'", (block["record_id"], record_id("WS_VERSION", report_version["id"]) if report_version else None)).fetchall()
                counts = {row["check_status"]: row["count"] for row in reports}
                blocks.append({**value, "owners": [people[uid] for uid in value["owners"]],
                               "can_assign_por": self.can_write(user_id, block), "por": por, "report_version": report_version,
                               "counts": counts, "total": sum(counts.values())})
            if blocks:
                projects.append({**data, "tops": [people[uid] for uid in data["tops"]], "blocks": blocks})
        return {"user": user, "projects": projects, "data_origin": "SYNTHETIC_PUBLIC_REFERENCE"}

    def versions(self, user_id, block_id):
        block, _ = self.access(user_id, block_id)
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_VERSION' AND workspace_block_ref=%s "
                               "ORDER BY payload->>'net_ver' DESC,payload->>'layout' DESC", (block["record_id"],)).fetchall()
        return {"items": [self.version_ecos(row) for row in rows], "por": self.por(block_id),
                "can_assign_por": self.can_write(user_id, block)}

    def reports(self, user_id, block_id, version_id, *, eco=None, item_id=None, family=None, status=None, search="", offset=0, limit=25, include_history=False):
        version = self.version(user_id, block_id, version_id)
        if eco not in version["payload"]["ecos"]:
            raise ReviewError("INVALID_ECO", "Select an ECO from this version")
        predicates, parameters = ["record_type='WS_REPORT'", "workspace_version_ref=%s", "payload->>'eco' IS NOT DISTINCT FROM %s"], [version["record_id"], eco]
        if not include_history:
            from oa.sanity_workspace_sources import CURRENT_SOURCES_SQL
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
        rows = self.db.execute("SELECT payload - 'raw_report' - 'records' AS item FROM {oa} WHERE " + where +
                               " ORDER BY workspace_key LIMIT %s OFFSET %s", (*parameters, limit, offset)).fetchall()
        from oa.sanity_workspace_review import review_summaries
        from oa.sanity_workspace_clustering import pipeline_summaries
        reviews = review_summaries(self.db, [row["item"]["id"] for row in rows])
        pipelines = pipeline_summaries(self.db, [row["item"]["id"] for row in rows], compact=True, ws=self, user_id=user_id)
        for row in rows:
            if row['item'].get('source_storage') == 'PARTS_V2':
                from oa.sanity_workspace_sources import delivery_status
                row['item'].update(delivery_status(self.db,row['item']))
            row["item"]["human_review"] = reviews.get(row["item"]["id"])
            row["item"]["group_review_summary"] = pipelines.get(row["item"]["id"])
        return {"items": [row["item"] for row in rows], "total": total, "offset": offset, "limit": limit,
                "counts": {row["status"]: row["count"] for row in counts}, "groups": list(groups.values()),
                "version": version["payload"]}

    def report(self, user_id, block_id, version_id, report_id, *, hydrate_source=True):
        version = self.version(user_id, block_id, version_id)
        report = self.db.execute("SELECT workspace_version_ref,workspace_block_ref,"
            "CASE WHEN octet_length(payload::text)<=%s THEN payload END AS payload "
            "FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key=%s",
            (MAX_REPORT_JSON_BYTES, report_id)).fetchone()
        if report is None:
            raise ReviewError("NOT_FOUND", "Workspace resource not found")
        if report["workspace_version_ref"] != version["record_id"] or report["workspace_block_ref"] != version["workspace_block_ref"]:
            raise ReviewError("NOT_FOUND", "Report is outside this version")
        if report["payload"] is None:
            raise ReviewError("REPORT_LIMIT", "Report exceeds the explicit server byte budget; no partial source accepted")
        value = report['payload']
        if value.get('source_storage') == 'PARTS_V2':
            from oa.sanity_workspace_sources import delivery_status, hydrate
            value = {**value,**delivery_status(self.db,value)}
            if hydrate_source:
                value = hydrate(self.db,value)
        return value

    def history(self, user_id, block_id):
        block, _ = self.access(user_id, block_id)
        rows = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_POR_EVENT' AND workspace_block_ref=%s "
                               "ORDER BY created_at DESC,workspace_key LIMIT 50", (block["record_id"],)).fetchall()
        return {"items": [row["payload"] for row in rows], "por": self.por(block_id)}

    def assign_por(self, user_id, block_id, version_id, expected_revision, request_id, reason):
        block, project = self.access(user_id, block_id, write=True)
        version = self.version(user_id, block_id, version_id)
        request_id = str(uuid_value(request_id))
        if type(expected_revision) is not int or expected_revision < 0 or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise ReviewError("INVALID_REQUEST", "Version, expected revision and reason are required")
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-request-" + request_id),))
        current = self.get("WS_POR", block_id, lock=True)
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id,
                   "expected_revision": expected_revision, "reason": reason.strip()}
        previous = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_POR_EVENT' AND workspace_key=%s", (request_id,)).fetchone()
        if previous:
            if previous["payload"]["command"] != command:
                raise ReviewError("REQUEST_CONFLICT", "Request ID has different contents")
            return previous["payload"]["result"]
        if current["version"] != expected_revision:
            raise ReviewError("REVISION_CONFLICT", "PoR changed; reload the current version")
        if current["state"].get("version_id") == version_id:
            raise ReviewError("POR_UNCHANGED", "This version is already the current PoR")
        at, revision, user = utcnow().isoformat(), current["version"] + 1, self.user(user_id)
        next_state = {"version_id": version_id, "version_record_id": str(version["record_id"]),
                      "assigned_by": user, "assigned_at": at}
        self.db.execute("UPDATE {oa} SET state=%s,version=%s,updated_at=clock_timestamp() WHERE record_id=%s",
                        (json_value(next_state), revision, current["record_id"]))
        result = {"status": "POR_ASSIGNED", "revision": revision, "version": version["payload"],
                  "assigned_by": user, "assigned_at": at}
        event = {"id": request_id, "command": command, "previous_version_id": current["state"].get("version_id"),
                 "version_record_id": str(version["record_id"]), "version": version["payload"], "actor": user,
                 "reason": reason.strip(), "at": at, "revision": revision, "result": result}
        self.put("WS_POR_EVENT", request_id, event, project=project["workspace_key"], block=block_id)
        return result


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
