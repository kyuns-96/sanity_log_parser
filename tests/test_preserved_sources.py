"""Preserved files: scope, exact bytes, immutable context and OA/AI boundaries."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from common.sanity_common import ReviewError, canonical_json, digest, unpack_json
from common.sanity_db import Database, Queue
from common.sanity_preserved_sources import metadata, validate_metadata
from common.sanity_submission import scope_digest, source_digest, validate_plan
from common.sanity_submission_files import snapshot, validate_manifest, verify_snapshot
from devtools.sanity_submission_fixture import fixture
from tests.support import PgTestCase, ROOT


def preserve(f, root):
    plan = f['prepared'].plan
    unit = next(u for u in plan['inventory']['units'] if u['source']['layout'] == 'POST')
    plan['schema_version'] = 3
    plan['inventory']['preserved_files'] = [
        {'path': 'reports/companion/full.rpt', 'role': 'canonical_companion',
         'binding': {'kind': 'execution', 'anchor_unit_id': unit['review_unit_id']}},
        {'path': 'reports/companion/input.sdc', 'role': 'constraint_input',
         'binding': {'kind': 'unit', 'review_unit_id': unit['review_unit_id']}}]
    for index, item in enumerate(plan['inventory']['preserved_files']):
        path = root/('preserved-'+str(index))
        path.write_bytes(b'SYNTHETIC ORIGINAL ONLY\r\n\x00\xff' if index == 0 else b'# synthetic constraint\n')
        f['prepared'].reports[item['path']] = path
    if plan.get('approval'):
        plan['approval']['scope_sha256'] = scope_digest(plan)
    return plan


class PreservedContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sanity-preserved-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.f = fixture(self.root)

    def test_plan_version_roles_binding_and_primary_paths_are_strict(self):
        plan = preserve(self.f, self.root)
        self.assertIs(plan, validate_plan(plan))
        for edit in ('v2', 'empty', 'context', 'wrong-unit', 'primary', 'duplicate', 'traversal', 'extra-binding'):
            value = copy.deepcopy(plan); items = value['inventory']['preserved_files']
            if edit == 'v2': value['schema_version'] = 2
            if edit == 'empty': items.clear()
            if edit == 'context': items[0]['role'] = 'context'
            if edit == 'wrong-unit': items[0]['binding']['anchor_unit_id'] = 'foreign-unit'
            if edit == 'primary': items[0]['path'] = value['inventory']['units'][0]['report']
            if edit == 'duplicate': items.append(copy.deepcopy(items[0]))
            if edit == 'traversal': items[0]['path'] = 'reports/../private.sdc'
            if edit == 'extra-binding': items[0]['binding']['block_id'] = 'foreign'
            with self.subTest(edit=edit), self.assertRaises(ReviewError): validate_plan(value)

    def test_scope_binds_preservation_intent_but_not_execution_or_file_bytes(self):
        legacy = copy.deepcopy(self.f['prepared'].plan)
        before = scope_digest(legacy)
        plan = preserve(self.f, self.root); expected = scope_digest(plan)
        self.assertNotEqual(before, expected)
        for edit in ('role', 'path', 'binding'):
            changed = copy.deepcopy(plan); item = changed['inventory']['preserved_files'][0]
            if edit == 'role': item['role'] = 'constraint_input'
            if edit == 'path': item['path'] = 'reports/another.rpt'
            if edit == 'binding': item['binding'] = {'kind': 'unit', 'review_unit_id': plan['inventory']['units'][0]['review_unit_id']}
            self.assertNotEqual(expected, scope_digest(changed))
        plan['inventory']['units'][1]['source']['execution_ref'] = 'synthetic-next-execution'
        self.assertEqual(expected, scope_digest(plan))
        self.assertEqual(before, scope_digest(legacy))

    def test_preservation_keeps_exact_binary_bytes_and_unknown_reports_blocked(self):
        preserve(self.f, self.root)
        extra = self.root/'unknown'; extra.write_bytes(b'unknown report')
        self.f['prepared'].reports['reports/unknown.rpt'] = extra
        missing = self.f['prepared'].plan['inventory']['units'][0]['report']
        del self.f['prepared'].reports[missing]
        manifest = snapshot(self.f['prepared'], self.root/'snapshot')
        self.assertEqual(['reports/unknown.rpt'], manifest['unexpected_files'])
        self.assertEqual(2, len(manifest['plan']['inventory']['units']))
        self.assertEqual(2, len(manifest['preserved_files']))
        self.assertEqual(manifest, verify_snapshot(self.root/'snapshot'))
        for item in manifest['preserved_files']:
            original = self.f['prepared'].reports[item['path']].read_bytes()
            self.assertEqual(original, (self.root/'snapshot'/item['path']).read_bytes())
            self.assertEqual(hashlib.sha256(original).hexdigest(), item['sha256'])
            self.assertFalse(item['content_available_to_ai'])

    def test_missing_or_tampered_declared_file_never_becomes_preserved(self):
        preserve(self.f, self.root)
        key = self.f['prepared'].plan['inventory']['preserved_files'][0]['path']
        original = self.f['prepared'].reports.pop(key)
        with self.assertRaises(ReviewError) as error: snapshot(self.f['prepared'], self.root/'missing')
        self.assertEqual('PRESERVED_FILE_MISSING', error.exception.code)
        self.assertFalse((self.root/'missing').exists())
        self.f['prepared'].reports[key] = original
        manifest = snapshot(self.f['prepared'], self.root/'good')
        for field, value in (('sha256', '0'*64), ('content_available_to_ai', True), ('size_bytes', True)):
            changed = copy.deepcopy(manifest); changed['preserved_files'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ReviewError): validate_manifest(changed)
        (self.root/'good'/key).write_bytes(b'changed')
        with self.assertRaises(ReviewError): verify_snapshot(self.root/'good')

    def test_metadata_is_not_an_alias_of_the_plan_and_rejects_rebinding(self):
        plan = preserve(self.f, self.root)
        manifest = snapshot(self.f['prepared'], self.root/'good')
        before = copy.deepcopy(plan)
        values = metadata(plan, manifest['files'])
        values[0]['binding']['anchor_unit_id'] = 'foreign-unit'
        self.assertEqual(before, plan)
        with self.assertRaises(ReviewError): validate_metadata(plan, values)

    def test_cli_preview_separates_preserved_missing_and_unexpected_files(self):
        from vwp.submission_cli import SubmissionClient
        preserve(self.f, self.root)
        client = SubmissionClient(self.f['client_config'], self.f['provider'], self.f['authority'], state_root=self.root/'state')
        summary = client.prepare(self.f['target'])[2]
        self.assertEqual((2, 0, 0), (summary['preserved'], summary['preserved_missing'], summary['unexpected']))


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class PreservedDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_raw_sources import RawSourcesDatabaseTests
        self.case = RawSourcesDatabaseTests()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        for name in ('011_payload_storage.sql', '012_source_evidence_pages.sql', '013_shared_source_packets.sql'):
            self.case.admin.execute((ROOT/'db'/name).read_text().replace('public.', self.case.schema+'.'))

    def deliver(self):
        c = self.case
        sid = c.submit()['submission_id']; self.assertEqual(1, c.bridge()['processed'])
        c.receive(); c.bridge()
        self.assertEqual(('OA_STORED', 'READY'), tuple(c.client.status(sid)[k] for k in ('delivery_state', 'review_readiness')))
        return sid

    def reports(self):
        return self.case.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                                    "AND payload->>'source_storage'='PARTS_V2' ORDER BY workspace_key").fetchall()

    def test_exact_raw_restore_metadata_and_old_reports_survive_new_context(self):
        self.deliver(); old = [r[0] for r in self.reports()]
        preserve(self.case.f, self.case.root); sid = self.deliver()
        new = [r[0] for r in self.reports() if r[0].get('preserved_sources')]
        self.assertEqual(1, len(new)); self.assertEqual(2, len(new[0]['preserved_sources']))
        self.assertTrue(all(r in [row[0] for row in self.reports()] for r in old))
        with self.case.store(sid) as store, store.materialize(sid) as bundle:
            for item in new[0]['preserved_sources']:
                self.assertEqual(self.case.f['prepared'].reports[item['path']].read_bytes(),
                                 (bundle/'snapshot'/item['path']).read_bytes())
        from oa.sanity_workspace_sources import hydrate
        with Database('oa') as db:
            self.assertEqual(new[0]['record_count'], len(hydrate(db, new[0])['records']))
        self.assertNotIn('SYNTHETIC ORIGINAL ONLY', json.dumps(self.reports()))

    def test_changed_companion_creates_new_analysis_and_pins_without_rewriting_history(self):
        preserve(self.case.f, self.case.root); self.deliver()
        original = next(r[0] for r in self.reports() if r[0].get('preserved_sources'))
        self.case.f['prepared'].reports['reports/companion/input.sdc'].write_bytes(b'# changed synthetic constraints\n')
        self.deliver()
        values = [r[0] for r in self.reports() if r[0].get('preserved_sources')]
        self.assertEqual(2, len(values)); self.assertIn(original, values)
        other = next(r for r in values if r != original)
        self.assertEqual(original['execution_id'], other['execution_id'])
        self.assertEqual(original['raw_sha256'], other['raw_sha256'])
        self.assertNotEqual(original['analysis_id'], other['analysis_id'])
        self.assertNotEqual(source_digest(original), source_digest(other))

    def test_oa_rejects_forged_metadata_and_wrong_report_binding(self):
        from oa.submission_receiver import SourceReceiver
        from devtools.sanity_submission_fixture import FixtureIdentity
        preserve(self.case.f, self.case.root); self.deliver()
        with Database('oa') as db:
            message = db.execute("SELECT * FROM {vwp_to_oa} WHERE message_type='SOURCE_MANIFEST'").fetchone()
            packet = unpack_json(message); receiver = SourceReceiver(db, FixtureIdentity())
            receiver.validate_manifest(packet)
            for edit in ('ai-body', 'role', 'wrong-report', 'producer'):
                changed = copy.deepcopy(packet)
                if edit == 'ai-body': changed['body']['preserved_files'][0]['content_available_to_ai'] = True
                if edit == 'role': changed['body']['preserved_files'][0]['role'] = 'context'
                if edit == 'wrong-report': changed['body']['reports'][0]['preserved_sources'] = changed['body']['preserved_files']
                if edit == 'producer': changed['body']['producer'] = 'postgresql-source-2.2'
                with self.subTest(edit=edit), self.assertRaises(ReviewError): receiver.validate_manifest(changed)


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class PreservedOwnerTests(unittest.TestCase):
    def test_real_ai_jobs_and_human_confirmation_keep_preserved_provenance(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests
        from oa.sanity_ai_model import OAAdvisor
        c = TokenlessSubmissionTests()
        calls = []
        prepare, call = c.collection_input, OAAdvisor._call
        def collection_input():
            unit = prepare(); preserve(c.f, c.root)
            return unit
        def inspect(advisor, packet, instruction, version, attempt):
            sources = packet['context']['report']['preserved_sources']
            self.assertEqual(2, len(sources))
            self.assertTrue(all(p['content_available_to_ai'] is False for p in sources))
            self.assertIn('파일 본문은 제공되지 않았습니다', instruction)
            calls.append(copy.deepcopy(sources))
            return call(advisor, packet, instruction, version, attempt)
        try:
            c.setUp()
            with patch.object(c, 'collection_input', collection_input), patch.object(OAAdvisor, '_call', inspect):
                c.test_collection_only_runs_owner_intake_ai_and_human_review_without_changing_source()
            self.assertEqual(2, len(calls))
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(calls[0], c.collection_report()['preserved_sources'])
        finally:
            c.doCleanups()

    def test_tokenless_owner_confirms_exact_files_and_none_parser_stays_supported(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests, TOP
        c = TokenlessSubmissionTests()
        try:
            c.setUp(); c.collection_input(); preserve(c.f, c.root)
            sid = c.submit(); detail = c.detail(sid)
            self.assertEqual(2, len(detail['preserved_files']))
            self.assertEqual('AWAITING_CONFIRMATION', detail['delivery_state'])
            self.assertEqual(0, c.report_count())
            self.assertEqual(403, c.decide(sid, user=TOP).status_code)
            stale = c.body(sid); stale['scope_sha256'] = '0'*64
            self.assertEqual(409, c.decide(sid, body=stale).status_code)
            c.success(c.decide(sid)); c.bridge()
            self.assertEqual('READY', c.client.status(sid)['review_readiness'])
            self.assertEqual(2, len(c.collection_report()['preserved_sources']))
        finally:
            c.doCleanups()


class PreservedAIInputTests(unittest.TestCase):
    def test_preserved_file_reference_cannot_be_cited_as_log_case_or_criterion(self):
        from common.sanity_integration_examples import group_review_example
        from oa.sanity_ai_model import OAAdvisor
        from tests.test_ai_model import chat, environment, criterion, review_reply
        context = group_review_example()['context']
        source = {'id': 'pf-'+digest('synthetic'), 'role': 'constraint_input',
                  'sha256': digest('bytes'), 'content_available_to_ai': False}
        context['report']['preserved_sources'] = [source]
        criteria = [criterion(context)]
        for key in ('evidence_ids', 'reference_ids', 'criterion_ids'):
            reply = review_reply(context, criteria=[criteria[0]['id']]); reply[key] = [source['id']]
            def model(request):
                self.assertIn('파일 본문은 제공되지 않았습니다', json.loads(request.content)['messages'][0]['content'])
                return httpx.Response(200, json=chat(reply))
            advisor = OAAdvisor(environment(), transport=httpx.MockTransport(model))
            with self.subTest(key=key), self.assertRaises(ReviewError): advisor.review(context, [], criteria)

    def test_actual_model_request_marks_metadata_only_and_hashes_context(self):
        from common.sanity_integration_examples import clustering_example
        from oa.sanity_ai_model import OAAdvisor
        from tests.test_ai_model import chat, environment
        packet = clustering_example(with_history=True)
        packet['context']['report']['preserved_sources'] = [{'id': 'pf-'+digest('synthetic'),
            'role': 'constraint_input', 'sha256': digest('synthetic bytes'), 'content_available_to_ai': False}]
        requests = []
        def reply(request):
            body = json.loads(request.content); requests.append(body)
            self.assertIn('파일 본문은 제공되지 않았습니다', body['messages'][0]['content'])
            context = json.loads(body['messages'][1]['content'])['context']
            self.assertEqual(packet['context']['report']['preserved_sources'], context['report']['preserved_sources'])
            self.assertEqual(packet['context']['report']['records'], context['report']['records'])
            return httpx.Response(200, json=chat(packet['reply']))
        advisor = OAAdvisor(environment(), transport=httpx.MockTransport(reply))
        first = advisor.cluster(packet['context'], packet['cases'], packet['identity_fields'])
        self.assertTrue(first['metadata']['prompt_version'].endswith('.preserved-metadata-v1'))
        packet['context']['report']['preserved_sources'][0]['sha256'] = digest('changed synthetic bytes')
        second = advisor.cluster(packet['context'], packet['cases'], packet['identity_fields'])
        self.assertNotEqual(first['metadata']['input_sha256'], second['metadata']['input_sha256'])
        self.assertEqual(2, len(requests))
