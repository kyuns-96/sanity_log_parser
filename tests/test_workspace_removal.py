"""Physical, scoped deletion on an isolated four-table shared-account database."""
import os
from pathlib import Path
import secrets
import unittest
from unittest.mock import patch
from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_db import Database
from common.sanity_removal import marker, PRIVATE_REMOVED
from oa.sanity_workspace import WorkspaceStore
from oa.sanity_workspace_removal import Removal
from tests.support import PgTestCase,ROOT


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class WorkspaceRemovalTests(unittest.TestCase):
    def setUp(self):
        from tests.test_submission_database import SubmissionDatabaseTests
        self.c=SubmissionDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        for name in ('009_sso_identity.sql','010_raw_sources.sql','011_payload_storage.sql',
                     '012_source_evidence_pages.sql','013_shared_source_packets.sql'):
            self.c.admin.execute((ROOT/'db'/name).read_text().replace('public.',self.c.schema+'.'))
        from psycopg2 import sql
        from psycopg2.extensions import make_dsn
        self.login='sanity_remove_'+uuid4().hex;password=secrets.token_urlsafe(32)
        self.restricted=os.environ['SANITY_OA_DSN']
        self.c.admin.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
            'NOBYPASSRLS PASSWORD %s').format(sql.Identifier(self.login)),(password,))
        self.addCleanup(self.drop_login)
        self.c.admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(
            sql.Identifier(self.c.schema),sql.Identifier(self.login)))
        self.c.admin.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}').format(
            sql.Identifier(self.c.schema),sql.Identifier(self.login)))
        dsn=make_dsn(os.environ['SANITY_TEST_ADMIN_DSN'],user=self.login,password=password)
        env=patch.dict(os.environ,{'SANITY_OA_DSN':dsn,'SANITY_VWP_DSN':dsn,'SANITY_DB_ALLOW_SINGLE_ACCOUNT':'true'})
        env.start();self.addCleanup(env.stop)

    def drop_login(self):
        from psycopg2 import sql
        self.c.admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(self.login)))
        self.c.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(self.login)))

    def delivered(self):
        sent=self.c.submit();self.c.bridge();self.c.receive();self.c.bridge()
        reports=[r for r in self.c.report_headers() if r.get('submission_id')==sent['submission_id']]
        self.assertEqual(2,len(reports))
        return sent,next(r for r in reports if r['source']['layout']=='PRE'),next(r for r in reports if r['source']['layout']=='POST')

    def originals(self,sid):
        from common.sanity_submission_files import verify_bundle
        from common.sanity_raw_sources import RawSourceStore,RAW_PROTOCOL
        bundle=Path(self.c.f['bridge_config']['archive_root'])/sid
        submission,manifest=verify_bundle(bundle)
        with Database('vwp') as db,db.transaction():
            store=RawSourceStore(db,'synthetic-node')
            store.initialize(sid,self.c.f['actor'],{'submission':submission,'manifest':manifest})
            for index,file in enumerate(manifest['files']):
                raw=(bundle/'snapshot'/file['path']).read_bytes();sha=file['sha256']
                store.immutable('RAW_CHUNK','rawc.'+sha,{'schema_version':1,'protocol':RAW_PROTOCOL,
                    'sha256':sha,'size_bytes':len(raw)},raw)
                store.immutable('RAW_FILE',f'rawf.{sid}.{index:05d}',{'schema_version':1,'protocol':RAW_PROTOCOL,
                    'submission_id':sid,'file_index':index,'file':file,'chunks':[sha]})
            store.commit(sid,self.c.f['actor'])

    def preview(self,report,*,version=False,user='user-04'):
        with Database('oa') as db,db.transaction():
            return Removal(WorkspaceStore(db)).preview(user,report['block_id'],report['version_id'],None if version else report['id'])

    def remove(self,report,*,version=False,preview=None,identity=None,user='user-04'):
        preview=preview or self.preview(report,version=version,user=user)
        with Database('oa') as db,db.transaction():
            return Removal(WorkspaceStore(db)).delete(user,report['block_id'],report['version_id'],
                report_id=None if version else report['id'],request_id=identity or uuid4(),
                snapshot_sha256=preview['snapshot_sha256'])

    def human(self,report):
        from oa.sanity_workspace_review import WorkspaceReviewStore
        with Database('oa') as db,db.transaction():
            return WorkspaceReviewStore(db).save('user-04',report['block_id'],report['version_id'],report['id'],
                conclusion='FAIL',reason='삭제 검증용 합성 사람 판정',criteria='',conditions={},reusable=False,
                compatible_versions=False,expected_revision=0,request_id=str(uuid4()))['event']

    def code(self,expected,operation):
        with self.assertRaises(ReviewError) as error:operation()
        self.assertEqual(expected,error.exception.code)

    def test_report_deletes_private_original_and_confirmed_history_but_keeps_other_report(self):
        sent,pre,post=self.delivered();self.originals(sent['submission_id']);self.human(post)
        from oa.sanity_ai_evidence_choices import EvidenceChoices
        with Database('oa') as db,db.transaction():
            store=EvidenceChoices(db)
            path=('user-04',post['block_id'],post['version_id'],post['id'],None,'REVIEW')
            state=store.view(*path)
            store.save(*path,enabled=True,expected_revision=state['revision'],binding_sha256=state['binding_sha256'],request_id=uuid4())
        result=self.remove(post);self.assertTrue(result['deleted'])
        self.assertEqual([pre],self.c.report_headers())
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {vwp} WHERE analysis_id=%s",(post['analysis_id'],)).fetchone()[0])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_REVIEW_EVENT' "
            "AND payload->>'report_id'=%s",(post['id'],)).fetchone()[0])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CONFIG' "
            "AND payload->>'namespace'='ai-evidence-choice-v1' AND payload->>'report_id'=%s",(post['id'],)).fetchone()[0])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RAW_FILE' "
            "AND payload->'file'->>'path'='reports/post/sample.rpt'").fetchone()[0])
        self.assertEqual(1,self.c.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RAW_FILE'").fetchone()[0])
        for part in post['source_parts']:
            self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_SOURCE_PART' "
                "AND workspace_key=%s",(part['id'],)).fetchone()[0])
        with Database('oa') as db,db.transaction():
            self.assertEqual(pre['record_count'],len(WorkspaceStore(db).report('user-04',pre['block_id'],pre['version_id'],pre['id'])['records']))
            self.assertIsNotNone(marker(db,'REPORT',post['id']))
        saved=self.c.admin_query("SELECT payload FROM {vwp} WHERE record_type='SUBMISSION' AND entity_key=%s",
                                (sent['submission_id'],)).fetchone()[0]
        self.assertEqual(PRIVATE_REMOVED,saved['namespace'])
