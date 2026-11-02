"""Rule codes partition every sanity before AI or human semantic grouping."""
from copy import deepcopy
from dataclasses import replace
import unittest
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError
from common.sanity_clustering_advice import validate_clustering_advice
from common.sanity_ai_projection import declaration,project_rows
from common.sanity_rule_groups import enforce,model_context,rule_code
from tests.support import PgTestCase


class RuleGroupTests(unittest.TestCase):
    def context(self):
        return {'report':{'id':'synthetic','rule':'item-name','record_count':3,
            'records':[{'id':key,'raw':'synthetic','fields':{'code':code,'object':'same'}}
                       for key,code in [('a','R2'),('b','R1'),('c','R2')]]}}

    def test_different_codes_cannot_be_one_group_in_any_sanity(self):
        for family in ('GCA','DC','STATIC','CUSTOM'):
            context=self.context();context['report']['family']=family
            value={'groups':[{'id':'g','label':'같은 내용','record_ids':['a','b','c']}],
                   'reason':'같은 의미','reference_ids':[]}
            with self.subTest(family=family),self.assertRaises(ReviewError) as error:
                validate_clustering_advice(value,context,[])
            self.assertEqual('RULE_CODE_CONFLICT',error.exception.code)

    def test_labels_start_with_actual_code_and_are_idempotent_without_mutating_input(self):
        context=self.context();groups=[{'id':'a','label':'간단한 설명','record_ids':['a','c']},
                                      {'id':'z','label':'R1 · 별도 조건','record_ids':['b']}]
        original=deepcopy(groups)
        result=enforce(groups,context['report']['records'],context['report'])
        self.assertEqual(['R1 · 별도 조건','R2 · 간단한 설명'],[g['label'] for g in result])
        self.assertEqual(groups,original)
        self.assertEqual(result,enforce(result,context['report']['records'],context['report']))

    def test_selected_input_keeps_code_as_structural_metadata_and_no_excluded_evidence(self):
        context=self.context();original=context['report']['records']
        policy=declaration({'ai_fields':['object'],'include_raw':False,'include_details':False})
        visible=project_rows(original,policy)
        projected=model_context({'report':{**context['report'],'records':visible}},original)
        self.assertEqual({'a':'R2','b':'R1','c':'R2'},projected['report']['rule_codes'])
        self.assertTrue(all(set(r['fields'])=={'object'} and 'raw' not in r for r in projected['report']['records']))
        self.assertEqual('R2',rule_code(visible[0],projected['report']))

    def test_transferred_records_cannot_use_item_name_as_missing_rule_code(self):
        row={'id':'a','fields':{}}
        self.assertEqual('LEGACY',rule_code(row,{'rule':'LEGACY'}))
        with self.assertRaises(ReviewError) as error:
            rule_code(row,{'source_storage':'PARTS_V2','rule':'item-name'})
        self.assertEqual('RULE_CODE_REQUIRED',error.exception.code)

    def test_leaf_and_merge_plans_never_cross_code_boundaries_without_reordering_records(self):
        from tests.test_ai_batches import BatchPureTests
        f=BatchPureTests();model=f.model(max_records=20,input_tokens=64000)
        template=model.records[0]
        model.records=[{**template,'id':str(i),'fields':{**template['fields'],'code':code}}
                       for i,code in enumerate(('R2','R2','R1','R1','R2'))]
        model.context['report'].update(records=model.records,record_count=len(model.records))
        self.assertEqual([(0,2),(2,4),(4,5)],[(p['start'],p['stop']) for p in model.leaf_plan()])
        nodes=[]
        for part in model.leaf_plan():nodes+=model.execute({'kind':'LEAF',**part})['nodes']
        by_id={n['id']:n for n in nodes}
        for round_number in (1,2):
            plan=model.merge_plan(nodes,round_number)
            self.assertEqual(set(by_id),{key for bucket in plan for key in bucket})
            for bucket in plan:
                codes={model.records[i]['fields']['code'] for key in bucket for i in by_id[key]['members']}
                self.assertEqual(1,len(codes))
        advice,_=model.advice(nodes)
        self.assertTrue(all(g['label'].startswith(model.records[int(g['record_ids'][0])]['fields']['code']+' · ') for g in advice['groups']))


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class RuleGroupDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        with self.c.service() as service:
            report=service.ws.get('WS_REPORT','report')['payload']
            report={**report,'id':'multi-rule','records':[
                {**row,'fields':{**row['fields'],'code':'R'+str(i+1)}} for i,row in enumerate(report['records'])]}
            service.ws.put('WS_REPORT','multi-rule',report,project='project',block='block')
        self.c.path=('owner','block','version','multi-rule')

    def test_manual_merge_move_and_new_group_reject_mixed_codes_and_rename_keeps_prefix(self):
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        groups=[{'id':'g1','label':'첫 조건','record_ids':['a']},{'id':'g2','label':'둘째 조건','record_ids':['b']}]
        with self.c.service() as service:
            store=WorkspaceClusteringStore(service.db,ws=service.ws)
            result=store.change(*self.c.path,action='PROPOSE',expected_revision=0,request_id=str(uuid4()),
                prepared={'groups':groups,'reason':'다른 규칙을 분리했습니다.','reference_ids':[]})
        self.assertEqual(['R1 · 첫 조건','R2 · 둘째 조건'],[g['label'] for g in result['groups']])
        operations=[{'kind':'MERGE','group_ids':['g1','g2'],'destination_id':'g1'},
                    {'kind':'MOVE','record_ids':['a'],'destination_id':'g2'},
                    {'kind':'MOVE','group_ids':['g1','g2'],'new_group_id':'new','label':'새 그룹'}]
        for operation in operations:
            with self.subTest(operation=operation),self.assertRaises(ReviewError) as error:
                with self.c.service() as service:
                    WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,
                        action='EDIT',expected_revision=1,request_id=str(uuid4()),reason='합성 변경',operation=operation)
            self.assertEqual('RULE_CODE_CONFLICT',error.exception.code)
        with self.c.service() as service:
            event=WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,
                action='EDIT',expected_revision=1,request_id=str(uuid4()),reason='이름 설명 변경',
                operation={'kind':'RENAME','destination_id':'g1','label':'새 설명'})
            self.assertEqual('R1 · 새 설명',event['groups'][0]['label'])

    def test_rolling_worker_keeps_all_codes_separate_through_publication(self):
        from oa.sanity_ai_budget import BatchSettings
        batch=BatchSettings(enabled=True,context_tokens=260000,input_tokens=64000,
            output_parameter='max_tokens',max_records=20,max_concurrency=2)
        self.c.advisor=type(self.c.advisor)(replace(self.c.advisor.settings,batch=batch),
            transport=httpx.MockTransport(self.c.respond))
        job=self.c.enqueue()
        for _ in range(16):
            result=self.c.run_job(job_id=job['id'])
            if result['status'] in ('COMPLETED','FAILED','STALE','UNKNOWN'):break
        self.assertEqual('COMPLETED',result['status'])
        self.assertEqual(2,result['current_group_count'])
        for packet in self.c.requests:
            self.assertEqual(1,len(set(packet['context']['report']['rule_codes'].values())))
        with self.c.service() as service:
            from oa.sanity_workspace_clustering import WorkspaceClusteringStore
            event,_=WorkspaceClusteringStore(service.db,ws=service.ws).current('multi-rule')
            self.assertEqual([['a'],['b']],[g['record_ids'] for g in event['groups']])
            self.assertTrue(all(g['label'].startswith(code+' · ') for code,g in zip(('R1','R2'),event['groups'])))
