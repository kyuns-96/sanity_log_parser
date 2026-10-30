"""Pure contract tests: rationale diagnostics never contain model content."""
from copy import deepcopy
import json
import unittest

from common.sanity_clustering_advice import validate_clustering_advice
from common.sanity_common import ReviewError
from common.sanity_validation_diagnostics import CODE_FIELDS, MAX_NUMBER, sanitize_validation_failure
from common.sanity_workspace_rationale import validate_content


class RationaleDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.context={'report':{'record_count':2,'records':[{'id':'r0'},{'id':'r1'}]}}
        self.group={'id':'g','label':'합성 그룹','record_ids':['r0','r1']}
        self.rationale={'group_id':'g','reason':'합성 이유', 'evidence_ids':['r0'], 'reference_groups':[]}
        self.advice={'groups':[self.group],'reason':'합성 분류','reference_ids':[], 'group_rationales':[self.rationale]}
        self.case={'id':'c','groups':[{'id':'old-g'}]}

    def fail_code(self, expected, advice, cases=None):
        with self.assertRaises(ReviewError) as raised:
            validate_clustering_advice(advice,self.context,cases or [],require_rationales=True)
        self.assertEqual('INVALID_RATIONALE',raised.exception.code)
        detail=raised.exception.validation_failure
        self.assertEqual(expected,detail['code'])
        self.assertEqual(CODE_FIELDS[expected],detail['field'])
        self.assertEqual(detail,sanitize_validation_failure(detail))
        encoded=json.dumps(detail,ensure_ascii=False)
        for secret in ('합성','SECRET_SENTINEL','r0','r1','old-g'):
            self.assertNotIn(secret,encoded)
        return detail

    def test_empty_cases_and_empty_references_are_valid_and_strict(self):
        result=validate_clustering_advice(self.advice,self.context,[],require_rationales=True)
        self.assertEqual([],result['reference_ids'])
        invalid=deepcopy(self.advice)
        invalid['group_rationales'][0]['reference_groups']=[{'case_id':'SECRET_SENTINEL','group_id':'x','reason':'invented'}]
        self.assertEqual(0,self.fail_code('RATIONALE_REFERENCE_CASE_UNKNOWN',invalid)['group_index'])
        invalid=deepcopy(self.advice);invalid['reference_ids']=['fake']
        with self.assertRaises(ReviewError) as error:
            validate_clustering_advice(invalid,self.context,[],require_rationales=True)
        self.assertEqual('INVALID_ADVICE',error.exception.code)

    def test_missing_count_shape_and_group_id(self):
        missing=deepcopy(self.advice);del missing['group_rationales']
        self.assertEqual({'schema_version':1,'code':'RATIONALE_MISSING','field':'group_rationales'},
                         self.fail_code('RATIONALE_MISSING',missing))
        wrong=deepcopy(self.advice);wrong['group_rationales']=[]
        self.assertEqual(0,self.fail_code('RATIONALE_COUNT',wrong)['actual_count'])
        wrong=deepcopy(self.advice);wrong['group_rationales']=[{'group_id':'SECRET_SENTINEL'}]
        self.assertEqual(0,self.fail_code('RATIONALE_ENTRY_SHAPE',wrong)['group_index'])
        wrong=deepcopy(self.advice);wrong['group_rationales'][0]['group_id']='SECRET_SENTINEL'
        self.assertEqual(0,self.fail_code('RATIONALE_GROUP_ID',wrong)['group_index'])

    def test_reason_and_evidence_failure_subtypes(self):
        for value,code in ((None,'RATIONALE_EVIDENCE_TYPE'),([], 'RATIONALE_EVIDENCE_COUNT'),
                           (['r0']*21,'RATIONALE_EVIDENCE_COUNT'),([True], 'RATIONALE_EVIDENCE_ITEM_TYPE'),
                           (['r0','r0'],'RATIONALE_EVIDENCE_DUPLICATE'),(['SECRET_SENTINEL'],'RATIONALE_EVIDENCE_OUTSIDE_GROUP')):
            with self.subTest(code=code,value=value):
                wrong=deepcopy(self.advice);wrong['group_rationales'][0]['evidence_ids']=value
                self.fail_code(code,wrong)
        wrong=deepcopy(self.advice);wrong['group_rationales'][0]['reason']='  '+('SECRET_SENTINEL'*155)+'  '
        self.assertEqual(len('SECRET_SENTINEL'*155),self.fail_code('RATIONALE_REASON',wrong)['actual_length'])
        wrong['group_rationales'][0]['reason']=None
        self.assertNotIn('actual_length',self.fail_code('RATIONALE_REASON',wrong))

    def test_reference_failure_subtypes(self):
        good={'case_id':'c','group_id':'old-g','reason':'근거 비교'}
        samples=[(None,'RATIONALE_REFERENCE_TYPE'),([{}]*21,'RATIONALE_REFERENCE_COUNT'),
                 ([{'secret':'SECRET_SENTINEL'}],'RATIONALE_REFERENCE_ENTRY_SHAPE'),
                 ([{**good,'reason':None}],'RATIONALE_REFERENCE_ENTRY_VALUE'),
                 ([{**good,'case_id':'SECRET_SENTINEL'}],'RATIONALE_REFERENCE_CASE_UNKNOWN'),
                 ([{**good,'group_id':'SECRET_SENTINEL'}],'RATIONALE_REFERENCE_GROUP_UNKNOWN'),
                 ([good,good],'RATIONALE_REFERENCE_DUPLICATE')]
        for refs,code in samples:
            with self.subTest(code=code):
                wrong=deepcopy(self.advice);wrong['group_rationales'][0]['reference_groups']=refs
                self.fail_code(code,wrong,[self.case])
        wrong=deepcopy(self.advice);wrong['reference_ids']=['c']
        self.fail_code('RATIONALE_REFERENCES_MISMATCH',wrong,[self.case])

    def test_legacy_human_validator_has_same_error_without_diagnostic(self):
        with self.assertRaises(ReviewError) as error:
            validate_content({'reason':'test','evidence_ids':[],'reference_groups':[]},self.group,[])
        self.assertEqual('INVALID_RATIONALE',error.exception.code)
        self.assertFalse(hasattr(error.exception,'validation_failure'))

    def test_sanitizer_rejects_extra_data_and_malformed_values(self):
        base={'schema_version':1,'code':'RATIONALE_REASON','field':'reason','group_index':1,'actual_length':0}
        result=sanitize_validation_failure(base)
        self.assertEqual(base,result);self.assertIsNot(base,result)
        for invalid in (None,[],{**base,'secret':'SECRET_SENTINEL'}, {**base,'schema_version':True},
                        {**base,'code':'SECRET_SENTINEL'}, {**base,'code':'UNKNOWN','field':None},
                        {**base,'field':'evidence_ids'},
                        {**base,'group_index':True}, {**base,'actual_count':-1},
                        {**base,'actual_length':MAX_NUMBER+1}, {**base,'actual_count':'12'}):
            with self.subTest(value=invalid):self.assertIsNone(sanitize_validation_failure(invalid))
