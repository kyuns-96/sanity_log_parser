"""Synthetic task defaults, selected model pins, and administrator API."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from common.sanity_common import ReviewError
from oa.sanity_ai_task_defaults import TaskDefaults
from tests.support import PgTestCase
from tests.test_ai_connections import configuration


class TaskModelDefaultsTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_connections import ConnectionDatabaseTests
        self.f=ConnectionDatabaseTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.c=self.f.c; self.admin,self.schema=self.c.admin,self.c.schema

    @staticmethod
    def choice(connection,model):
        return {'connection_id':connection,'model_id':model}

    def set_defaults(self, defaults, revision=0):
        with self.f.store() as (_,actor,service):
            return TaskDefaults(service.db).save(actor,defaults,revision)

    def capability(self, *, group=False):
        header={'ready':True,'can_review':True,'group_ready':True,'record_count':2}
        with self.c.service() as service, patch('oa.sanity_ai_submissions.header',return_value=header), \
             patch('oa.sanity_ai_monitor.Monitor.latest',return_value=None), \
             patch('oa.sanity_ai_monitor.Monitor.legacy',return_value=None):
            return service.capability(*self.c.path, **({'group_id':'group-1'} if group else {}))

    def test_legacy_unset_and_independent_defaults_apply_without_crossing_credentials(self):
        first=self.f.create(configuration(label='first'))
        second=self.f.create(configuration(label='second'))
        self.f.token(first)
        legacy=self.capability()
        self.assertNotIn('task_default',legacy)
        defaults={'CLUSTER':self.choice(first,'model-b'),'REVIEW':self.choice(second,'model-a')}
        self.assertEqual({'revision':1,'defaults':defaults},self.set_defaults(defaults))
        cluster=self.capability(); review=self.capability(group=True)
        self.assertEqual((first,'model-b',True), (cluster['connection_id'],cluster['model'],cluster['available']))
        self.assertEqual((second,'model-a',False,'AI_USER_TOKEN_REQUIRED'),
                         (review['connection_id'],review['model'],review['available'],review['credential_error']))
        self.assertEqual((defaults['CLUSTER'],True),({k:cluster['task_default'][k] for k in ('connection_id','model_id')},
                                                    cluster['task_default']['applied']))
        self.assertEqual('model-b',next(x for x in cluster['connections'] if x['id']==first)['model'])
        self.assertEqual('model-a',next(x for x in review['connections'] if x['id']==second)['model'])
        self.assertFalse(next(x for x in cluster['connections'] if x['id']==second)['credential_ready'])

    def test_disabled_deleted_or_removed_model_falls_back_with_reason(self):
        first=self.f.create(configuration(label='first'))
        second=self.f.create(configuration(label='second'))
        defaults={'CLUSTER':self.choice(first,'model-b'),'REVIEW':None}
        self.set_defaults(defaults)
        self.f.edit(first,model='model-a',models=['model-a'])
        selected=self.capability()
        self.assertEqual(('AI_MODEL_NOT_ALLOWED',False),
                         (selected['task_default']['reason'],selected['task_default']['applied']))
        self.f.edit(first,enabled=False)
        selected=self.capability()
        self.assertEqual((second,'AI_CONNECTION_UNAVAILABLE'),(selected['connection_id'],selected['task_default']['reason']))
        with self.f.store() as (store,actor,_):store.delete(actor,first,3)
        selected=self.capability()
        self.assertEqual((second,'AI_CONNECTION_UNAVAILABLE'),(selected['connection_id'],selected['task_default']['reason']))

    def test_selection_authority_revision_and_closed_schema(self):
        connection=self.f.create()
        valid={'CLUSTER':self.choice(connection,'model-a'),'REVIEW':None}
        with self.f.store() as (_,actor,service):
            store=TaskDefaults(service.db)
            self.assertEqual({'revision':0,'defaults':{'CLUSTER':None,'REVIEW':None}},store.read(actor))
            denied=SimpleNamespace(user_id='other',auth_method='sso',access=actor.access)
            with self.assertRaises(ReviewError):store.read(denied)
            with self.assertRaises(ReviewError):store.save(denied,valid,0)
            for invalid in ({'CLUSTER':None}, {'CLUSTER':None,'REVIEW':None,'OTHER':None},
                            {'CLUSTER':{'connection_id':connection,'model_id':'missing'},'REVIEW':None},
                            {'CLUSTER':{'connection_id':connection,'model_id':'model-a','secret':'x'},'REVIEW':None}):
                with self.subTest(invalid=invalid),self.assertRaises(ReviewError):store.save(actor,invalid,0)
            for bad in (True,-1,'0',1.5):
                with self.subTest(revision=bad),self.assertRaises(ReviewError):store.save(actor,valid,bad)
            store.save(actor,valid,0)
            with self.assertRaises(ReviewError) as error:store.save(actor,valid,0)
            self.assertEqual('REVISION_CONFLICT',error.exception.code)
            self.assertEqual({'revision':1,'defaults':valid},store.read(actor))

    def test_explicit_job_selection_is_pinned_across_default_changes(self):
        first=self.f.create(configuration(label='first'))
        second=self.f.create(configuration(label='second'))
        self.f.token(first);self.f.token(second)
        self.set_defaults({'CLUSTER':self.choice(first,'model-b'),'REVIEW':None})
        job=self.c.enqueue(connection_id=first,model_id='model-b')
        self.set_defaults({'CLUSTER':self.choice(second,'model-a'),'REVIEW':None},1)
        result=self.c.run_job()
        self.assertEqual(('COMPLETED',first,'model-b'),(result['status'],result['connection_id'],result['model']))
        self.assertEqual(job['id'],result['id'])
        self.assertEqual('model-b',__import__('json').loads(self.f.wire[0].content)['model'])
        self.assertEqual(second,self.capability()['connection_id'])

    def test_classification_and_group_review_use_their_selected_connections(self):
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        import json
        cluster_connection=self.f.create(configuration(label='classification'))
        review_connection=self.f.create(configuration(label='review'))
        self.f.token(cluster_connection,'synthetic-cluster-token')
        self.f.token(review_connection,'synthetic-review-token')
        self.set_defaults({'CLUSTER':self.choice(cluster_connection,'model-b'),
                           'REVIEW':self.choice(review_connection,'model-a')})
        self.c.enqueue(connection_id=cluster_connection,model_id='model-b')
        self.assertEqual('COMPLETED',self.c.run_job()['status'])
        with self.c.service() as service:
            WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,action='CONFIRM',
                expected_revision=1,request_id=uuid4(),reason='합성 분류 근거 확인')
        self.c.enqueue(group_id='g1',expected_cluster_revision=2,connection_id=review_connection,model_id='model-a')
        self.assertEqual('COMPLETED',self.c.run_job()['status'])
        self.assertEqual(['model-b','model-a'],[json.loads(request.content)['model'] for request in self.f.wire])
        self.assertEqual(['Bearer synthetic-cluster-token','Bearer synthetic-review-token'],
                         [request.headers['authorization'] for request in self.f.wire])


class TaskDefaultsApiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_connections import ConnectionApiTests
        self.f=ConnectionApiTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.actor.user_id='admin'

    def test_admin_route_uses_csrf_strict_revision_and_closed_payload(self):
        from unittest.mock import Mock
        path='/api/admin/ai/task-defaults'
        defaults={'CLUSTER':None,'REVIEW':None}
        store=Mock();store.read.return_value={'revision':0,'defaults':defaults}
        store.save.return_value={'revision':1,'defaults':defaults}
        with patch('oa.sanity_ai_task_defaults.TaskDefaults',return_value=store):
            response=self.f.client.get(path)
            self.assertEqual({'revision':0,'defaults':defaults},response.json())
            denied=self.f.client.post(path,json={'expected_revision':0,'defaults':defaults})
            self.assertIn(denied.status_code,(401,403));store.save.assert_not_called()
            for body in ({'expected_revision':True,'defaults':defaults},
                         {'expected_revision':0,'defaults':defaults,'unknown':1}):
                self.assertEqual(400,self.f.client.post(path,headers=self.f.headers,json=body).status_code)
            response=self.f.client.post(path,headers=self.f.headers,json={'expected_revision':0,'defaults':defaults})
            self.assertEqual({'revision':1,'defaults':defaults},response.json())
            self.assertEqual((self.f.actor,defaults,0),store.save.call_args.args)


class TaskModelVwpAuthorizationTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.f=VwpWorkerTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.c=self.f.c; self.admin,self.schema=self.c.admin,self.c.schema

    def test_vwp_default_remains_selected_when_requesting_user_is_forbidden(self):
        vwp=self.f.connection
        alternative=str(uuid4())
        with self.f.v.store() as (connections,actor,service):
            connections.save(actor,alternative,configuration(),0)
            connections.tokens(connections.get(alternative)).save('other','synthetic-other-token',0)
            TaskDefaults(service.db).save(actor,{'CLUSTER':{'connection_id':vwp,'model_id':'vwp-fast'},'REVIEW':None},0)
        header={'ready':True,'can_review':True,'group_ready':True,'record_count':2}
        with self.c.service() as service, patch('oa.sanity_ai_submissions.header',return_value=header), \
             patch('oa.sanity_ai_monitor.Monitor.latest',return_value=None), \
             patch('oa.sanity_ai_monitor.Monitor.legacy',return_value=None):
            capability=service.capability('other',*self.c.path[1:])
        self.assertEqual((vwp,'vwp-fast',False,'FORBIDDEN',True),
                         (capability['connection_id'],capability['model'],capability['available'],
                          capability['credential_error'],capability['task_default']['applied']))
        self.assertTrue(next(item for item in capability['connections'] if item['id']==alternative)['credential_ready'])
        self.assertEqual([],self.f.v.wire)
