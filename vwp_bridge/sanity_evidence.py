"""Bounded PostgreSQL evidence batches with deterministic replay and immutable sealing."""
from __future__ import annotations

import copy
import hashlib
import uuid

from common.sanity_common import ReviewError, canonical_json, digest, pack_json, validate_content_id
from common.sanity_db import Queue, json_value, lock_key, stable_uuid

PRODUCER_VERSION = "postgresql-2"


class PgEvidence:
    def __init__(self, db, manifest, parser_contract=None):
        self.db, self.run = db, manifest["run"]
        contract = {key: manifest[key] for key in ("run", "inventory", "files", "unexpected_files")}
        contract["files"] = sorted(contract["files"], key=lambda item: item["path"])
        contract["unexpected_files"] = sorted(contract["unexpected_files"])
        contract["producer_version"] = PRODUCER_VERSION
        if parser_contract:
            contract["parser_contract"] = parser_contract
        self.analysis_id = stable_uuid("analysis", digest(contract))
        if not db.conn.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key(self.analysis_id),)).fetchone()["acquired"]:
            raise ReviewError("ANALYSIS_BUSY", "This analysis is already running")
        self.pending, self.pending_bytes, self.record_count = {}, 0, 0
        existing = db.execute("SELECT * FROM {vwp} WHERE record_id=%s", (self.analysis_id,)).fetchone()
        self.sealed = existing is not None and existing["status"] == "SEALED"
        if existing is not None and (existing["record_type"] != "RUN" or existing["payload"] != contract):
            raise ReviewError("EVIDENCE_CONFLICT", "Existing analysis differs from this input")
        self.root_state = existing["state"] if existing else {}
        self.stage("RUN", self.run["run_id"], contract, "BUILDING", row_id=self.analysis_id)
        self.flush()
        units_by_path = {unit["report"]: unit for unit in manifest["inventory"]["units"]}
        for unit in manifest["inventory"]["units"]:
            self.stage("UNIT", unit["review_unit_id"], unit, "PENDING", unit["review_unit_id"], self.analysis_id)
        for file in manifest["files"]:
            unit = units_by_path.get(file["path"])
            self.stage("FILE", file["file_id"], file, "RECORDED",
                       unit["review_unit_id"] if unit else None, self.analysis_id)
        self.expected_units = len(manifest["inventory"]["units"])
        self.expected_files = len(manifest["files"])

    def row_id(self, kind, key):
        return uuid.uuid5(self.analysis_id, kind + ":" + key)

    def stage(self, kind, key, payload, status, unit=None, parent=None, raw=None, row_id=None):
        row_id = row_id or self.row_id(kind, key)
        raw_bytes = raw.encode("utf-8") if raw is not None else None
        if raw_bytes is not None and len(raw_bytes) > 67108864:
            raise ReviewError("SIZE_LIMIT", "Individual evidence exceeds storage limit")
        row = {"record_id": row_id, "record_type": kind, "analysis_id": self.analysis_id,
               "entity_key": key, "review_unit_id": unit, "parent_id": parent, "payload": payload,
               "raw_bytes": raw_bytes,
               "raw_sha256": hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else None}
        previous = self.pending.get(row_id)
        if previous is not None and previous[0] != row:
            raise ReviewError("EVIDENCE_CONFLICT", "A logical evidence key has conflicting contents")
        if previous is None:
            self.pending[row_id] = (row, status)
            self.pending_bytes += len(canonical_json(payload)) + len(raw_bytes or b"")
        if kind == "EVIDENCE":
            self.record_count += 1
        if len(self.pending) >= 256 or self.pending_bytes >= 4 * 1024 * 1024:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        with self.db.transaction():
            existing = {row["record_id"]: row for row in self.db.execute(
                "SELECT * FROM {vwp} WHERE record_id=ANY(%s)", (list(self.pending),)).fetchall()}
            new = []
            for row_id, (expected, status) in self.pending.items():
                old = existing.get(row_id)
                if old is not None:
                    if any(old[key] != value for key, value in expected.items()):
                        raise ReviewError("EVIDENCE_CONFLICT", "Persisted evidence differs from regenerated data")
                elif self.sealed:
                    raise ReviewError("EVIDENCE_CONFLICT", "Sealed analysis has missing evidence")
                else:
                    new.append((row_id, expected["record_type"], self.analysis_id, expected["entity_key"],
                                expected["review_unit_id"], expected["parent_id"], json_value(expected["payload"]),
                                status, expected["raw_bytes"], expected["raw_sha256"]))
            if new:
                self.db.executemany("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,review_unit_id,"
                    "parent_id,payload,status,raw_bytes,raw_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", new)
            if not self.sealed:
                self.db.execute("UPDATE {vwp} SET state=%s,version=version+1,updated_at=clock_timestamp() "
                                "WHERE record_id=%s AND status<>'SEALED'",
                                (json_value({"checkpoint_records_verified": self.record_count}), self.analysis_id))
        self.pending.clear()
        self.pending_bytes = 0

    def unit_result(self, result):
        self.flush()
        self.set_state(self.row_id("UNIT", result["review_unit_id"]), {"result": result}, result["parse_status"])

    def set_state(self, row_id, state, status):
        if self.sealed:
            row = self.db.execute("SELECT state,status FROM {vwp} WHERE record_id=%s", (row_id,)).fetchone()
            if row is None or row["state"] != state or row["status"] != status:
                raise ReviewError("EVIDENCE_CONFLICT", "Sealed aggregate differs from regenerated evidence")
        else:
            self.db.execute("UPDATE {vwp} SET state=%s,status=%s,version=version+1,updated_at=clock_timestamp() "
                            "WHERE record_id=%s", (json_value(state), status, row_id))

    def groups(self):
        self.flush()
        groups = []
        current = None
        query = self.db.tables
        from psycopg2 import sql
        with self.db.transaction():
            with self.db.conn.cursor(name="groups_" + uuid.uuid4().hex) as cursor:
                cursor.itersize = 256
                cursor.execute(sql.SQL("SELECT e.payload AS evidence,g.payload AS definition "
                    "FROM {vwp} e JOIN {vwp} g ON g.analysis_id=e.analysis_id AND g.record_type='GROUP' "
                    "AND g.entity_key=e.payload->>'finding_group_id' "
                    "WHERE e.analysis_id=%s AND e.record_type='EVIDENCE' "
                    "ORDER BY g.entity_key,(e.payload->>'record_index')::bigint").format(**query), (self.analysis_id,))
                for row in cursor:
                    ev = row["evidence"]
                    if current is None or current["finding_group_id"] != ev["finding_group_id"]:
                        if current is not None:
                            groups.append(current)
                        current = copy.deepcopy(row["definition"]["template"])
                    current["occurrence_count"] += 1
                    current["status_counts"][ev["check_status"]] += 1
                    if len(current["examples"]) < 3:
                        current["examples"].append(ev["example"])
                    current["examples_omitted"] = current["occurrence_count"] - len(current["examples"])
        if current is not None:
            groups.append(current)
        with self.db.transaction():
            for group in groups:
                self.set_state(self.row_id("GROUP", group["finding_group_id"]), {"group": group}, "RECORDED")
        return groups

    def seal(self, payload):
        validate_content_id(payload)
        from common.sanity_privacy import SecretGuard
        # Original evidence remains private. Do not leak credentials in legacy bundle examples/fields.
        SecretGuard().ensure_public(payload)
        expected = {"RUN": 1, "UNIT": self.expected_units, "FILE": self.expected_files,
                    "GROUP": len(payload["finding_groups"]), "EVIDENCE": self.record_count}
        actual = {row["record_type"]: row["n"] for row in self.db.execute(
            "SELECT record_type,count(*) AS n FROM {vwp} WHERE analysis_id=%s GROUP BY record_type", (self.analysis_id,))}
        if any(actual.get(kind, 0) != count for kind, count in expected.items()):
            raise ReviewError("EVIDENCE_CONFLICT", "Unexpected or missing evidence rows; analysis cannot be sealed")
        counts = {row["review_unit_id"]: row["n"] for row in self.db.execute(
            "SELECT review_unit_id,count(*) AS n FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE' "
            "GROUP BY review_unit_id", (self.analysis_id,))}
        if any(counts.get(u["review_unit_id"], 0) != u["record_count"] for u in payload["review_units"]):
            raise ReviewError("EVIDENCE_CONFLICT", "Full evidence coverage does not match review units")
        packed = pack_json(payload)
        final_state = {"bundle_id": payload["bundle_id"], "counts": expected,
                       "payload_sha256": packed["payload_sha256"]}
        if self.sealed and self.root_state != final_state:
            raise ReviewError("EVIDENCE_CONFLICT", "Sealed root metadata differs")
        with self.db.transaction():
            self.db.execute("SELECT record_id FROM {vwp} WHERE record_id=%s FOR UPDATE", (self.analysis_id,))
            Queue(self.db, "vwp_to_oa").enqueue({"message_id": payload["bundle_id"], "message_type": "ANALYSIS_BUNDLE",
                "project": self.run["project"], "run_id": self.run["run_id"], "bundle_id": payload["bundle_id"],
                "payload": {"schema_version": 1, "inventory_sha256": payload["inventory"]["sha256"],
                            "producer_version": payload["producer"]["version"]}, **packed})
            if not self.sealed:
                self.set_state(self.analysis_id, final_state, "SEALED")
        return {"analysis_id": str(self.analysis_id), "message_id": payload["bundle_id"], "bundle_id": payload["bundle_id"]}
