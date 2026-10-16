#!/usr/bin/env python3
"""Replay a successful synthetic Codex experiment into a retained test workspace.

No model call, schema change, existing-row update or company configuration lookup.
The saved results remain drafts; only the scripted source example is confirmed.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sanity_codex_mock import MODEL, REPORT_IDS, fixture_report
from sanity_common import ReviewError, digest
from sanity_workspace import WorkspaceStore
from sanity_workspace_clustering import WorkspaceClusteringStore


def without_import_clock(value):
    if isinstance(value, list):
        return [without_import_clock(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: without_import_clock(item) for key, item in value.items()
            if key not in ('at', 'reviewed_at', 'edited_at')}


def install(db, archive, result, trace):
    if (result.get('status') != 'PASSED' or result.get('real_model_called') is not True
            or result.get('model') != MODEL or result.get('model_calls') != 3
            or result.get('export_sha256') != digest(archive)
            or archive.get('schema_version') not in (1, 2) or archive.get('environment') != 'SYNTHETIC_CODEX_MOCK'
            or archive.get('reports') != [fixture_report(name) for name in REPORT_IDS]
            or len(archive.get('events', [])) != (5 if archive.get('schema_version') == 1 else 6) or len(trace) != 3):
        raise ReviewError('INVALID_WORKSPACE_SEED', 'A complete synthetic inference result is required')
    receipts = {item['event_id']: item['receipt'] for item in trace}
    if len(receipts) != 3:
        raise ReviewError('INVALID_WORKSPACE_SEED', 'Three distinct model calls are required')
    store = WorkspaceStore(db)
    store.check_workspace()
    # Compare every original row, including timestamps and revisions, after replay.
    snapshot = db.execute("SELECT record_id,md5(jsonb_build_array(record_type,payload,state,version,created_at,updated_at)::text) AS hash FROM {oa}").fetchall()
    original = {str(row['record_id']): row['hash'] for row in snapshot}
    if archive['schema_version'] == 1 and any(not db.execute(
            "SELECT 1 FROM {oa} WHERE record_type='WS_CLUSTER_EVENT' AND workspace_key=%s", (event['id'],)).fetchone()
            for event in archive['events']):
        raise ReviewError('LEGACY_ARCHIVE_READ_ONLY', 'V1 archives remain verifiable in their original workspace; create a V2 experiment for a new import')
    added = 0
    for report in archive['reports']:
        present = db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key=%s", (report['id'],)).fetchone()
        if present:
            if present['payload'] != report:
                raise ReviewError('SEED_CONFLICT', 'An existing report must never be replaced')
        else:
            store.put('WS_REPORT', report['id'], report, project=report['project_id'], block=report['block_id'])
            added += 1
    clustering = WorkspaceClusteringStore(db)
    for event in archive['events']:
        if event['report_id'] not in REPORT_IDS:
            raise ReviewError('INVALID_WORKSPACE_SEED', 'Unexpected report')
        command = dict(event['command'])
        command['request_id'] = event['id']
        if command['mode'] == 'MOCK_CODEX':
            receipt = receipts.get(event['id'])
            if not receipt or command.pop('mock_receipt_sha256', None) != digest(receipt):
                raise ReviewError('INVALID_WORKSPACE_SEED', 'Model receipt mismatch')
            command['mock_receipt'] = receipt
        replayed = clustering.change(**command)
        # Import time can differ. All grouping, actor, reference and inference data
        # must match the original successful experiment.
        if without_import_clock(replayed) != without_import_clock(event):
            raise ReviewError('SEED_CONFLICT', 'Workspace context differs from the experiment')
    after = db.execute("SELECT record_id,md5(jsonb_build_array(record_type,payload,state,version,created_at,updated_at)::text) AS hash FROM {oa} WHERE record_id=ANY(%s::uuid[])", (list(original),)).fetchall()
    if {str(row['record_id']): row['hash'] for row in after} != original:
        raise ReviewError('SEED_CONFLICT', 'Existing workspace rows changed')
    return {'status': 'IMPORTED' if added else 'VERIFIED', 'reports_added': added,
            'original_rows_preserved': len(original), 'model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--result-dir', type=Path, required=True)
    args = parser.parse_args()
    directory = args.result_dir.resolve()
    if not directory.is_relative_to(ROOT/'artifacts'):
        parser.error('Only this checkout\'s ignored synthetic artifacts are supported')
    config = json.loads(args.config.read_text())
    if (config.get('environment') != 'SYNTHETIC_WORKSPACE'
            or config.get('port') not in (8001, 8002, 8003)
            or not Path(config.get('root', '')).name.startswith('sanity-mock-pg-')
            or config.get('settings', {}).get('SANITY_DB_ACCESS') != 'postgresql'
            or config['settings'].get('SANITY_DB_SCHEMA') != 'public'):
        parser.error('An isolated synthetic workspace is required')
    from psycopg2.extensions import parse_dsn
    params = parse_dsn(config['settings']['SANITY_OA_DSN'])
    if (not set(params) <= {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'connect_timeout'}
            or params.get('host') != config['root'] or params.get('port') != '55439'
            or params.get('dbname') != 'sanity_test_mock' or params.get('user') != 'sanity_mock_oa'):
        parser.error('Only the restricted local fixture login is supported')
    from run_workspace import load_runner
    environment = load_runner().isolated_environment(os.environ)
    environment.update({key: config['settings'][key] for key in ('SANITY_DB_ACCESS', 'SANITY_DB_SCHEMA', 'SANITY_OA_DSN')})
    environment['SANITY_AI_MODE'] = 'disabled'
    os.environ.clear(); os.environ.update(environment)
    from sanity_db import Database
    with Database('oa') as db, db.transaction():
        value = install(db, json.loads((directory/'export.json').read_text()),
                        json.loads((directory/'result.json').read_text()), json.loads((directory/'trace.json').read_text()))
    print(json.dumps(value))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('CODEX_MOCK_IMPORT_FAILED type='+type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
