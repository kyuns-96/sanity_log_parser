"""Synthetic checks for transporting optional per-unit AI input declarations."""
import copy
from pathlib import Path
import tempfile
import unittest

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database
from common.sanity_submission import AI_INPUT_KEYS, source_digest, source_unit, validate_plan
from devtools.sanity_submission_fixture import fixture


class ProjectionPlanTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='sanity-ai-projection-plan-')
        self.addCleanup(tmp.cleanup)
        self.plan=fixture(Path(tmp.name))['prepared'].plan

    def test_each_declaration_is_optional_and_bound_to_plan_content(self):
        original=copy.deepcopy(self.plan)
        validate_plan(original)
        for declaration in ({'ai_fields':['code','message']},{'include_raw':False},
                            {'include_details':False},{'ai_fields':['code'],'include_raw':False,'include_details':True}):
            with self.subTest(declaration=declaration):
                changed=copy.deepcopy(original);changed['inventory']['units'][0].update(declaration)
                validate_plan(changed)
                self.assertNotEqual(digest(original),digest(changed))
                self.assertEqual(declaration,{k:changed['inventory']['units'][0][k] for k in AI_INPUT_KEYS if k in changed['inventory']['units'][0]})
        self.assertFalse(any(k in original['inventory']['units'][0] for k in AI_INPUT_KEYS))

    def test_invalid_field_lists_and_nonboolean_flags_are_rejected(self):
        changes=({'ai_fields':[]},{'ai_fields':['code','code']},{'ai_fields':['bad field']},
                 {'ai_fields':['a\nb']},{'ai_fields':['x'*101]},{'ai_fields':[1]},
                 {'ai_fields':['x'+str(i) for i in range(257)]}, {'ai_fields':None},
                 {'include_raw':1},{'include_details':'false'})
        for edit in changes:
            plan=copy.deepcopy(self.plan);plan['inventory']['units'][0].update(edit)
            with self.subTest(edit=edit),self.assertRaises(ReviewError):validate_plan(plan)

    def test_legacy_plan_and_parser_keep_the_complete_source(self):
        from vwp.main import demo_plan,validate_plan as validate_legacy
        from tests.test_parsers import ParserTests
        plan=demo_plan('complete');plan['inventory']['units'][0].update(ai_fields=['code'],include_raw=False)
        validate_legacy(plan)
        plan['inventory']['units'][0]['include_raw']='false'
        with self.assertRaises(ReviewError):validate_legacy(plan)
        helper=ParserTests();helper.setUp();self.addCleanup(helper.doCleanups)
        unit={**helper.unit,'ai_fields':['code'],'include_raw':False,'include_details':False}
        result,records=helper.analyze('SYNTHETIC REPORT v1\nA|P1|M1|C1|INFO|PASS\nEND records=1\n',unit=unit)
        self.assertEqual({k:unit[k] for k in AI_INPUT_KEYS},{k:result[k] for k in AI_INPUT_KEYS})
        self.assertEqual(1,len(records))
        self.assertIn('A|P1|M1|C1|INFO|PASS',records[0]['raw'])
        self.assertIn('object',records[0]['fields'])


class ProjectionTransferTests(unittest.TestCase):
    def setUp(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests
        self.f=TokenlessSubmissionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.admin,self.schema=self.f.admin,self.f.schema

    def test_source_to_report_preserves_declaration_and_full_records(self):
        unit=self.f.collection_input()
        declared={'ai_fields':['code','message'],'include_raw':False,'include_details':False}
        unit.update(declared)
        sid=self.f.submit();self.f.success(self.f.decide(sid));self.f.bridge()
        report=self.f.collection_report()
        self.assertEqual(declared,{k:report[k] for k in AI_INPUT_KEYS})
        with Database('oa') as db:
            workspace=self.f.receiver(db).ws
            from tests.test_scoped_dashboard import OWNER
            context=workspace.report(OWNER,'block-a1',report['version_id'],report['id'])
        self.assertEqual(2,len(context['records']))
        self.assertEqual(['clock fact A','clock fact B'],[r['fields']['message'] for r in context['records']])
        self.assertTrue(all(r['raw'] and r['evidence_id']==r['id'] and r['source_raw_sha256'] for r in context['records']))
        changed={**report,'ai_fields':['message']}
        self.assertNotEqual(source_digest(report),source_digest(changed))

    def test_receiver_rejects_changed_added_and_removed_declarations(self):
        unit=self.f.collection_input();unit.update(ai_fields=['code'],include_raw=False)
        sid=self.f.submit()
        state=self.f.admin_query("SELECT state FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_key=%s",(sid,)).fetchone()[0]
        manifest=state['manifest']
        unit_key=source_unit(manifest['plan']['collection'],unit)
        with Database('oa') as db:
            receiver=self.f.receiver(db)
            for edit in ('changed','missing','added','boolean_type'):
                changed=copy.deepcopy(manifest)
                value=next(v for v in changed['reports'] if v['unit_key']==unit_key)
                original=next(u for u in changed['plan']['inventory']['units']
                              if source_unit(changed['plan']['collection'],u)==unit_key)
                if edit=='changed':value['result']['ai_fields']=['other']
                elif edit=='missing':del value['result']['include_raw']
                elif edit=='added':value['result']['include_details']=False
                else:value['result']['include_raw']=0
                with self.subTest(edit=edit),self.assertRaises(ReviewError) as error:receiver.header(changed,original,value)
                self.assertEqual('SOURCE_SCOPE_MISMATCH',error.exception.code)
