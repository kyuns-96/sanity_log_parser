"""PostgreSQL-only connections and directional queues; no DDL or local fallback."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import uuid

from sanity_common import ReviewError, canonical_json
from sanity_config import load_environment, access_mode

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:
    psycopg = None

TABLES = {"vwp": "sanity_vwp_data", "oa": "sanity_oa_data",
          "vwp_to_oa": "sanity_vwp_to_oa", "oa_to_vwp": "sanity_oa_to_vwp"}
MAX_BYTES = 64 * 1024 * 1024


def json_value(value):
    canonical_json(value)
    return Jsonb(value, dumps=lambda obj: canonical_json(obj).decode("utf-8"))


def stable_uuid(*parts):
    return uuid.uuid5(uuid.NAMESPACE_URL, "sanity-reviewer/" + canonical_json(parts).decode())


def uuid_value(value, label="request_id"):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReviewError("INVALID_ID", f"{label} must be a UUID") from exc


def lock_key(value):
    return int.from_bytes(hashlib.sha256(str(value).encode()).digest()[:8], "big", signed=True)


class Database:
    def __init__(self, side):
        if side not in ("vwp", "oa"):
            raise ReviewError("DATABASE_CONFIG", "Unknown application side")
        settings = load_environment()
        if access_mode(settings) == "dbaas_http":
            # A stateless /query endpoint is not a PostgreSQL connection/session.
            # Do not silently weaken locks, atomic commits or role checks, or fall back to a DSN.
            raise ReviewError("DBAAS_SQL_CONTRACT_REQUIRED",
                "DBaaS HTTP access is configured separately; bridge storage needs the query and transaction contract")
        if psycopg is None:
            raise ReviewError("DATABASE_DRIVER_MISSING", "Install requirements.txt in the application environment")
        dsn = settings.get(f"SANITY_{side.upper()}_DSN")
        if not dsn:
            raise ReviewError("DATABASE_NOT_CONFIGURED", f"Set SANITY_{side.upper()}_DSN; there is no local fallback")
        schema = settings.get("SANITY_DB_SCHEMA", "public")
        if not schema or len(schema.encode()) > 63 or "\x00" in schema:
            raise ReviewError("DATABASE_CONFIG", "Invalid database schema")
        self.side, self.schema = side, schema
        self.tables = {key: sql.Identifier(schema, name) for key, name in TABLES.items()}
        self.conn = None
        try:
            self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=10,
                                        application_name=f"sanity-{side}-bridge")
            self.conn.execute("SET statement_timeout = '60s'")
            self.conn.execute("SET lock_timeout = '5s'")
            self.conn.execute("SET idle_in_transaction_session_timeout = '60s'")
            self._check_contract()
        except ReviewError:
            self.close()
            raise
        except psycopg.Error as exc:
            self.close()
            raise ReviewError("DATABASE_UNAVAILABLE", "Cannot open a compatible PostgreSQL connection") from exc

    def _check_contract(self):
        role = self.conn.execute("SELECT oid, rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
        if role["rolsuper"] or role["rolcreaterole"] or role["rolbypassrls"]:
            raise ReviewError("DATABASE_PRIVILEGES", "Use a restricted application login, not an administrator")
        elevated = self.conn.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE "
            "(rolsuper OR rolcreaterole OR rolbypassrls) AND pg_has_role(current_user,oid,'MEMBER')) AS allowed").fetchone()
        if elevated["allowed"]:
            raise ReviewError("DATABASE_PRIVILEGES", "Application login must not inherit or switch to an elevated role")
        if self.conn.execute("SELECT has_schema_privilege(current_user,%s,'CREATE') AS allowed", (self.schema,)).fetchone()["allowed"]:
            raise ReviewError("DATABASE_PRIVILEGES", "Application login must not create objects in the application schema")
        memberships = self.conn.execute("SELECT pg_has_role(current_user,%s,'MEMBER') AS own, "
                                        "pg_has_role(current_user,%s,'MEMBER') AS other",
                                        (f"sanity_{self.side}_app", f"sanity_{'oa' if self.side == 'vwp' else 'vwp'}_app")).fetchone()
        if not memberships["own"] or memberships["other"]:
            raise ReviewError("DATABASE_PRIVILEGES", "Application login must inherit only its own bridge role")
        rows = self.conn.execute("SELECT c.oid,c.relname,c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                 "WHERE n.nspname=%s AND c.relname=ANY(%s) AND c.relkind='r'",
                                 (self.schema, list(TABLES.values()))).fetchall()
        if len(rows) != 4:
            raise ReviewError("DATABASE_SCHEMA", "Install all four tables before running the application")
        for row in rows:
            if self.conn.execute("SELECT pg_has_role(current_user,%s,'MEMBER') AS owns", (row["relowner"],)).fetchone()["owns"]:
                raise ReviewError("DATABASE_PRIVILEGES", "Application login must not own the tables")
            self._check_table_acl(row["oid"], row["relname"])
        other = "oa" if self.side == "vwp" else "vwp"
        other_oid = next(r["oid"] for r in rows if r["relname"] == TABLES[other])
        if self.conn.execute("SELECT has_table_privilege(current_user,%s,'SELECT') AS exposed", (other_oid,)).fetchone()["exposed"]:
            raise ReviewError("DATABASE_PRIVILEGES", "Private tables must be isolated between environments")
        self._check_indexes()

    def _check_indexes(self):
        """Accept existing object names only when the required index definitions match."""
        definitions = [
            ("vwp_to_oa", ("bundle_id",), "message_type", "ANALYSIS_BUNDLE"),
            ("vwp_to_oa", ("correlation_id",), "message_type", "EVIDENCE_RESPONSE"),
            ("oa", ("bundle_id",), "record_type", "RECEIVED_BUNDLE"),
            ("oa", ("bundle_id", "review_unit_id"), "record_type", "REVIEW_UNIT"),
            ("oa", ("bundle_id", "review_revision"), "record_type", "REVIEW_EVENT"),
            ("oa", ("bundle_id", "review_revision"), "record_type", "REPORT"),
        ]
        # PostgreSQL's deparsed expressions for the VARCHAR predicates in db/002_indexes.sql.
        # Exact comparison deliberately rejects unrecognized predicates instead of guessing equivalence.
        required = {(TABLES[table], columns, f"(({field})::text = '{value}'::text)")
                    for table, columns, field, value in definitions}
        required.add((TABLES["oa"], ("request_id",), "(request_id IS NOT NULL)"))
        indexes = self.conn.execute(
            "SELECT t.relname AS table_name, pg_get_expr(i.indpred,i.indrelid) AS predicate, "
            "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,position) "
            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
            "WHERE k.position<=i.indnkeyatts ORDER BY k.position) AS key_columns "
            "FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname=ANY(%s) AND i.indisvalid AND i.indisready "
            "AND i.indislive AND i.indisunique AND i.indimmediate AND i.indexprs IS NULL",
            (self.schema, list(TABLES.values()))).fetchall()
        actual = {(row["table_name"], tuple(row["key_columns"]), row["predicate"]) for row in indexes}
        if not required <= actual:
            raise ReviewError("DATABASE_SCHEMA", "Required UNIQUE index definitions must match db/002_indexes.sql")

    def _check_table_acl(self, oid, name):
        """Check effective column rights too: table-level checks miss direct login grants."""
        columns = self.conn.execute("SELECT attname,has_column_privilege(current_user,attrelid,attnum,'SELECT') AS r,"
            "has_column_privilege(current_user,attrelid,attnum,'INSERT') AS i,"
            "has_column_privilege(current_user,attrelid,attnum,'UPDATE') AS u "
            "FROM pg_attribute WHERE attrelid=%s AND attnum>0 AND NOT attisdropped", (oid,)).fetchall()
        all_columns = {row["attname"] for row in columns}
        controls = {"status", "error_message", "attempt_count", "next_attempt_at", "lease_token",
                    "lease_owner", "lease_expires_at", "completed_at", "updated_at"}
        if name == TABLES[self.side]:
            reads = inserts = all_columns
            updates = {"state", "status", "version", "updated_at"}
        elif name in (TABLES["vwp"], TABLES["oa"]):
            reads = inserts = updates = set()
        else:
            reads = all_columns
            outgoing = name == TABLES["vwp_to_oa" if self.side == "vwp" else "oa_to_vwp"]
            inserts = ({"message_id", "message_type", "payload", "project", "run_id", "bundle_id"}
                       | ({"correlation_id", "payload_gzip", "payload_sha256", "uncompressed_bytes"}
                          if self.side == "vwp" else {"expires_at"})) if outgoing else set()
            updates = set() if outgoing else controls
        for right, expected in (("r", reads), ("i", inserts), ("u", updates)):
            if {row["attname"] for row in columns if row[right]} != expected:
                raise ReviewError("DATABASE_PRIVILEGES", "Effective table/column rights differ from the bridge contract")
        forbidden = self.conn.execute("SELECT has_table_privilege(current_user,%s,'DELETE,TRUNCATE,REFERENCES,TRIGGER') AS exposed",
                                      (oid,)).fetchone()["exposed"]
        if forbidden:
            raise ReviewError("DATABASE_PRIVILEGES", "Destructive or DDL-related table rights are not allowed")

    def execute(self, query, params=None):
        return self.conn.execute(sql.SQL(query).format(**self.tables), params)

    def executemany(self, query, rows):
        with self.conn.cursor() as cur:
            cur.executemany(sql.SQL(query).format(**self.tables), rows)

    @contextmanager
    def transaction(self, readonly=False):
        with self.conn.transaction():
            if readonly:
                self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield self

    def close(self):
        if self.conn is not None:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        if exc is not None and isinstance(exc, psycopg.Error):
            # Do not print driver errors: they can include row contents, hosts or credentials.
            raise ReviewError("DATABASE_ERROR", "PostgreSQL operation failed; no local fallback was used") from exc


class Queue:
    def __init__(self, db, direction):
        if direction not in ("vwp_to_oa", "oa_to_vwp"):
            raise ReviewError("DATABASE_CONFIG", "Invalid queue direction")
        self.db, self.direction = db, direction
        self.table = "{" + direction + "}"

    def get(self, message_id):
        return self.db.execute(f"SELECT * FROM {self.table} WHERE message_id=%s", (message_id,)).fetchone()

    def enqueue(self, message):
        allowed = {"message_id", "message_type", "payload", "project", "run_id", "bundle_id"}
        allowed |= ({"correlation_id", "payload_gzip", "payload_sha256", "uncompressed_bytes"}
                    if self.direction == "vwp_to_oa" else {"expires_at"})
        if not set(message) <= allowed:
            raise ReviewError("INVALID_MESSAGE", "Sender cannot set delivery state")
        keys = list(message)
        values = [json_value(message[k]) if k == "payload" else message[k] for k in keys]
        # Column names come from the fixed allowlist, never caller SQL.
        self.db.execute(f"INSERT INTO {self.table} ({','.join(keys)}) VALUES ({','.join(['%s']*len(keys))}) "
                        "ON CONFLICT DO NOTHING", values)
        existing = self.get(message["message_id"])
        if existing is None or any(existing[key] != value for key, value in message.items()):
            raise ReviewError("MESSAGE_CONFLICT", "Existing message ID or logical key has different content")
        return existing

    def claim(self, message_types, message_id=None, lease_seconds=300):
        if not 1 <= lease_seconds <= 3600:
            raise ReviewError("INVALID_LEASE", "Lease must be between 1 and 3600 seconds")
        token = uuid.uuid4()
        with self.db.transaction():
            row = self.db.execute(f"SELECT message_id FROM {self.table} WHERE message_type=ANY(%s) "
                "AND (%s::varchar IS NULL OR message_id=%s) AND "
                "((status IN ('READY','RETRY') AND next_attempt_at<=clock_timestamp()) "
                "OR (status='PROCESSING' AND lease_expires_at<=clock_timestamp())) "
                "ORDER BY next_attempt_at,message_id FOR UPDATE SKIP LOCKED LIMIT 1",
                (list(message_types), message_id, message_id)).fetchone()
            if row is None:
                return None
            self.db.execute(f"UPDATE {self.table} SET status='PROCESSING',attempt_count=attempt_count+1,"
                "lease_token=%s,lease_owner=%s,lease_expires_at=clock_timestamp()+(%s * interval '1 second'),"
                "completed_at=NULL,error_message=NULL,updated_at=clock_timestamp() WHERE message_id=%s",
                (token, self.db.side + "-" + token.hex[:16], lease_seconds, row["message_id"]))
        return self.get(row["message_id"])

    def finish(self, message, status="RECEIVED", error_code=None):
        if status not in ("RECEIVED", "REJECTED", "RETRY"):
            raise ReviewError("INVALID_STATE", "Invalid delivery completion")
        row = self.db.execute(f"UPDATE {self.table} SET status=%s,error_message=%s,"
            "completed_at=CASE WHEN %s='RETRY' THEN NULL ELSE clock_timestamp() END,"
            "next_attempt_at=clock_timestamp()+interval '10 seconds',"
            "lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp() "
            "WHERE message_id=%s AND status='PROCESSING' AND lease_token=%s AND lease_expires_at>clock_timestamp() "
            "RETURNING message_id", (status, error_code, status, message["message_id"], message["lease_token"])).fetchone()
        if row is None:
            raise ReviewError("LEASE_LOST", "Message lease expired or was reclaimed; completion was rolled back")
