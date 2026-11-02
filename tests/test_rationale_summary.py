"""Optional short summaries preserve full reasons, old receipts and audit history."""
from copy import deepcopy
import json
import unittest

import httpx

from common.sanity_ai_knowledge import shared_case
from common.sanity_common import ReviewError, digest
import common.sanity_workspace_rationale as rationale
from tests.support import PgTestCase


class RationaleSummaryTests(unittest.TestCase):
    def content(self):
        return {'reason':'같은 클록 설정 누락을 묶으며 공통 원인은 미확인입니다.',
                'evidence_ids':['a'],'reference_groups':[]}

    def group(self):return {'id':'g','label':'C1 · 클록 설정 누락','record_ids':['a','b']}

    def test_legacy_content_does_not_gain_a_stored_summary(self):
        from oa.workspace_api import RationaleContent
        before=self.content();sha=digest(before)
        self.assertEqual(before,rationale.validate_content(before,self.group(),[]))
        self.assertEqual(before,RationaleContent(**before).model_dump(exclude_none=True))
        self.assertEqual(sha,digest(before))
        value={**before,'summary':'  클록 설정 누락 · 공통 원인 미확인  '}
        self.assertEqual('클록 설정 누락 · 공통 원인 미확인',rationale.validate_content(value,self.group(),[])['summary'])

    def test_summary_limits_and_closed_content_return_noncontent_diagnostics(self):
        for summary in ('',' ',None,True,'가'*121):
            with self.subTest(summary=summary),self.assertRaises(ReviewError) as caught:
                rationale.validate_content({**self.content(),'summary':summary},self.group(),[],diagnostics=True)
            self.assertEqual('INVALID_RATIONALE',caught.exception.code)
            self.assertEqual('RATIONALE_SUMMARY',caught.exception.validation_failure['code'])
            self.assertEqual('summary',caught.exception.validation_failure['field'])
            self.assertLessEqual(set(caught.exception.validation_failure),{'schema_version','code','field','actual_length'})
        with self.assertRaises(ReviewError):
            rationale.validate_content({**self.content(),'extra':'unexpected'},self.group(),[])
        self.assertEqual('가'*120,rationale.validate_content({**self.content(),'summary':'가'*120},self.group(),[])['summary'])

    def test_summary_survives_group_validation_packing_and_brief_without_changing_details(self):
        content={**self.content(),'summary':'클록 설정 누락 · 원인 미확인'}
        rows=rationale.validate_group_rationales([{'group_id':'g',**content}],[self.group()],[],[])
        event={'id':'e','revision':1,'source_sha256':'source','model_called':True,'groups':[self.group()]}
        event['rationales']=rationale.carry(None,event,rows)
        before=deepcopy(event)
        from unittest.mock import patch
        with patch.object(rationale,'PACK_THRESHOLD',1):stored=rationale.pack(event)
        self.assertEqual(before,rationale.unpack(stored))
        self.assertEqual(content,event['rationales']['g']['content'])
        self.assertEqual(content['summary'],rationale.brief(event,self.group())['summary'])
        self.assertEqual(content['reason'],rationale.brief(event,self.group())['reason'])

    def test_shared_knowledge_keeps_summary_and_edits_require_a_new_evidence_choice(self):
        from oa.sanity_ai_evidence_choices import binding
        event={'id':'e','source_sha256':'source','groups':[self.group()],
               'rationales':{'g':{'content':{**self.content(),'summary':'클록 설정 누락'}}}}
        before=deepcopy(event);key=binding(event,'CLUSTER','g')
        case=shared_case(event)
        self.assertEqual('클록 설정 누락',case['rationales']['g']['content']['summary'])
        self.assertEqual(self.content()['reason'],case['rationales']['g']['content']['reason'])
        self.assertNotIn('evidence_ids',case['rationales']['g']['content'])
        self.assertEqual(before,event)
        event['rationales']['g']['content']['summary']='클록 설정 누락 · 원인 미확인'
        self.assertNotEqual(key,binding(event,'CLUSTER','g'))

    def test_single_and_batch_summaries_use_the_existing_calls_and_keep_complete_reasons(self):
        from tests.test_ai_batches import BatchPureTests,response
        fixture=BatchPureTests();model=fixture.model(max_records=1)
        calls=[];detail='같은 규칙과 조건의 클록 설정 누락입니다. '+('원인과 예외는 추가 확인이 필요합니다. '*20)
        def respond(request):
            wire=json.loads(request.content);packet=json.loads(wire['messages'][1]['content']);calls.append(packet)
            self.assertIn('summary',wire['messages'][0]['content'])
            value=response(packet)
            for row in value['group_rationales']:row.update(summary='클록 설정 누락 · 원인과 예외 미확인',reason=detail)
            return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':json.dumps(value,ensure_ascii=False)}}]})
        model.advisor.transport=httpx.MockTransport(respond)
        single=model.advisor.cluster(model.context,[],model.fields)['advice']
        self.assertEqual(1,len(calls))
        self.assertTrue(all(row['summary'] and row['reason']==detail for row in single['group_rationales']))
        nodes=[]
        for part in model.leaf_plan():nodes.extend(model.execute({'kind':'LEAF',**part})['nodes'])
        packet=model.merge(nodes)[0]
        self.assertTrue(all(row['fields']['summary'] and row['fields']['reason']==detail for row in packet['context']['report']['records']))
        merged=model.execute({'kind':'MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)['nodes']
        advice,_=model.advice(merged)
        self.assertEqual(len(model.records)+2,len(calls))
        self.assertTrue(all(row['summary'] and row['reason']==detail for row in advice['group_rationales']))
        self.assertEqual({r['id'] for r in model.records},{key for group in advice['groups'] for key in group['record_ids']})
        legacy=deepcopy(merged)
        for node in legacy:node.pop('summary')
        self.assertTrue(all('summary' not in row for row in model.advice(legacy)[0]['group_rationales']))


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class RationaleSummaryDatabaseTests(unittest.TestCase):
    def test_edit_reload_and_clear_preserve_original_reason_and_history(self):
        from oa.test_workspace_pipeline import WorkspacePipelineTests,BASE
        f=WorkspacePipelineTests();f.setUp();self.addCleanup(f.doCleanups)
        initial=f.propose();before=deepcopy(initial);group=initial['groups'][0]
        old=group['id'];content=initial['rationales'][old]['content']
        command=f.command(action='SAVE',expected_revision=initial['revision'],reviewed=True,reason='짧은 요약을 추가합니다.',
                          content={**content,'summary':'공통 설정 누락 · 원인은 추가 확인'})
        saved=f.post('sample-0001/groups/'+old+'/rationale',command)
        self.assertEqual(before,initial)
        self.assertEqual(saved,f.post('sample-0001/groups/'+old+'/rationale',command))
        detail=f.client.get(BASE+'sample-0001/groups/'+old+'/rationale?expected_revision='+str(saved['revision'])).json()
        self.assertEqual(command['content']['summary'],detail['rationale']['content']['summary'])
        self.assertEqual(content['reason'],detail['rationale']['content']['reason'])
        self.assertEqual(content,detail['rationale']['proposal']['content'])
        cleared=f.post('sample-0001/groups/'+old+'/rationale',f.command(action='SAVE',expected_revision=saved['revision'],
            reviewed=True,reason='요약을 지우고 상세 근거를 유지합니다.',content=content))
        detail=f.client.get(BASE+'sample-0001/groups/'+old+'/rationale?expected_revision='+str(cleared['revision'])).json()
        self.assertNotIn('summary',detail['rationale']['content'])
        self.assertTrue(any(row['rationale']['content'].get('summary')==command['content']['summary'] for row in detail['history']['items']))
