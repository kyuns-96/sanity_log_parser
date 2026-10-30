"""Synthetic reasoning options: strict settings, wire shape, pins and privacy."""
from dataclasses import replace
import json
import unittest

import httpx

from common.sanity_ai_reasoning import ReasoningSettings
from common.sanity_common import ReviewError, canonical_json
from common.sanity_integration_examples import clustering_example
from common.sanity_vwp_ai_http import invoke
from oa.sanity_ai_connections import validate_config
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.support import PgTestCase
from tests.test_ai_connections import configuration as oa_configuration
from tests.test_ai_model import chat, environment
from tests.test_vwp_inference import PERSONAL_TOKEN, body, configuration as vwp_configuration, profile


def setting(mode='default',protocol='openai',effort='default'):
    return {'mode':mode,'protocol':protocol,'effort':effort}


class ReasoningSettingsTests(unittest.TestCase):
    def test_valid_wire_contracts_and_default_no_wire(self):
        for config,api,expected in (
            (setting(), 'chat_completions', {}),
            (setting(protocol='chat_template'), 'chat_completions', {}),
            (setting('on','openai','minimal'), 'chat_completions', {'reasoning_effort':'minimal'}),
            (setting('on','openai','high'), 'responses', {'reasoning':{'effort':'high'}}),
            (setting('off'), 'chat_completions', {'reasoning_effort':'none'}),
            (setting('off'), 'responses', {'reasoning':{'effort':'none'}}),
            (setting('on','chat_template'), 'chat_completions', {'chat_template_kwargs':{'enable_thinking':True}}),
            (setting('off','chat_template'), 'chat_completions', {'chat_template_kwargs':{'enable_thinking':False}}),
        ):
            with self.subTest(config=config,api=api):
                value=ReasoningSettings.from_config(config)
                value.validate_api(api)
                self.assertEqual(config,value.public())
                self.assertEqual(expected,value.body(api))
        for effort in ('minimal','low','medium','high','xhigh','max'):
            value=ReasoningSettings.from_config(setting('on','openai',effort))
            self.assertEqual({'reasoning_effort':effort},value.body('chat_completions'))

    def test_invalid_or_ambiguous_settings_are_rejected_at_both_registration_boundaries(self):
        invalid=(None,{}, {'mode':'on'}, {**setting(),'extra':True}, setting('on'),setting('off',effort='high'),
                 setting(effort='low'),setting('on','chat_template','low'),setting('unknown'),
                 setting(protocol='other'),setting(effort='none'),setting(mode=True),setting(effort=4))
        for value in invalid:
            for factory in (oa_configuration,vwp_configuration):
                with self.subTest(value=value,factory=factory.__name__),self.assertRaises(ReviewError):
                    validate_config(factory(reasoning=value))
        for factory in (oa_configuration,vwp_configuration):
            with self.assertRaises(ReviewError):
                validate_config(factory(api='responses',reasoning=setting('on','chat_template')))

    def test_settings_are_optional_in_old_connections_and_valid_on_both_sites(self):
        for factory in (oa_configuration,vwp_configuration):
            legacy=factory();self.assertNotIn('reasoning',validate_config(legacy))
            for config in (setting(),setting('on','openai','medium'),setting('off','chat_template')):
                self.assertEqual(config,validate_config(factory(reasoning=config))['reasoning'])


class ReasoningWireTests(unittest.TestCase):
    def advisor(self,api='chat_completions',reasoning=None):
        settings=replace(OASettings.from_env(environment()),api=api,reasoning=reasoning)
        return OAAdvisor(settings,transport=httpx.MockTransport(lambda _:self.fail('unexpected HTTP')))

    def test_legacy_body_and_config_hash_are_unchanged_and_new_values_are_pinned(self):
        original=OAAdvisor(OASettings.from_env(environment()))
        explicit=self.advisor(reasoning=None)
        raw,_,_=original._request({'synthetic':'payload'},'synthetic instruction')
        same,_,_=explicit._request({'synthetic':'payload'},'synthetic instruction')
        self.assertEqual(raw,same);self.assertEqual(original.describe(),explicit.describe())
        self.assertNotIn('reasoning',original.describe())
        for key in ('reasoning','reasoning_effort','chat_template_kwargs'):self.assertNotIn(key,json.loads(raw))
        high=self.advisor(reasoning=ReasoningSettings.from_config(setting('on','openai','high')))
        low=self.advisor(reasoning=ReasoningSettings.from_config(setting('on','openai','low')))
        self.assertNotEqual(original.describe()['config_sha256'],high.describe()['config_sha256'])
        self.assertNotEqual(high.describe()['config_sha256'],low.describe()['config_sha256'])

    def test_real_oa_request_has_only_the_selected_api_specific_reasoning_fields(self):
        for api,config,extra in (
            ('chat_completions',setting('on','openai','high'),{'reasoning_effort':'high'}),
            ('chat_completions',setting('off'),{'reasoning_effort':'none'}),
            ('chat_completions',setting('off','chat_template'),{'chat_template_kwargs':{'enable_thinking':False}}),
            ('responses',setting('on','openai','medium'),{'reasoning':{'effort':'medium'}}),
            ('responses',setting('off'),{'reasoning':{'effort':'none'}}),
        ):
            with self.subTest(api=api,config=config):
                adapter=self.advisor(api,ReasoningSettings.from_config(config))
                raw,_,_=adapter._request({'synthetic':'payload'},'synthetic instruction')
                request=json.loads(raw)
                self.assertEqual(extra,{key:request[key] for key in ('reasoning','reasoning_effort','chat_template_kwargs') if key in request})
                self.assertFalse(request['stream'])
                if api=='responses':self.assertIs(request['store'],False)

    def test_vwp_strict_transport_accepts_only_exact_supported_shapes(self):
        for api,extra in (
            ('chat_completions',{'reasoning_effort':'max'}),('chat_completions',{'reasoning_effort':'none'}),
            ('chat_completions',{'chat_template_kwargs':{'enable_thinking':False}}),
            ('responses',{'reasoning':{'effort':'high'}}),('responses',{'reasoning':{'effort':'none'}}),
        ):
            with self.subTest(api=api,extra=extra):
                base=body() if api=='chat_completions' else {'model':'synthetic-model','stream':False,
                    'input':[{'role':'system','content':'Return JSON only.'},{'role':'user','content':'Synthetic log.'}],'store':False}
                raw=canonical_json({**base,**extra});seen=[]
                private={**profile(api=api),'token':PERSONAL_TOKEN}
                def respond(request):
                    seen.append(request)
                    return httpx.Response(200,json={'model':'synthetic-model'})
                invoke(raw,private,timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(respond))
                self.assertEqual(raw,seen[0].content)
        invalid=( {'reasoning_effort':'unlimited'}, {'reasoning_effort':True},
                  {'reasoning_effort':'high','chat_template_kwargs':{'enable_thinking':True}},
                  {'chat_template_kwargs':{'enable_thinking':'true'}},
                  {'chat_template_kwargs':{'enable_thinking':True,'arbitrary':'value'}},
                  {'reasoning':{'effort':'high'}}, {'reasoning_effort':'default'})
        for extra in invalid:
            seen=[]
            with self.subTest(extra=extra),self.assertRaises(ReviewError):
                invoke(canonical_json(body(**extra)),{**profile(),'token':PERSONAL_TOKEN},timeout=30,
                       max_output_bytes=4096,transport=httpx.MockTransport(lambda request:seen.append(request)))
            self.assertEqual([],seen)
        base={'model':'synthetic-model','stream':False,'input':[{'role':'system','content':'x'},
              {'role':'user','content':'y'}],'store':False}
        for extra in ({'reasoning':{'effort':'high','summary':'auto'}},{'reasoning':{'effort':True}},
                      {'reasoning_effort':'high'},{'chat_template_kwargs':{'enable_thinking':True}}):
            with self.subTest(extra=extra),self.assertRaises(ReviewError):
                invoke(canonical_json({**base,**extra}),{**profile(api='responses'),'token':PERSONAL_TOKEN},timeout=30,
                       max_output_bytes=4096,transport=httpx.MockTransport(lambda _:self.fail('unexpected HTTP')))

    def test_hidden_thought_is_not_returned_in_advice_or_metadata(self):
        example=clustering_example();marker='SYNTHETIC_HIDDEN_THOUGHT_NOT_A_RESULT'
        for api in ('chat_completions','responses'):
            def respond(_request):
                if api=='chat_completions':
                    envelope=chat(example['reply']);envelope['choices'][0]['message']['reasoning_content']=marker
                else:
                    envelope={'model':'synthetic-model-v1','status':'completed','output':[
                        {'type':'reasoning','summary':[{'type':'summary_text','text':marker}]},
                        {'type':'message','role':'assistant','status':'completed',
                         'content':[{'type':'output_text','text':canonical_json(example['reply']).decode()}]}]}
                return httpx.Response(200,json=envelope)
            config=replace(OASettings.from_env(environment()),api=api,
                           reasoning=ReasoningSettings.from_config(setting('on','openai','high')))
            result=OAAdvisor(config,transport=httpx.MockTransport(respond)).cluster(example['context'],example['cases'],example['identity_fields'])
            self.assertEqual(example['reply'],result['advice'])
            self.assertNotIn(marker,json.dumps(result,ensure_ascii=False))


class ReasoningJobTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.f=AIJobDatabaseTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.admin,self.schema=self.f.admin,self.f.schema

    def test_changed_reasoning_invalidates_queued_job_without_call(self):
        high=ReasoningSettings.from_config(setting('on','openai','high'))
        low=ReasoningSettings.from_config(setting('on','openai','low'))
        self.f.advisor=OAAdvisor(replace(self.f.advisor.settings,reasoning=high),transport=self.f.advisor.transport)
        self.f.enqueue()
        self.f.advisor=OAAdvisor(replace(self.f.advisor.settings,reasoning=low),transport=self.f.advisor.transport)
        result=self.f.run_job()
        self.assertEqual(('STALE','AI_CONFIG_CHANGED'),(result['status'],result['error']))
        self.assertEqual([],self.f.requests)

    def test_hidden_thought_is_not_persisted_in_job_or_published_proposal(self):
        marker='SYNTHETIC_HIDDEN_THOUGHT_NEVER_STORED'
        def respond(request):
            value=self.f.respond(request).json();value['choices'][0]['message']['reasoning_content']=marker
            return httpx.Response(200,json=value)
        self.f.advisor=OAAdvisor(replace(self.f.advisor.settings,reasoning=ReasoningSettings.from_config(setting('on','openai','high'))),
                                 transport=httpx.MockTransport(respond))
        self.f.enqueue();result=self.f.run_job();self.assertEqual('COMPLETED',result['status'],result)
        rows=self.f.admin_query("SELECT payload FROM {oa}").fetchall()
        self.assertTrue(rows);self.assertNotIn(marker,json.dumps(rows,ensure_ascii=False,default=str))


class VwpReasoningJobTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.f=VwpWorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.admin,self.schema=self.f.c.admin,self.f.c.schema

    def test_registered_vwp_reasoning_reaches_the_actual_model_request(self):
        config=vwp_configuration(reasoning=setting('on','chat_template'))
        self.f.connection=self.f.v.create(config)
        self.f.request();self.f.prepare();result=self.f.v.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(1,len(self.f.v.wire))
        sent=json.loads(self.f.v.wire[0].content)
        self.assertEqual({'enable_thinking':True},sent['chat_template_kwargs'])
        self.assertNotIn('reasoning_effort',sent)

    def test_vwp_reasoning_change_stales_waiting_job_without_call(self):
        config=vwp_configuration(reasoning=setting('on','openai','high'))
        self.f.connection=self.f.v.create(config)
        self.f.request()
        with self.f.v.store() as (store,actor,_):
            store.save(actor,self.f.connection,{**config,'reasoning':setting('on','openai','low')},1)
        result=self.f.prepare()
        self.assertEqual(('STALE','AI_CONFIG_CHANGED'),(result['status'],result['error']))
        self.assertEqual([],self.f.v.wire)
