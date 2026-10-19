#!/usr/bin/env python3
"""Exercise the real submission pipeline on synthetic files + a fresh private PG.

Never accepts a company endpoint or reads an existing runtime config. Company
identity/input are explicitly replaced by synthetic fixture implementations.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(rows, padding, pg_bin):
    spec = importlib.util.spec_from_file_location('submission_demo_runner',ROOT/'db/tests/run_mock.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    database = runner.MockDatabase(runner.pg_binaries(pg_bin))
    result = {'status':'FAILED','synthetic_only':True,'real_reports_used':False,'real_ai_called':False,
              'records_per_post_report':rows,'fixture':str(database.root)}
    started = time.monotonic()
    try:
        source = database.root/'source'
        runner.stage_source(ROOT,source)
        database.start();database.setup(source)
        from common import sanity_config
        # Exported DSNs take precedence but do not suppress dotenv reads. Point
        # the config loader at the credential-free snapshot before any DB call.
        with patch.dict(os.environ,database.test_environment(),clear=True), \
                patch.object(sanity_config,'ENV_FILE',source/'.env'):
            from common.sanity_db import Database
            from common.sanity_parsers import ParserRegistry
            from devtools.sanity_submission_fixture import fixture,FixtureIdentity
            from oa.sanity_workspace import WorkspaceStore,seed_workspace
            from oa.sanity_workspace_pipeline import WorkspacePipelineStore
            from oa.submission_receiver import SourceReceiver
            from oa.test_workspace import fixture as workspace_fixture
            from vwp.submission_cli import SubmissionClient
            from vwp_bridge.submission_worker import SubmissionWorker
            with Database('oa') as db,db.transaction():seed_workspace(db,workspace_fixture())
            f = fixture(database.root/'synthetic-input',count=rows,padding=padding)
            client = SubmissionClient(f['client_config'],f['provider'],f['authority'],state_root=database.root/'client-state')
            sent = client.submit(f['target'],confirm=lambda _:True,preview=lambda _:None)
            print('phase=LOCAL_QUEUED',flush=True)
            with Database('vwp') as db:
                SubmissionWorker(db,f['bridge_config'],f['authority'],ParserRegistry()).once()
            print('phase=DB_QUEUED',flush=True)
            with Database('oa') as db:
                receiver = SourceReceiver(db,FixtureIdentity())
                while receiver.receive_one()['status']!='NO_MESSAGE':pass
            with Database('vwp') as db:
                SubmissionWorker(db,f['bridge_config'],f['authority'],ParserRegistry()).once()
            receipt = client.status(sent['submission_id'])
            assert receipt['delivery_state']=='OA_STORED' and receipt['review_readiness']=='READY'
            with Database('oa') as db,db.transaction(readonly=True):
                header = db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                    "AND payload->>'source_storage'='PARTS_V2' AND payload->'source'->>'layout'='POST'").fetchone()['payload']
                store = WorkspacePipelineStore(db)
                metadata = store.view_paged('user-04','project-1-cpu',header['version_id'],header['id'])
                tail = store.records_page('user-04','project-1-cpu',header['version_id'],header['id'],
                    expected_revision=0,offset=rows-1,limit=1)
                assert metadata['ready'] and metadata['record_count']==tail['total']==rows and len(tail['items'])==1
                assert tail['items'][0]['line_start']==rows+1
                assert WorkspaceStore(db).por('project-1-cpu')['version'] is None
                parts = db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_SOURCE_PART'").fetchone()['n']
            with Database('vwp') as db,db.transaction(readonly=True):
                sizes = db.execute("SELECT max(uncompressed_bytes) AS largest,sum(uncompressed_bytes) AS total "
                    "FROM {vwp_to_oa} WHERE message_type LIKE 'SOURCE_%'").fetchone()
                records = db.execute("SELECT count(*) AS n FROM {vwp} WHERE record_type='EVIDENCE'").fetchone()['n']
                assert sizes['largest']<=4*1024*1024 and records==rows+4
            result.update(status='PASS',delivery_state=receipt['delivery_state'],parts=parts,stored_records=records,
                largest_message_bytes=sizes['largest'],total_uncompressed_bytes=int(sizes['total']),
                last_record_verified=True,por_unchanged=True,original_bytes_retained=True)
    finally:
        database.stop()
        result.update(database_stopped=True,seconds=round(time.monotonic()-started,3))
        artifact = ROOT/'artifacts'/f'submission-demo-{rows}.json'
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin')
    parser.add_argument('--rows',type=int,default=10000)
    parser.add_argument('--padding',type=int,default=0)
    args = parser.parse_args()
    if not 1<=args.rows<=1000000 or not 0<=args.padding<=2000:
        parser.error('Synthetic probe budget: 1..1000000 records, padding 0..2000')
    try:
        return 0 if run(args.rows,args.padding,args.pg_bin)['status']=='PASS' else 1
    except Exception as error:
        print('code=SUBMISSION_DEMO_FAILED type='+type(error).__name__,file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
