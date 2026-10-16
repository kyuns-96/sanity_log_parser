#!/usr/bin/env python3
"""Opt-in live Codex experiment in a new socket-only synthetic PostgreSQL fixture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.run_workspace import load_runner
CHILD = """
import json,sys,uuid
from pathlib import Path
from common.sanity_common import canonical_json, digest
from devtools.sanity_codex_mock import CodexMockClusteringAdvisor, fixture_report, REPORT_IDS, MODEL
from common.sanity_db import Database
from oa.sanity_workspace import seed_workspace
from oa.sanity_workspace_clustering import WorkspaceClusteringStore

out=Path(sys.argv[1]);base=json.loads(Path(sys.argv[2]).read_text())
reports=[fixture_report(name) for name in REPORT_IDS]
base['items']=reports
base['dataset_sha256']=digest({key:value for key,value in base.items() if key!='dataset_sha256'})
with Database('oa') as db,db.transaction():seed_workspace(db,base)
FIELDS=['check_point','mode','object']
def inputs(name):
    with Database('oa') as db,db.transaction(readonly=True):
        store=WorkspaceClusteringStore(db)
        context=store.context('user-04','project-1-cpu','project-1-cpu-post-1',name,write=True)
        return context,store.catalog(context)
def change(name,**kwargs):
    with Database('oa') as db,db.transaction():
        return WorkspaceClusteringStore(db).change('user-04','project-1-cpu','project-1-cpu-post-1',name,**kwargs)
trace=[]
def propose(name):
    context,cases=inputs(name)
    print(json.dumps({'phase':'model_call','report_id':name,'records':context['report']['record_count'],'cases':len(cases)}),flush=True)
    advisor=CodexMockClusteringAdvisor(enabled=True)
    advisor.propose(context,cases,FIELDS)
    # The model is called before opening the write transaction/lock.
    event=change(name,action='PROPOSE',expected_revision=0,request_id=str(uuid.uuid4()),
                 mode='MOCK_CODEX',identity_fields=FIELDS,mock_receipt=advisor.receipt)
    assert event['model_called'] and not event['confirmed']
    trace.append({'report_id':name,'receipt':advisor.receipt,'event_id':event['id']})
    (out/'trace.json').write_bytes(canonical_json(trace))
    print(json.dumps({'phase':'model_draft_saved','report_id':name,'groups':len(event['groups'])}),flush=True)
    return event
original={name:inputs(name)[0]['source_sha256'] for name in REPORT_IDS}
with Database('oa') as db,db.transaction(readonly=True):
    from oa.sanity_workspace import WorkspaceStore
    por=WorkspaceStore(db).por('project-1-cpu')
source=propose(REPORT_IDS[0])
control=propose(REPORT_IDS[1])
human_label='제약 설정 누락 통합 검토'
policy=('테스트 담당자의 검토 기준: 클록 정의 누락과 입력 지연 누락은 제약 설정 누락이라는 하나의 검토 그룹으로 묶는다. '
        '대상 경로와 문구가 바뀌어도 이 두 유형은 함께 검토한다. 데이터 폭 불일치처럼 다른 문제는 분리한다.')
change(REPORT_IDS[0],action='EDIT',expected_revision=1,request_id=str(uuid.uuid4()),reason=policy,
       groups=[{'id':'human-constraints','label':human_label,'record_ids':[row['id'] for row in reports[0]['records']]}])
change(REPORT_IDS[0],action='EDIT',expected_revision=2,request_id=str(uuid.uuid4()),reason='합친 그룹의 설명과 대표 로그를 명시적으로 확인',
       rationale_operation={'kind':'SAVE','group_id':'human-constraints','reviewed':True,'content':{
           'reason':policy,'evidence_ids':[reports[0]['records'][0]['id'],reports[0]['records'][4]['id']],'reference_groups':[]}})
confirmed=change(REPORT_IDS[0],action='CONFIRM',expected_revision=3,request_id=str(uuid.uuid4()),reason=policy)
assert confirmed['confirmed'] and not confirmed['reusable'] and confirmed['criteria']==''
feedback=propose(REPORT_IDS[2])
assert [ref['id'] for ref in feedback['references']]==[confirmed['id']]
expected={row['id'] for row in reports[2]['records'][:8]}
matched=[group for group in feedback['groups'] if set(group['record_ids'])==expected]
assert len(matched)==1,'Model did not follow the human-confirmed grouping intent; no automatic retry'
assert matched[0]['label']==human_label,'Model did not retain the confirmed name; no automatic retry'
assert len(feedback['groups'])==2,'The new width-mismatch pattern must remain a separate proposed group'
assert original=={name:inputs(name)[0]['source_sha256'] for name in REPORT_IDS}
with Database('oa') as db,db.transaction(readonly=True):
    assert WorkspaceStore(db).por('project-1-cpu')==por
    events=db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CLUSTER_EVENT' AND payload->>'report_id'=ANY(%s) ORDER BY created_at,workspace_key",(list(REPORT_IDS),)).fetchall()
    heads=db.execute("SELECT workspace_key AS report_id,payload,state,version FROM {oa} WHERE record_type='WS_CLUSTER' AND workspace_key=ANY(%s)",(list(REPORT_IDS),)).fetchall()
export={'schema_version':2,'environment':'SYNTHETIC_CODEX_MOCK','reports':reports,
        'events':[row['payload'] for row in events],'heads':heads}
(out/'export.json').write_bytes(canonical_json(export))
def owner(groups,key):return next(group['id'] for group in groups if key in group['record_ids'])
control_merged=owner(control['groups'],REPORT_IDS[1]+'-e-001')==owner(control['groups'],REPORT_IDS[1]+'-e-005')
summary={'status':'PASSED','model':MODEL,'real_model_called':True,'model_calls':len(trace),
    'records_per_report':[report['record_count'] for report in reports],
    'source_initial_groups':len(source['groups']),'control_initial_groups':len(control['groups']),
    'human_confirmed_groups':1,'feedback_initial_groups':len(feedback['groups']),
    'control_clock_and_delay_merged':control_merged,'feedback_clock_and_delay_merged':True,
    'human_name_retained':True,'new_pattern_separate':True,'confirmed_case_referenced':True,
    'reuse_checkbox_required':False,'source_and_por_unchanged':True,'human_changes_simulated_by_test':True,
    'usage':[item['receipt']['metadata']['usage'] for item in trace],'export_sha256':digest(export)}
(out/'result.json').write_bytes(canonical_json(summary))
print(json.dumps(summary),flush=True)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='explicitly allow three real synthetic-only model calls')
    parser.add_argument('--pg-bin')
    parser.add_argument('--out', type=Path, default=ROOT/'artifacts'/time.strftime('codex-mock-%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; ordinary tests never call an external model')
    args.out = args.out.resolve()
    if not args.out.is_relative_to(ROOT/'artifacts'):
        parser.error('--out must be in the ignored artifacts directory')
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    runner = load_runner()
    database = runner.MockDatabase(runner.pg_binaries(args.pg_bin))
    started = time.monotonic()
    summary = {'status': 'FAILED', 'database_stopped': False}
    try:
        staged = database.root/'source'
        runner.stage_source(ROOT, staged)
        database.start(); database.setup(staged)
        script = staged/'run_live_codex_mock.py'; script.write_text(CHILD)
        source = ROOT/'oa/dashboard/public/sample-data/reports-library.json'
        with (args.out/'live.log').open('wb') as log:
            result = subprocess.run([sys.executable, str(script), str(args.out), str(source)],
                cwd=staged, env=database.test_environment(), stdout=log, stderr=subprocess.STDOUT, timeout=600)
        if result.returncode:
            raise RuntimeError('Live mock did not pass; inspect its retained synthetic log, without automatic retries')
        summary = json.loads((args.out/'result.json').read_text())
    except Exception as error:
        summary['error_type'] = type(error).__name__
    finally:
        database.stop()
        summary.update(database_stopped=True, seconds=round(time.monotonic()-started, 3))
        (args.out/'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
        (args.out/'result.json').chmod(0o600)
        print(json.dumps({'status': summary['status'], 'model': summary.get('model'), 'model_calls': summary.get('model_calls'),
                          'database_stopped': True, 'result': str(args.out/'result.json')}))
    return 0 if summary['status']=='PASSED' else 1


if __name__=='__main__':
    raise SystemExit(main())
