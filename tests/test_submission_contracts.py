"""User invocation, byte integrity, identity boundaries and immutable retries."""
import os
from pathlib import Path
import tempfile
import unittest

from common.sanity_common import ReviewError
from devtools.sanity_submission_fixture import fixture
from vwp.submission_cli import SubmissionClient


class SubmissionContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sanity-submission-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.f = fixture(self.root)
        self.client = SubmissionClient(self.f['client_config'],self.f['provider'],self.f['authority'],state_root=self.root/'state')

    def submit(self):
        return self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)


    def test_hardlinks_symlinks_and_changed_snapshot_are_rejected(self):
        original = self.f['prepared'].reports['reports/post/sample.rpt']
        linked = original.with_name('hardlink.rpt')
        os.link(original,linked)
        with self.assertRaises(ReviewError): self.submit()
        linked.unlink()
        original.rename(linked)
        original.symlink_to(linked)
        with self.assertRaises(ReviewError): self.submit()
        original.unlink(); linked.rename(original)
        value = self.submit()
        ready = self.root/'spool/inbox'/self.f['actor'].key/(value['submission_id']+'.ready')
        (ready/'snapshot/reports/post/sample.rpt').write_text('tampered')
        with self.assertRaises(ReviewError): self.client.retry(value['submission_id'])
