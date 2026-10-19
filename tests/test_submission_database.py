"""Real restricted PostgreSQL: submission -> source pages -> review -> receipt."""
import copy
from pathlib import Path
import tempfile
import uuid

from common.sanity_common import ReviewError, canonical_json, digest, unpack_json
from common.sanity_db import Database
from common.sanity_parsers import ParserRegistry
from common.sanity_submission import scope_digest
from common.sanity_submission_files import verify_bundle
from devtools.sanity_submission_fixture import FixtureIdentity, fixture
from oa.sanity_workspace import WorkspaceStore, record_id, seed_workspace
from oa.sanity_workspace_pipeline import WorkspacePipelineStore
from oa.submission_receiver import SourceReceiver
from oa.test_workspace import fixture as workspace_fixture
from tests.support import PgTestCase, ROOT
from vwp.submission_cli import SubmissionClient
from vwp_bridge.submission_worker import SubmissionWorker


class SubmissionDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        script = (ROOT/'db/008_submissions.sql').read_text().replace('public.',self.schema+'.')
        self.admin.execute(script)
        temp = tempfile.TemporaryDirectory(prefix='sanity-submission-db-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.f = fixture(self.root)
        self.client = SubmissionClient(self.f['client_config'],self.f['provider'],self.f['authority'],state_root=self.root/'state')
        with Database('oa') as db,db.transaction():
            seed_workspace(db,workspace_fixture())

    def submit(self):
        return self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)

    def bridge(self):
        with Database('vwp') as db:
            return SubmissionWorker(db,self.f['bridge_config'],self.f['authority'],ParserRegistry()).once()

    def receive(self, *, order=None):
        with Database('oa') as db:
            receiver = SourceReceiver(db,FixtureIdentity())
            if order is not None:
                return [receiver.receive_one(key) for key in order]
            results = []
            while True:
                value = receiver.receive_one()
                if value['status']=='NO_MESSAGE':break
                results.append(value)
            return results

    def report_headers(self):
        with Database('oa') as db,db.transaction(readonly=True):
            return [r['payload'] for r in db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                "AND payload->>'source_storage'='PARTS_V2'").fetchall()]

    def test_complete_submission_roundtrip_keeps_por_and_exposes_all_records(self):
        sent = self.submit()
        self.assertEqual('LOCAL_QUEUED',self.client.status(sent['submission_id'])['delivery_state'])
        self.bridge()
        self.assertEqual('DB_QUEUED',self.client.status(sent['submission_id'])['delivery_state'])
        self.receive(); self.bridge()
        receipt = self.client.status(sent['submission_id'])
        self.assertEqual(('OA_STORED','READY'),(receipt['delivery_state'],receipt['review_readiness']))
        self.assertEqual(1,len(receipt['workspace_links']))
        reports = self.report_headers()
        self.assertEqual(2,len(reports))
        post = next(r for r in reports if r['source']['layout']=='POST')
        with Database('oa') as db,db.transaction(readonly=True):
            ws = WorkspaceStore(db)
            self.assertIsNone(ws.por('project-1-cpu')['version'])
            report = ws.report('user-04','project-1-cpu',post['version_id'],post['id'])
            self.assertEqual(4,len(report['records']))
            self.assertNotIn('records',post)
            versions = ws.versions('user-04','project-1-cpu')['items']
            self.assertEqual(['eco01'],next(v for v in versions if v['id']==post['version_id'])['ecos'])
            page = WorkspacePipelineStore(db).records_page('user-04','project-1-cpu',post['version_id'],post['id'],expected_revision=0,offset=3,limit=1)
            self.assertEqual((4,1),(page['total'],len(page['items'])))

    def test_parts_may_arrive_before_manifest_and_commit_without_partial_publication(self):
        self.submit(); self.bridge()
        with Database('oa') as db:
            messages = db.execute("SELECT message_id,message_type FROM {vwp_to_oa} WHERE message_type LIKE 'SOURCE_%'").fetchall()
        manifest = [m['message_id'] for m in messages if m['message_type']=='SOURCE_MANIFEST']
        parts = [m['message_id'] for m in messages if m['message_type']=='SOURCE_PART']
        commit = [m['message_id'] for m in messages if m['message_type']=='SOURCE_COMMIT']
        self.receive(order=commit+parts[:-1]+manifest)
        self.assertEqual([],self.report_headers())
        self.receive(order=parts[-1:])
        self.assertEqual(2,len(self.report_headers()))

    def test_resubmission_deduplicates_shared_pre_and_preserves_a_human_decision(self):
        first = self.submit(); self.bridge(); self.receive(); self.bridge()
        with Database('oa') as db,db.transaction():
            report = self.report_headers()[0]
            from oa.sanity_workspace_review import WorkspaceReviewStore
            event = WorkspaceReviewStore(db).save('user-04','project-1-cpu',report['version_id'],report['id'],
                conclusion='FAIL',reason='Synthetic human review',criteria='',conditions={},reusable=False,
                compatible_versions=False,expected_revision=0,request_id=str(uuid.uuid4()))['event']
        second = self.submit(); self.bridge(); self.receive(); self.bridge()
        self.assertNotEqual(first['submission_id'],second['submission_id'])
        self.assertEqual(2,len(self.report_headers()))
        with Database('oa') as db,db.transaction(readonly=True):
            from oa.sanity_workspace_review import WorkspaceReviewStore
            current,_ = WorkspaceReviewStore(db).current(report['id'])
            self.assertEqual(event['id'],current['id'])
            self.assertEqual(2,db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_SUBMISSION'").fetchone()['n'])

    def test_new_eco_adds_to_existing_version_and_keeps_pre_as_one_original(self):
        self.submit(); self.bridge(); self.receive(); self.bridge()
        plan = self.f['prepared'].plan
        post = plan['inventory']['units'][1]
        plan['collection']['target_eco']='eco02'
        new_unit = copy.deepcopy(post)
        new_unit.update(review_unit_id='unit-post-2',report='reports/post2/sample.rpt')
        new_unit['source'].update(eco='eco02',execution_ref='synthetic-post-eco02-run-1')
        plan['inventory']['units'].append(new_unit)
        self.f['prepared'].reports[new_unit['report']]=self.f['prepared'].reports[post['report']]
        self.f['target']['eco']='eco02'
        plan['approval']['scope_sha256']=scope_digest(plan)
        self.submit(); self.bridge(); self.receive(); self.bridge()
        reports = self.report_headers()
        self.assertEqual(3,len(reports))
        self.assertEqual(1,len([r for r in reports if r['source']['layout']=='PRE']))
        post = next(r for r in reports if r['source']['layout']=='POST')
        with Database('oa') as db,db.transaction(readonly=True):
            version = WorkspaceStore(db).version('user-04','project-1-cpu',post['version_id'])['payload']
            self.assertEqual(['eco01','eco02'],version['ecos'])

    def test_scope_approval_or_missing_report_never_becomes_review_ready(self):
        self.f['prepared'].plan['approval']=None
        missing = self.f['prepared'].plan['inventory']['units'][1]['report']
        del self.f['prepared'].reports[missing]
        sent = self.submit(); self.bridge(); self.receive(); self.bridge()
        value = self.client.status(sent['submission_id'])
        self.assertEqual(('OA_STORED','BLOCKED'),(value['delivery_state'],value['review_readiness']))
        self.assertIn('INVENTORY_UNCONFIRMED',value['blockers'])
        report = next(r for r in self.report_headers() if r['source']['layout']=='POST')
        self.assertEqual('MISSING',report['parse_status'])
        with Database('oa') as db,db.transaction(readonly=True):
            page = WorkspacePipelineStore(db).view_paged('user-04','project-1-cpu',report['version_id'],report['id'])
            self.assertFalse(page['ready'])

    def test_repeated_execution_with_changed_bytes_is_rejected_without_overwriting(self):
        self.submit(); self.bridge(); self.receive(); self.bridge()
        original = self.f['prepared'].reports['reports/post/sample.rpt']
        original.write_text(original.read_text().replace('12,ns','13,ns'))
        self.submit()
        with self.assertRaises(ReviewError) as error:self.bridge()
        self.assertEqual('SOURCE_IDENTITY_CONFLICT',error.exception.code)
        self.assertEqual(2,len(self.report_headers()))

    def test_new_execution_with_identical_bytes_is_a_separate_run(self):
        self.submit(); self.bridge(); self.receive(); self.bridge()
        source = self.f['prepared'].plan['inventory']['units'][1]['source']
        source.update(execution_ref='synthetic-post-run-2',execution_order=2)
        self.submit(); self.bridge(); self.receive(); self.bridge()
        reports = self.report_headers()
        self.assertEqual(3,len(reports))
        posts = [r for r in reports if r['source']['layout']=='POST']
        self.assertEqual(posts[0]['raw_sha256'],posts[1]['raw_sha256'])
        self.assertNotEqual(posts[0]['execution_id'],posts[1]['execution_id'])
        with Database('oa') as db,db.transaction(readonly=True):
            ws = WorkspaceStore(db)
            latest = ws.reports('user-04','project-1-cpu',posts[0]['version_id'],eco='eco01')
            history = ws.reports('user-04','project-1-cpu',posts[0]['version_id'],eco='eco01',include_history=True)
            self.assertEqual((1,2),(latest['total'],history['total']))
            self.assertEqual(2,latest['items'][0]['source']['execution_order'])

    def test_received_pages_support_group_confirmation_judgment_and_next_run_cases(self):
        self.submit(); self.bridge(); self.receive(); self.bridge()
        report = next(r for r in self.report_headers() if r['source']['layout']=='POST')
        args = ('user-04','project-1-cpu',report['version_id'],report['id'])
        with Database('oa') as db,db.transaction():
            pipeline = WorkspacePipelineStore(db)
            event = pipeline.change(*args,action='PROPOSE',expected_revision=0,request_id=str(uuid.uuid4()))
            for group in event['groups']:
                event = pipeline.change(*args,action='EDIT',expected_revision=event['revision'],request_id=str(uuid.uuid4()),
                    reason='사람이 그룹 근거를 검토',rationale_operation={'kind':'ACCEPT','group_id':group['id']})
            event = pipeline.change(*args,action='CONFIRM',expected_revision=event['revision'],request_id=str(uuid.uuid4()),
                reason='전체 구성과 근거 확인',reusable=True,criteria='Synthetic confirmed grouping',compatible_versions=True)
            from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
            decision = WorkspaceGroupReviewStore(db,event['groups'][0]['id'],event['revision']).save(*args,
                conclusion='PASS',reason='Synthetic explicit human override',criteria='',conditions={},reusable=False,
                compatible_versions=False,expected_revision=0,request_id=str(uuid.uuid4()))['event']
            self.assertEqual('PASS',decision['conclusion'])
        self.f['prepared'].plan['inventory']['units'][1]['source'].update(execution_ref='synthetic-next-run',execution_order=2)
        self.submit(); self.bridge(); self.receive(); self.bridge()
        target = next(r for r in self.report_headers() if r['source']['layout']=='POST' and r['source']['execution_order']==2)
        with Database('oa') as db,db.transaction():
            value = WorkspacePipelineStore(db).change('user-04','project-1-cpu',target['version_id'],target['id'],
                action='PROPOSE',expected_revision=0,request_id=str(uuid.uuid4()))
            self.assertIn(event['id'],[r['id'] for r in value['references']])
            self.assertEqual(target['record_count'],sum(len(g['record_ids']) for g in value['groups']))

    def test_metadata_and_submission_api_are_scoped_and_do_not_return_full_sources(self):
        self.submit(); self.bridge(); self.receive(); self.bridge()
        report = next(r for r in self.report_headers() if r['source']['layout']=='POST')
        from fastapi.testclient import TestClient
        from oa.workspace_api import create_app
        from oa.sanity_workspace import SYNTHETIC_PASSWORD
        with TestClient(create_app(origins=['http://testserver'])) as client:
            client.post('/workspace/api/login',json={'username':'user04','password':SYNTHETIC_PASSWORD},headers={'Origin':'http://testserver'})
            base='/workspace/api/blocks/project-1-cpu'
            metadata=client.get(base+'/versions/'+report['version_id']+'/reports/'+report['id'])
            self.assertEqual(200,metadata.status_code)
            self.assertNotIn('records',metadata.json())
            listing=client.get(base+'/submissions').json()
            self.assertEqual(1,listing['total'])
            self.assertNotIn('subject',canonical_json(listing).decode())
            self.assertEqual(403,client.get('/workspace/api/blocks/project-1-gpu/submissions').status_code)

    def test_oa_rechecks_latest_owner_and_sends_a_rejection_receipt(self):
        sent = self.submit(); self.bridge()
        # Fixture owner simulates the management assignment source changing.
        # Application UPDATE(payload) remains forbidden; no protection is removed.
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,ARRAY['owners'],'[\"user-05\"]') "
                         "WHERE record_type='WS_BLOCK' AND workspace_key='project-1-cpu'")
        with self.assertRaises(ReviewError):self.receive()
        self.bridge()
        self.assertEqual('REJECTED',self.client.status(sent['submission_id'])['delivery_state'])
        self.assertEqual([],self.report_headers())

    def test_archive_without_db_registration_and_db_ack_without_receipt_file_are_recoverable(self):
        import shutil
        from unittest.mock import patch
        sent = self.submit()
        with Database('vwp') as db:
            worker = SubmissionWorker(db,self.f['bridge_config'],self.f['authority'],ParserRegistry())
            with patch.object(worker.store,'accept',side_effect=RuntimeError('synthetic crash before commit')):
                with self.assertRaises(RuntimeError):worker.once()
            self.assertTrue((self.root/'archive'/sent['submission_id']).is_dir())
        shutil.rmtree(self.root/'spool/inbox'/self.f['actor'].key/(sent['submission_id']+'.ready'))
        self.bridge(); self.receive()
        with Database('vwp') as db:
            worker = SubmissionWorker(db,self.f['bridge_config'],self.f['authority'],ParserRegistry())
            with patch.object(worker,'write_receipt',side_effect=OSError('synthetic receipt disk failure')):
                with self.assertRaises(OSError):worker.once()
        self.assertEqual('DB_QUEUED',self.client.status(sent['submission_id'])['delivery_state'])
        self.bridge()
        self.assertEqual('OA_STORED',self.client.status(sent['submission_id'])['delivery_state'])
        self.assertEqual(2,len(self.report_headers()))

    def test_accepted_submission_resumes_from_private_archive_without_shared_copy(self):
        import shutil
        from unittest.mock import patch
        sent = self.submit()
        with patch('vwp_bridge.submission_worker.build_transfer',side_effect=RuntimeError('synthetic parsing crash')):
            with self.assertRaises(RuntimeError):self.bridge()
        shutil.rmtree(self.root/'spool/inbox'/self.f['actor'].key/(sent['submission_id']+'.ready'))
        self.bridge(); self.receive(); self.bridge()
        self.assertEqual('OA_STORED',self.client.status(sent['submission_id'])['delivery_state'])
        self.assertEqual(2,len(self.report_headers()))

    def test_partial_outbox_resumes_without_shared_copy_or_duplicate_messages(self):
        import shutil
        from unittest.mock import patch
        from common.sanity_db import Queue
        sent = self.submit()
        original, calls = Queue.enqueue, []
        def interrupted(queue, value):
            calls.append(value['message_id'])
            if len(calls)==2:
                raise RuntimeError('synthetic publication crash')
            return original(queue,value)
        with patch.object(Queue,'enqueue',new=interrupted):
            with self.assertRaises(RuntimeError):self.bridge()
        with Database('vwp') as db:
            self.assertEqual(1,db.execute('SELECT count(*) AS n FROM {vwp_to_oa}').fetchone()['n'])
        shutil.rmtree(self.root/'spool/inbox'/self.f['actor'].key/(sent['submission_id']+'.ready'))
        self.bridge(); self.receive(); self.bridge()
        self.assertEqual('OA_STORED',self.client.status(sent['submission_id'])['delivery_state'])
        with Database('vwp') as db:
            count=db.execute('SELECT count(*) AS n FROM {vwp_to_oa}').fetchone()['n']
            expected=db.execute("SELECT state FROM {vwp} WHERE record_type='SUBMISSION'").fetchone()['state']['message_count']
            self.assertEqual(expected,count)

    def test_tampered_record_is_rejected_and_no_partial_report_is_published(self):
        from common.sanity_common import pack_json
        from common.sanity_db import Queue
        from common.sanity_submission import message_id,PART_BYTES
        self.submit(); self.bridge()
        with Database('oa') as db:
            messages=db.execute("SELECT * FROM {vwp_to_oa} WHERE message_type LIKE 'SOURCE_%'").fetchall()
        first=next(m for m in messages if m['message_type']=='SOURCE_MANIFEST')
        self.receive(order=[first['message_id']])
        original=next(m for m in messages if m['message_type']=='SOURCE_PART')
        packet=unpack_json(original,max_bytes=PART_BYTES)
        packet['body']['body']['records'][0]['raw']='changed without source hash'
        packet['body']['id']='sp-'+digest(packet['body']['body'])
        key=message_id('SOURCE_PART',packet)
        with Database('vwp') as db,db.transaction():
            Queue(db,'vwp_to_oa').enqueue({'message_id':key,'message_type':'SOURCE_PART','project':original['project'],
                'run_id':None,'bundle_id':None,'correlation_id':packet['submission_id'],'payload':original['payload'],
                **pack_json(packet,max_bytes=PART_BYTES)})
        with self.assertRaises(ReviewError):self.receive(order=[key])
        self.assertEqual([],self.report_headers())

    def test_unexpected_company_parser_failure_is_rejected_without_automatic_retry(self):
        from common.sanity_parsers import ReportParser
        calls=[]
        class BrokenParser(ReportParser):
            def parse(self, source, context):
                calls.append(context)
                raise RuntimeError('SYNTHETIC_SECRET_adapter_failure')
        self.f['prepared'].plan['inventory']['units'][0]['parser']='broken_fixture_v1'
        registry=ParserRegistry()
        registry.register('broken_fixture_v1',BrokenParser(),implementation_version='1',evaluation='numeric',
            item_ids=('gca_constraints',),tool='synthetic',tool_versions=('1',),rule_versions=('1',))
        sent=self.submit()
        with Database('vwp') as db:
            worker=SubmissionWorker(db,self.f['bridge_config'],self.f['authority'],registry)
            with self.assertRaises(ReviewError) as error:worker.once()
            self.assertEqual('PARSER_FAILED',error.exception.code)
            self.assertNotIn('SYNTHETIC_SECRET',str(error.exception))
            self.assertEqual('NO_MESSAGE',worker.once()['status'])
            self.assertEqual(0,db.execute('SELECT count(*) AS n FROM {vwp_to_oa}').fetchone()['n'])
        self.assertEqual(1,len(calls))
        receipt=self.client.status(sent['submission_id'])
        self.assertEqual(('REJECTED',['PARSER_FAILED']),(receipt['delivery_state'],receipt['blockers']))

    def test_migration_preserves_existing_data_and_reapply_is_transactionally_rejected(self):
        before = self.admin_query('SELECT count(*) FROM {oa}').fetchone()[0]
        with self.assertRaises(Exception):
            self.admin.execute((ROOT/'db/008_submissions.sql').read_text().replace('public.',self.schema+'.'))
        self.admin.execute('ROLLBACK')
        self.assertEqual(before,self.admin_query('SELECT count(*) FROM {oa}').fetchone()[0])
        with Database('vwp') as db:
            self.assertEqual(4,len(db.tables))
