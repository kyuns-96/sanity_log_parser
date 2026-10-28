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


def _benchmark(rows, output, *, evidence_pages=False, source_bytes=None):
    from common.sanity_common import canonical_json, digest
    from common.sanity_db import Database, lock_key
    from common.sanity_privacy import SecretGuard
    from common.sanity_raw_sources import CHUNK_BYTES, RawSourceStore
    from common.sanity_submission_store import SubmissionStore
    from common.sanity_evidence_pages import KIND as PAGE_KIND, decode_page
    from tests.support import PgTestCase
    from vwp_bridge.payload_compaction import MaintenanceDatabase, compact
    from vwp_bridge.submission_analysis import SourceEvidence, public_records, record_parts
    import os

    fixture = PgTestCase()
    try:
        fixture.setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql', '010_raw_sources.sql'):
            fixture.admin.execute((ROOT/'db'/name).read_text().replace('public.', fixture.schema+'.'))
        if evidence_pages:
            for name in ('011_payload_storage.sql', '012_source_evidence_pages.sql'):
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
            if source_bytes is not None:
                token = hashlib.sha256(('synthetic-'+str(i)).encode()).hexdigest()[:16]
                prefix = (f'WARNING SYNTHETIC-CHECK instance=/synthetic/block/cell_{i:06d} '
                          f'net=/synthetic/net_{token} value={i % 997}.{i % 101:03d}ns '
                          f'coordinate=({i % 10007},{i % 9001}) mode=SYNTHETIC-MODE '
                          'reason=synthetic-log-only ')
                width = source_bytes // rows + (i < source_bytes % rows)
                if len(prefix.encode()) + 1 > width:
                    raise ValueError('Synthetic byte budget is too small for the requested rows')
                fill = 'synthetic detail '
                raw = prefix + (fill * (width // len(fill) + 1))[:width-len(prefix)-1] + '\n'
                fields.update(net='/synthetic/net_'+token, observed=f'{i % 997}.{i % 101:03d}',
                              coordinate={'x': i % 10007, 'y': i % 9001})
            records.append((raw, fields))
        original = ''.join(raw for raw, _ in records).encode()
        if source_bytes is not None and len(original) != source_bytes:
            raise RuntimeError('Synthetic original byte count changed')
        manifest = {'run': {'run_id': 'synthetic-storage-run', 'project': 'synthetic',
                           'design': 'synthetic-block', 'stage': 'sanity', 'tool': 'synthetic', 'tool_version': '1'},
                    'inventory': {'confirmed': True, 'source': 'synthetic', 'version': '1',
                                  'units': [{'review_unit_id': uid, 'report': 'reports/synthetic.rpt',
                                             'item_id': 'synthetic-item', 'rule_version': '1'}]},
                    'files': [{'file_id': fid, 'path': 'reports/synthetic.rpt',
                               'sha256': hashlib.sha256(original).hexdigest(), 'size_bytes': len(original)}],
                    'unexpected_files': []}
        started = time.monotonic()
        expected_records = hashlib.sha256()
        expected_outbox = []
        with Database('vwp') as db:
            raw_store = RawSourceStore(db, 'synthetic-storage-node')
            chunk_ids = []
            for offset in range(0, len(original), CHUNK_BYTES):
                chunk = original[offset:offset+CHUNK_BYTES]
                sha = hashlib.sha256(chunk).hexdigest()
                chunk_ids.append(sha)
                raw_store.immutable('RAW_CHUNK', 'rawc.'+sha,
                    {'schema_version': 1, 'protocol': 'raw-source-v1', 'sha256': sha, 'size_bytes': len(chunk)}, chunk)
            evidence = SourceEvidence(db, manifest)
            try:
                for i, (raw, fields) in enumerate(records, 1):
                    evidence.record(manifest['inventory']['units'][0], manifest['files'][0], i, fields, raw,
                                    {'line_start': i, 'line_end': i}, 'UNKNOWN', ['SYNTHETIC-NO-CRITERION'])
                    record_id = 'r-'+digest([manifest['run']['run_id'], uid, fid, i])
                    expected_records.update(canonical_json({'id': record_id, 'fields': fields,
                        'check_status': 'UNKNOWN', 'raw': raw, 'line_start': i, 'line_end': i,
                        'reasons': ['SYNTHETIC-NO-CRITERION'], 'evidence_id': record_id,
                        'source_raw_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'redacted': False}))
                evidence.seal_source({'record_count': rows, 'status_counts': {'PASS': 0, 'FAIL': 0, 'UNKNOWN': rows}})
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(evidence.analysis_id),))
            store = SubmissionStore(db)
            for i, part in enumerate(record_parts(public_records(db, evidence.analysis_id, SecretGuard()),
                                                 'sr-'+digest('synthetic-storage-report'))):
                packet = {'collection_id': 'sc-'+digest('synthetic-storage-collection'), 'synthetic_only': True,
                          'body': {'id': 'sp-'+digest(part), 'body': part}}
                mid = store.put_outbox('00000000-0000-4000-8000-000000000001', i, 'SOURCE_PART', packet)
                expected_outbox.append({'payload': {'submission_id': '00000000-0000-4000-8000-000000000001',
                    'position': i, 'kind': 'SOURCE_PART', 'packet': packet, 'message_id': mid}})
            raw_store.immutable('RAW_FILE', 'rawf.00000000-0000-4000-8000-000000000001.00000',
                {'schema_version': 1, 'protocol': 'raw-source-v1',
                 'submission_id': '00000000-0000-4000-8000-000000000001', 'file_index': 0,
                 'file': manifest['files'][0], 'chunks': chunk_ids})
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
                    batch = db.execute("SELECT *,to_jsonb(t)-'payload'-'raw_bytes'-'payload_bytes'-'page_bytes' AS metadata "
                        'FROM {vwp} t '+('WHERE record_id>%s ' if after else '')+'ORDER BY record_id LIMIT 64',
                        (after,) if after else None).fetchall()
                    if not batch:
                        break
                    for row in batch:
                        restored = restore_row(row['record_type'], row)
                        pieces = (canonical_json(row['metadata']),
                                  canonical_json(restored['payload']), bytes(restored['raw_bytes'] or b''),
                                  canonical_json(restored['raw_bytes'] is not None))
                        if row['record_type'] == PAGE_KIND:
                            pieces += (canonical_json([{'payload': r['payload'], 'raw': r['raw_bytes'].decode('utf-8')}
                                                      for r in decode_page(row)]),)
                        for piece in pieces:
                            logical.update(len(piece).to_bytes(8, 'big')); logical.update(piece)
                        after = row['record_id']; count += 1
            with Database('vwp') as db:
                actual_records, record_count = hashlib.sha256(), 0
                for record in public_records(db, evidence.analysis_id, SecretGuard()):
                    encoded = canonical_json(record)
                    wire.update(encoded); actual_records.update(encoded); record_count += 1
                if actual_records.digest() != expected_records.digest() or record_count != rows:
                    raise RuntimeError('Complete source records differ from synthetic input')
                packets = SubmissionStore(db).outbox('00000000-0000-4000-8000-000000000001')
                if packets != expected_outbox:
                    raise RuntimeError('Immutable OUTBOX packets differ from generated originals')
                for packet in packets:
                    wire.update(canonical_json(packet['payload']))
                raw_sha, raw_length = hashlib.sha256(), 0
                raw_store = RawSourceStore(db, 'synthetic-storage-node')
                for sha in chunk_ids:
                    chunk = bytes(raw_store.get('RAW_CHUNK', 'rawc.'+sha)['raw_bytes'])
                    raw_sha.update(chunk); raw_length += len(chunk)
                if raw_sha.hexdigest() != manifest['files'][0]['sha256'] or raw_length != len(original):
                    raise RuntimeError('Exact original file bytes changed')
            return {'logical_sha256': logical.hexdigest(), 'source_wire_sha256': wire.hexdigest(), 'stored_rows': count}

        report = {'synthetic_only': True, 'record_count': rows, 'original_bytes': len(original),
                  'layout': 'evidence_pages_012' if evidence_pages else 'row_gzip_011',
                  'size_scope': 'private_vwp_table_including_toast_and_indexes',
                  'seed_seconds': round(time.monotonic()-started, 3), 'before': measure()}
        before = fingerprints()
        if not evidence_pages:
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
        if evidence_pages:
            report.update(target_bytes=20000000, target_met_on_fresh_write=report['before']['total_bytes'] <= 20000000,
                          exact_original_verified=True, complete_records_verified=True, exact_outbox_verified=True)
        reduction = 'repack_reduction_percent' if evidence_pages else 'total_reduction_percent'
        report[reduction] = round(100*(1-report['after_repack']['total_bytes']/report['before']['total_bytes']), 2)
        with output.open('x') as stream:
            json.dump(report, stream, indent=2); stream.write('\n')
    finally:
        fixture.doCleanups()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', help='local PostgreSQL 12.1+ binary directory')
    parser.add_argument('--rows', type=int, default=10000)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--evidence-pages', action='store_true', help='use optional 012 before writing new records')
    parser.add_argument('--source-bytes', type=int, help='exact synthetic original size; minimum 240 bytes per record')
    args = parser.parse_args(argv)
    if not 1 <= args.rows <= 50000:
        parser.error('--rows must be 1..50000')
    if args.source_bytes is not None and not 240*args.rows <= args.source_bytes <= 32*1024*1024:
        parser.error('--source-bytes must be between rows*240 and 32 MiB')
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
                'import sys;_benchmark(int(sys.argv[1]),Path(sys.argv[2]),evidence_pages=sys.argv[3]=="true",'
                'source_bytes=None if sys.argv[4]=="none" else int(sys.argv[4]))')
        with (directory/'benchmark.log').open('wb') as log:
            completed = subprocess.run([sys.executable, '-c', code, str(args.rows), str(report_path),
                                        'true' if args.evidence_pages else 'false',
                                        str(args.source_bytes) if args.source_bytes is not None else 'none'],
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
