"""Synthetic HTTP tests for complete text coverage and whole-group judgments."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError, digest
from common.sanity_integration_examples import clustering_example
from oa.sanity_ai_evidence_batch import EvidenceBatchModel
from oa.sanity_ai_batch_jobs import Ledger
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.test_ai_batches import batch_settings, response
from tests.support import PgTestCase


def reply(packet):
    stage=packet['batch']['stage']
    if stage.startswith('TEXT'):
        rows=packet.get('fragments',packet.get('analyses'))
        ids=([r['parent_id'] for r in rows] if stage=='TEXT_LEAF'
             else [key for r in rows for key in r['evidence_ids']])
        return {'input_ids':[r['id'] for r in rows],'summary':'합성 조건과 예외를 비교했습니다.',
                'evidence_ids':list(dict.fromkeys(ids))[:20],'unresolved':False}
    context=packet['context']
    if stage.startswith('REVIEW'):
        value={'schema_version':2,'conclusion':'FAIL','reason':'현재 경로는 승인된 기준을 위반합니다.',
               'evidence_ids':[context['group_evidence'][0]['id']], 'reference_ids':[],
               'criterion_ids':['C1'], 'examined_ids':[r['id'] for r in packet.get('analyses',context['group_evidence'])]}
    else: value=response(packet)
    if 'preserved_analysis' in context:
        target='review' if stage.startswith('REVIEW') else value['groups'][0]['id']
        ids=context['preserved_analysis']['evidence_ids']
        value['preserved_citations']=([{'target_id':target,'evidence_ids':ids,'reason':'현재 본문 분석 조건을 비교했습니다.'}] if ids else [])
    return value


def envelope(value):
    return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop',
                         'message':{'role':'assistant','content':json.dumps(value)}}]})


class EvidenceBatchPureTests(unittest.TestCase):
    def model(self, *, review=True, text=None, **options):
        context=deepcopy(clustering_example()['context'])
        if review:
            context['group_evidence']=context['report']['records']
            context['group_snapshot']={'id':'g','label':'합성 그룹','record_ids':[r['id'] for r in context['group_evidence']]}
        criteria=[{'id':'C1','version':'1','text':'합성 점검 기준','scope':{'item_id':context['scope']['item_id']}}]
        if text is not None:
            from tests.test_preserved_text import PreservedTextTests
            fixture=PreservedTextTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
            file,_,segments=fixture.extracted(raw=text.encode())
            context['report']['preserved_sources']=[file]
            context['preserved_evidence']=[{'source_id':file['id'],'segments':segments}]
        self.requests=[];self.transform=lambda value,packet:value
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content']);self.requests.append(packet)
            return envelope(self.transform(reply(packet),packet))
        advisor=OAAdvisor(OASettings(enabled=True,endpoint='https://model.example.test/v1/chat/completions',
            token='synthetic-token',model='synthetic-model',batch=batch_settings(**options)),
            transport=httpx.MockTransport(respond))
        return EvidenceBatchModel(advisor,context,[],['object','check_point','mode'],criteria if review else [])

    def text(self, model):
        nodes=[model.execute(d)['nodes'][0] for d in model.text_plan()]
        while len(nodes)>1:
            lookup={n['id']:n for n in nodes};out=[]
            for group in model.bounded_groups(nodes,text=True):
                selected=[lookup[k] for k in group]
                out.extend(selected if len(group)==1 else model.execute({'kind':'TEXT_MERGE','nodes':group},nodes=selected)['nodes'])
            nodes=out
        model.bind_preserved(nodes[0]);return nodes[0]

    def leaves(self,model):
        return [node for d in model.leaf_plan() for node in model.execute(
            {'kind':'REVIEW_LEAF' if model.review else 'LEAF',**d})['nodes']]

    def test_large_unicode_text_is_fully_read_once_and_citations_keep_parent_provenance(self):
        original=('합성 제약 αβ\r\n'*1900)+'last condition END\n'
        model=self.model(text=original,input_tokens=18000,max_records=1)
        root=self.text(model)
        fragments=[f for p in self.requests for f in p.get('fragments',[])]
        self.assertGreater(len(fragments),3)
        self.assertEqual(original,''.join(f['text'] for f in fragments))
        self.assertEqual({s['id'] for s in model.segments},{f['parent_id'] for f in fragments})
        nodes=self.leaves(model)
        merged=model.execute({'kind':'REVIEW_MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)['nodes']
        advice,cites=model.advice(merged)
        self.assertEqual('FAIL',advice['conclusion']);self.assertTrue(cites)
        self.assertEqual(root['evidence_ids'],cites[0]['evidence_ids'])
        for packet in self.requests:
            self.assertNotIn('preserved_evidence',packet['context'])
            self.assertNotIn('raw_report',packet['context']['report'])

    def test_long_single_line_and_masked_text_use_masked_character_offsets(self):
        model=self.model(text='TOKEN=synthetic-private\n'+'한'*18000+'\n',input_tokens=18000)
        self.text(model)
        fragments=[f for p in self.requests for f in p.get('fragments',[])]
        self.assertNotIn('synthetic-private',''.join(f['text'] for f in fragments))
        for f in fragments:
            parent=next(s for s in model.segments if s['id']==f['parent_id'])
            self.assertEqual(parent['text'][f['text_start']:f['text_stop']],f['text'])
            self.assertEqual(parent['byte_start'],f['byte_start'])
            self.assertEqual(parent['source_sha256'],f['source_sha256'])

    def test_text_missing_overlap_and_foreign_citations_are_rejected(self):
        model=self.model(text='condition\n'*2000,input_tokens=18000)
        root=self.text(model)
        for coverage in (root['coverage'][:-1],root['coverage']*2,[[0,1,2]]):
            with self.subTest(coverage=len(coverage)),self.assertRaises(ReviewError):
                model.bind_preserved({**root,'coverage':coverage})
        self.transform=lambda v,p:{**v,'evidence_ids':['pe-foreign']}
        with self.assertRaises(ReviewError):model.execute(model.text_plan()[0])
        for invalid in ([None], {'evidence_ids':[]}, [{'evidence_ids':[[1]]}]):
            self.transform=lambda v,p:{**v,'preserved_citations':invalid}
            with self.subTest(invalid=invalid),self.assertRaises(ReviewError) as caught:self.leaves(model)
            self.assertEqual('INVALID_PRESERVED_CITATION',caught.exception.code)

    def test_classification_consumes_complete_text_analysis_and_keeps_partition(self):
        model=self.model(review=False,text='合成 조건\n'*3000,input_tokens=18000,max_records=1)
        self.text(model);nodes=self.leaves(model)
        nodes=model.execute({'kind':'MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)['nodes']
        advice,cites=model.advice(nodes)
        self.assertTrue(all(p['batch']['version']==model.version for p in self.requests))
        self.assertEqual({r['id'] for r in model.records},{k for g in advice['groups'] for k in g['record_ids']})
        self.assertTrue(cites)

    def test_review_reads_every_original_and_reduction_includes_every_child(self):
        model=self.model(max_records=1,input_tokens=24000)
        nodes=self.leaves(model)
        observed=[r for p in self.requests for r in p['context']['group_evidence']]
        self.assertEqual(model.records,observed)
        result=model.execute({'kind':'REVIEW_MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)
        self.assertEqual('FAIL',model.advice(result['nodes'])[0]['conclusion'])
        self.assertEqual([n['id'] for n in nodes],[n['id'] for n in self.requests[-1]['analyses']])

    def test_conflicting_or_deferred_child_cannot_be_promoted_to_pass(self):
        model=self.model(max_records=1,input_tokens=24000);nodes=self.leaves(model)
        self.transform=lambda v,p:{**v,'conclusion':'PASS'}
        for conclusion in ('FAIL','DEFER'):
            nodes[0]['advice']['conclusion']=conclusion
            with self.subTest(conclusion=conclusion),self.assertRaises(ReviewError) as caught:
                model.execute({'kind':'REVIEW_MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)
            self.assertEqual('AI_BATCH_CONFLICT',caught.exception.code)

    def test_all_pass_still_requires_ai_integration_and_approved_grounding(self):
        model=self.model(max_records=1,input_tokens=24000)
        self.transform=lambda v,p:{**v,'conclusion':'PASS'}
        nodes=self.leaves(model)
        result=model.execute({'kind':'REVIEW_MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)
        self.assertEqual('PASS',model.advice(result['nodes'])[0]['conclusion'])
        self.transform=lambda v,p:{**v,'conclusion':'PASS','criterion_ids':[]}
        with self.assertRaises(ReviewError):self.leaves(model)

    def test_missing_criteria_defers_and_foreign_group_evidence_is_rejected(self):
        model=self.model(max_records=1,input_tokens=24000);model.criteria=[]
        self.transform=lambda v,p:{**v,'conclusion':'DEFER','criterion_ids':[]}
        nodes=self.leaves(model)
        result=model.execute({'kind':'REVIEW_MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)
        self.assertEqual('DEFER',model.advice(result['nodes'])[0]['conclusion'])
        self.transform=lambda v,p:{**v,'evidence_ids':['other-group']}
        with self.assertRaises(ReviewError):self.leaves(model)

    def test_ignored_review_child_and_incomplete_final_coverage_are_rejected(self):
        model=self.model(max_records=1,input_tokens=24000);nodes=self.leaves(model)
        self.transform=lambda v,p:{**v,'examined_ids':v['examined_ids'][:-1]}
        with self.assertRaises(ReviewError):model.execute({'kind':'REVIEW_MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)
        with self.assertRaises(ReviewError):model.advice([nodes[0]])

    def test_unresolved_text_survives_reduction_and_blocks_pass(self):
        model=self.model(text='condition\n'*3000,input_tokens=18000,max_records=1)
        self.transform=lambda v,p:{**v,'unresolved':p['batch']['stage']=='TEXT_LEAF'}
        root=self.text(model);self.assertTrue(root['unresolved'])
        self.transform=lambda v,p:{**v,'conclusion':'PASS'}
        with self.assertRaises(ReviewError) as caught:self.leaves(model)
        self.assertEqual('AI_BATCH_UNRESOLVED',caught.exception.code)

    def test_oversized_last_record_and_required_context_fail_before_review_http(self):
        model=self.model(max_records=1);model.records[-1]['raw']='x'*100000
        with self.assertRaises(ReviewError):model.leaf_plan()
        self.assertEqual([],self.requests)
        model=self.model(text='hello',input_tokens=1)
        with self.assertRaises(ReviewError):model.text_plan()
        self.assertEqual([],self.requests)

    def test_empty_preserved_file_is_not_invented_evidence(self):
        model=self.model(text='',max_records=100,input_tokens=24000)
        self.assertEqual([],model.text_plan())
        advice,citations=model.advice(self.leaves(model))
        self.assertEqual('FAIL',advice['conclusion']);self.assertEqual([],citations)


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class EvidenceBatchDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.c.enqueue();self.assertEqual('COMPLETED',self.c.run_job()['status'])
        with self.c.service() as service:
            WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,action='CONFIRM',
                expected_revision=1,request_id=uuid4(),reason='사람의 확정 근거')
        self.c.requests=[];self.callback=None
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content']);self.c.requests.append(packet)
            if self.callback:
                result=self.callback(packet)
                if result is not None:return result
            return envelope(reply(packet))
        self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,batch=batch_settings(max_records=1,input_tokens=24000)),
                                transport=httpx.MockTransport(respond))

    def enqueue(self):
        return self.c.enqueue(group_id='g1',expected_cluster_revision=2)

    def finish(self):
        for _ in range(40):
            result=self.c.run_job()
            if result is None or result['status'] in ('COMPLETED','FAILED','STALE','UNKNOWN','CANCELLED'):return result
        self.fail('Review did not terminate')

    def test_review_resumes_without_replaying_and_publishes_only_full_proposal(self):
        job=self.enqueue();first=self.c.run_job()
        self.assertEqual('REVIEW',first['progress']['phase'])
        self.assertEqual(1,first['progress']['completed_records'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])


        self.assertEqual('COMPLETED',self.finish()['status']);self.assertEqual(3,len(self.c.requests))
        with self.c.service() as service:
            event=service._load(job['id']);self.assertEqual(2,event['data']['batch']['version'])
            ledger=Ledger(service,event);self.assertIsNotNone(ledger.audit())
            result=ledger.get('final')[0];self.assertEqual(2,result['metadata']['batch_receipt']['records'])
        self.assertEqual(1,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_EVENT'").fetchone()[0])
        self.assertIsNone(self.c.run_job())

    def test_review_criteria_change_between_children_is_stale(self):
        self.enqueue();self.c.run_job();self.c.criteria=[{**self.c.criteria[0],'version':'2'}]
        result=self.finish();self.assertEqual(('STALE','AI_CRITERIA_STALE'),(result['status'],result['error']))
        self.assertEqual(1,len(self.c.requests))

    def test_second_review_call_timeout_never_retries_or_publishes_partial(self):
        self.enqueue();self.c.run_job()
        def timeout(packet):raise httpx.ReadTimeout('synthetic timeout')
        self.callback=timeout;result=self.finish();self.assertEqual('UNKNOWN',result['status'])
        self.assertEqual(2,len(self.c.requests));self.assertIsNone(self.c.run_job())
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])

    def test_cancelled_review_never_resumes(self):
        job=self.enqueue();self.c.run_job()
        with self.c.service() as service:service.cancel('owner',job['id'])
        self.assertIsNone(self.c.run_job());self.assertEqual(1,len(self.c.requests))

    def test_conflicting_merge_is_failed_without_a_proposal(self):
        self.enqueue()
        self.callback=lambda p:envelope({**reply(p),'conclusion':'PASS'}) if p['batch']['stage']=='REVIEW_MERGE' else None
        result=self.finish();self.assertEqual(('FAILED','AI_BATCH_CONFLICT'),(result['status'],result['error']))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class EvidenceTextDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests
        from tests.test_preserved_text import text_plan
        self.c=TokenlessSubmissionTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.c.collection_input();plan=text_plan(self.c.f,self.c.root)
        self.original=('합성 조건과 예외\n'*2800)+'END\n'
        path=plan['inventory']['preserved_files'][1]['path']
        self.c.f['prepared'].reports[path].write_text(self.original)
        sid=self.c.submit();self.c.success(self.c.decide(sid));self.c.bridge()
        self.report=self.c.collection_report()
        self.requests=[];self.callback=None
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content']);self.requests.append(packet)
            if self.callback:
                result=self.callback(packet)
                if result is not None:return result
            return envelope(reply(packet))
        self.advisor=OAAdvisor(OASettings(enabled=True,endpoint='https://model.example.test/v1/chat/completions',
            token='synthetic-token',model='synthetic-model',batch=batch_settings(max_records=1,input_tokens=24000)),
            transport=httpx.MockTransport(respond))
        self.criteria=lambda context:[{'id':'C1','version':'1','text':'합성 점검 기준',
                                      'scope':{'item_id':self.report['item_id']}}]

    @staticmethod
    def workspace(db):
        from oa.sanity_access import AccessStore
        from oa.sanity_production_workspace import ProductionWorkspaceStore
        from tests.test_submission_tokenless import ADMIN
        return ProductionWorkspaceStore(db,AccessStore(db,ADMIN))

    def enqueue(self, **kwargs):
        from common.sanity_db import Database
        from oa.sanity_access import access_lock
        from oa.sanity_ai_jobs import AIJobService
        from tests.test_submission_tokenless import OWNER
        with Database('oa') as db,db.transaction():
            access_lock(db)
            return AIJobService(db,self.workspace(db),self.advisor,self.criteria).enqueue(OWNER,'block-a1',
                self.report['version_id'],self.report['id'],expected_revision=0,request_id=uuid4(),**kwargs)

    def step(self):
        from common.sanity_db import Database
        from oa.sanity_ai_jobs import run_once
        return run_once(lambda:Database('oa'),self.workspace,self.advisor,self.criteria)

    def finish(self):
        for _ in range(150):
            result=self.step()
            if result is None or result['status'] in ('COMPLETED','FAILED','UNKNOWN','STALE','CANCELLED'):return result
        self.fail('Text workflow did not finish')

    def test_real_source_packet_reader_and_ledger_cover_text_for_classification_and_review(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        from oa.sanity_ai_jobs import AIJobService
        from tests.test_submission_tokenless import OWNER
        job=self.enqueue();self.assertEqual('TEXT',self.step()['progress']['phase'])
        self.assertEqual('COMPLETED',self.finish()['status'])
        with Database('oa') as db,db.transaction():
            store=WorkspaceClusteringStore(db,ws=self.workspace(db));event,_=store.current(self.report['id'])
            group=event['groups'][0]['id'];self.assertFalse(event['confirmed'])
            store.change(OWNER,'block-a1',self.report['version_id'],self.report['id'],action='CONFIRM',
                         expected_revision=1,request_id=uuid4(),reason='사람의 전체 분류 검토')
        first_requests=self.requests[:];self.requests=[]
        review_job=self.enqueue(group_id=group,expected_cluster_revision=2)
        self.assertEqual('COMPLETED',self.finish()['status'])
        for packets in (first_requests,self.requests):
            fragments=[f for p in packets for f in p.get('fragments',[])]
            self.assertGreater(len(fragments),4)
            source_id=self.report['preserved_sources'][1]['id']
            self.assertEqual(self.original,''.join(f['text'] for f in fragments if f['source_id']==source_id))
        with Database('oa') as db,db.transaction():
            service=AIJobService(db,self.workspace(db),self.advisor,self.criteria)
            for item in (job,review_job):
                event=service._load(item['id']);ledger=Ledger(service,event)
                self.assertEqual('COMPLETED',event['status']);self.assertIsNotNone(ledger.audit())
                final=ledger.get('final')[0]
                _,context=service._context(event['data']['command'],write=False)
                self.assertEqual(digest(context['preserved_evidence']),final['metadata']['preserved_input_sha256'])
                self.assertTrue(final['metadata']['preserved_citation_sources'])
        self.assertEqual(self.report,self.c.collection_report());self.assertIsNone(self.step())

    def test_text_timeout_never_publishes_or_starts_record_review(self):
        self.enqueue();self.step()
        def timeout(packet):raise httpx.ReadTimeout('synthetic text timeout')
        self.callback=timeout
        self.assertEqual('UNKNOWN',self.finish()['status']);self.assertIsNone(self.step())
        self.assertTrue(all(p['batch']['stage']=='TEXT_LEAF' for p in self.requests))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])


class EvidenceBatchLargeTests(unittest.TestCase):
    def test_hundred_thousand_group_records_are_all_read_before_final_judgment(self):
        helper=EvidenceBatchPureTests();self.addCleanup(helper.doCleanups)
        model=helper.model(input_tokens=64000,output_tokens=32768)
        template=model.records[0]
        model.records=[{**template,'id':f'large-{i}','raw':f'Synthetic current evidence {i}'} for i in range(100000)]
        model.context['report'].update(records=model.records,record_count=len(model.records))
        model.context['group_evidence']=model.records
        model.context['group_snapshot']['record_ids']=[r['id'] for r in model.records]
        model.context['source_sha256']=digest(model.context['report'])
        nodes=helper.leaves(model)
        observed=[r for p in helper.requests for r in p['context']['group_evidence']]
        self.assertEqual(digest(model.records),digest(observed))
        self.assertGreater(len(helper.requests),1)
        rounds=0
        while len(nodes)>1:
            lookup={n['id']:n for n in nodes};following=[]
            for group in model.merge_plan(nodes):
                selected=[lookup[k] for k in group]
                following.extend(selected if len(group)==1 else model.execute(
                    {'kind':'REVIEW_MERGE','nodes':group},nodes=selected)['nodes'])
            nodes=following;rounds+=1
            self.assertLessEqual(rounds,model.settings.max_rounds)
        advice,_=model.advice(nodes)
        self.assertEqual('FAIL',advice['conclusion'])
        self.assertEqual(list(range(100000)),nodes[0]['members'])
