"""Explicit DBA maintenance for 011, separate from restricted application logins.

Dry run is the default. Never deletes evidence, changes logical content, runs DDL,
or vacuums. Each applied batch is locked and committed independently; rerunning
after an interruption is safe. Restore plaintext before deploying older readers.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re

from common.sanity_common import ReviewError, canonical_json
from common.sanity_db import json_value
from common.sanity_payload_storage import PrivatePayloadStorage, encode_evidence, encode_payload, restore_row


class MaintenanceDatabase:
    """A deliberately separate owner connection; not a Database permission bypass."""
    side = 'vwp'

    def __init__(self, dsn, schema):
        if not isinstance(schema, str) or not schema or '\x00' in schema or len(schema.encode()) > 63:
            raise ReviewError('DATABASE_CONFIG', 'Invalid maintenance schema')
        from psycopg2 import sql
        from psycopg2.extras import RealDictCursor
        from common.sanity_postgres import connect
        self.schema = schema
        self.table = sql.Identifier(schema, 'sanity_vwp_data')
        self.conn = connect(dsn, autocommit=True, cursor_factory=RealDictCursor,
                            connect_timeout=10, application_name='sanity-payload-maintenance')
        try:
            self.conn.execute("SET TIME ZONE 'UTC'")
            self.conn.execute("SET lock_timeout = '5s'")
            self.conn.execute("SET statement_timeout = '60s'")
        except BaseException:
            self.conn.close()
            raise

    def execute(self, query, params=None):
        from psycopg2 import sql
        return self.conn.execute(sql.SQL(query).format(vwp=self.table), params)

    @contextmanager
    def transaction(self, *, readonly=False):
        with self.conn.transaction():
            if readonly:
                self.conn.execute('SET TRANSACTION READ ONLY')
            yield

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()


def logical_row(row):
    """Include all identity, source-byte, state, revision, and timestamp columns."""
    restored = restore_row(row['record_type'], row)
    return {key: value for key, value in restored.items() if key != 'payload_bytes'}


def fingerprint(db):
    """Read-only, consistent digest of every logical column and exact source byte."""
    result, after, count = hashlib.sha256(), None, 0
    with db.transaction(readonly=True):
        db.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        PrivatePayloadStorage(db)
        while True:
            identities = db.execute('SELECT record_id FROM {vwp} '+('WHERE record_id>%s ' if after else '')
                                    +'ORDER BY record_id LIMIT 64', (after,) if after else None).fetchall()
            if not identities:
                break
            for identity in identities:
                row = db.execute("SELECT *,to_jsonb(t)-'payload'-'raw_bytes'-'payload_bytes' AS metadata "
                                 'FROM {vwp} t WHERE record_id=%s', (identity['record_id'],)).fetchone()
                restored = logical_row(row)
                pieces = (canonical_json(row['metadata']), canonical_json(restored['payload']),
                          bytes(restored['raw_bytes'] or b''), canonical_json(restored['raw_bytes'] is not None))
                for piece in pieces:
                    result.update(len(piece).to_bytes(8, 'big'))
                    result.update(piece)
                after = row['record_id']
                count += 1
    return {'rows_verified': count, 'logical_sha256': result.hexdigest()}


def compact(db, *, apply=False, restore_plain=False, batch_size=32, max_rows=None):
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ReviewError('COMPACTION_CONFIG', 'Invalid batch size')
    if max_rows is not None and (type(max_rows) is not int or max_rows < 1):
        raise ReviewError('COMPACTION_CONFIG', 'Invalid row limit')
    if not PrivatePayloadStorage(db).enabled:
        raise ReviewError('PAYLOAD_STORAGE_SCHEMA_REQUIRED', 'DBA migration 011 is required')
    if apply:
        owner = db.conn.execute("SELECT pg_has_role(current_user,t.relowner,'MEMBER') AS owns "
            "FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname='sanity_vwp_data'", (db.schema,)).fetchone()
        if owner is None or not owner['owns']:
            raise ReviewError('COMPACTION_OWNER_REQUIRED', 'Use an explicitly authorized maintenance owner')
    report = {'mode': 'restore_plain' if restore_plain else 'compress', 'applied': apply,
              'rows_examined': 0, 'rows_changed': 0, 'rows_would_change': 0, 'batches_committed': 0,
              'column_bytes_before': 0, 'column_bytes_after': 0,
              'max_rows': max_rows, 'scan_scope': 'limited' if max_rows else 'eligible_rows',
              'physical_space_reclaimed': False}
    after = None
    while max_rows is None or report['rows_examined'] < max_rows:
        limit = min(batch_size, max_rows-report['rows_examined']) if max_rows else batch_size
        pending = {'rows_examined': 0, 'rows_changed': 0, 'rows_would_change': 0,
                   'column_bytes_before': 0, 'column_bytes_after': 0}
        with db.transaction(readonly=not apply):
            if apply:
                db.execute('LOCK TABLE {vwp} IN ACCESS EXCLUSIVE MODE')
            # Only V2 source evidence is eligible; legacy clustering evidence stays plain.
            rows = db.execute("SELECT e.record_id FROM {vwp} e WHERE "
                "(e.record_type='SOURCE_OUTBOX' OR (e.record_type='EVIDENCE' AND EXISTS "
                "(SELECT 1 FROM {vwp} r WHERE r.record_id=e.analysis_id AND r.record_type='RUN' AND r.status='SEALED' "
                "AND r.state->>'source_protocol'='2'))) AND e.payload_bytes IS "
                + ('NOT NULL ' if restore_plain else 'NULL ')
                + ('AND e.record_id>%s ' if after else '')
                + 'ORDER BY e.record_id LIMIT %s', ((after, limit) if after else (limit,))).fetchall()
            if not rows:
                break
            for identity in rows:
                row = db.execute('SELECT * FROM {vwp} WHERE record_id=%s', (identity['record_id'],)).fetchone()
                before = logical_row(row)
                raw = before['raw_bytes']
                if restore_plain:
                    payload, packed = before['payload'], None
                elif row['record_type'] == 'EVIDENCE':
                    payload, raw, packed = encode_evidence(before['payload'], raw)
                else:
                    payload, packed = encode_payload(row['record_type'], before['payload'])
                pending['rows_examined'] += 1
                pending['column_bytes_before'] += (len(canonical_json(row['payload']))
                    + len(row.get('payload_bytes') or b'') + len(row['raw_bytes'] or b''))
                pending['column_bytes_after'] += len(canonical_json(payload)) + len(packed or b'') + len(raw or b'')
                changed = row['payload'] != payload or row.get('payload_bytes') != packed or row['raw_bytes'] != raw
                pending['rows_would_change'] += int(changed)
                pending['rows_changed'] += int(changed and apply)
                if apply and changed:
                    db.execute('UPDATE {vwp} SET payload=%s,payload_bytes=%s,raw_bytes=%s WHERE record_id=%s',
                               (json_value(payload), packed, raw, row['record_id']))
                    saved = db.execute('SELECT * FROM {vwp} WHERE record_id=%s', (row['record_id'],)).fetchone()
                    if logical_row(saved) != before:
                        raise ReviewError('COMPACTION_INTEGRITY', 'Logical row changed; batch rolled back')
                after = row['record_id']
        for key, value in pending.items():
            report[key] += value
        report['batches_committed'] += int(apply)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--dsn-env', required=True, help='private environment variable name; never a DSN argument')
    parser.add_argument('--apply', action='store_true', help='owner-only writes; default is a read-only dry run')
    parser.add_argument('--restore-plain', action='store_true', help='restore original payload JSON for an older reader')
    parser.add_argument('--fingerprint', action='store_true', help='read-only digest of all logical rows; stop writers for before/after comparison')
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--max-rows', type=int)
    args = parser.parse_args(argv)
    if args.fingerprint and (args.apply or args.restore_plain or args.max_rows is not None):
        parser.error('--fingerprint is a complete read-only check, separate from conversion flags')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', args.dsn_env) or not os.environ.get(args.dsn_env):
        parser.error('A configured private DSN environment variable is required')
    try:
        with MaintenanceDatabase(os.environ[args.dsn_env], args.schema) as db:
            report = ({'mode': 'fingerprint', 'applied': False, **fingerprint(db)} if args.fingerprint
                      else compact(db, apply=args.apply, restore_plain=args.restore_plain,
                                   batch_size=args.batch_size, max_rows=args.max_rows))
        print(json.dumps({'status': 'COMPLETED', **report}, sort_keys=True))
        return 0
    except ReviewError as exc:
        code = exc.code
    except Exception:
        # Never expose DSNs, stored payloads, schema names or raw driver exceptions.
        code = 'COMPACTION_FAILED'
    print(json.dumps({'status': 'STOP', 'code': code,
                      'prior_batches_may_be_committed': bool(args.apply)}))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
