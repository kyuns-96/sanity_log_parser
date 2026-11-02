"""Explicit AI-knowledge opt-in, guarded choices, and immutable review history."""
from copy import deepcopy
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

from common.sanity_common import ReviewError,canonical_json,digest
from oa.sanity_ai_evidence_choices import EvidenceChoices,binding,selected_case
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from tests.support import PgTestCase


class EvidenceChoiceTests(unittest.TestCase):
    def event(self):
        return {'id':'event','source_sha256':'a'*64,'report_id':'source','reason':'UNSELECTED-REPORT-NOTE',
            'groups':[{'id':key,'label':key,'record_ids':[key+'-log']} for key in ('a','b')],
            'rationales':{key:{'content':{'reason':key+' reason','evidence_ids':[key+'-log'],
                          'reference_groups':[]}} for key in ('a','b')}}

    def choice(self,event,group,enabled=True):
        return {'group_id':group,'enabled':enabled,'binding_sha256':binding(event,'CLUSTER',group),'sha256':digest(group)}

    def test_missing_false_stale_and_removed_choices_never_enable_a_case(self):
        event=self.event()
        for rows in ([],[self.choice(event,'a',False)],[{**self.choice(event,'a'),'binding_sha256':'b'*64}],
                     [{**self.choice(event,'a'),'group_id':'removed'}]):
            with self.subTest(rows=rows):self.assertIsNone(selected_case(event,'CLUSTER',rows))

    def test_selected_group_only_has_its_own_reason_and_no_whole_report_memo(self):
        event=self.event();before=deepcopy(event)
        result=selected_case(event,'CLUSTER',[self.choice(event,'a')])
        self.assertEqual(['a'],[g['id'] for g in result['groups']])
        self.assertEqual({'a'},set(result['rationales']))
        self.assertNotIn('UNSELECTED-REPORT-NOTE',canonical_json(result).decode())
        self.assertNotIn('b reason',canonical_json(result).decode())
        self.assertEqual(before,event)

    def test_membership_label_or_reason_edit_changes_binding_but_other_group_edit_does_not(self):
        event=self.event();fingerprint=binding(event,'CLUSTER','a')
        other=deepcopy(event);other['rationales']['b']['content']['reason']='changed'
        self.assertEqual(fingerprint,binding(other,'CLUSTER','a'))
        for field in ('membership','label','reason'):
            changed=deepcopy(event)
            if field=='membership':changed['groups'][0]['record_ids'].append('new')
            elif field=='label':changed['groups'][0]['label']='new'
            else:changed['rationales']['a']['content']['reason']='new'
            with self.subTest(field=field):self.assertNotEqual(fingerprint,binding(changed,'CLUSTER','a'))

    def test_judgment_registration_does_not_publish_the_separate_classification_reason(self):
        review={'id':'review','source_sha256':'a'*64,'group_id':'a','reason':'판정 근거',
                'clustering_rationale':{'content':{'reason':'UNSELECTED-CLASSIFICATION'}}}
        choice={'group_id':'a','enabled':True,'binding_sha256':binding(review,'REVIEW','a'),'sha256':'b'*64}
        result=selected_case(review,'REVIEW',[choice])
        self.assertEqual('판정 근거',result['reason'])
        self.assertNotIn('clustering_rationale',result)
        self.assertIn('clustering_rationale',review)


class EvidenceChoiceApiTests(unittest.TestCase):
    def test_server_actor_origin_strict_boolean_revision_and_closed_body(self):
        from tests.test_ai_workbench_api import ProductionWorkbenchApiTests,BASE,HEADERS,TARGET
        f=ProductionWorkbenchApiTests();f.setUp();self.addCleanup(f.doCleanups)
        store=Mock();store.view.return_value={'enabled':False};store.save.return_value={'enabled':True}
        path=BASE+'/groups/g1/evidence/CLUSTER'
        body={'enabled':True,'expected_revision':0,'binding_sha256':'a'*64,'request_id':str(uuid4())}
        with patch('oa.sanity_ai_evidence_choices.EvidenceChoices',return_value=store):
            self.assertEqual(200,f.client.get(path).status_code)
            self.assertEqual(403,f.client.post(path,json=body).status_code)
            for bad in ({'enabled':1},{'expected_revision':True},{'user_id':'forged'},{'binding_sha256':'bad'}):
                with self.subTest(bad=bad):self.assertEqual(400,f.client.post(path,headers=HEADERS,json={**body,**bad}).status_code)
            store.save.assert_not_called()
            self.assertEqual(200,f.client.post(path,headers=HEADERS,json=body).status_code)
            self.assertEqual((*TARGET,'g1','CLUSTER'),store.save.call_args.args)
            self.assertEqual(True,store.save.call_args.kwargs['enabled'])
            f.actor=type('LocalActor',(),{'auth_method':'local_token','user_id':TARGET[0],'access':f.access})()
            self.assertEqual(403,f.client.get(path).status_code)


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

    def test_existing_confirmed_classification_and_judgment_are_not_auto_registered(self):
        self.assertEqual([],self.cases());self.assertEqual([],self.cases('REVIEW'))
        for stage in ('CLUSTER','REVIEW'):
            state=self.choice(stage);self.assertFalse(state['enabled']);self.assertTrue(state['can_enable'])
        self.assertEqual([],self.c.requests)

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

    def test_withdrawal_blocks_pinned_ai_inputs_without_changing_human_history(self):
        self.toggle(True,'REVIEW');cases=self.cases('REVIEW')
        self.assertEqual([self.review['id']],[c['id'] for c in cases])
        self.toggle(False,'REVIEW');self.assertEqual([],self.cases('REVIEW'))
        with self.c.service() as service:
            store=WorkspaceGroupReviewStore(service.db,'g1',ws=service.ws)
            current,_=store.current('report');self.assertEqual(self.review,current)
            cluster,_=WorkspaceClusteringStore(service.db,service.ws).current('report');self.assertEqual(self.cluster,cluster)
            context=store.context('owner','block','version','target')
            self.assertFalse(CaseRetriever(service.db,service.ws).revalidate(context,cases,stage='REVIEW'))
            self.assertFalse(store.decision_stale(current,store.context(*self.c.path)))

    def test_choice_replay_conflict_revision_and_changed_content_are_guarded(self):
        before=self.choice();identity=uuid4()
        first=self.toggle(True,snapshot=before,request=identity)
        self.assertEqual(first,self.toggle(True,snapshot=before,request=identity))
        for expected,operation in [('REQUEST_CONFLICT',lambda:self.toggle(False,snapshot=before,request=identity)),
                                   ('REVISION_CONFLICT',lambda:self.toggle(False,snapshot=before)),
                                   ('AI_EVIDENCE_CHANGED',lambda:self.toggle(False,snapshot={**first,'binding_sha256':'f'*64}))]:
            with self.subTest(expected=expected),self.assertRaises(ReviewError) as error:operation()
            self.assertEqual(expected,error.exception.code)
        self.assertEqual(first,self.choice())

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

    def test_updated_judgment_defaults_to_unregistered_and_defer_cannot_be_registered(self):
        self.toggle(True,'REVIEW')
        with self.c.service() as service:
            store=WorkspaceGroupReviewStore(service.db,'g1',self.cluster['revision'],ws=service.ws)
            store.save(*self.c.path,conclusion='DEFER',reason='추가 근거가 필요합니다.',criteria='',conditions={},
                reusable=False,compatible_versions=False,expected_revision=1,request_id=uuid4())
        state=self.choice('REVIEW');self.assertFalse(state['enabled']);self.assertFalse(state['can_enable'])
        self.assertEqual([],self.cases('REVIEW'))
        with self.assertRaises(ReviewError) as error:self.toggle(True,'REVIEW')
        self.assertEqual('AI_EVIDENCE_NOT_READY',error.exception.code)

    def test_only_current_owner_can_change_registration(self):
        with self.c.service() as service:
            service.ws.put('WS_USER','other',{'id':'other','name':'다른 사용자','username':'other','role':'OWNER'},state={'active':True})
        state=self.choice()
        with self.assertRaises(ReviewError) as error:
            with self.c.service() as service:
                EvidenceChoices(service.db,service.ws).save('other',*self.c.path[1:],'g1','CLUSTER',enabled=True,
                    expected_revision=0,binding_sha256=state['binding_sha256'],request_id=uuid4())
        self.assertIn(error.exception.code,('FORBIDDEN','NOT_FOUND','UNAUTHORIZED'))
        self.assertFalse(self.choice()['enabled'])

    def test_reviewed_classification_can_be_selected_pending_whole_partition_confirmation(self):
        with self.c.service() as service:
            store=WorkspaceClusteringStore(service.db,service.ws)
            draft=store.change(*self.c.path,action='WITHDRAW',expected_revision=self.cluster['revision'],
                               request_id=uuid4(),reason='전체 분류를 다시 검토합니다.')
        state=self.toggle(True);self.assertTrue(state['enabled']);self.assertFalse(state['active'])
        self.assertEqual([],self.cases())
        with self.c.service() as service:
            WorkspaceClusteringStore(service.db,service.ws).change(*self.c.path,action='CONFIRM',
                expected_revision=draft['revision'],request_id=uuid4(),reason='전체 분류와 근거 재확인')
        self.assertTrue(self.choice()['active']);self.assertEqual(1,len(self.cases()))
