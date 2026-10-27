#!/usr/bin/env python3
"""Compare plaintext/011 storage in a NEW socket-only synthetic PostgreSQL DB.

No DSN, company report, or existing database argument is accepted. Reports contain
aggregate sizes/timings/digests only. The generated database is stopped on exit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _benchmark(rows, output):
    from common.sanity_common import canonical_json, digest
    from common.sanity_db import Database, lock_key
    from common.sanity_privacy import SecretGuard
    from common.sanity_raw_sources import CHUNK_BYTES, RawSourceStore
    from common.sanity_submission_store import SubmissionStore
    from tests.support import PgTestCase
    from vwp_bridge.payload_compaction import MaintenanceDatabase, compact
    from vwp_bridge.submission_analysis import SourceEvidence, public_records, record_parts
    import os

    fixture = PgTestCase()
    try:
        fixture.setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql', '010_raw_sources.sql'):
            fixture.admin.execute((ROOT/'db'/name).read_text().replace('public.', fixture.schema+'.'))
        uid, fid = 'u-'+digest('synthetic-storage-unit'), 'f-'+digest('synthetic-storage-file')
        records = []
        for i in range(rows):
            raw = (f'WARNING SYNTHETIC-CAPACITY record={i:06d} object=synthetic/block/cell_{i:06d} '
                   'condition=synthetic-mode reason=synthetic-evidence-only '
                   + 'repeated-synthetic-log '*8+'\n')
            fields = {'object': f'synthetic/block/cell_{i:06d}', 'check_point': 'SYNTHETIC-CHECK',
                      'mode': 'SYNTHETIC-MODE', 'code': 'SYNTHETIC-CAPACITY', 'severity': 'WARNING',
                      'observed': str(i % 997), 'unit': 'ns', 'detail': 'synthetic repeated field content '*6}
            records.append((raw, fields))
        original = ''.join(raw for raw, _ in records).encode()
        manifest = {'run': {'run_id': 'synthetic-storage-run', 'project': 'synthetic',
                           'design': 'synthetic-block', 'stage': 'sanity', 'tool': 'synthetic', 'tool_version': '1'},
                    'inventory': {'confirmed': True, 'source': 'synthetic', 'version': '1',
                                  'units': [{'review_unit_id': uid, 'report': 'reports/synthetic.rpt',
                                             'item_id': 'synthetic-item', 'rule_version': '1'}]},
                    'files': [{'file_id': fid, 'path': 'reports/synthetic.rpt',
                               'sha256': hashlib.sha256(original).hexdigest(), 'size_bytes': len(original)}],
                    'unexpected_files': []}
        started = time.monotonic()
        with Database('vwp') as db:
            raw_store = RawSourceStore(db, 'synthetic-storage-node')
            for offset in range(0, len(original), CHUNK_BYTES):
                chunk = original[offset:offset+CHUNK_BYTES]
                sha = hashlib.sha256(chunk).hexdigest()
                raw_store.immutable('RAW_CHUNK', 'rawc.'+sha,
                    {'schema_version': 1, 'protocol': 'raw-source-v1', 'sha256': sha, 'size_bytes': len(chunk)}, chunk)
            evidence = SourceEvidence(db, manifest)
            try:
                for i, (raw, fields) in enumerate(records, 1):
                    evidence.record(manifest['inventory']['units'][0], manifest['files'][0], i, fields, raw,
                                    {'line_start': i, 'line_end': i}, 'UNKNOWN', ['SYNTHETIC-NO-CRITERION'])
                evidence.seal_source({'record_count': rows, 'status_counts': {'PASS': 0, 'FAIL': 0, 'UNKNOWN': rows}})
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(evidence.analysis_id),))
            store = SubmissionStore(db)
            for i, part in enumerate(record_parts(public_records(db, evidence.analysis_id, SecretGuard()),
                                                 'sr-'+digest('synthetic-storage-report'))):
                store.put_outbox('00000000-0000-4000-8000-000000000001', i, 'SOURCE_PART',
                    {'collection_id': 'sc-'+digest('synthetic-storage-collection'), 'synthetic_only': True,
                     'body': {'id': 'sp-'+digest(part), 'body': part}})
        table = '"'+fixture.schema+'"."sanity_vwp_data"'

        def measure():
            values = fixture.admin.execute('SELECT pg_relation_size(%s::regclass),pg_table_size(%s::regclass),'
                'pg_indexes_size(%s::regclass),pg_total_relation_size(%s::regclass)', (table,)*4).fetchone()
            return dict(zip(('main_bytes', 'table_toast_bytes', 'index_bytes', 'total_bytes'), values))

        def fingerprints():
            from common.sanity_payload_storage import restore_row
            logical, wire, count = hashlib.sha256(), hashlib.sha256(), 0
            with MaintenanceDatabase(os.environ['SANITY_TEST_ADMIN_DSN'], fixture.schema) as db:
                after = None
                while True:
                    batch = db.execute("SELECT *,to_jsonb(t)-'payload'-'raw_bytes'-'payload_bytes' AS metadata "
                        'FROM {vwp} t '+('WHERE record_id>%s ' if after else '')+'ORDER BY record_id LIMIT 64',
                        (after,) if after else None).fetchall()
                    if not batch:
                        break
                    for row in batch:
                        restored = restore_row(row['record_type'], row)
                        pieces = (canonical_json(row['metadata']),
                                  canonical_json(restored['payload']), bytes(restored['raw_bytes'] or b''),
                                  canonical_json(restored['raw_bytes'] is not None))
                        for piece in pieces:
                            logical.update(len(piece).to_bytes(8, 'big')); logical.update(piece)
                        after = row['record_id']; count += 1
            with Database('vwp') as db:
                for record in public_records(db, evidence.analysis_id, SecretGuard()):
                    wire.update(canonical_json(record))
                packets = SubmissionStore(db).outbox('00000000-0000-4000-8000-000000000001')
                for packet in packets:
                    wire.update(canonical_json(packet['payload']))
            return {'logical_sha256': logical.hexdigest(), 'source_wire_sha256': wire.hexdigest(), 'stored_rows': count}

        report = {'synthetic_only': True, 'record_count': rows, 'original_bytes': len(original),
                  'seed_seconds': round(time.monotonic()-started, 3), 'before': measure()}
        before = fingerprints()
        fixture.admin.execute((ROOT/'db/011_payload_storage.sql').read_text().replace('public.', fixture.schema+'.'))
        with MaintenanceDatabase(os.environ['SANITY_TEST_ADMIN_DSN'], fixture.schema) as db:
            started = time.monotonic()
            report['compaction'] = compact(db, apply=True)
            report['compaction_seconds'] = round(time.monotonic()-started, 3)
        report['after_backfill'] = measure()
        started = time.monotonic()
        fixture.admin.execute((ROOT/'db/maintenance/repack_payload_storage.sql').read_text().replace('public.', fixture.schema+'.'))
        report['rewrite_seconds'] = round(time.monotonic()-started, 3)
        report['after_repack'] = measure()
        after = fingerprints()
        if before != after:
            raise RuntimeError('Synthetic logical content, source bytes, timestamps or wire payload changed')
        report.update(after)
        report['total_reduction_percent'] = round(100*(1-report['after_repack']['total_bytes']/report['before']['total_bytes']), 2)
        with output.open('x') as stream:
            json.dump(report, stream, indent=2); stream.write('\n')
    finally:
        fixture.doCleanups()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', help='local PostgreSQL 12.1+ binary directory')
    parser.add_argument('--rows', type=int, default=10000)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.rows <= 50000:
        parser.error('--rows must be 1..50000')
    if args.out.exists():
        parser.error('--out must be a new file')
    from db.tests.run_mock import MockDatabase, pg_binaries, stage_source
    directory = Path(tempfile.mkdtemp(prefix='sanity-storage-benchmark-', dir='/tmp'))
    source = directory/'source'
    stage_source(ROOT, source)
    database = MockDatabase(pg_binaries(args.pg_bin))
    try:
        database.start(); database.setup(source)
        report_path = directory/'result.json'
        code = ('from pathlib import Path;from db.tests.benchmark_payload_storage import _benchmark;'
                'import sys;_benchmark(int(sys.argv[1]),Path(sys.argv[2]))')
        with (directory/'benchmark.log').open('wb') as log:
            completed = subprocess.run([sys.executable, '-c', code, str(args.rows), str(report_path)],
                                       cwd=source, env=database.test_environment(), stdout=log, stderr=subprocess.STDOUT)
        if completed.returncode:
            print('status=FAILED artifact_directory='+str(directory))
            return completed.returncode
        report = json.loads(report_path.read_text())
        report['server_version_num'] = database.server_version_num
    finally:
        database.stop()
    report['database_stopped'] = True
    with args.out.open('x') as stream:
        json.dump(report, stream, indent=2); stream.write('\n')
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
