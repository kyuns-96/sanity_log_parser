#!/usr/bin/env python3
"""Measure ALL FOUR relations for a new synthetic source submission on PostgreSQL.

No company file, DSN or external endpoint is accepted. The full API/worker/parser/
OA receipt path runs against a fresh socket-only DB; all bodies are verified.
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


def _benchmark(rows, source_bytes, output, *, shared=True, preserved=False, preserved_text=False, measure_reads=False, measure_ai=False):
    from common.sanity_common import canonical_json, unpack_json
    from common.sanity_db import Database, Queue
    from common.sanity_privacy import SecretGuard
    from common.sanity_raw_sources import RawSourceStore
    from common.sanity_submission_store import SubmissionStore
    from oa.sanity_workspace_sources import part
    from tests.test_raw_sources import RawSourcesDatabaseTests
    from vwp_bridge.submission_analysis import public_records

    case = RawSourcesDatabaseTests()
    try:
        case.setUp()
        for name in ('011_payload_storage.sql', '012_source_evidence_pages.sql') + (
                ('013_shared_source_packets.sql',) if shared else ()):
            case.admin.execute((ROOT/'db'/name).read_text().replace('public.', case.schema+'.'))

        def sizes():
            values = {}
            for name in ('sanity_vwp_data', 'sanity_oa_data', 'sanity_vwp_to_oa', 'sanity_oa_to_vwp'):
                relation = '"'+case.schema+'"."'+name+'"'
                result = case.admin.execute('SELECT pg_relation_size(%s::regclass),pg_table_size(%s::regclass),'
                    'pg_indexes_size(%s::regclass),pg_total_relation_size(%s::regclass)', (relation,)*4).fetchone()
                values[name] = dict(zip(('main_bytes','table_toast_bytes','index_bytes','total_bytes'), result))
            return values

        before = sizes()
        requested_bytes = source_bytes
        if preserved:
            from tests.test_preserved_sources import preserve
            if preserved_text:
                from tests.test_preserved_text import text_plan
                text_plan(case.f, case.root)
            else:
                preserve(case.f, case.root)
            original_pre_bytes = sum(path.stat().st_size for key, path in case.f['prepared'].reports.items()
                                     if key not in ('reports/companion/full.rpt', 'reports/companion/input.sdc')
                                     and key != next(u['report'] for u in case.f['prepared'].plan['inventory']['units']
                                                     if u['source']['layout'] == 'POST'))
            preserved_bytes = source_bytes * 3 // 10
            for name, size in (('full.rpt', preserved_bytes * 14 // 15),
                               ('input.sdc', preserved_bytes - preserved_bytes * 14 // 15)):
                path = case.f['prepared'].reports['reports/companion/'+name]
                with path.open('wb') as stream:
                    remaining, index = size, 0
                    while remaining:
                        line = ('# SYNTHETIC '+name+' '+hashlib.sha256(str(index).encode()).hexdigest()+'\n').encode()
                        block = line[:remaining]; stream.write(block); remaining -= len(block); index += 1
            source_bytes -= preserved_bytes + original_pre_bytes
        unit = next(u for u in case.f['prepared'].plan['inventory']['units'] if u['source']['layout'] == 'POST')
        source = case.f['prepared'].reports[unit['report']]
        header = b'object,check_point,mode,code,severity,observed,unit\n'
        footer = ('# END rows='+str(rows)+'\n').encode()
        budget = source_bytes - len(header) - len(footer)
        with source.open('wb') as stream:
            stream.write(header)
            for index in range(rows):
                suffix = f',P1,M1,SYNTHETIC-CHECK,WARNING,{8 if index % 4 == 0 else 12},ns\n'
                token = hashlib.sha256(str(index).encode()).hexdigest()[:16]
                prefix = f'synthetic/block/cell_{index}_{token}_'
                width = budget // rows + (index < budget % rows)
                if width < len(prefix) + len(suffix):
                    raise ValueError('Synthetic byte budget is too small for requested rows')
                filler = ('synthetic_' * (width // 10 + 1))[:width-len(prefix)-len(suffix)]
                line = (prefix + filler + suffix).encode()
                if len(line) != width:
                    raise RuntimeError('Synthetic line width changed')
                stream.write(line)
            stream.write(footer)
        if source.stat().st_size != source_bytes:
            raise RuntimeError('Synthetic source size changed')

        started = time.monotonic()
        sent = case.submit()
        sid = sent['submission_id']
        if sent['delivery_state'] != 'VWP_UPLOADED' or case.bridge()['processed'] != 1:
            raise RuntimeError('Synthetic submission was not processed')
        case.receive(); case.bridge()
        if case.client.status(sid)['delivery_state'] != 'OA_STORED':
            raise RuntimeError('Complete OA receipt was not reached')
        after = sizes()
        pipeline_seconds = round(time.monotonic()-started, 3)

        with Database('vwp') as vwp, Database('oa') as oa:
            originals = RawSourceStore(vwp, case.config['node_id'])
            manifest = originals.load(sid)['manifest']
            for index, item in enumerate(manifest['files']):
                result = hashlib.sha256()
                for chunk in originals.iter_file(sid, index, item): result.update(chunk)
                expected = hashlib.sha256(case.f['prepared'].reports[item['path']].read_bytes()).hexdigest()
                if result.hexdigest() != expected:
                    raise RuntimeError('Exact original bytes changed')
            headers = oa.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                                 "AND payload->>'source_storage'='PARTS_V2' ORDER BY workspace_key").fetchall()
            read_performance = None
            if measure_reads:
                from db.tests.benchmark_source_reads import measure
                target = next(row['payload'] for row in headers if row['payload']['source']['layout'] == 'POST')
                read_performance = measure(oa, target)
            total_records, hashes = 0, []
            for row in headers:
                report = row['payload']
                left, right, count = hashlib.sha256(), hashlib.sha256(), 0
                for value in public_records(vwp, report['analysis_id'], SecretGuard()):
                    left.update(canonical_json(value)); count += 1
                for index, ref in enumerate(report['source_parts']):
                    for value in part(oa, report, ref, index): right.update(canonical_json(value))
                if left.digest() != right.digest() or count != report['record_count']:
                    raise RuntimeError('OA logs differ from complete VWP records')
                total_records += count
                hashes.append({'layout': report['source']['layout'], 'records': count, 'records_sha256': left.hexdigest()})
            if preserved_text:
                from oa.sanity_preserved_text import read
                for row in headers:
                    for file in row['payload'].get('preserved_sources', []):
                        actual, segments = read(oa, row['payload'], file['id'])
                        text = ''.join(s['text'] for s in segments).encode()
                        if text != case.f['prepared'].reports[file['path']].read_bytes():
                            raise RuntimeError('Complete synthetic preserved text changed')
            if total_records != rows + 4:
                raise RuntimeError('Synthetic POST/PRE record count changed')
            store, packets = SubmissionStore(vwp), 0
            identities = vwp.execute("SELECT entity_key FROM {vwp} WHERE record_type='SOURCE_OUTBOX' "
                                     "AND payload->>'submission_id'=%s ORDER BY entity_key", (sid,)).fetchall()
            for identity in identities:
                value = store.get('SOURCE_OUTBOX', identity['entity_key'])['payload']
                message = Queue(vwp, 'vwp_to_oa').get(value['message_id'])
                if canonical_json(value['packet']) != canonical_json(unpack_json(message)):
                    raise RuntimeError('Retained OUTBOX packet differs from exact shared message')
                packets += 1

        ai_performance=None
        if measure_ai:
            from db.tests.benchmark_ai_batches import measure
            target=next(r['payload'] for r in headers if r['payload']['source']['layout']=='POST')
            ai_performance=measure(target)
            expected=next(r['records_sha256'] for r in hashes if r['layout']=='POST')
            if ai_performance['complete_input_sha256']!=expected:raise RuntimeError('Actual model input lost source content')
            ai_performance['all_four_bytes_before_ai']=sum(v['total_bytes'] for v in after.values())
            after=sizes()
        total = sum(value['total_bytes'] for value in after.values())
        report = {'synthetic_only': True, 'layout': 'shared_013' if shared else 'pages_012',
                  'preserved_sources': preserved, 'preserved_text': preserved_text, 'requested_source_bytes': requested_bytes,
                  'preserved_original_bytes': sum(f['size_bytes'] for f in manifest.get('preserved_files', [])),
                  'preserved_metadata_verified': (not preserved or sum(len(r['payload'].get('preserved_sources', []))
                                                                       for r in headers) == 2),
                  'transport': 'in_process_https_api', 'post_original_bytes': source_bytes,
                  'all_original_bytes': sum(item['size_bytes'] for item in manifest['files']),
                  'post_records': rows, 'complete_records': total_records, 'exact_packets_verified': packets,
                  'all_original_bytes_verified': True, 'vwp_oa_records_equal': True,
                  'records': hashes, 'pipeline_seconds': pipeline_seconds, 'before': before, 'after': after,
                  'all_four_total_bytes': total, 'target_bytes': 20000000, 'target_met': total <= 20000000,
                  'size_scope': 'all_four_tables_including_toast_indexes_and_existing_fixture_metadata',
                  'owner_repack_used': False}
        if preserved and (report['all_original_bytes'] != requested_bytes or not report['preserved_metadata_verified']):
            raise RuntimeError('Preserved source budget or metadata changed')
        if measure_reads: report['read_performance'] = read_performance
        if measure_ai: report['ai_performance']=ai_performance
        with output.open('x') as stream:
            json.dump(report, stream, indent=2); stream.write('\n')
    finally:
        case.doCleanups()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin')
    parser.add_argument('--rows', type=int, default=50000)
    parser.add_argument('--source-bytes', type=int, default=14000000)
    parser.add_argument('--layout', choices=('pages', 'shared'), default='shared')
    parser.add_argument('--preserved-sources', action='store_true',
                        help='plan 3: use 30%% of source bytes for full.rpt/SDC; budget includes all original files')
    parser.add_argument('--preserved-text', action='store_true', help='plan 4: also verify all masked text on OA')
    parser.add_argument('--measure-reads', action='store_true', help='compare exact legacy, cold and warm complete hydration')
    parser.add_argument('--measure-ai',action='store_true',help='include synthetic batched AI classification and its durable audit storage')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    args.preserved_sources = args.preserved_sources or args.preserved_text
    if not 1 <= args.rows <= 100000 or not args.rows*128 <= args.source_bytes <= 64*1024*1024:
        parser.error('Use 1..100000 rows and rows*128..64MiB synthetic bytes')
    if args.out.exists(): parser.error('--out must be a new file')
    from db.tests.run_mock import MockDatabase, pg_binaries, stage_source
    directory = Path(tempfile.mkdtemp(prefix='sanity-total-storage-', dir='/tmp'))
    source = directory/'source'
    stage_source(ROOT, source)
    database = MockDatabase(pg_binaries(args.pg_bin))
    try:
        database.start(); database.setup(source)
        result = directory/'result.json'
        code = ('from pathlib import Path;from db.tests.benchmark_total_storage import _benchmark;import sys;'
                '_benchmark(int(sys.argv[1]),int(sys.argv[2]),Path(sys.argv[3]),shared=sys.argv[4]=="shared",'
                'preserved=sys.argv[5]=="True",preserved_text=sys.argv[6]=="True",measure_reads=sys.argv[7]=="True",measure_ai=sys.argv[8]=="True")')
        with (directory/'benchmark.log').open('wb') as log:
            done = subprocess.run([sys.executable,'-c',code,str(args.rows),str(args.source_bytes),str(result),args.layout,
                                   str(args.preserved_sources),str(args.preserved_text),str(args.measure_reads),str(args.measure_ai)],
                                  cwd=source,env=database.test_environment(),stdout=log,stderr=subprocess.STDOUT)
        if done.returncode:
            print('status=FAILED artifact_directory='+str(directory)); return done.returncode
        report = json.loads(result.read_text()); report['server_version_num'] = database.server_version_num
    finally:
        database.stop()
    report['database_stopped'] = True
    with args.out.open('x') as stream:
        json.dump(report, stream, indent=2); stream.write('\n')
    print(json.dumps(report, sort_keys=True))
    return 0 if args.layout != 'shared' or report['target_met'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
