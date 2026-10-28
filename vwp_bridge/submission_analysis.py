"""Reuse checked parsers for each actual source; stream complete OA record parts."""
from __future__ import annotations

import copy
from datetime import timezone

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import lock_key
from common.sanity_parsers import ParserRegistry
from common.sanity_privacy import SecretGuard
from common.sanity_payload_storage import PrivatePayloadStorage
from common.sanity_evidence_pages import (EvidencePageStorage, KIND as PAGE_KIND, MAX_BYTES as PAGE_BYTES,
    MAX_RECORDS as PAGE_RECORDS, decode_page, encode_page, page_key)
from common.sanity_submission import (MAX_PARTS, PART_BYTES, PART_RECORDS, check, packet_base, source_execution,
    source_unit, source_version, validate_packet)
from common.sanity_submission_files import regular
from common.sanity_submission_intake import unverified
from vwp_bridge.main import analyze_unit
from vwp_bridge.sanity_evidence import PgEvidence

SOURCE_PRODUCER_VERSION = "postgresql-source-2.1"


class SourceEvidence(PgEvidence):
    """Parsing is independent of the later AI/human grouping workflow."""
    def __init__(self, db, *args, **kwargs):
        self.payload_storage = PrivatePayloadStorage(db)
        self.page_storage = EvidencePageStorage(db)
        self.page_records, self.page_size = [], 16
        super().__init__(db, *args, **kwargs)
        try:
            kinds = {row['record_type'] for row in db.execute(
                "SELECT DISTINCT record_type FROM {vwp} WHERE analysis_id=%s "
                "AND record_type IN ('EVIDENCE','SOURCE_EVIDENCE_PAGE')", (self.analysis_id,)).fetchall()}
            check(len(kinds) <= 1, 'EVIDENCE_PAGE_CORRUPT')
            self.use_pages = self.page_storage.enabled and 'EVIDENCE' not in kinds
            check(PAGE_KIND not in kinds or self.use_pages, 'EVIDENCE_PAGE_SCHEMA_REQUIRED')
        except ReviewError:
            db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(self.analysis_id),))
            raise

    def record(self, unit, file, index, fields, raw, location, status, reasons):
        rid = "r-" + digest([self.run['run_id'],unit['review_unit_id'],file['file_id'],index])
        payload = {'record_id':rid,'record_index':index,'file_id':file['file_id'],**location,
                   'fields':fields,'check_status':status,'reasons':reasons,
                   'review_unit_id':unit['review_unit_id'],'item_id':unit['item_id'],
                   'rule_version':unit['rule_version'],'finding_group_id':None}
        if self.use_pages:
            check(type(index) is int and index == self.record_count + 1, 'SOURCE_COVERAGE_MISMATCH')
            item = {'payload': payload, 'raw': raw}
            size = len(canonical_json(item)) + 1
            check(size + 16 <= PAGE_BYTES, 'RECORD_TRANSFER_LIMIT')
            if self.page_records and self.page_size + size > PAGE_BYTES:
                self._stage_page()
            self.page_records.append(item)
            self.page_size += size
            self.record_count += 1
            if len(self.page_records) == PAGE_RECORDS:
                self._stage_page()
            return
        self.stage('EVIDENCE',rid,payload,'RECORDED',unit['review_unit_id'],
                   self.row_id('UNIT',unit['review_unit_id']),raw=raw)

    def _stage_page(self):
        if not self.page_records:
            return
        header, packed = encode_page(self.page_records)
        unit = self.page_records[0]['payload']['review_unit_id']
        self.page_records, self.page_size = [], 16
        self.stage(PAGE_KIND, page_key(header['record_index']), header, 'RECORDED',
                   unit, self.row_id('UNIT', unit), page_bytes=packed)

    def flush(self):
        # A partial page has not been staged yet; never claim it is durable.
        total = self.record_count
        self.record_count -= len(self.page_records)
        try:
            super().flush()
        finally:
            self.record_count = total

    def unit_result(self, result):
        self._stage_page()
        super().unit_result(result)

    def seal_source(self, result):
        """Seal parsing coverage, without pretending parser output is a partition."""
        self._stage_page()
        self.flush()
        actual = {r['record_type']:r['n'] for r in self.db.execute(
            'SELECT record_type,count(*) AS n FROM {vwp} WHERE analysis_id=%s GROUP BY record_type',
            (self.analysis_id,)).fetchall()}
        expected = {'RUN':1,'UNIT':1,'FILE':self.expected_files,'EVIDENCE':result['record_count']}
        evidence_count = actual.get('EVIDENCE', 0)
        if actual.get(PAGE_KIND):
            evidence_count = self.db.execute("SELECT COALESCE(sum((payload->>'record_count')::bigint),0) AS n "
                "FROM {vwp} WHERE analysis_id=%s AND record_type='SOURCE_EVIDENCE_PAGE'",
                (self.analysis_id,)).fetchone()['n']
        check(all(actual.get(k,0)==v for k,v in expected.items() if k != 'EVIDENCE')
              and evidence_count == result['record_count']
              and not set(actual)-set(expected)-{PAGE_KIND}
              and not (actual.get(PAGE_KIND) and actual.get('EVIDENCE')),
              'SOURCE_COVERAGE_MISMATCH')
        counts = {'PASS': 0, 'FAIL': 0, 'UNKNOWN': 0}
        total = 0
        for row in private_records(self.db, self.analysis_id):
            total += 1
            counts[row['payload']['check_status']] += 1
        check(total == result['record_count'], 'SOURCE_COVERAGE_MISMATCH')
        check(all(counts.get(k,0)==v for k,v in result['status_counts'].items()),'SOURCE_COVERAGE_MISMATCH')
        state = {'source_protocol':2,'counts':expected,'result_sha256':digest(result)}
        if self.sealed:
            check(self.root_state==state,'EVIDENCE_CONFLICT')
        else:
            with self.db.transaction():
                self.db.execute('SELECT record_id FROM {vwp} WHERE record_id=%s FOR UPDATE',(self.analysis_id,))
                self.set_state(self.analysis_id,state,'SEALED')


def analyze_source(db, store, collection, original, file, snapshot_root, *, confirmed, registry, record_execution=True):
    uid, execution = source_unit(collection,original), source_execution(collection,original)
    raw_sha = file['sha256'] if file else None
    if file and record_execution:
        with db.transaction():
            db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(execution),))
            store.immutable('SOURCE_RUN',execution,{'execution_id':execution,'unit_key':uid,
                'source':original['source'],'raw_sha256':raw_sha})
    source = original['source']
    run = {'run_id':execution,'project':collection['project_key'],'design':collection['block_key'],
           'stage':collection['stage'],'tool':source['tool'],'tool_version':source['tool_version']}
    unit = {k:copy.deepcopy(v) for k,v in original.items() if k not in ('source','family')}
    unit.update(review_unit_id=uid,report='reports/source.rpt')
    inventory = {'confirmed':confirmed,'source':'verified-source-unit-v2','version':'2','units':[unit]}
    inventory['sha256'] = digest(inventory)
    source_file = ({'file_id':'f-'+digest([execution,raw_sha]),'path':'reports/source.rpt',
                    'sha256':raw_sha,'size_bytes':file['size_bytes']} if file else None)
    manifest = {'run':run,'inventory':inventory,'files':[source_file] if file else [],'unexpected_files':[]}
    contract = (registry or ParserRegistry()).contract(run,[unit])
    ev = SourceEvidence(db,manifest,parser_contract=contract,producer_version=SOURCE_PRODUCER_VERSION)
    try:
        parsing_file = {**source_file,'path':file['path']} if file else None
        result = analyze_unit(unit,parsing_file,snapshot_root,ev,registry)
        ev.unit_result(result)
        ev.seal_source(result)
        created = db.execute("SELECT created_at FROM {vwp} WHERE record_id=%s",(ev.analysis_id,)).fetchone()['created_at']
        lines, last = 0, b''
        if file:
            stream,_ = regular(snapshot_root/file['path'],file['size_bytes'])
            with stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    lines += block.count(b'\n')
                    last = block[-1:]
            lines += bool(last and last!=b'\n')
        return {'version_id':source_version(collection,source),'unit_key':uid,'execution_id':execution,
                'source':source,'item_id':original['item_id'],'family':original['family'],
                'parser':original['parser'],'rule_version':original['rule_version'],
                'analysis_id':str(ev.analysis_id),'parser_contract':contract or [],
                'analysis_created_at':created.astimezone(timezone.utc).isoformat(timespec='microseconds'),
                'raw_line_count':lines,
                'raw_sha256':raw_sha,'result':result}
    finally:
        db.conn.execute('SELECT pg_advisory_unlock(%s)',(lock_key(ev.analysis_id),))


def private_records(db, analysis_id):
    """Restore all logical records with bounded reads and exact coverage order."""
    after = 0
    storage = PrivatePayloadStorage(db)
    pages = EvidencePageStorage(db)
    root = db.execute("SELECT payload,state,status FROM {vwp} WHERE record_id=%s AND record_type='RUN'",
                      (analysis_id,)).fetchone()
    expected = None
    if root and root['status'] == 'SEALED' and root['state'].get('source_protocol') == 2:
        expected = root['state'].get('counts', {}).get('EVIDENCE')
        check(type(expected) is int and expected >= 0, 'SOURCE_COVERAGE_MISMATCH')
    kinds = {row['record_type'] for row in db.execute(
        "SELECT DISTINCT record_type FROM {vwp} WHERE analysis_id=%s "
        "AND record_type IN ('EVIDENCE','SOURCE_EVIDENCE_PAGE')", (analysis_id,)).fetchall()}
    check(len(kinds) <= 1, 'EVIDENCE_PAGE_CORRUPT')
    if PAGE_KIND in kinds:
        check(pages.enabled, 'EVIDENCE_PAGE_SCHEMA_REQUIRED')
        while True:
            identities = db.execute("SELECT record_id FROM {vwp} WHERE analysis_id=%s "
                "AND record_type='SOURCE_EVIDENCE_PAGE' AND (payload->>'record_index')::bigint>%s "
                "ORDER BY (payload->>'record_index')::bigint LIMIT 16", (analysis_id, after)).fetchall()
            if not identities:
                check(expected is None or after == expected, 'SOURCE_COVERAGE_MISMATCH')
                return
            for identity in identities:
                row = db.execute('SELECT * FROM {vwp} WHERE record_id=%s', (identity['record_id'],)).fetchone()
                for restored in decode_page(row):
                    check(restored['payload']['record_index'] == after + 1, 'SOURCE_COVERAGE_MISMATCH')
                    p = restored['payload']
                    check(root is not None and p['record_id'] == 'r-' + digest([
                        root['payload']['run']['run_id'], p['review_unit_id'], p['file_id'], p['record_index']]),
                        'EVIDENCE_PAGE_CORRUPT')
                    after += 1
                    yield restored
    while True:
        rows = db.execute("SELECT entity_key,payload,raw_bytes,raw_sha256" + storage.selection + " FROM {vwp} WHERE analysis_id=%s "
            "AND record_type='EVIDENCE' AND (payload->>'record_index')::bigint>%s "
            "ORDER BY (payload->>'record_index')::bigint LIMIT 256",(analysis_id,after)).fetchall()
        if not rows:
            check(expected is None or after == expected, 'SOURCE_COVERAGE_MISMATCH')
            break
        for row in rows:
            row = storage.restore('EVIDENCE', row)
            check(row['payload']['record_index'] == after + 1, 'SOURCE_COVERAGE_MISMATCH')
            after += 1
            yield row


def public_records(db, analysis_id, guard):
    for row in private_records(db, analysis_id):
        p = row['payload']
        raw = bytes(row['raw_bytes']).decode('utf-8')
        fields, masked = guard.redact(p['fields']), guard.text(raw)
        yield {'id':p['record_id'],'fields':fields,'check_status':p['check_status'],
            'raw':masked,'line_start':p['line_start'],'line_end':p['line_end'],
            **{key:p[key] for key in ('byte_start','byte_end','json_pointer') if key in p},
            'reasons':p['reasons'],'evidence_id':p['record_id'],'source_raw_sha256':row['raw_sha256'],
            'redacted':masked!=raw or fields!=p['fields']}


def record_parts(records, report_id):
    records_part, size, part_index = [], 0, 0
    # Leave a conservative envelope budget; exact packets are checked on publication.
    budget = PART_BYTES - 65536
    for record in records:
        encoded = len(canonical_json(record)) + 1
        check(encoded <= budget, 'RECORD_TRANSFER_LIMIT')
        if records_part and (len(records_part)>=PART_RECORDS or size+encoded>budget):
            yield {'report_id':report_id,'part_index':part_index,'records':records_part}
            records_part, size, part_index = [], 0, part_index+1
        records_part.append(record); size += encoded
    if records_part:
        yield {'report_id':report_id,'part_index':part_index,'records':records_part}


def build_transfer(db, store, submission, manifest, root, *, confirmed, registry):
    collection, reports, position = submission['plan']['collection'], [], 1
    guard = SecretGuard()
    # Structural scope/approval facts cannot be silently changed by redaction.
    guard.ensure_public(submission['plan'])
    files = {f['path']:f for f in manifest['files']}
    blockers = ([] if confirmed else ['INVENTORY_UNCONFIRMED']) + (['UNEXPECTED_REPORTS'] if manifest['unexpected_files'] else [])
    contracts = []
    for unit in submission['plan']['inventory']['units']:
        source = unit['source']
        run = {'tool':source['tool'],'tool_version':source['tool_version']}
        contracts.append((registry or ParserRegistry()).contract(run,[unit]))
    collection_id = 'sc-'+digest([submission['content_sha256'],confirmed,SOURCE_PRODUCER_VERSION,contracts])
    # A prior revision may have committed some immutable parts before crashing.
    # Reject before writing any new analysis/part under the same submission UUID.
    conflict = db.execute("SELECT 1 FROM {vwp} WHERE record_type='SOURCE_OUTBOX' "
        "AND payload->>'submission_id'=%s AND payload->'packet'->>'collection_id' IS DISTINCT FROM %s LIMIT 1",
        (submission['submission_id'],collection_id)).fetchone()
    check(not conflict,'SUBMISSION_CONTRACT_MISMATCH')
    base = packet_base(submission,collection_id)
    for unit in submission['plan']['inventory']['units']:
        value = analyze_source(db,store,collection,unit,files.get(unit['report']),root,confirmed=confirmed,
                               registry=registry,record_execution=not unverified(submission['actor']))
        rid = 'sr-'+digest([value['execution_id'],value['raw_sha256'],value['analysis_id']])
        header = {**value,'report_id':rid,'parts':[],'record_bytes':0}
        for part in record_parts(public_records(db,value['analysis_id'],guard),rid):
            key = 'sp-'+digest(part)
            header['parts'].append({'id':key,'sha256':digest(part),'count':len(part['records'])})
            header['record_bytes'] += len(canonical_json(part['records']))
            header['raw_line_count'] = max(header['raw_line_count'],max(r['line_end'] for r in part['records']))
            packet = {**base,'body':{'id':key,'body':part}}
            validate_packet(packet)
            with db.transaction():
                store.put_outbox(submission['submission_id'],position,'SOURCE_PART',packet)
            position += 1
            check(position <= MAX_PARTS,'SOURCE_MESSAGE_LIMIT')
        check(sum(p['count'] for p in header['parts']) == value['result']['record_count'],'SOURCE_COVERAGE_MISMATCH')
        reports.append(header)
    output = {'plan':submission['plan'],'inventory_confirmed':confirmed,'global_blockers':blockers,'reports':reports,
              'producer':SOURCE_PRODUCER_VERSION}
    plan_packet = {**base,'body':output}
    validate_packet(plan_packet)
    commit = {**base,'body':{'manifest_sha256':digest(output),'parts':position-1}}
    validate_packet(commit)
    with db.transaction():
        store.put_outbox(submission['submission_id'],0,'SOURCE_MANIFEST',plan_packet)
        store.put_outbox(submission['submission_id'],position,'SOURCE_COMMIT',commit)
        store.state(submission['submission_id'],'DB_QUEUED',{'collection_id':collection_id,'next_publish':0,
                    'message_count':position+1,'blockers':blockers})
    return collection_id
