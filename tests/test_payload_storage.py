"""Private payload corruption checks and shared synthetic evidence."""
import copy
import hashlib
import unittest

from common.sanity_common import ReviewError, digest
from common.sanity_payload_storage import MARKER, _restore, encode_payload


def payload():
    return {'record_id': 'r-'+digest('synthetic-record'), 'record_index': 1,
            'check_status': 'UNKNOWN', 'finding_group_id': None,
            'fields': {'detail': 'synthetic evidence detail ' * 300},
            'reasons': ['SYNTHETIC-NO-CRITERION'], 'json_pointer': '/results/0'}


class PayloadCodecTests(unittest.TestCase):


    def test_corrupt_hash_gzip_and_indexed_header_fail_closed(self):
        header, packed = encode_payload('EVIDENCE', payload())
        variants = []
        wrong_hash = copy.deepcopy(header); wrong_hash[MARKER]['sha256'] = '0'*64
        variants.append((wrong_hash, packed))
        wrong_index = copy.deepcopy(header); wrong_index['record_index'] = 2
        variants.append((wrong_index, packed))
        wrong_type = copy.deepcopy(header); wrong_type['record_index'] = True
        variants.append((wrong_type, packed))
        truncated = packed[:-8]
        wrong_gzip = copy.deepcopy(header); wrong_gzip[MARKER]['sha256'] = hashlib.sha256(truncated).hexdigest()
        variants.append((wrong_gzip, truncated))
        for changed, blob in variants:
            with self.subTest(header=changed), self.assertRaises(ReviewError) as error:
                _restore('EVIDENCE', changed, blob)[0]
            self.assertEqual('STORED_PAYLOAD_CORRUPT', error.exception.code)
