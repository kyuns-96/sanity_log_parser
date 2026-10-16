#!/usr/bin/env python3
"""Add immutable synthetic drag-and-drop fixtures only to a local 8003 QA DB.

Existing reports, model outputs, PoRs and schema are never replaced. No AI call.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    from psycopg2.extensions import parse_dsn
    params = parse_dsn(config['settings']['SANITY_OA_DSN'])
    if (config.get('environment') != 'SYNTHETIC_WORKSPACE' or config.get('port') != 8003
            or not Path(config.get('root', '')).name.startswith('sanity-mock-pg-')
            or config['settings'].get('SANITY_DB_ACCESS') != 'postgresql'
            or config['settings'].get('SANITY_DB_SCHEMA') != 'public'
            or not set(params) <= {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'connect_timeout'}
            or params.get('host') != config['root'] or params.get('port') != '55439'
            or params.get('dbname') != 'sanity_test_mock' or params.get('user') != 'sanity_mock_oa'):
        parser.error('Only a restricted local synthetic 8003 QA database is allowed')
    from tools.run_workspace import load_runner
    safe = load_runner().isolated_environment(os.environ)
    safe.update({key: config['settings'][key] for key in ('SANITY_DB_ACCESS', 'SANITY_DB_SCHEMA', 'SANITY_OA_DSN')})
    safe['SANITY_AI_MODE'] = 'disabled'
    os.environ.clear()
    os.environ.update(safe)
    from common.sanity_db import Database
    from oa.sanity_workspace import WorkspaceStore, record_id
    from oa.sanity_workspace_clustering import WorkspaceClusteringStore
    with Database('oa') as db, db.transaction():
        ws = WorkspaceStore(db)
        ws.check_workspace()
        cluster = WorkspaceClusteringStore(db)
        for suffix in ('single', 'multi', 'new', 'keyboard', 'touch', 'conflict', 'retry', 'cancel'):
            name = 'qa-log-drag-'+suffix
            records = []
            for index in range(1, 121):
                category = 'clock' if index <= 100 else 'delay' if index <= 115 else 'width'
                obj = 'cpu/'+category
                raw = f'[ERROR] DND_QA {obj} record={index} synthetic {category} violation'
                records.append({'id': f'{name}-e-{index:03}', 'check_status': 'FAIL',
                    'fields': {'object': obj, 'message': f'{category} 설정을 확인하세요 · 합성 로그 {index}',
                               'code': 'DND_QA', 'severity': 'ERROR', 'mode': 'M1', 'check_point': 'P1'},
                    'raw': raw, 'line_start': index, 'line_end': index, 'reasons': []})
            report = {'id': name, 'project_id': 'project-1', 'block_id': 'project-1-cpu',
                'version_id': 'project-1-cpu-post-1',
                'version_record_id': str(record_id('WS_VERSION', 'project-1-cpu-post-1')),
                'eco': 'chip_finish', 'item_id': 'gca_constraints', 'family': 'GCA', 'rule': 'DND_QA',
                'rule_version': 'dnd-ui-qa-v1', 'parser': 'synthetic_drag_qa_fixture',
                'record_count': 120, 'records': records, 'raw_report': '\n'.join(record['raw'] for record in records),
                'check_status': 'FAIL', 'parse_status': 'COMPLETE', 'analysis_ready': True, 'blockers': []}
            existing = db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key=%s",
                                  (name,)).fetchone()
            if existing:
                if existing['payload'] != report:
                    raise ValueError('Existing QA sources must never be replaced')
                continue
            ws.put('WS_REPORT', name, report, project='project-1', block='project-1-cpu')
            draft = cluster.change('user-04', 'project-1-cpu', 'project-1-cpu-post-1', name,
                action='PROPOSE', expected_revision=0, request_id=str(uuid.uuid4()))
            groups = [{'id': 'qa-'+key, 'label': label,
                       'record_ids': [r['id'] for r in records if r['fields']['object'] == 'cpu/'+key]}
                      for key, label in [('clock', '클럭 설정 누락'), ('delay', '입출력 지연 누락'), ('width', '데이터 폭 불일치')]]
            cluster.change('user-04', 'project-1-cpu', 'project-1-cpu-post-1', name, action='EDIT',
                expected_revision=draft['revision'], request_id=str(uuid.uuid4()),
                reason='드래그 UI 검증을 위한 합성 그룹 구성', groups=groups)
    print(json.dumps({'status': 'SYNTHETIC_DRAG_QA_READY', 'reports': 8,
                      'records_per_report': 120, 'model_calls': 0}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('DRAG_QA_SEED_FAILED type='+type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
