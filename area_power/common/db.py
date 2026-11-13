from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb, set_json_dumps, set_json_loads

from .config import Settings
from .errors import ConfigurationError, DomainError
from .jsonutil import digest, dumps, loads


RECORD_FIELDS = frozenset("id record_type entity_id revision payload_schema_version row_version parent_id previous_id project_id version_id block_id snapshot_id condition_key selection_scope upload_id baseline_upload_id dedupe_key state payload created_by updated_by job_kind consumer_server claim_owner claimed_at started_at finished_at error_detail read_at read_by acknowledged_at acknowledged_by resolved_at resolved_by resolution_note".split())
UPLOAD_FIELDS = frozenset("id project_id version_id block_id snapshot_id execution_name original_filename uploaded_by process_label voltage_v temp_c pex condition_key conditions_complete analysis_conditions structure_digest input_contract root_summary encoding csv_gzip original_sha256 original_byte_length compressed_byte_length row_count state validation_report error_detail validated_at".split())
UPLOAD_METADATA = "id project_id version_id block_id snapshot_id execution_name original_filename uploaded_by process_label voltage_v temp_c pex condition_key conditions_complete analysis_conditions structure_digest input_contract root_summary encoding original_sha256 original_byte_length compressed_byte_length row_count state validation_report error_detail validated_at created_at".split()
JSON_FIELDS = frozenset({"payload", "error_detail", "analysis_conditions", "input_contract", "root_summary", "validation_report"})
SCOPE_FIELDS = frozenset({"project_id", "version_id", "block_id", "snapshot_id", "condition_key"})


def _value(key, value):
    return Jsonb(value, dumps=dumps) if key in JSON_FIELDS and value is not None else value


def _where(filters):
    active = {key: value for key, value in filters.items() if value is not None}
    clauses = [sql.SQL("{} = %s").format(sql.Identifier(key)) for key in active]
    return sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("TRUE"), list(active.values())


class Database:
    def __init__(self, settings=None):
        self.settings = settings or Settings.from_env()

    def table(self, name):
        if name not in {"di_area_power_csv", "di_area_power_tracking_record"}:
            raise ValueError("Unknown application table")
        return sql.Identifier(self.settings.db_schema, name)

    @contextmanager
    def connect(self):
        try:
            conn = psycopg.connect(self.settings.database_url, row_factory=dict_row, connect_timeout=10)
        except psycopg.Error:
            raise ConfigurationError("Database connection is unavailable") from None
        set_json_dumps(dumps, conn)
        set_json_loads(loads, conn)
        try:
            with conn:
                yield conn
        except psycopg.errors.UniqueViolation:
            raise DomainError("An equivalent record already exists", "conflict", 409) from None
        except (psycopg.errors.ForeignKeyViolation, psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation, psycopg.errors.InvalidTextRepresentation):
            raise DomainError("Data does not satisfy the installed database contract", "database_constraint", 422) from None
        except psycopg.Error:
            raise ConfigurationError("Database operation failed; check the internal schema and permissions") from None

    def record(self, conn, id, *, lock=False, kind=None):
        query = sql.SQL("SELECT * FROM {} WHERE id = %s").format(self.table("di_area_power_tracking_record"))
        params = [id]
        if kind is not None:
            query += sql.SQL(" AND record_type = %s")
            params.append(kind)
        if lock:
            query += sql.SQL(" FOR UPDATE")
        row = conn.execute(query, params).fetchone()
        if row is None:
            raise DomainError("Record not found", "not_found", 404)
        return row

    def latest_record(self, conn, record_type, entity_id, *, lock=False):
        query = sql.SQL("SELECT * FROM {} WHERE record_type = %s AND entity_id = %s ORDER BY revision DESC LIMIT 1").format(self.table("di_area_power_tracking_record"))
        if lock:
            query += sql.SQL(" FOR UPDATE")
        row = conn.execute(query, (record_type, entity_id)).fetchone()
        if row is None:
            raise DomainError("Record not found", "not_found", 404)
        return row

    def records(self, conn, record_type=None, *, project_id=None, version_id=None, block_id=None, snapshot_id=None, parent_id=None, state=None, limit=100, offset=0, latest=False):
        limit, offset = self._page(limit, offset)
        condition, params = _where(dict(record_type=record_type, project_id=project_id, version_id=version_id, block_id=block_id, snapshot_id=snapshot_id, parent_id=parent_id, state=state))
        if latest:
            query = sql.SQL("SELECT * FROM (SELECT DISTINCT ON (record_type, entity_id) * FROM {} ORDER BY record_type, entity_id, revision DESC) AS current_records WHERE {} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s").format(self.table("di_area_power_tracking_record"), condition)
        else:
            query = sql.SQL("SELECT * FROM {} WHERE {} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s").format(self.table("di_area_power_tracking_record"), condition)
        return conn.execute(query, params + [limit, offset]).fetchall()

    @staticmethod
    def _page(limit, offset):
        if not isinstance(limit, int) or not isinstance(offset, int) or limit <= 0 or limit > 10000 or offset < 0:
            raise DomainError("Pagination requires limit 1..10000 and nonnegative offset")
        return limit, offset

    def insert_record(self, conn, record_type, payload, actor, **fields):
        if set(fields) - RECORD_FIELDS:
            raise ValueError("Unknown tracking column")
        id = fields.pop("id", None) or uuid4()
        values = dict(id=id, record_type=record_type, entity_id=id, state="active", payload=payload, created_by=actor, updated_by=actor)
        values.update(fields)
        return self._insert(conn, "di_area_power_tracking_record", values)

    def update_record(self, conn, id, actor, *, expected_row_version=None, **fields):
        if not fields or set(fields) - RECORD_FIELDS or set(fields) & {"id", "record_type", "entity_id", "revision", "row_version", "created_by", "updated_by"}:
            raise ValueError("Invalid tracking update")
        assignments = [sql.SQL("{} = %s").format(sql.Identifier(key)) for key in fields]
        assignments.extend([sql.SQL("row_version = row_version + 1"), sql.SQL("updated_at = CURRENT_TIMESTAMP"), sql.SQL("updated_by = %s")])
        params = [_value(key, value) for key, value in fields.items()] + [actor, id]
        query = sql.SQL("UPDATE {} SET {} WHERE id = %s").format(self.table("di_area_power_tracking_record"), sql.SQL(", ").join(assignments))
        if expected_row_version is not None:
            query += sql.SQL(" AND row_version = %s")
            params.append(expected_row_version)
        row = conn.execute(query + sql.SQL(" RETURNING *"), params).fetchone()
        if row is None:
            raise DomainError("Record changed or no longer exists; refresh before saving", "conflict", 409)
        return row

    def upload(self, conn, id, *, include_blob=False, lock=False):
        columns = sql.SQL("*") if include_blob else sql.SQL(", ").join(map(sql.Identifier, UPLOAD_METADATA))
        query = sql.SQL("SELECT {} FROM {} WHERE id = %s").format(columns, self.table("di_area_power_csv"))
        if lock:
            query += sql.SQL(" FOR UPDATE")
        row = conn.execute(query, (id,)).fetchone()
        if row is None:
            raise DomainError("Upload not found", "not_found", 404)
        return row

    def uploads(self, conn, *, project_id=None, version_id=None, block_id=None, snapshot_id=None, state=None, limit=100, offset=0):
        limit, offset = self._page(limit, offset)
        condition, params = _where(dict(project_id=project_id, version_id=version_id, block_id=block_id, snapshot_id=snapshot_id, state=state))
        query = sql.SQL("SELECT {} FROM {} WHERE {} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s").format(sql.SQL(", ").join(map(sql.Identifier, UPLOAD_METADATA)), self.table("di_area_power_csv"), condition)
        return conn.execute(query, params + [limit, offset]).fetchall()

    def insert_upload(self, conn, **fields):
        if set(fields) - UPLOAD_FIELDS:
            raise ValueError("Unknown upload column")
        return self._insert(conn, "di_area_power_csv", fields, upload=True)

    def _insert(self, conn, table, values, upload=False):
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
            self.table(table), sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
            sql.SQL(", ").join(map(sql.Identifier, UPLOAD_METADATA)) if upload else sql.SQL("*"),
        )
        return conn.execute(query, [_value(key, value) for key, value in values.items()]).fetchone()

    def enqueue(self, conn, job_kind, *, actor, parent_id=None, upload_id=None, payload=None, dedupe_key=None, previous_id=None, **scope_fields):
        if job_kind not in {"ingest", "compare", "extract", "judge", "mail"} or set(scope_fields) - SCOPE_FIELDS:
            raise ValueError("Invalid job contract")
        if (job_kind == "ingest" and upload_id is None) or (job_kind != "ingest" and parent_id is None):
            raise DomainError("The job requires a persistent subject")
        consumer = "oa_server" if job_kind == "mail" else "vwp_server"
        payload = payload or {}
        identity = dict(job_kind=job_kind, parent_id=parent_id, upload_id=upload_id, payload=payload, previous_id=previous_id, **scope_fields)
        key = dedupe_key or digest({**identity, **({"attempt": str(uuid4())} if previous_id else {})})
        def existing():
            row = conn.execute(sql.SQL("SELECT * FROM {} WHERE record_type = 'job' AND dedupe_key = %s FOR UPDATE").format(self.table("di_area_power_tracking_record")), (key,)).fetchone()
            if row is not None:
                for field, expected in identity.items():
                    if digest(row.get(field)) != digest(expected):
                        raise DomainError("Request key conflicts with different job inputs", "conflict", 409)
            return row
        row = existing()
        if row is not None:
            return row
        try:
            with conn.transaction():
                return self.insert_record(conn, "job", payload, actor, state="pending", job_kind=job_kind,
                                          consumer_server=consumer, parent_id=parent_id, upload_id=upload_id,
                                          previous_id=previous_id, dedupe_key=key, **scope_fields)
        except psycopg.errors.UniqueViolation:
            row = existing()
            if row is None:
                raise
            return row

    def claim_job(self, consumer_server, owner):
        if consumer_server not in {"vwp_server", "oa_server"} or not owner.strip():
            raise ValueError("Invalid worker identity")
        with self.connect() as conn:
            row = conn.execute(sql.SQL("SELECT * FROM {} WHERE record_type = 'job' AND consumer_server = %s AND state = 'pending' ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1").format(self.table("di_area_power_tracking_record")), (consumer_server,)).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            return self.update_record(conn, row["id"], consumer_server, expected_row_version=row["row_version"],
                                      state="running", claim_owner=owner, claimed_at=now, started_at=now)

    def finish_job(self, job_id, owner, *, error=None):
        with self.connect() as conn:
            row = self.record(conn, job_id, lock=True, kind="job")
            if row["state"] != "running" or row["claim_owner"] != owner:
                raise DomainError("Job claim is no longer owned by this worker", "conflict", 409)
            return self.update_record(conn, job_id, row["consumer_server"], expected_row_version=row["row_version"],
                                      state="failed" if error else "completed", error_detail=error,
                                      finished_at=datetime.now(timezone.utc))

    def check_schema(self):
        """Read-only readiness check. Never creates tables or leaks private identifiers."""
        with self.connect() as conn:
            if not 180000 <= conn.info.server_version < 190000:
                raise ConfigurationError("PostgreSQL 18 is required")
            rows = conn.execute("SELECT t.relname, a.attname FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_attribute a ON a.attrelid=t.oid WHERE n.nspname=%s AND t.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped", (self.settings.db_schema, ["di_area_power_csv", "di_area_power_tracking_record"])).fetchall()
            installed = {}
            for row in rows:
                installed.setdefault(row["relname"], set()).add(row["attname"])
            if not (RECORD_FIELDS | {"created_at", "updated_at"}) <= installed.get("di_area_power_tracking_record", set()) or not (UPLOAD_FIELDS | {"created_at"}) <= installed.get("di_area_power_csv", set()):
                raise ConfigurationError("Required application tables or columns are not installed")
            constraints = conn.execute("SELECT t.relname AS table_name,c.contype,c.convalidated,f.relname AS referenced_table, ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY x(num,ord) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=x.num ORDER BY x.ord) AS columns, ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY x(num,ord) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=x.num ORDER BY x.ord) AS referenced_columns, nf.nspname AS referenced_schema FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_class f ON f.oid=c.confrelid LEFT JOIN pg_namespace nf ON nf.oid=f.relnamespace WHERE n.nspname=%s AND t.relname=ANY(%s)", (self.settings.db_schema, ["di_area_power_csv", "di_area_power_tracking_record"])).fetchall()
            signatures = {(row["table_name"], row["contype"], tuple(row["columns"]), row["referenced_table"], tuple(row["referenced_columns"])) for row in constraints if row["convalidated"] and (row["contype"] != "f" or row["referenced_schema"] == self.settings.db_schema)}
            scope = ("id", "project_id", "version_id", "block_id", "snapshot_id", "condition_key")
            expected = {("di_area_power_csv", "p", ("id",), None, ()), ("di_area_power_tracking_record", "p", ("id",), None, ()), ("di_area_power_csv", "u", scope, None, ()), ("di_area_power_tracking_record", "u", ("record_type", "entity_id", "revision"), None, ()), ("di_area_power_tracking_record", "u", ("record_type", "dedupe_key"), None, ()), ("di_area_power_tracking_record", "f", ("parent_id",), "di_area_power_tracking_record", ("id",)), ("di_area_power_tracking_record", "f", ("previous_id",), "di_area_power_tracking_record", ("id",)), ("di_area_power_tracking_record", "f", ("upload_id",), "di_area_power_csv", ("id",)), ("di_area_power_tracking_record", "f", ("baseline_upload_id",), "di_area_power_csv", ("id",)), ("di_area_power_tracking_record", "f", ("upload_id", *scope[1:]), "di_area_power_csv", scope)}
            if not expected <= signatures:
                raise ConfigurationError("Required primary, unique or foreign keys are not installed and validated")
            indexes = conn.execute("SELECT i.indisunique,i.indisvalid,i.indisready,pg_get_expr(i.indpred,i.indrelid) AS predicate, ARRAY(SELECT a.attname FROM unnest(i.indkey::smallint[]) WITH ORDINALITY x(num,ord) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.num WHERE x.ord<=i.indnkeyatts ORDER BY x.ord) AS columns FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname='di_area_power_tracking_record'", (self.settings.db_schema,)).fetchall()
            selection_ready = any(row["indisunique"] and row["indisvalid"] and row["indisready"] and tuple(row["columns"]) == ("selection_scope", "snapshot_id", "condition_key") and re.sub(r"[\s()]", "", row["predicate"] or "").replace("::text", "") == "record_type='selection'" for row in indexes)
            if not selection_ready or sum(row["contype"] == "c" and row["convalidated"] for row in constraints) < 19:
                raise ConfigurationError("Required selection uniqueness or row validation constraints are missing")
