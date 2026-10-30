"""Synthetic strict no-cache settings and exact model request contracts."""
from dataclasses import replace
import json
import unittest

from common.sanity_ai_cache import CacheSettings, validate_wire_cache
from common.sanity_common import ReviewError
from oa.sanity_ai_connections import validate_config
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.test_ai_connections import configuration as oa_configuration
from tests.test_ai_model import environment
from tests.test_vwp_inference import configuration as vwp_configuration


class CacheContractTests(unittest.TestCase):
    def test_only_explicit_true_is_accepted(self):
        setting=CacheSettings.from_config({'no-cache':True})
        self.assertEqual({'no-cache':True},setting.public())
        self.assertEqual({'cache':{'no-cache':True}},setting.body())
        for value in (None,{},False,True,{'no-cache':False},{'no-cache':1},{'no-cache':'true'},
                      {'no-cache':None},{'no-store':True},{'no-cache':True,'no-store':True}):
            with self.subTest(value=value),self.assertRaises(ReviewError) as error:
                CacheSettings.from_config(value)
            self.assertEqual('AI_CACHE_CONFIG',error.exception.code)
        for value in (False,1,None,'true'):
            with self.subTest(no_cache=value),self.assertRaises(ReviewError):CacheSettings(no_cache=value)

    def test_both_connection_types_reject_unknown_cache_values_and_preserve_absence(self):
        for factory in (oa_configuration,vwp_configuration):
            base=factory();self.assertEqual(base,validate_config(base));self.assertNotIn('cache',base)
            self.assertEqual({'no-cache':True},validate_config(factory(cache={'no-cache':True}))['cache'])
            for value in ({},None,{'no-cache':False},{'no-cache':1}, {'no-cache':True,'other':True}):
                with self.subTest(factory=factory.__name__,value=value),self.assertRaises(ReviewError):
                    validate_config(factory(cache=value))

    def test_legacy_request_and_config_fingerprint_are_unchanged(self):
        settings=OASettings.from_env(environment())
        first=OAAdvisor(settings);second=OAAdvisor(replace(settings,cache=None))
        body1,headers1,_=first._request({'synthetic':'record'},'synthetic instruction')
        body2,headers2,_=second._request({'synthetic':'record'},'synthetic instruction')
        self.assertEqual((body1,headers1,first.describe()),(body2,headers2,second.describe()))
        self.assertNotIn('cache',json.loads(body1));self.assertNotIn('cache',first.describe())
        enabled=OAAdvisor(replace(settings,cache=CacheSettings()))
        self.assertNotEqual(first.describe()['config_sha256'],enabled.describe()['config_sha256'])
        self.assertEqual({'no-cache':True},enabled.describe()['cache'])

    def test_chat_and_responses_change_only_top_level_cache(self):
        for api in ('chat_completions','responses'):
            with self.subTest(api=api):
                settings=replace(OASettings.from_env(environment()),api=api)
                legacy=OAAdvisor(settings);enabled=OAAdvisor(replace(settings,cache=CacheSettings()))
                raw,headers,_=legacy._request({'synthetic':'record'},'synthetic instruction')
                changed,changed_headers,_=enabled._request({'synthetic':'record'},'synthetic instruction')
                old,new=json.loads(raw),json.loads(changed)
                self.assertEqual({'no-cache':True},new.pop('cache'))
                self.assertEqual(old,new);self.assertEqual(headers,changed_headers)

    def test_vwp_wire_guard_rejects_extra_or_nonboolean_controls(self):
        validate_wire_cache({});validate_wire_cache({'cache':{'no-cache':True}})
        for value in (None,False,[],{}, {'no-cache':False},{'no-cache':1},{'no-cache':True,'no-store':True}):
            with self.subTest(value=value),self.assertRaises(ReviewError) as error:
                validate_wire_cache({'cache':value})
            self.assertEqual('AI_VWP_SCHEMA',error.exception.code)
