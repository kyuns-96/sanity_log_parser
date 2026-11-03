"""Real restricted PostgreSQL: submission -> source pages -> review -> receipt."""
from pathlib import Path
import tempfile

from common.sanity_db import Database
from common.sanity_parsers import ParserRegistry
from devtools.sanity_submission_fixture import FixtureIdentity, fixture
from oa.sanity_workspace import seed_workspace
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
