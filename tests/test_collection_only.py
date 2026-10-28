"""Synthetic collection-only contracts; no company reports or model calls."""
import unittest
from unittest.mock import Mock, patch

from common.sanity_common import ReviewError
from common.sanity_parsers import (ParseContext, ParsedRecord, ParserRegistry, ParserError,
    ReportParser, checked_records, collection_only_contract, NOT_PERFORMED)
from oa.submission_receiver import validate_record
from tests import test_parsers as helpers
from tests import test_normalization as normalization
from vwp_bridge.integration.parser_example import SyntheticTextParser
from vwp_bridge.submission_analysis import producer_version


class CollectionTextParser(ReportParser):
    def parse(self, source, context):
        for event in SyntheticTextParser().parse(source, context):
            if isinstance(event, ParsedRecord):
                fields = dict(event.fields)
                fields['message'] = fields.pop('status')
                yield ParsedRecord(fields, event.span, event.json_pointer)
            else:
                yield event


def collecting_registry(version='1'):
    registry = ParserRegistry()
    registry.register('synthetic_collection_v1', CollectionTextParser(), implementation_version=version,
        evaluation='none', item_ids=('gca_constraints',), tool='synthetic',
        tool_versions=('1',), rule_versions=('1',))
    return registry


def prepare_collection(fixture, body=None):
    unit = next(u for u in fixture['prepared'].plan['inventory']['units'] if u['source']['layout']=='POST')
    unit.update(parser='synthetic_collection_v1',evaluation=None)
    body = body if body is not None else (
        'SYNTHETIC REPORT v1\nA|P1|M1|C1|WARNING|clock fact A\n'
        'B|P1|M1|C1|INFO|clock fact B\nEND records=2\n')
    fixture['prepared'].reports[unit['report']].write_bytes(body if isinstance(body,bytes) else body.encode())
    return unit


class CollectionOnlyTests(unittest.TestCase):
    setUp = helpers.ParserTests.setUp
    registry = helpers.ParserTests.registry
    analyze = helpers.ParserTests.analyze
    fields = helpers.ParserTests.fields

    def parser(self, **fields):
        def parse(source, context):
            count = 0
            for line in source.lines():
                count += 1
                values = self.fields(**fields)
                values.pop('status')
                yield ParsedRecord(values,line.span)
            yield helpers.ParseFinished(count,True)
        return helpers.FunctionParser(parse)

    def test_complete_collection_has_no_verdict_and_never_calls_evaluator(self):
        with patch.object(helpers.bridge,'evaluate',side_effect=AssertionError('No verdict generation')):
            result, records = self.analyze('fact A\nfact B\n',self.parser(),mode='none')
        self.assertEqual(('COMPLETE','UNKNOWN',[],0,2),
            (result['parse_status'],result['check_status'],result['blockers'],result['evaluated_count'],result['not_evaluated_count']))
        self.assertEqual('none',result['evaluation_mode'])
        self.assertEqual({'PASS':0,'FAIL':0,'UNKNOWN':2},result['status_counts'])
        self.assertEqual(['fact A','fact B'],[r['raw'] for r in records])
        self.assertTrue(all(r['location']['evaluation_status']==NOT_PERFORMED and not r['reasons'] for r in records))
        self.assertTrue(all('status' not in r['fields'] for r in records))

    def test_source_facts_and_explicit_status_are_not_overwritten(self):
        result, records = self.analyze('SYNTHETIC REPORT v1\nA|P1|M1|C1|ERROR|FAIL\nEND records=1\n',mode='none')
        self.assertEqual('FAIL',records[0]['fields']['status'])
        self.assertEqual('UNKNOWN',records[0]['status'])
        self.assertEqual(1,result['not_evaluated_count'])

    def test_numeric_policy_cannot_use_collection_only_even_empty_or_false(self):
        for policy in ({},{'operator':'le','limit':10,'unit':'ns'},False,'none'):
            with self.subTest(policy=policy), self.assertRaises(ParserError) as error:
                self.analyze('fact\n',self.parser(),unit={**self.unit,'evaluation':policy},mode='none')
            self.assertEqual('PARSER_SCOPE_MISMATCH',error.exception.code)

    def test_direct_stream_api_also_rejects_a_numeric_policy(self):
        self.path.write_text('fact\n')
        callback = Mock(side_effect=AssertionError('Must not evaluate'))
        with self.assertRaises(ParserError):
            list(checked_records(self.parser(),self.path,ParseContext(self.run,{**self.unit,'evaluation':{}}),set(),callback,evaluation='none'))
        callback.assert_not_called()

    def test_wrong_mode_or_malformed_fields_remain_real_errors(self):
        for changes, code in (({'mode':'M2'},'MODE_MISMATCH'),({'severity':'unconfirmed'},'INVALID_SEVERITY'),
                              ({'object':42},'INVALID_OBJECT'),({'code':None},'MISSING_CODE')):
            with self.subTest(changes=changes):
                result, records = self.analyze('fact\n',self.parser(**changes),mode='none')
                self.assertEqual('PARTIAL',result['parse_status'])
                self.assertIn(code,result['blockers'])
                self.assertEqual(0,result['not_evaluated_count'])
                self.assertNotIn('evaluation_status',records[0]['location'])

    def test_empty_truncated_unparsed_encoding_and_wrong_total_stay_blocked(self):
        bodies = ('', 'SYNTHETIC REPORT v1\nEND records=0\n',
                  'SYNTHETIC REPORT v1\nA|P1|M1|C1|INFO|fact\n',
                  'SYNTHETIC REPORT v1\nA|P1|M1|C1|INFO|fact\nEND records=2\n',
                  b'SYNTHETIC REPORT v1\n\xff\nEND records=0\n',
                  'SYNTHETIC REPORT v1\nunknown section\nEND records=0\n')
        for body in bodies:
            with self.subTest(body=body):
                result, _ = self.analyze(body,CollectionTextParser(),mode='none')
                self.assertEqual('PARTIAL',result['parse_status'])
                self.assertTrue(result['blockers'])
                self.assertEqual('UNKNOWN',result['check_status'])

    def test_zero_violation_proof_is_a_fact_and_not_a_pass(self):
        result, records = self.analyze('SYNTHETIC REPORT v1\nCLEAN block|all_checks|M1\nEND records=1\n',
                                     CollectionTextParser(),mode='none')
        self.assertEqual(('COMPLETE','UNKNOWN',1),
            (result['parse_status'],result['check_status'],result['not_evaluated_count']))
        self.assertIn('CLEAN',records[0]['raw'])

    def test_collection_contract_remains_explicit_and_versioned(self):
        unit = {**self.unit,'item_id':'gca_constraints','parser':'synthetic_collection_v1'}
        contract = collecting_registry().contract(self.run,[unit])
        self.assertTrue(collection_only_contract(contract,self.run,unit))
        self.assertNotEqual(contract,collecting_registry('2').contract(self.run,[unit]))
        self.assertEqual('postgresql-source-2.2',producer_version(contract))
        self.assertEqual('postgresql-source-2.1',producer_version([]))
        for changed in ({**unit,'item_id':'other'},{**unit,'evaluation':{}},{**unit,'parser':'numeric_csv_v1'}):
            with self.assertRaises(ParserError):collection_only_contract(contract,self.run,changed)
        for fields in ({'implementation_version':'*'},{'tool_versions':['*']},{'extra':'unrecognized'}):
            with self.assertRaises(ParserError):collection_only_contract([{**contract[0],**fields}],self.run,unit)

    def test_public_marker_cannot_disguise_bad_fields_or_an_existing_verdict(self):
        record = {**normalization.NormalizationTests.record(),'fields':self.fields(),'evaluation_status':NOT_PERFORMED}
        validate_record(record)
        for changes in ({'check_status':'PASS'},{'reasons':['INVALID_ENCODING']},{'evaluation_status':None},
                        {'evaluation_status':'other'},{'fields':{}},{'fields':self.fields(object=42)}):
            with self.subTest(changes=changes),self.assertRaises(ReviewError) as error:
                validate_record({**record,**changes})
            self.assertEqual('INVALID_SOURCE_RECORD',error.exception.code)

    def test_missing_report_keeps_the_collection_policy_but_never_looks_complete(self):
        ev = helpers.RecordingEvidence(self.run)
        result = helpers.bridge.analyze_unit(self.unit,None,self.root,ev,self.registry(self.parser(),evaluation='none'))
        self.assertEqual(('MISSING','none',0),
            (result['parse_status'],result['evaluation_mode'],result['not_evaluated_count']))

    def test_collection_does_not_relax_legacy_review_coverage(self):
        from common.sanity_common import _unit_blockers
        result,_=self.analyze('fact\n',self.parser(),mode='none')
        self.assertEqual('COMPLETE',result['parse_status'])
        self.assertIn('UNEVALUATED_RECORDS',_unit_blockers(result))
        self.assertIn('CHECK_UNKNOWN',_unit_blockers(result))

    def test_missing_identity_is_preserved_without_inventing_fields(self):
        result,records=self.analyze('fact\n',self.parser(object=None,check_point=None),mode='none')
        self.assertEqual(('COMPLETE',1),(result['parse_status'],result['not_evaluated_count']))
        self.assertIsNone(records[0]['fields']['object'])
        self.assertIsNone(records[0]['fields']['check_point'])
