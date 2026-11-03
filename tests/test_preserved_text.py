"""Synthetic preserved text through RAW, shared packets, OWNER and actual AI jobs."""
import copy
import json
import os
import uuid
import unittest
from unittest.mock import patch

import httpx

from common.sanity_preserved_text import FORMAT
from common.sanity_submission import scope_digest
from tests.test_preserved_sources import preserve
from tests.support import PgTestCase
from common.sanity_db import Database
from oa.sanity_access import AccessStore
from oa.sanity_production_workspace import ProductionWorkspaceStore


def text_plan(f, root, *, multi=False):
    plan = preserve(f, root); plan['schema_version'] = 4
    files = plan['inventory']['preserved_files']
    for item in files: item['content'] = FORMAT
    if multi:
        files[0]['binding'] = {'kind': 'units', 'review_unit_ids': [u['review_unit_id'] for u in plan['inventory']['units']]}
    f['prepared'].reports[files[0]['path']].write_bytes(b'SYNTHETIC Rule Information\r\nClock must be declared.\r\n')
    f['prepared'].reports[files[1]['path']].write_bytes(b'# SYNTHETIC ONLY\ncreate_clock -period 10 [get_ports clk]\nTOKEN=synthetic-private-value\n')
    if plan.get('approval'): plan['approval']['scope_sha256'] = scope_digest(plan)
    return plan


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class PreservedTextOwnerTests(unittest.TestCase):
    def test_owner_full_ai_cycle_and_authorized_masked_text_view(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests, ADMIN, OWNER, OTHER
        c = TokenlessSubmissionTests(); prepare = c.collection_input
        def collection_input():
            result = prepare(); text_plan(c.f, c.root); return result
        original_transport, observed = httpx.MockTransport, []
        def transport(handler):
            def model(request):
                packet = json.loads(json.loads(request.content)['messages'][1]['content'])
                values = packet['context']['preserved_evidence']; observed.append(copy.deepcopy(values))
                response = handler(request); body = response.json(); value = json.loads(body['choices'][0]['message']['content'])
                target = value['groups'][0]['id'] if 'groups' in value else 'review'
                value['preserved_citations'] = [{'target_id': target, 'evidence_ids': [values[1]['segments'][0]['id']], 'reason': '제공된 입력 제약을 비교했다.'}]
                body['choices'][0]['message']['content'] = json.dumps(value)
                return httpx.Response(200, json=body)
            return original_transport(model)
        try:
            c.setUp()
            with patch.object(c, 'collection_input', collection_input), patch('httpx.MockTransport', side_effect=transport):
                from oa.sanity_ai_jobs import AIJobService, run_once
                from oa.sanity_ai_model import OAAdvisor
                c.collection_input()
                sid=c.submit()
                c.assertEqual('AWAITING_CONFIRMATION',c.detail(sid)['delivery_state'])
                c.assertEqual(0,c.report_count())
                c.assertEqual(404,c.decide(sid,user=OTHER).status_code)
                c.success(c.decide(sid));c.bridge()
                c.assertEqual('READY',c.client.status(sid)['review_readiness'])
                original=c.collection_report()
                c.assertEqual(('COMPLETE','UNKNOWN',2,True),
                    (original['parse_status'],original['check_status'],original['not_evaluated_count'],original['analysis_ready']))
                base='/api/workbench/blocks/block-a1/versions/'+original['version_id']+'/reports/'+original['id']
                records=c.success(c.clients[OWNER].get(base+'/clustering/records?expected_revision=0'))['items']
                c.assertEqual(2,len(records))
                c.assertTrue(all(r['evaluation_status']=='NOT_PERFORMED' and not r['reasons'] for r in records))
                c.assertEqual(['clock fact A','clock fact B'],[r['fields']['message'] for r in records])
                c.assertTrue(all('status' not in r['fields'] for r in records))
                packets=[]
                def model(request):
                    body=json.loads(request.content);packet=json.loads(body['messages'][1]['content']);packets.append(packet)
                    context=packet['context']
                    if 'identity_fields' in packet:
                        ids=[r['id'] for r in context['report']['records']]
                        reply={'groups':[{'id':'facts','label':'클록 관측 사실','record_ids':ids}],
                            'reason':'같은 검사에서 수집한 사실을 검토한다.','reference_ids':[],
                            'group_rationales':[{'group_id':'facts','reason':'같은 클록 검사 범위의 두 사실을 함께 검토한다.',
                                'evidence_ids':ids,'reference_groups':[]}]}
                    else:
                        reply={'schema_version':2,'conclusion':'PASS','reason':'이번 두 사실을 확인된 기준과 비교했다.',
                            'evidence_ids':[r['id'] for r in context['group_evidence']],
                            'reference_ids':[],'criterion_ids':['synthetic-observation']}
                    return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop',
                        'message':{'role':'assistant','content':json.dumps(reply,ensure_ascii=False)}}]})
                advisor=OAAdvisor({'SANITY_OA_AI_ENABLED':'true','SANITY_OA_AI_URL':'https://synthetic.invalid/chat',
                    'SANITY_OA_AI_TOKEN':'synthetic-collection-token','SANITY_OA_AI_MODEL':'synthetic-model'},
                    transport=httpx.MockTransport(model))
                criterion={'id':'synthetic-observation','version':'1',
                    'scope':{**{key:original[key] for key in ('project_id','block_id','item_id','family','parser','rule_version')},
                             'layout':'POST'},'text':'합성 클록 관측 사실이 모두 확인되면 PASS를 제안한다.'}
                criteria_path=c.root/'collection-criteria.json'
                criteria_path.write_text(json.dumps({'schema_version':1,'criteria':[criterion]},ensure_ascii=False))
                workspace=lambda db:ProductionWorkspaceStore(db,AccessStore(db,ADMIN))
                factory=lambda scope:AIJobService(scope.db,scope.ws,advisor=advisor)
                from common.sanity_ai_execution import executing_on
                with executing_on('vwp'),patch.dict(os.environ,{'SANITY_OA_AI_CRITERIA_FILE':str(criteria_path)}), \
                     patch('oa.ai_workbench_api.jobs',side_effect=factory):
                    response=c.post(OWNER,base+'/clustering/ai',{'request_id':str(uuid.uuid4()),'expected_revision':0})
                    c.assertEqual(202,response.status_code,response.text)
                    c.assertEqual('PENDING',run_once(lambda:Database('oa'),workspace,advisor)['status'])
                    c.assertEqual('COMPLETED',run_once(lambda:Database('oa'),workspace,advisor)['status'])
                    view=c.success(c.clients[OWNER].get(base+'/workbench'))
                    c.assertFalse(view['current']['confirmed'])
                    c.assertFalse(view['summary']['complete'])
                    confirmed=c.success(c.post(OWNER,base+'/clustering?compact=true',{
                        'action':'CONFIRM','expected_revision':1,'request_id':str(uuid.uuid4()),'reason':'사람이 두 사실과 그룹 근거를 확인했다.'}))
                    review=base+'/groups/facts/review'
                    response=c.post(OWNER,review+'/ai',{'expected_revision':0,'expected_cluster_revision':confirmed['revision'],
                                                         'request_id':str(uuid.uuid4())})
                    c.assertEqual(202,response.status_code,response.text)
                    c.assertEqual('PENDING',run_once(lambda:Database('oa'),workspace,advisor)['status'])
                    c.assertEqual('COMPLETED',run_once(lambda:Database('oa'),workspace,advisor)['status'])
                    value=c.success(c.clients[OWNER].get(review+'?compact=true'))
                    c.assertIsNone(value['current'])
                    c.assertEqual('PASS',value['proposal']['conclusion'])
                    c.assertEqual(2,len(value['proposal']['evidence_ids']))
                    c.success(c.post(OWNER,review+'?compact=true',{'expected_revision':0,
                        'expected_cluster_revision':confirmed['revision'],'request_id':str(uuid.uuid4()),
                        'conclusion':'PASS','reason':'사람이 현재 원본과 기준을 확인했다.','proposal_id':value['proposal']['id']}))
                c.assertEqual(2,len(packets))
                c.assertEqual('none',packets[0]['context']['report']['evaluation_mode'])
                c.assertEqual(2,len(packets[1]['context']['group_evidence']))
                c.assertEqual(original,c.collection_report())
                from vwp_bridge.submission_analysis import private_records
                with Database('vwp') as db:
                    private=list(private_records(db,original['analysis_id']))
                c.assertEqual(2,len(private))
                c.assertTrue(all(row['payload']['evaluation_status']=='NOT_PERFORMED'
                                    and row['payload']['check_status']=='UNKNOWN' for row in private))
                c.assertEqual([record['fields'] for record in records],[row['payload']['fields'] for row in private])
                c.assertEqual([record['raw'].encode() for record in records],[bytes(row['raw_bytes']) for row in private])
                c.assertEqual([record['source_raw_sha256'] for record in records],[row['raw_sha256'] for row in private])
            self.assertEqual(2, len(observed)); self.assertEqual(observed[0], observed[1])
            report = c.collection_report(); source = report['preserved_sources'][1]
            base = '/api/workbench/blocks/block-a1/versions/'+report['version_id']+'/reports/'+report['id']
            endpoint = base+'/preserved/'+source['id']
            value = c.success(c.clients[OWNER].get(endpoint))
            self.assertTrue(value['complete']); self.assertNotIn('synthetic-private-value', str(value))
            download = c.clients[OWNER].get(endpoint+'?download=true')
            self.assertEqual(200, download.status_code); self.assertIn('create_clock', download.text)
            self.assertNotIn('synthetic-private-value', download.text)
            self.assertIn(c.clients[OTHER].get(endpoint).status_code, (403, 404))
            self.assertIn(c.clients[OWNER].get(base+'/preserved/pf-'+'0'*64).status_code, (403, 404))
        finally:
            c.doCleanups()
