"""Synthetic no-cache transport, immutable job pins and retry boundaries."""
from dataclasses import replace
from datetime import timedelta
from collections import Counter
import hashlib
import json
import unittest
from unittest.mock import patch

import httpx

from common.sanity_ai_cache import CacheSettings
from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError, canonical_json
from common.sanity_integration_examples import clustering_example
from common.sanity_vwp_ai_http import invoke
from oa.sanity_ai_connections import validate_config
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.support import PgTestCase
from tests.test_ai_model import chat, environment
from tests.test_vwp_inference import PERSONAL_TOKEN, body, configuration, profile


class CacheTransportTests(unittest.TestCase):
    def test_oa_and_vwp_registration_accept_only_exact_cache_config(self):
        from tests.test_ai_connections import configuration as oa_configuration
        for factory in (oa_configuration,configuration):
            self.assertNotIn('cache',validate_config(factory()))
            self.assertEqual({'no-cache':True},validate_config(factory(cache={'no-cache':True}))['cache'])
            for invalid in (None,True,False,{}, {'no-cache':1}, {'no-cache':False},
                            {'no-cache':True,'ttl':1}, {'arbitrary':True}):
                with self.subTest(factory=factory.__name__,invalid=invalid),self.assertRaises(ReviewError):
                    validate_config(factory(cache=invalid))

    def test_legacy_request_and_pin_are_unchanged_when_cache_is_absent(self):
        settings=OASettings.from_env(environment())
        old=OAAdvisor(settings);explicit=OAAdvisor(replace(settings,cache=None))
        old_request=old._request({'synthetic':'input'},'synthetic instruction')[0]
        new_request=explicit._request({'synthetic':'input'},'synthetic instruction')[0]
        self.assertEqual(old_request,new_request);self.assertEqual(old.describe(),explicit.describe())
        self.assertNotIn('cache',json.loads(old_request));self.assertNotIn('cache',old.describe())
        enabled=OAAdvisor(replace(settings,cache=CacheSettings()))
        self.assertNotEqual(old.describe()['config_sha256'],enabled.describe()['config_sha256'])

    def test_real_oa_chat_and_responses_requests_include_cache_and_hash_actual_bytes(self):
        example=clustering_example()
        for api in ('chat_completions','responses'):
            seen=[]
            def respond(request):
                seen.append(request)
                if api=='chat_completions':return httpx.Response(200,json=chat(example['reply']))
                return httpx.Response(200,json={'model':'synthetic-model-v1','status':'completed','output':[
                    {'type':'message','role':'assistant','status':'completed',
                     'content':[{'type':'output_text','text':canonical_json(example['reply']).decode()}]}]})
            settings=replace(OASettings.from_env(environment()),api=api,cache=CacheSettings())
            adapter=OAAdvisor(settings,transport=httpx.MockTransport(respond))
            result=adapter.cluster(example['context'],example['cases'],example['identity_fields'])
            self.assertEqual(1,len(seen));self.assertEqual({'no-cache':True},json.loads(seen[0].content)['cache'])
            self.assertEqual(hashlib.sha256(seen[0].content).hexdigest(),result['metadata']['input_sha256'])

    def test_cache_bytes_count_towards_oa_and_vwp_input_limits_before_http(self):
        example=clustering_example();seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,json=chat(example['reply']))
        base=OASettings.from_env(environment())
        OAAdvisor(base,transport=httpx.MockTransport(respond)).cluster(
            example['context'],example['cases'],example['identity_fields'])
        boundary=len(seen[0].content);seen.clear()
        enabled=replace(base,cache=CacheSettings(),max_input_bytes=boundary)
        with self.assertRaises(ReviewError) as error:
            OAAdvisor(enabled,transport=httpx.MockTransport(respond)).cluster(
                example['context'],example['cases'],example['identity_fields'])
        self.assertEqual('AI_INPUT_LIMIT',error.exception.code);self.assertEqual([],seen)

        legacy=canonical_json(body());private={**profile(max_input_bytes=len(legacy)),'token':PERSONAL_TOKEN}
        vwp_seen=[]
        def vwp_response(request):
            vwp_seen.append(request)
            return httpx.Response(200,json={'model':'synthetic-model'})
        invoke(legacy,private,timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(vwp_response))
        self.assertEqual(1,len(vwp_seen));vwp_seen.clear()
        with self.assertRaises(ReviewError) as error:
            invoke(canonical_json(body(cache={'no-cache':True})),private,timeout=30,max_output_bytes=4096,
                   transport=httpx.MockTransport(vwp_response))
        self.assertEqual('AI_INPUT_LIMIT',error.exception.code);self.assertEqual([],vwp_seen)

    def test_vwp_cache_body_is_strict_for_both_api_types(self):
        for api in ('chat_completions','responses'):
            base=body() if api=='chat_completions' else {'model':'synthetic-model','stream':False,
                'input':[{'role':'system','content':'Return JSON.'},{'role':'user','content':'synthetic'}], 'store':False}
            private={**profile(api=api),'token':PERSONAL_TOKEN}
            for enabled in (False,True):
                raw=canonical_json({**base,**({'cache':{'no-cache':True}} if enabled else {})});seen=[]
                def respond(request):
                    seen.append(request)
                    return httpx.Response(200,json={'model':'synthetic-model'})
                invoke(raw,private,timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(respond))
                self.assertEqual(raw,seen[0].content)
            for cache in (None,False,True,0,1,'true',[],{}, {'no-cache':False}, {'no-cache':1},
                          {'no-cache':'true'},{'no-cache':True,'ttl':1},{'enabled':True}):
                with self.subTest(api=api,cache=cache),self.assertRaises(ReviewError):
                    invoke(canonical_json({**base,'cache':cache}),private,timeout=30,max_output_bytes=4096,
                           transport=httpx.MockTransport(lambda _:self.fail('invalid cache reached HTTP')))
            with self.assertRaises(ReviewError):
                invoke(canonical_json({**base,'cache':{'no-cache':True},'arbitrary':True}),private,
                       timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(lambda _:self.fail('unexpected HTTP')))

    def test_vwp_connect_only_fallback_preserves_cache_and_exact_body(self):
        raw=canonical_json(body(cache={'no-cache':True}));seen=[]
        private={**profile(fallback_endpoint='https://backup.example.test/chat/completions'),'token':PERSONAL_TOKEN}
        def respond(request):
            seen.append(request)
            if len(seen)==1:raise httpx.ConnectError('synthetic failure',request=request)
            return httpx.Response(200,json={'model':'synthetic-model'})
        _,routing=invoke(raw,private,timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(respond))
        self.assertEqual([raw,raw],[request.content for request in seen])
        self.assertEqual('fallback',routing['endpoint'])

    def test_oa_connect_only_fallback_preserves_cache_and_exact_body(self):
        example=clustering_example();seen=[]
        def respond(request):
            seen.append(request)
            if len(seen)==1:raise httpx.ConnectError('synthetic failure',request=request)
            return httpx.Response(200,json=chat(example['reply']))
        settings=replace(OASettings.from_env(environment()),cache=CacheSettings(),
                         fallback_endpoint='https://backup.example.test/v1/chat/completions')
        result=OAAdvisor(settings,transport=httpx.MockTransport(respond)).cluster(
            example['context'],example['cases'],example['identity_fields'])
        self.assertEqual(2,len(seen));self.assertEqual(seen[0].content,seen[1].content)
        self.assertEqual({'no-cache':True},json.loads(seen[1].content)['cache'])
        self.assertEqual(hashlib.sha256(seen[1].content).hexdigest(),result['metadata']['input_sha256'])


class VwpCacheJobTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.f=VwpWorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c=self.f.c;self.admin,self.schema=self.c.admin,self.c.schema

    def cached_connection(self,**changes):
        self.f.connection=self.f.v.create(configuration(cache={'no-cache':True},**changes))
        return self.f.connection

    def test_vwp_job_sends_cache_and_persists_exact_wire_hash(self):
        self.cached_connection();job=self.f.request();self.f.prepare()
        result=self.f.v.finish();self.assertEqual('COMPLETED',result['status'],result)
        request=self.f.v.wire[0]
        self.assertEqual({'no-cache':True},json.loads(request.content)['cache'])
        self.assertEqual('Bearer '+PERSONAL_TOKEN,request.headers['authorization'])
        with self.c.service() as service:
            metadata=service._load(job['id'])['data']['result']['metadata']
            self.assertEqual(hashlib.sha256(request.content).hexdigest(),metadata['input_sha256'])

    def test_cache_setting_change_stales_prepared_job_without_invalidating_personal_key(self):
        connection=self.cached_connection();self.f.request();self.f.prepare()
        with self.f.v.store() as (store,actor,_):
            old=store.get(connection);new={k:v for k,v in old['config'].items() if k!='cache'}
            store.save(actor,connection,new,old['revision'])
        with self.f.v.store() as (store,_,_):
            self.assertEqual((PERSONAL_TOKEN,1),store.tokens(store.get(connection)).get_token('owner'))
        result=self.f.prepare()
        self.assertEqual(('STALE','AI_CONFIG_CHANGED'),(result['status'],result['error']))
        self.assertEqual([],self.f.v.wire)

    def test_source_change_during_cached_call_blocks_result_publication(self):
        from common.sanity_db import json_value
        self.cached_connection();self.f.request();self.f.prepare()
        def change(_request):
            self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{rule}}',%s) WHERE record_type='WS_REPORT'",
                               (json_value('synthetic changed source'),))
        self.c.callback=change
        result=self.f.prepare();self.assertEqual('STALE',result['status'],result)
        self.assertEqual({'no-cache':True},json.loads(self.f.v.wire[0].content)['cache'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_credential_rotation_during_cached_call_blocks_result_publication(self):
        connection=self.cached_connection();self.f.request();self.f.prepare()
        def rotate(_request):
            with self.f.v.store() as (store,_,_):
                store.tokens(store.get(connection)).save('owner','synthetic-rotated-key',1)
        self.c.callback=rotate
        result=self.f.prepare()
        self.assertEqual(('STALE','AI_CREDENTIAL_CHANGED'),(result['status'],result['error']))
        self.assertEqual({'no-cache':True},json.loads(self.f.v.wire[0].content)['cache'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_cached_ambiguous_call_remains_unknown_and_never_auto_retries(self):
        self.cached_connection();self.f.request();self.f.prepare()
        def timeout(request):raise httpx.ReadTimeout('synthetic unknown',request=request)
        self.c.callback=timeout
        result=self.f.prepare();self.assertEqual('UNKNOWN',result['status'],result)
        self.assertEqual({'no-cache':True},json.loads(self.f.v.wire[0].content)['cache'])
        self.assertIsNone(self.f.prepare());self.assertEqual(1,len(self.f.v.wire))

    def test_cached_batch_safe_retry_retains_exact_body_and_checkpoint_hash(self):
        from oa.sanity_ai_batch_jobs import Ledger
        from oa.sanity_ai_jobs import _now
        from tests.test_ai_batches import batch_settings
        self.cached_connection(batch=batch_settings(max_records=1).public())
        job=self.f.request();self.f.prepare()
        def refused(request):raise httpx.ConnectError('synthetic unsent',request=request)
        self.c.callback=refused
        waiting=self.f.prepare();self.assertEqual('RUNNING',waiting['status'])
        self.assertEqual(1,waiting['progress']['retry_count'])
        self.c.callback=None;at=_now()+timedelta(seconds=400)
        with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):self.c.run_job(now=at)
        result=self.f.v.finish();self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(4,len(self.f.v.wire))
        self.assertEqual(self.f.v.wire[0].content,self.f.v.wire[1].content)
        self.assertTrue(all(json.loads(request.content)['cache']=={'no-cache':True} for request in self.f.v.wire))
        expected=hashlib.sha256(self.f.v.wire[0].content).hexdigest()
        with self.c.service() as service:
            ledger=Ledger(service,service._load(job['id']))
            first=[step for step,_ in ledger.rows('step.') if step['status']=='RESERVED'
                   and step['state']['round']==0 and step['state']['index']==0]
            self.assertEqual(2,len(first));self.assertEqual([expected,expected],[step['input_sha256'] for step in first])
            self.assertIsNotNone(ledger.audit())

    def test_parallel_cached_leaves_and_merge_preserve_wire_hashes(self):
        from oa.sanity_ai_parallel_ledger import ParallelLedger
        from tests.test_ai_batches import batch_settings
        batch=replace(batch_settings(max_records=1),max_concurrency=2)
        self.cached_connection(batch=batch.public())
        job=self.f.request();self.f.prepare()
        result=self.f.v.finish();self.assertEqual('COMPLETED',result['status'],result)
        requests=self.f.v.wire;self.assertEqual(3,len(requests))
        self.assertTrue(all(json.loads(request.content)['cache']=={'no-cache':True} for request in requests))
        wire_hashes=Counter(hashlib.sha256(request.content).hexdigest() for request in requests)
        with self.c.service() as service:
            ledger=ParallelLedger(service,service._load(job['id']))
            outcomes=[row for row,_ in ledger.rows('call.') if row.get('kind')=='OUTCOME' and row.get('status')=='SUCCESS']
            self.assertEqual(3,len(outcomes))
            stored_hashes=Counter(ledger.get(row['result']['key'],row['result'])[0]['metadata']['input_sha256']
                                  for row in outcomes)
            self.assertEqual(wire_hashes,stored_hashes)
            self.assertIsNotNone(ledger.audit())
