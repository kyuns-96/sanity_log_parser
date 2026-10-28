"""Synthetic preserved text through RAW, shared packets, OWNER and actual AI jobs."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from common.sanity_common import ReviewError, digest
from common.sanity_preserved_sources import metadata, validate_metadata
from common.sanity_preserved_text import FORMAT, extract, verify_parts
from common.sanity_privacy import SecretGuard
from common.sanity_submission import scope_digest, validate_plan
from common.sanity_submission_files import snapshot
from devtools.sanity_submission_fixture import fixture
from tests.test_preserved_sources import preserve
from tests.support import PgTestCase, ROOT


def text_plan(f, root, *, multi=False):
    plan = preserve(f, root); plan['schema_version'] = 4
    files = plan['inventory']['preserved_files']
    for item in files: item['content'] = FORMAT
    if multi:
        files[0]['binding'] = {'kind': 'units', 'review_unit_ids': [u['review_unit_id'] for u in plan['inventory']['units']]}
    f['prepared'].reports[files[0]['path']].write_bytes(b'SYNTHETIC Rule Information\r\nClock must be declared.\r\n')
    f['prepared'].reports[files[1]['path']].write_bytes(b'# SYNTHETIC ONLY\ncreate_clock -period 10 [get_ports clk]\nTOKEN=synthetic-private-value\n')
    if plan.get('approval'): plan['approval']['scope_sha256'] = scope_digest(plan)
    return plan


class PreservedTextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.f = fixture(self.root)
        self.plan = text_plan(self.f, self.root, multi=True)
        self.manifest = snapshot(self.f['prepared'], self.root/'snapshot')

    def extracted(self, index=1, raw=None):
        file = self.manifest['preserved_files'][index]
        if raw is None: raw = self.f['prepared'].reports[file['path']].read_bytes()
        else: file = {**file, 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
        value, parts = extract(file, raw, SecretGuard({}))
        bodies = {'sp-'+digest(p): p for p in parts}
        return value, parts, verify_parts(value, lambda ref: bodies[ref['id']])

    def test_multiple_bindings_and_format_are_scope_bound_without_changing_plan3(self):
        validate_plan(self.plan)
        initial = scope_digest(self.plan)
        for change in ('binding', 'content'):
            plan = copy.deepcopy(self.plan)
            if change == 'binding': plan['inventory']['preserved_files'][0]['binding']['review_unit_ids'].pop()
            else: plan['inventory']['preserved_files'][0]['content'] = None
            self.assertNotEqual(initial, scope_digest(plan))
        for change in ('foreign', 'duplicate', 'format', 'legacy'):
            plan = copy.deepcopy(self.plan); item = plan['inventory']['preserved_files'][0]
            if change == 'foreign': item['binding']['review_unit_ids'].append('foreign-unit')
            if change == 'duplicate': item['binding']['review_unit_ids'] *= 2
            if change == 'format': item['content'] = 'sdc-execute'
            if change == 'legacy': plan['schema_version'] = 3
            with self.subTest(change=change), self.assertRaises(ReviewError): validate_plan(plan)
        values = copy.deepcopy(self.manifest['preserved_files'])
        values[0]['binding']['review_unit_ids'].pop()
        self.assertEqual(2, len(self.plan['inventory']['preserved_files'][0]['binding']['review_unit_ids']))
        with self.assertRaises(ReviewError): validate_metadata(self.plan, values)

    def test_complete_text_positions_and_multiline_masking_preserve_raw(self):
        raw = ('# 합성\r\n' + 'a'*200 + '\r\nTOKEN=synthetic-private\r\n'
               '-----BEGIN PRIVATE KEY-----\r\nsynthetic-private-key\r\n-----END PRIVATE KEY-----\r\n'
               'create_clock -period 10 [get_ports clk]\r\n').encode()
        with patch('common.sanity_preserved_text.TARGET_SEGMENT_BYTES', 70), patch('common.sanity_preserved_text.TEXT_PART_BYTES', 2048):
            value, parts, segments = self.extracted(raw=raw)
        self.assertGreater(len(parts), 1)
        public = ''.join(s['text'] for s in segments)
        self.assertNotIn('synthetic-private', public)
        self.assertIn('create_clock -period 10', public)
        self.assertEqual(len(raw.decode().splitlines()), len(public.splitlines()))
        self.assertEqual(len(raw), segments[-1]['byte_end'])
        for segment in segments:
            original = raw[segment['byte_start']:segment['byte_end']]
            self.assertEqual(hashlib.sha256(original).hexdigest(), segment['source_sha256'])
        self.assertTrue(value['extraction']['redacted'])

    def test_invalid_encoding_controls_limits_and_partial_parts_fail_closed(self):
        for raw in (b'bad\xff', b'bad\x00'):
            with self.subTest(raw=raw), self.assertRaises(ReviewError): self.extracted(raw=raw)
        with patch('common.sanity_preserved_text.MAX_FILE_BYTES', 2), self.assertRaises(ReviewError):
            self.extracted(raw=b'long')
        with patch('common.sanity_preserved_text.MAX_SEGMENT_BYTES', 2), self.assertRaises(ReviewError):
            self.extracted(raw=b'long\n')
        value, parts, _ = self.extracted()
        damaged = copy.deepcopy(parts[0]); damaged['segments'][0]['text'] += 'extra'
        with self.assertRaises(ReviewError): verify_parts(value, lambda _: damaged)
        missing = copy.deepcopy(value); missing['extraction']['parts'] = []
        with self.assertRaises(ReviewError): verify_parts(missing, lambda _: None)
        empty, parts, segments = self.extracted(raw=b'')
        self.assertEqual(([], [], 0), (parts, segments, empty['extraction']['line_count']))

    def test_actual_model_calls_receive_complete_text_and_validate_separate_citations(self):
        from common.sanity_integration_examples import clustering_example, group_review_example
        from oa.sanity_ai_model import OAAdvisor
        from tests.test_ai_model import chat, environment, criterion, review_reply
        file, _, segments = self.extracted()
        for stage in ('cluster', 'review'):
            packet = clustering_example(with_history=True) if stage == 'cluster' else group_review_example()
            context = packet['context']; context['report']['preserved_sources'] = [file]
            context['preserved_evidence'] = [{'source_id': file['id'], 'segments': segments}]
            reply = packet['reply'] if stage == 'cluster' else review_reply(context, criteria=['synthetic-clock-policy'])
            target = reply['groups'][0]['id'] if stage == 'cluster' else 'review'
            citation = {'target_id': target, 'evidence_ids': [segments[0]['id']], 'reason': '제공된 클록 선언을 직접 비교했다.'}
            def model(request):
                body = json.loads(request.content); actual = json.loads(body['messages'][1]['content'])['context']
                self.assertEqual(context['preserved_evidence'], actual['preserved_evidence'])
                self.assertNotIn('synthetic-private-value', str(body))
                return httpx.Response(200, json=chat({**reply, 'preserved_citations': [citation]}))
            advisor = OAAdvisor(environment(), transport=httpx.MockTransport(model))
            call = lambda: advisor.cluster(context, packet['cases'], packet['identity_fields']) if stage == 'cluster' else advisor.review(context, [], [criterion(context)])
            result = call(); self.assertEqual([citation], result['metadata']['preserved_citations'])
            self.assertTrue(result['metadata']['prompt_version'].endswith('.preserved-text-v1'))
            self.assertNotIn('preserved_citations', result['advice'])
            citation['evidence_ids'] = [file['id']]
            with self.assertRaises(ReviewError): call()
            context['preserved_evidence'][0]['segments'] = []
            with self.assertRaises(ReviewError): call()

    def test_model_input_limit_does_not_send_a_partial_text_or_call_model(self):
        from common.sanity_integration_examples import clustering_example
        from oa.sanity_ai_model import OAAdvisor
        from tests.test_ai_model import environment
        file, _, segments = self.extracted()
        packet = clustering_example(with_history=True)
        context = packet['context']; context['report']['preserved_sources'] = [file]
        context['preserved_evidence'] = [{'source_id': file['id'], 'segments': segments}]
        advisor = OAAdvisor(environment(SANITY_OA_AI_MAX_INPUT_BYTES='100'),
                            transport=httpx.MockTransport(lambda _: self.fail('Must not call model')))
        with self.assertRaises(ReviewError) as caught:
            advisor.cluster(context, packet['cases'], packet['identity_fields'])
        self.assertEqual('AI_INPUT_LIMIT', caught.exception.code)
        self.assertIs(caught.exception.model_called, False)


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class PreservedTextDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_raw_sources import RawSourcesDatabaseTests
        self.case = RawSourcesDatabaseTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        for name in ('011_payload_storage.sql', '012_source_evidence_pages.sql', '013_shared_source_packets.sql'):
            self.case.admin.execute((ROOT/'db'/name).read_text().replace('public.', self.case.schema+'.'))

    def test_shared_body_is_once_per_file_but_bound_to_both_reports_and_exact_raw_restores(self):
        from common.sanity_db import Database
        from oa.sanity_preserved_text import read
        c = self.case; text_plan(c.f, c.root, multi=True)
        sid = c.submit()['submission_id']; self.assertEqual(1, c.bridge()['processed']); c.receive(); c.bridge()
        self.assertEqual('READY', c.client.status(sid)['review_readiness'])
        reports = [r[0] for r in c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND payload ? 'preserved_sources'").fetchall()]
        self.assertEqual(2, len(reports))
        ids = {r['preserved_sources'][0]['id'] for r in reports}; self.assertEqual(1, len(ids))
        refs = {p['id'] for r in reports for f in r['preserved_sources'] for p in f['extraction']['parts']}
        self.assertEqual(2, len(refs))
        for ref in refs:
            self.assertEqual(1, c.admin_query("SELECT count(*) FROM {oa} WHERE workspace_key=%s AND source_packet_ref IS NOT NULL", (ref,)).fetchone()[0])
        with Database('oa') as db:
            for r in reports:
                for file in r['preserved_sources']:
                    actual, segments = read(db, r, file['id'])
                    self.assertEqual(file, actual); self.assertTrue(segments)
                    self.assertNotIn('synthetic-private-value', str(segments))
        with c.store(sid) as store, store.materialize(sid) as bundle:
            for item in c.f['prepared'].plan['inventory']['preserved_files']:
                self.assertEqual(c.f['prepared'].reports[item['path']].read_bytes(), (bundle/'snapshot'/item['path']).read_bytes())

    def test_changed_text_preserves_prior_body_and_corrupt_shared_reference_is_rejected(self):
        from common.sanity_db import Database
        from oa.sanity_preserved_text import read
        c = self.case; text_plan(c.f, c.root)
        def deliver():
            c.submit(); c.bridge(); c.receive(); c.bridge()
        deliver()
        old = c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND payload ? 'preserved_sources'").fetchone()[0]
        old_file = old['preserved_sources'][1]
        c.f['prepared'].reports[old_file['path']].write_bytes(b'create_clock -period 20 [get_ports clk]\n')
        deliver()
        reports = [r[0] for r in c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND payload ? 'preserved_sources'").fetchall()]
        self.assertEqual(2, len(reports)); self.assertIn(old, reports)
        new = next(r for r in reports if r != old)
        self.assertNotEqual(old['analysis_id'], new['analysis_id'])
        with Database('oa') as db:
            self.assertIn('-period 10', str(read(db, old, old_file['id'])))
            self.assertIn('-period 20', str(read(db, new, new['preserved_sources'][1]['id'])))
            with self.assertRaises(ReviewError): read(db, new, old_file['id'])
        ref = old_file['extraction']['parts'][0]['id']
        c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,ARRAY['report_id'],%s::jsonb) WHERE workspace_key=%s", (json.dumps('pf-wrong'), ref))
        with Database('oa') as db, self.assertRaises(ReviewError): read(db, old, old_file['id'])


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class PreservedTextOwnerTests(unittest.TestCase):
    def test_owner_full_ai_cycle_and_authorized_masked_text_view(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests, OWNER, OTHER
        c = TokenlessSubmissionTests(); prepare = c.collection_input
        def collection_input():
            result = prepare(); text_plan(c.f, c.root); return result
        original_transport, observed = httpx.MockTransport, []
        def transport(handler):
            def model(request):
                packet = json.loads(json.loads(request.content)['messages'][1]['content'])
                values = packet['context']['preserved_evidence']; observed.append(copy.deepcopy(values))
                response = handler(request); body = response.json(); value = json.loads(body['choices'][0]['message']['content'])
                target = value['groups'][0]['id'] if 'groups' in value else 'review'
                value['preserved_citations'] = [{'target_id': target, 'evidence_ids': [values[1]['segments'][0]['id']], 'reason': '제공된 입력 제약을 비교했다.'}]
                body['choices'][0]['message']['content'] = json.dumps(value)
                return httpx.Response(200, json=body)
            return original_transport(model)
        try:
            c.setUp()
            with patch.object(c, 'collection_input', collection_input), patch('httpx.MockTransport', side_effect=transport):
                c.test_collection_only_runs_owner_intake_ai_and_human_review_without_changing_source()
            self.assertEqual(2, len(observed)); self.assertEqual(observed[0], observed[1])
            report = c.collection_report(); source = report['preserved_sources'][1]
            base = '/api/workbench/blocks/block-a1/versions/'+report['version_id']+'/reports/'+report['id']
            endpoint = base+'/preserved/'+source['id']
            value = c.success(c.clients[OWNER].get(endpoint))
            self.assertTrue(value['complete']); self.assertNotIn('synthetic-private-value', str(value))
            download = c.clients[OWNER].get(endpoint+'?download=true')
            self.assertEqual(200, download.status_code); self.assertIn('create_clock', download.text)
            self.assertNotIn('synthetic-private-value', download.text)
            self.assertIn(c.clients[OTHER].get(endpoint).status_code, (403, 404))
            self.assertIn(c.clients[OWNER].get(base+'/preserved/pf-'+'0'*64).status_code, (403, 404))
        finally:
            c.doCleanups()
