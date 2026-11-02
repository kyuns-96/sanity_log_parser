"""New submission identities preserve source facts and bind every run header."""
from copy import deepcopy
import unittest
from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_submission import source_execution,source_unit,validate_run_binding


class SubmissionRunTests(unittest.TestCase):
    def test_new_submissions_share_one_source_unit_but_have_distinct_run_identities(self):
        collection={'source_namespace':'synthetic','project_key':'p','block_key':'b'}
        unit={'item_id':'synthetic','source':{'net_ver':'N1','revision':'R1','layout':'PRE','eco':None,
              'mode':'M1','execution_ref':'real-source-reference','execution_order':7}}
        before=deepcopy(unit);a,b=str(uuid4()),str(uuid4())
        stable=source_unit(collection,unit)
        self.assertNotEqual(source_execution(collection,unit,a),source_execution(collection,unit,b))
        self.assertEqual(source_execution(collection,unit,a),source_execution(collection,unit,a))
        self.assertNotEqual(source_execution(collection,unit),source_execution(collection,unit,a))
        self.assertEqual(stable,source_unit(collection,unit));self.assertEqual(before,unit)

    def test_every_new_header_must_match_envelope_submission_and_timestamp(self):
        packet={'submission_id':str(uuid4()),'submitted_at':'2026-11-02T00:00:00.000001+00:00'}
        validate_run_binding(dict(packet),packet)
        bad=({}, {'submission_id':packet['submission_id']}, {'submitted_at':packet['submitted_at']},
             {**packet,'submission_id':str(uuid4())}, {**packet,'submitted_at':'2026-11-02T00:00:00.000002+00:00'})
        for header in bad:
            with self.subTest(header=header),self.assertRaises(ReviewError) as error:
                validate_run_binding(header,packet)
            self.assertEqual('SOURCE_SCOPE_MISMATCH',error.exception.code)

    def test_legacy_envelope_accepts_only_legacy_run_headers(self):
        packet={'submission_id':str(uuid4())};validate_run_binding({},packet)
        with self.assertRaises(ReviewError):
            validate_run_binding({**packet,'submitted_at':'2026-11-02T00:00:00.000001+00:00'},packet)
