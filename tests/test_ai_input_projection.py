"""Synthetic item-specific AI input selection; source evidence stays unchanged."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from dataclasses import replace

import httpx

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_ai_projection import declaration, policy_for, project_rows, alias_rows, restore_clustering, restore_review
from common.sanity_integration_examples import clustering_example, group_review_example
from oa.sanity_ai_model import OAAdvisor
from tests.support import PgTestCase
from tests.test_ai_model import environment, chat, criterion


def selected_context(example):
    context=deepcopy(example['context'])
    rows=context['report']['records']
    for index,row in enumerate(rows):
        row['raw']='SYNTHETIC_RAW_PRIVATE_'+str(index)+'x'*3000
        row['fields'].update(optional=None, falsey=False, numeric=0, details='selected details',
                             excluded='SYNTHETIC_EXCLUDED_'+str(index))
        row.update(details='SYNTHETIC_TOP_LEVEL_DETAILS',evidence_id='SYNTHETIC_EVIDENCE_ID',
                   source_raw_sha256='SYNTHETIC_SOURCE_HASH')
    context['report'].update(ai_fields=['code','optional','falsey','numeric','details'],include_raw=False,include_details=False,
        source_parts=[{'id':'SYNTHETIC_SOURCE_PART_PRIVATE'}],raw_sha256='SYNTHETIC_REPORT_HASH_PRIVATE')
    if 'group_evidence' in context:
        members=set(context['group_snapshot']['record_ids'])
        context['group_evidence']=deepcopy([row for row in rows if row['id'] in members])
    return context


class ProjectionContractTests(unittest.TestCase):
    def test_absent_is_legacy_and_explicit_values_are_versioned(self):
        self.assertIsNone(declaration({}))
        self.assertIsNone(policy_for({'report':{'id':'legacy'}}))
        self.assertEqual({'version':'ai-input-fields-v1','ai_fields':['code','mode'],
                          'include_raw':True,'include_details':True},declaration({'ai_fields':['mode','code']}))
        self.assertEqual({'version':'ai-input-fields-v1','ai_fields':None,'include_raw':False,'include_details':True},
                         declaration({'include_raw':False}))

    def test_invalid_declarations_fail_closed(self):
        for value in ({'ai_fields':[]},{'ai_fields':['code','code']},{'ai_fields':['']},
                      {'ai_fields':['bad name']},{'ai_fields':['bad\nname']},{'ai_fields':['x'*101]},
                      {'ai_fields':[False]},{'ai_fields':['x']*257},{'ai_fields':None},
                      {'include_raw':1},{'include_details':'false'}):
            with self.subTest(value=value),self.assertRaises(ReviewError) as error:declaration(value)
            self.assertEqual('AI_INPUT_POLICY',error.exception.code)

    def test_selected_fields_preserve_falsy_values_and_missing_without_touching_input(self):
        rows=[{'id':'source-1','fields':{'code':'C','optional':None,'falsey':False,'numeric':0,
              'details':'selected details','ignored':'not selected'},'raw':'original raw',
              'details':'top-level details','evidence_id':'original-evidence','source_raw_sha256':'original-hash'}]
        original=deepcopy(rows);policy=declaration({'ai_fields':['code','optional','falsey','numeric','details','missing'],
                                                       'include_raw':False,'include_details':False})
        projected=project_rows(rows,policy)
        self.assertEqual(rows,original)
        self.assertEqual([{'id':'source-1','fields':{'code':'C','optional':None,'falsey':False,'numeric':0,
                                                    'details':'selected details'}}],projected)
        self.assertNotIn('missing',projected[0]['fields'])
        self.assertIs(project_rows(rows,None),rows)

    def test_empty_selection_without_raw_fails_and_raw_can_supply_evidence(self):
        rows=[{'id':'source-1','fields':{'code':'C'},'raw':'synthetic raw'}]
        policy=declaration({'ai_fields':['absent'],'include_raw':False})
        with self.assertRaises(ReviewError) as error:project_rows(rows,policy)
        self.assertEqual('AI_INPUT_POLICY',error.exception.code)
        allowed=project_rows(rows,declaration({'ai_fields':['absent'],'include_raw':True}))
        self.assertEqual({},allowed[0]['fields']);self.assertEqual('synthetic raw',allowed[0]['raw'])

    def test_explicit_details_keep_business_metadata_but_not_server_identifiers(self):
        row={'id':'source-1','fields':{'code':'C','details':'selected nested details'},'raw':'synthetic raw',
             'line_start':5,'details':'business detail','evidence_id':'server-only-id','source_raw_sha256':'server-only-hash'}
        policy=declaration({'ai_fields':['code','details'],'include_raw':True,'include_details':True})
        result=project_rows([row],policy)[0]
        self.assertEqual('selected nested details',result['fields']['details'])
        self.assertEqual('business detail',result['details']);self.assertEqual(5,result['line_start'])
        self.assertNotIn('evidence_id',result);self.assertNotIn('source_raw_sha256',result)

    def test_alias_roundtrip_rejects_foreign_and_duplicate_ids(self):
        rows=[{'id':'source-a','fields':{'code':'A'}},{'id':'source-b','fields':{'code':'B'}}]
        aliased,mapping=alias_rows(rows)
        self.assertEqual({'r0':'source-a','r1':'source-b'},mapping)
        value={'groups':[{'id':'g','label':'group','record_ids':['r0','r1']}],
               'group_rationales':[{'group_id':'g','reason':'reason','evidence_ids':['r0'],'reference_groups':[]}],
               'reason':'reason','reference_ids':[]}
        restored=restore_clustering(value,mapping)
        self.assertEqual(['source-a','source-b'],restored['groups'][0]['record_ids'])
        self.assertEqual(['source-a'],restored['group_rationales'][0]['evidence_ids'])
        self.assertEqual(['source-b'],restore_review({'evidence_ids':['r1']},mapping)['evidence_ids'])
        for invalid in (['r9'],['r0','r0']):
            with self.subTest(ids=invalid),self.assertRaises(ReviewError):restore_review({'evidence_ids':invalid},mapping)


class ProjectionModelTests(unittest.TestCase):
    def classify(self,context):
        captured=[]
        def respond(request):
            wire=json.loads(request.content);packet=json.loads(wire['messages'][1]['content']);captured.append((wire,packet))
            ids=[row['id'] for row in packet['context']['report']['records']]
            answer={'groups':[{'id':'g','label':'합성 그룹','record_ids':ids}],
                'reason':'합성 조건을 분류했습니다.','reference_ids':[],
                'group_rationales':[{'group_id':'g','reason':'제공된 조건을 대조했습니다.',
                                     'evidence_ids':ids,'reference_groups':[]}]}
            return httpx.Response(200,json=chat(answer))
        adapter=OAAdvisor(environment(),transport=httpx.MockTransport(respond))
        return adapter.cluster(context,[],['object','check_point','mode']),captured[0]

    def test_single_classification_sends_only_selected_fields_and_restores_original_ids(self):
        context=selected_context(clustering_example());before=deepcopy(context)
        result,(wire,packet)=self.classify(context)
        rows=packet['context']['report']['records']
        self.assertEqual(len(before['report']['records']),len(rows))
        self.assertEqual({'code','optional','falsey','numeric','details'},set(rows[0]['fields']))
        self.assertEqual({'id','fields'},set(rows[0]))
        self.assertEqual({row['id'] for row in before['report']['records']},set(result['advice']['groups'][0]['record_ids']))
        self.assertEqual(context,before)
        encoded=json.dumps(packet,ensure_ascii=False)
        for secret in ('SYNTHETIC_RAW_PRIVATE_','SYNTHETIC_EXCLUDED_','SYNTHETIC_TOP_LEVEL_DETAILS',
                       'SYNTHETIC_EVIDENCE_ID','SYNTHETIC_SOURCE_HASH','SYNTHETIC_SOURCE_PART_PRIVATE',
                       'SYNTHETIC_REPORT_HASH_PRIVATE',*[r['id'] for r in before['report']['records']]):
            self.assertNotIn(secret,encoded)
        self.assertIn('fields-v1',result['metadata']['prompt_version'])
        legacy=deepcopy(before)
        for key in ('ai_fields','include_raw','include_details'):legacy['report'].pop(key)
        _,(_,legacy_packet)=self.classify(legacy)
        self.assertEqual(legacy['report']['records'],legacy_packet['context']['report']['records'])
        self.assertLess(len(canonical_json(packet)),len(canonical_json(legacy_packet)))

    def test_single_review_sends_selected_group_evidence_and_restores_original_citation(self):
        context=selected_context(group_review_example());before=deepcopy(context);seen=[]
        criteria=[criterion(context)]
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content']);seen.append(packet)
            row=packet['context']['group_evidence'][0]
            return httpx.Response(200,json=chat({'schema_version':2,'conclusion':'FAIL',
                'reason':'선택된 근거와 기준을 비교했습니다.','evidence_ids':[row['id']],
                'reference_ids':[],'criterion_ids':[criteria[0]['id']]}))
        result=OAAdvisor(environment(),transport=httpx.MockTransport(respond)).review(context,[],criteria)
        packet=seen[0];rows=packet['context']['group_evidence']
        self.assertEqual(len(before['group_evidence']),len(rows))
        self.assertEqual({'id','fields'},set(rows[0]))
        self.assertEqual([before['group_evidence'][0]['id']],result['advice']['evidence_ids'])
        self.assertEqual(before,context)
        encoded=json.dumps(packet,ensure_ascii=False)
        for secret in ('SYNTHETIC_RAW_PRIVATE_','SYNTHETIC_EXCLUDED_','SYNTHETIC_TOP_LEVEL_DETAILS',
                       'SYNTHETIC_EVIDENCE_ID','SYNTHETIC_SOURCE_HASH','SYNTHETIC_SOURCE_PART_PRIVATE',
                       'SYNTHETIC_REPORT_HASH_PRIVATE',*[r['id'] for r in before['group_evidence']]):
            self.assertNotIn(secret,encoded)

    def test_single_model_cannot_cite_a_foreign_alias(self):
        context=selected_context(group_review_example());criteria=[criterion(context)];seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,json=chat({'schema_version':2,'conclusion':'FAIL',
                'reason':'합성 근거 확인','evidence_ids':['foreign-record'],
                'reference_ids':[],'criterion_ids':[criteria[0]['id']]}))
        with self.assertRaises(ReviewError):
            OAAdvisor(environment(),transport=httpx.MockTransport(respond)).review(context,[],criteria)
        self.assertEqual(1,len(seen))

    def test_batch_classification_projects_each_leaf_and_never_leaks_omitted_mode_to_merge(self):
        from oa.sanity_ai_batch_model import BatchModel
        from tests.test_ai_batches import batch_settings, response
        context=selected_context(clustering_example());before=deepcopy(context);seen=[]
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content']);seen.append(packet)
            return httpx.Response(200,json=chat(response(packet)))
        advisor=OAAdvisor(replace(OAAdvisor(environment()).settings,batch=batch_settings(max_records=1,input_tokens=24000)),
                          transport=httpx.MockTransport(respond))
        model=BatchModel(advisor,context,[],['object','check_point','mode'])
        nodes=[]
        for descriptor in model.leaf_plan():
            nodes.extend(model.execute({'kind':'LEAF',**descriptor})['nodes'])
        merged=model.execute({'kind':'MERGE','nodes':[node['id'] for node in nodes]},nodes=nodes)['nodes']
        advice,_=model.advice(merged)
        self.assertEqual({row['id'] for row in before['report']['records']},
                         {key for group in advice['groups'] for key in group['record_ids']})
        self.assertEqual(3,len([p for p in seen if p['batch']['stage']=='LEAF']))
        for packet in seen:
            encoded=json.dumps(packet,ensure_ascii=False)
            for secret in ('SYNTHETIC_RAW_PRIVATE_','SYNTHETIC_EXCLUDED_','SYNTHETIC_TOP_LEVEL_DETAILS',
                           'SYNTHETIC_EVIDENCE_ID','SYNTHETIC_SOURCE_HASH','M1',
                           *[row['id'] for row in before['report']['records']]):
                self.assertNotIn(secret,encoded)
            if packet['batch']['stage']=='LEAF':
                self.assertNotIn('record_aliases',packet['batch'])
                self.assertEqual({'id','fields'},set(packet['context']['report']['records'][0]))
        self.assertEqual(before,context)

    def test_batch_review_projects_leaves_and_merge_then_restores_evidence(self):
        from oa.sanity_ai_evidence_batch import EvidenceBatchModel
        from tests.test_ai_batches import batch_settings
        from tests.test_ai_evidence_batches import reply
        context=selected_context(group_review_example());before=deepcopy(context);seen=[]
        criteria=[{'id':'C1','version':'1','text':'합성 확인 기준',
                   'scope':{'item_id':context['scope']['item_id']}}]
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content']);seen.append(packet)
            return httpx.Response(200,json=chat(reply(packet)))
        advisor=OAAdvisor(replace(OAAdvisor(environment()).settings,batch=batch_settings(max_records=1,input_tokens=24000)),
                          transport=httpx.MockTransport(respond))
        model=EvidenceBatchModel(advisor,context,[],['object','check_point','mode'],criteria)
        nodes=[]
        for descriptor in model.leaf_plan():
            nodes.extend(model.execute({'kind':'REVIEW_LEAF',**descriptor})['nodes'])
        merged=model.execute({'kind':'REVIEW_MERGE','nodes':[node['id'] for node in nodes]},nodes=nodes)['nodes']
        advice,_=model.advice(merged)
        self.assertEqual('FAIL',advice['conclusion'])
        self.assertTrue(set(advice['evidence_ids']) <= {row['id'] for row in before['group_evidence']})
        self.assertTrue(advice['evidence_ids'])
        self.assertEqual(['REVIEW_LEAF','REVIEW_LEAF','REVIEW_MERGE'],[p['batch']['stage'] for p in seen])
        for packet in seen:
            encoded=json.dumps(packet,ensure_ascii=False)
            for secret in ('SYNTHETIC_RAW_PRIVATE_','SYNTHETIC_EXCLUDED_','SYNTHETIC_TOP_LEVEL_DETAILS',
                           'SYNTHETIC_EVIDENCE_ID','SYNTHETIC_SOURCE_HASH',*[row['id'] for row in before['group_evidence']]):
                self.assertNotIn(secret,encoded)
            self.assertTrue(all(set(row)=={'id','fields'} for row in packet['context']['group_evidence']))
        self.assertEqual(before,context)

    def test_projection_keeps_complete_preserved_text_and_citations_in_actual_calls(self):
        from tests.test_ai_evidence_batches import EvidenceBatchPureTests
        fixture=EvidenceBatchPureTests()
        self.addCleanup(fixture.doCleanups)
        original=('합성 보존 조건 αβ\r\n'*1900)+'synthetic END\n'
        model=fixture.model(text=original,input_tokens=18000,max_records=1)
        model.context['report'].update(ai_fields=['code'],include_raw=False,include_details=False)
        model.policy=policy_for(model.context)
        before=deepcopy(model.context)
        root=fixture.text(model)
        fragments=[fragment for packet in fixture.requests for fragment in packet.get('fragments',[])]
        self.assertGreater(len(fragments),3)
        self.assertEqual(original,''.join(fragment['text'] for fragment in fragments))
        self.assertEqual({segment['id'] for segment in model.segments},{fragment['parent_id'] for fragment in fragments})
        nodes=fixture.leaves(model)
        merged=model.execute({'kind':'REVIEW_MERGE','nodes':[node['id'] for node in nodes]},nodes=nodes)['nodes']
        advice,citations=model.advice(merged)
        self.assertEqual('FAIL',advice['conclusion'])
        self.assertEqual(root['evidence_ids'],citations[0]['evidence_ids'])
        self.assertEqual(before,model.context)
        self.assertTrue(any(packet['batch']['stage']=='TEXT_LEAF' for packet in fixture.requests))
        self.assertTrue(any(packet['batch']['stage']=='TEXT_MERGE' for packet in fixture.requests))
        for packet in fixture.requests:
            if packet['batch']['stage'].startswith('REVIEW'):
                self.assertTrue(all(set(row)=={'id','fields'} and set(row['fields'])=={'code'}
                                    for row in packet['context']['group_evidence']))


class ProjectionJobTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.c=self.w.c;self.v=self.w.v;self.admin,self.schema=self.c.admin,self.c.schema

    def test_policy_is_pinned_and_change_stops_batch_without_another_http_call(self):
        from common.sanity_db import json_value
        from oa.sanity_ai_budget import BatchSettings
        from tests.test_vwp_inference import configuration
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens')
        self.w.connection=self.v.create(configuration(batch=settings.public()))
        report=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        report.update(ai_fields=['object'],include_raw=False,include_details=False)
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='report'",(json_value(report),))
        job=self.w.request();self.w.prepare();first=self.w.prepare()
        self.assertEqual(1,first['progress']['completed_calls'])
        with self.c.service() as service:
            pinned=service._load(job['id'])['data']['pins']['source']
            self.assertIn('ai_input_policy_sha256',pinned)
        stored=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        self.assertEqual(report,stored)
        report['ai_fields']=['mode']
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='report'",(json_value(report),))
        result=self.w.prepare()
        self.assertEqual('STALE',result['status'],result)
        self.assertEqual(1,len(self.v.wire))

    def test_projection_survives_unknown_recovery_and_preserves_final_provenance(self):
        from common.sanity_db import json_value
        from common.sanity_ai_projection import source_policy_pin
        from oa.sanity_ai_batch_jobs import Ledger
        from oa.sanity_ai_budget import BatchSettings
        from tests.test_vwp_inference import configuration
        from uuid import uuid4
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens')
        self.w.connection=self.v.create(configuration(batch=settings.public()))
        report=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        report.update(ai_fields=['object'],include_raw=False,include_details=False)
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='report'",(json_value(report),))
        expected_pin=source_policy_pin({'report':report})['ai_input_policy_sha256']
        job=self.w.request();self.w.prepare();first=self.w.prepare()
        self.assertEqual(1,first['progress']['completed_calls'])
        def timeout(request):raise httpx.ReadTimeout('synthetic timeout',request=request)
        self.c.callback=timeout
        unknown=self.w.prepare();self.assertEqual('UNKNOWN',unknown['status'],unknown)
        info=unknown['unknown_recovery']
        with self.c.service() as service:
            resumed=service.retry_unknown('owner',job['id'],uuid4(),info['event_sha256'],info['step_sha256'],True)
        self.assertEqual(('RUNNING',1),(resumed['status'],resumed['progress']['completed_calls']))
        self.c.callback=None;result=self.v.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(4,len(self.v.wire))
        self.assertEqual(self.v.wire[1].content,self.v.wire[2].content)
        self.assertNotEqual(self.v.wire[0].content,self.v.wire[2].content)
        with self.c.service() as service:
            event=service._load(job['id']);ledger=Ledger(service,event)
            metadata=ledger.get('final')[0]['metadata']
            self.assertEqual(expected_pin,event['data']['pins']['source']['ai_input_policy_sha256'])
            self.assertEqual(expected_pin,metadata['ai_input_policy_sha256'])
            self.assertTrue(metadata['prompt_version'].endswith('.fields-v1'))
            self.assertIsNotNone(ledger.audit())
        stored=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        self.assertEqual(report,stored)
