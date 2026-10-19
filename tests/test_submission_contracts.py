"""User invocation, byte integrity, identity boundaries and immutable retries."""
import copy
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from common.sanity_common import ReviewError, digest
from common.sanity_submission import SubmissionInput, scope_digest, source_execution, source_unit, validate_plan
from common.sanity_submission_files import read_json_file, verify_bundle, write_json_file
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

    def test_scope_approval_ignores_file_arrival_and_executions_but_binds_expected_units(self):
        plan = copy.deepcopy(self.f['prepared'].plan)
        expected = scope_digest(plan)
        plan['inventory']['units'][0]['execution_status'] = 'FAILED'
        plan['inventory']['units'][0]['source']['execution_ref'] = 'another-real-run'
        self.assertEqual(expected,scope_digest(plan))
        plan['inventory']['units'][0]['source']['mode'] = 'different-mode'
        self.assertNotEqual(expected,scope_digest(plan))

    def test_scope_approval_binds_applicability_and_exclusion_reasons(self):
        plan = copy.deepcopy(self.f['prepared'].plan)
        expected = scope_digest(plan)
        plan['inventory']['units'][0]['applicable'] = False
        self.assertNotEqual(expected,scope_digest(plan))
        plan['inventory']['units'][0]['applicable'] = True
        plan['inventory']['units'][0]['exclusion'] = {'reason':'Synthetic changed scope'}
        self.assertNotEqual(expected,scope_digest(plan))

    def test_shared_pre_keeps_source_identity_and_foreign_blocks_do_not(self):
        plan = self.f['prepared'].plan
        unit = plan['inventory']['units'][0]
        other = {**plan['collection'],'submission_revision':'preA_postC-BE'}
        self.assertEqual(source_execution(plan['collection'],unit),source_execution(other,unit))
        self.assertNotEqual(source_unit(plan['collection'],unit),source_unit({**other,'block_key':'other-block'},unit))

    def test_version_layout_eco_and_schema_are_strict(self):
        for edit in ('schema','pre-eco','post-revision','mode','order'):
            with self.subTest(edit=edit):
                plan = copy.deepcopy(self.f['prepared'].plan)
                if edit=='schema': plan['schema_version']=1
                if edit=='pre-eco': plan['inventory']['units'][0]['source']['eco']='pre'
                if edit=='post-revision': plan['inventory']['units'][1]['source']['revision']='foreign'
                if edit=='mode': plan['inventory']['units'][0]['condition']['mode']='other'
                if edit=='order': plan['inventory']['units'][0]['source']['execution_order']=True
                with self.assertRaises(ReviewError): validate_plan(plan)

    def test_dry_run_and_cancel_never_publish_or_create_a_local_snapshot(self):
        self.client.submit(self.f['target'],confirm=lambda _:self.fail('dry run must not confirm'),preview=lambda _:None,dry_run=True)
        with self.assertRaises(ReviewError) as error:
            self.client.submit(self.f['target'],confirm=lambda _:False,preview=lambda _:None)
        self.assertEqual('SUBMISSION_CANCELLED',error.exception.code)
        self.assertEqual([],list((self.root/'state').iterdir()))
        self.assertEqual([],list((self.root/'spool/inbox'/self.f['actor'].key).iterdir()))

    def test_authenticated_mount_owner_must_match_the_actual_caller(self):
        from unittest.mock import patch
        from common.sanity_submission import VerifiedSubmitter
        actual = self.f['actor']
        foreign = VerifiedSubmitter(actual.issuer,'different-account','user-05',actual.os_uid)
        with patch.object(self.f['authority'],'inbox_owner',return_value=foreign):
            with self.assertRaises(ReviewError) as error:self.submit()
        self.assertEqual('SUBMISSION_IDENTITY_MISMATCH',error.exception.code)
        self.assertEqual([],list((self.root/'state').iterdir()))

    def test_preview_identifies_account_profile_approval_and_missing_unit(self):
        from vwp.submission_cli import display_preview
        missing=self.f['prepared'].plan['inventory']['units'][0]['report']
        del self.f['provider'].value.reports[missing]
        self.f['provider'].value.plan['inventory']['units'][0]['execution_status']='NOT_RUN'
        _,_,summary=self.client.prepare(self.f['target'])
        output=io.StringIO()
        with redirect_stdout(output):display_preview(summary)
        for expected in ('user-04','synthetic-top @ 1','연결 PRE: preA','누락 1','실행 미완료 1',
                         'SYNTHETIC-APPROVAL','NOT_RUN, 파일 누락','synthetic-pre-run-1'):
            self.assertIn(expected,output.getvalue())
        self.assertEqual([],list((self.root/'state').iterdir()))

    def test_submit_preserves_bytes_missing_and_unexpected_reports(self):
        missing = self.f['prepared'].plan['inventory']['units'][0]['report']
        del self.f['provider'].value.reports[missing]
        extra = self.root/'input-post/unexpected.rpt'
        extra.write_bytes(b'opaque\r\nbytes\x00\xff')
        self.f['provider'].value.reports['reports/unexpected.rpt']=extra
        value = self.submit()
        self.assertEqual('LOCAL_QUEUED',value['delivery_state'])
        ready = self.root/'spool/inbox'/self.f['actor'].key/(value['submission_id']+'.ready')
        _, manifest = verify_bundle(ready)
        self.assertEqual(2,len(manifest['plan']['inventory']['units']))
        self.assertEqual(['reports/unexpected.rpt'],manifest['unexpected_files'])
        self.assertEqual(extra.read_bytes(),(ready/'snapshot/reports/unexpected.rpt').read_bytes())
        self.assertEqual('LOCAL_QUEUED',self.client.status(value['submission_id'])['delivery_state'])

    def test_retry_uses_same_uuid_and_source_changes_cannot_change_the_enqueued_bytes(self):
        first = self.submit()
        self.f['prepared'].reports['reports/post/sample.rpt'].write_text('changed after submission')
        retried = self.client.retry(first['submission_id'])
        self.assertEqual(first,retried)
        self.assertEqual(1,len(list((self.root/'spool/inbox'/self.f['actor'].key).iterdir())))

    def test_scope_or_storage_identity_changes_are_rejected(self):
        with self.assertRaises(ReviewError):
            self.client.prepare({**self.f['target'],'block':'foreign-block'})
        write_json_file(self.root/'spool/storage.json',{'schema_version':1,'storage_id':'wrong-mounted-disk'})
        with self.assertRaises(ReviewError) as error:self.submit()
        self.assertEqual('SUBMISSION_STORAGE_NOT_CONFIGURED',error.exception.code)

    def test_unverified_confirmed_flag_never_becomes_an_approval(self):
        self.f['prepared'].plan['approval']['reference']='not-a-real-approval'
        _,_,summary = self.client.prepare(self.f['target'])
        self.assertFalse(summary['inventory_confirmed'])

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

    def test_path_entrypoint_is_usable_from_an_unrelated_directory_without_database_packages(self):
        entry = Path(__file__).resolve().parents[1]/'bin/sanity'
        result = subprocess.run([sys.executable,'-S',str(entry),'--help'],cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)

        self.assertIn('submit',result.stdout)
        result = subprocess.run([sys.executable,'-S','-c',"import sys;sys.path.insert(0,"+repr(str(entry.parents[1]))+
            ");import vwp.submission_cli;assert 'psycopg2' not in sys.modules and 'oa' not in sys.modules"],
            cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_record_transfer_does_not_silently_shorten_an_oversized_record(self):
        from vwp_bridge.submission_analysis import record_parts
        large = {'id':'r-synthetic','raw':'x'*(4*1024*1024)}
        with self.assertRaises(ReviewError) as error:
            list(record_parts(iter([large]),'sr-synthetic'))
        self.assertEqual('RECORD_TRANSFER_LIMIT',error.exception.code)
