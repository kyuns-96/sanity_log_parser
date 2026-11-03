"""Explicit AI-knowledge opt-in, guarded choices, and immutable review history."""
import unittest
from uuid import uuid4

from common.sanity_common import canonical_json
from oa.sanity_ai_evidence_choices import EvidenceChoices
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class EvidenceChoiceDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        with self.c.service() as service:
            groups=[{'id':'g1','label':'선택할 분류','record_ids':['a']},
                    {'id':'g2','label':'별도 분류','record_ids':['b']}]
            store=WorkspaceClusteringStore(service.db,service.ws)
            draft=store.change(*self.c.path,action='PROPOSE',expected_revision=0,request_id=uuid4(),prepared={
                'groups':groups,'reason':'UNSELECTED-REPORT-NOTE','reference_ids':[],
                'group_rationales':[{'group_id':g['id'],'reason':'clock '+('선택된 근거' if i==0 else 'SECRET-UNSELECTED'),
                                     'evidence_ids':g['record_ids'],'reference_groups':[]} for i,g in enumerate(groups)]})
            self.cluster=store.change(*self.c.path,action='CONFIRM',expected_revision=draft['revision'],
                                      request_id=uuid4(),reason='현재 원본과 분류를 확인했습니다.')
            self.review=WorkspaceGroupReviewStore(service.db,'g1',self.cluster['revision'],ws=service.ws).save(*self.c.path,
                conclusion='FAIL',reason='clock 판정 근거',criteria='',conditions={},reusable=False,
                compatible_versions=False,expected_revision=0,request_id=uuid4())['event']
            source=service.ws.get('WS_REPORT','report')['payload']
            target={**source,'id':'target'}
            service.ws.put('WS_REPORT','target',target,project='project',block='block')
            draft=store.change('owner','block','version','target',action='PROPOSE',expected_revision=0,
                request_id=uuid4(),prepared={'groups':groups,'reason':'대상 분류','reference_ids':[],
                'group_rationales':[{'group_id':g['id'],'reason':'clock 조건을 확인합니다.',
                                     'evidence_ids':g['record_ids'],'reference_groups':[]} for g in groups]})
            self.target_cluster=store.change('owner','block','version','target',action='CONFIRM',expected_revision=draft['revision'],
                                           request_id=uuid4(),reason='대상 그룹 확인')

    def choice(self,stage='CLUSTER',group='g1'):
        with self.c.service() as service:
            return EvidenceChoices(service.db,service.ws).view(*self.c.path,group,stage)

    def toggle(self,enabled,stage='CLUSTER',group='g1',snapshot=None,request=None):
        state=snapshot or self.choice(stage,group)
        with self.c.service() as service:
            return EvidenceChoices(service.db,service.ws).save(*self.c.path,group,stage,enabled=enabled,
                expected_revision=state['revision'],binding_sha256=state['binding_sha256'],request_id=request or uuid4())

    def cases(self,stage='CLUSTER'):
        with self.c.service() as service:
            context=(WorkspaceClusteringStore(service.db,service.ws).context('owner','block','version','target')
                     if stage=='CLUSTER' else WorkspaceGroupReviewStore(service.db,'g1',ws=service.ws).context(
                         'owner','block','version','target'))
            return CaseRetriever(service.db,service.ws).select(context,stage=stage)['cases']


    def test_only_chosen_group_is_sent_and_stages_have_independent_switches(self):
        self.assertTrue(self.toggle(True)['active'])
        cases=self.cases();self.assertEqual([self.cluster['id']],[c['id'] for c in cases])
        self.assertEqual(['g1'],[g['id'] for g in cases[0]['groups']])
        encoded=canonical_json(cases).decode()
        self.assertNotIn('SECRET-UNSELECTED',encoded);self.assertNotIn('UNSELECTED-REPORT-NOTE',encoded)
        self.assertEqual([],self.cases('REVIEW'))
        self.toggle(True,'REVIEW')
        self.assertNotIn('clustering_rationale',self.cases('REVIEW')[0])
        self.assertNotIn('선택된 근거',canonical_json(self.cases('REVIEW')).decode())
        self.toggle(True,group='g2');self.assertEqual(2,len(self.cases()[0]['groups']))
        self.toggle(False);self.assertEqual(['g2'],[g['id'] for g in self.cases()[0]['groups']])
        self.toggle(False,group='g2');self.assertEqual([],self.cases())
        self.assertEqual([self.review['id']],[c['id'] for c in self.cases('REVIEW')])


    def test_unregistering_a_reference_keeps_the_dependent_human_partition_valid(self):
        self.toggle(True);cases=self.cases()
        with self.c.service() as service:
            store=WorkspaceClusteringStore(service.db,service.ws)
            groups=self.target_cluster['groups']
            event=store.apply_ai('owner','block','version','target',expected_revision=self.target_cluster['revision'],
                request_id=uuid4(),cases=cases,retrieval={'selected':[{k:c[k] for k in ('id','revision','source_sha256')} for c in cases]},
                metadata={'provider':'OA_OPENAI_COMPATIBLE','model_called':True,'model':'synthetic',
                          'prompt_version':'synthetic-choice-test','input_sha256':'a'*64},
                advice={'groups':groups,'reason':'등록된 분류 근거를 비교했습니다.','reference_ids':[cases[0]['id']],
                    'group_rationales':[{'group_id':g['id'],'reason':'clock 현재 조건을 확인했습니다.',
                        'evidence_ids':g['record_ids'],'reference_groups':[
                            {'case_id':cases[0]['id'],'group_id':'g1','reason':'선택된 근거와 이번 조건을 대조했습니다.'}]
                            if g['id']=='g1' else []} for g in groups]})
            confirmed=store.change('owner','block','version','target',action='CONFIRM',expected_revision=event['revision'],
                                   request_id=uuid4(),reason='사람이 분류를 확인했습니다.')
        self.toggle(False)
        with self.c.service() as service:
            store=WorkspaceClusteringStore(service.db,service.ws)
            current,_=store.current('target');self.assertEqual(confirmed,current)
            self.assertFalse(store.stale(current,store.context('owner','block','version','target')))
