"""Core OA workspace primitives and base store without review/clustering summary dependencies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import secrets

from common.sanity_common import ReviewError, digest, require_id
from common.sanity_db import json_value, lock_key, stable_uuid, uuid_value
from oa.sanity_workspace_sources import CURRENT_SOURCES_SQL, delivery_status, hydrate

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


def catalog_name_id(kind, name, project_id=None):
    """Exact normalized names, unambiguous UTF-8 canonical JSON, native-safe IDs."""
    if kind == "WS_PROJECT":
        return "project-" + digest(["atlas-project-v1", name])
    if kind == "WS_BLOCK" and project_id is not None:
        return "block-" + digest(["atlas-block-v1", project_id, name])
    raise ReviewError("INVALID_REQUEST", "Invalid catalog identity scope")


class WorkspaceCoreStore:
    def __init__(self, db):
        self.db = db

    def row(self, kind, key):
        return self.db.execute("SELECT * FROM {oa} WHERE record_type=%s AND workspace_key=%s", (kind, key)).fetchone()

    def get(self, kind, key, lock=False):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type=%s AND workspace_key=%s" +
                              (" FOR UPDATE" if lock else ""), (kind, key)).fetchone()
        if row is None:
            raise ReviewError("NOT_FOUND", "Workspace resource not found")
        return row

    def rows(self, kind):
        return self.db.execute("SELECT * FROM {oa} WHERE record_type=%s ORDER BY workspace_key", (kind,)).fetchall()

    def processes(self):
        rows = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' "
                               "AND workspace_key LIKE 'atlas.process.%' ORDER BY payload->>'name'").fetchall()
        return [row["payload"] for row in rows]

    def require_process(self, name):
        if not self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                               ("atlas.process." + digest(name),)).fetchone():
            raise ReviewError("PROCESS_NOT_FOUND", "Select a registered process")

    def initial_process(self, project_id):
        meta_row = self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE 'atlas.pmeta.%' "
            "AND payload->>'project_id'=%s ORDER BY COALESCE((payload->>'revision')::bigint, 0) DESC, created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if meta_row and "process" in (meta_row["payload"] or {}):
            return meta_row["payload"]["process"]
        key = "atlas.project-process." + str(record_id("WS_PROJECT", project_id))
        row = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                              (key,)).fetchone()
        return row["payload"]["process"] if row else None

    def select_initial_process(self, project_id, process, *, existing):
        # The caller holds the named catalog lock in the project creation transaction.
        if process is None:
            return
        self.require_process(process)
        if existing:
            if self.initial_process(project_id) != process:
                raise ReviewError("REQUEST_CONFLICT", "The existing project was registered with a different process")
            return
        self.put("WS_CONFIG", "atlas.project-process." + str(record_id("WS_PROJECT", project_id)),
                 {"project_id": project_id, "process": process})

    def register_process(self, name, unit_area):
        key = "atlas.process." + digest(name)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(key),))
        existing = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                                   (key,)).fetchone()
        if existing:
            if Decimal(existing["payload"]["unit_area"]) != unit_area:
                raise ReviewError("PROCESS_AREA_CONFLICT", "A different NAND2_D1 area is registered for this process")
            return existing["payload"]
        payload = {"name": name, "unit_area": format(unit_area, "f")}
        self.put("WS_CONFIG", key, payload)
        return payload

    def named_catalog(self, kind, name, project_id=None):
        """Resolve legacy business names before deriving an ID; never mutate rows."""
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))
        rows = self.db.execute(
            "SELECT * FROM {oa} WHERE record_type=%s AND payload->>'name'=%s" +
            (" AND payload->>'project_id'=%s" if project_id is not None else ""),
            (kind, name, project_id) if project_id is not None else (kind, name)).fetchall()
        if len(rows) > 1:
            raise ReviewError("REQUEST_CONFLICT", "Catalog name is ambiguous")
        if rows:
            row = rows[0]
            if (row["payload"].get("id") != row["workspace_key"]
                    or (kind == "WS_PROJECT" and row["payload"].get("project_id") != row["workspace_key"])
                    or (project_id is not None and row["workspace_project_ref"] != record_id("WS_PROJECT", project_id))):
                raise ReviewError("REQUEST_CONFLICT", "Catalog ownership does not match")
            return require_id(row["workspace_key"]), row
        key = catalog_name_id(kind, name, project_id)
        if self.db.execute("SELECT 1 FROM {oa} WHERE record_type=%s AND workspace_key=%s", (kind, key)).fetchone():
            raise ReviewError("REQUEST_CONFLICT", "Catalog identity already belongs to another name")
        return key, None

    def put(self, kind, key, payload, *, project=None, block=None, state=None, source_packet_ref=None):
        project_ref = record_id("WS_PROJECT", project) if project else None
        block_ref = record_id("WS_BLOCK", block) if block else None
        scope = payload.get("project_id") if kind == "WS_PROJECT" else project
        extra = ',source_packet_ref' if source_packet_ref is not None else ''
        values = (record_id(kind, key), kind, key, scope, json_value(payload), json_value(state or {}), project_ref, block_ref)
        if source_packet_ref is not None:
            values += (source_packet_ref,)
        self.db.execute("INSERT INTO {oa} (record_id,record_type,workspace_key,project,payload,state,"
                        "workspace_project_ref,workspace_block_ref" + extra + ") VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s" + (',%s' if extra else '') + ")", values)

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
        received = [r['eco'] for r in ecos]
        if value.get('eco_num') is not None:
            return {**value, 'ecos': received}
        return {**value, 'ecos': received if value['layout'] == 'POST' else [None]}

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

    def _block_report_summary(self, block_record_id, por):
        report_version = por["version"]
        if report_version is None:
            received = self.db.execute(
                "SELECT v.* FROM {oa} v WHERE v.record_type='WS_VERSION' "
                "AND v.workspace_block_ref=%s AND EXISTS (SELECT 1 FROM {oa} r WHERE r.record_type='WS_REPORT' "
                "AND r.workspace_version_ref=v.record_id) ORDER BY v.payload->>'net_ver' DESC,v.created_at DESC LIMIT 1",
                (block_record_id,),
            ).fetchone()
            report_version = self.version_ecos(received) if received else None
        reports = self.db.execute(
            "SELECT payload->>'check_status' AS check_status,count(*) AS count FROM {oa} "
            "WHERE record_type='WS_REPORT' AND workspace_block_ref=%s AND workspace_version_ref=%s "
            "AND " + CURRENT_SOURCES_SQL + " GROUP BY payload->>'check_status'",
            (block_record_id, record_id("WS_VERSION", report_version["id"]) if report_version else None),
        ).fetchall()
        counts = {row["check_status"]: row["count"] for row in reports}
        return report_version, counts, sum(counts.values())

    def overview(self, user_id):
        from oa.project_lifecycle import apply_project_overlay
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
                report_version, counts, total = self._block_report_summary(block["record_id"], por)
                blocks.append({**value, "owners": [people[uid] for uid in value["owners"]],
                               "can_assign_por": self.can_write(user_id, block), "por": por, "report_version": report_version,
                               "counts": counts, "total": total})
            if blocks:
                projects.append({**data, "tops": [people[uid] for uid in data["tops"]], "blocks": blocks})
        projects = apply_project_overlay(self.db, projects, include_hidden=False)
        return {"user": user, "projects": projects, "data_origin": "SYNTHETIC_PUBLIC_REFERENCE"}

    def versions(self, user_id, block_id):
        block, _ = self.access(user_id, block_id)
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_VERSION' AND workspace_block_ref=%s "
                               "ORDER BY payload->>'net_ver' DESC,payload->>'layout' DESC", (block["record_id"],)).fetchall()
        return {"items": [self.version_ecos(row) for row in rows], "por": self.por(block_id),
                "can_assign_por": self.can_write(user_id, block)}

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
            value = {**value,**delivery_status(self.db,value)}
            if hydrate_source:
                value = hydrate(self.db,value)
        return value

    def history(self, user_id, block_id):
        block, _ = self.access(user_id, block_id)
        rows = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_POR_EVENT' AND workspace_block_ref=%s "
                               "ORDER BY created_at DESC,workspace_key LIMIT 50", (block["record_id"],)).fetchall()
        return {"items": [row["payload"] for row in rows], "por": self.por(block_id)}

    def assign_por_record(
        self,
        project,
        block,
        version,
        *,
        user_id: str,
        actor_info: dict | None = None,
        expected_revision: int,
        request_id,
        reason: str,
        production_lock: bool = False,
    ):
        request_id = str(uuid_value(request_id))
        if type(expected_revision) is not int or expected_revision < 0 or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise ReviewError("INVALID_REQUEST", "Version, expected revision and reason are required")
        block_id = block["workspace_key"]
        version_id = version["workspace_key"] if "workspace_key" in version else version["payload"]["id"]
        if production_lock:
            self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("production-por-" + block_id),))
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-request-" + request_id),))
        command = {
            "user_id": user_id,
            "block_id": block_id,
            "version_id": version_id,
            "expected_revision": expected_revision,
            "reason": reason.strip(),
        }
        previous = self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_POR_EVENT' AND workspace_key=%s", (request_id,)
        ).fetchone()
        if previous:
            if previous["payload"]["command"] != command:
                raise ReviewError("REQUEST_CONFLICT", "Request ID has different contents")
            return previous["payload"]["result"]
        if production_lock:
            exists = self.db.execute(
                "SELECT 1 FROM {oa} WHERE record_type='WS_POR' AND workspace_key=%s", (block_id,)
            ).fetchone()
            if not exists:
                if expected_revision != 0:
                    raise ReviewError("REVISION_CONFLICT", "PoR changed; reload the current version")
                self.put("WS_POR", block_id, {"block_id": block_id},
                         project=project["workspace_key"], block=block["workspace_key"])
        current = self.get("WS_POR", block_id, lock=True)
        if current["version"] != expected_revision:
            raise ReviewError("REVISION_CONFLICT", "PoR changed; reload the current version")
        if current["state"].get("version_id") == version_id:
            raise ReviewError("POR_UNCHANGED", "This version is already the current PoR")
        at = utcnow().isoformat()
        revision = current["version"] + 1
        user = actor_info if actor_info is not None else self.user(user_id)
        version_payload = version["payload"] if "ecos" in version["payload"] and version["payload"]["ecos"] else self.version_ecos(version)
        next_state = {
            "version_id": version_id,
            "version_record_id": str(version["record_id"]),
            "assigned_by": user,
            "assigned_at": at,
        }
        self.db.execute(
            "UPDATE {oa} SET state=%s,version=%s,updated_at=clock_timestamp() WHERE record_id=%s",
            (json_value(next_state), revision, current["record_id"]),
        )
        result = {
            "status": "POR_ASSIGNED",
            "revision": revision,
            "version": version_payload,
            "assigned_by": user,
            "assigned_at": at,
        }
        event = {
            "id": request_id,
            "command": command,
            "previous_version_id": current["state"].get("version_id"),
            "version_record_id": str(version["record_id"]),
            "version": version_payload,
            "actor": user,
            "reason": reason.strip(),
            "at": at,
            "revision": revision,
            "result": result,
        }
        self.put("WS_POR_EVENT", request_id, event, project=project["workspace_key"], block=block_id)
        return result

    def assign_por(self, user_id, block_id, version_id, expected_revision, request_id, reason):
        block, project = self.access(user_id, block_id, write=True)
        version = self.version(user_id, block_id, version_id)
        return self.assign_por_record(
            project,
            block,
            version,
            user_id=user_id,
            expected_revision=expected_revision,
            request_id=request_id,
            reason=reason,
            production_lock=False,
        )
