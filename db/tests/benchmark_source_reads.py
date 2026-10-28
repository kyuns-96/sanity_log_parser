"""Compare exact previous hydration with cold/warm reads on synthetic source parts.

Called inside benchmark_total_storage's disposable fixture. No external DB,
file or model endpoint is accepted by the public benchmark entry point.
"""
import hashlib
import statistics
import time

from common.sanity_common import canonical_json, digest
from common.sanity_shared_sources import SharedSourcePackets
from oa.sanity_source_reader import CACHE
from oa.sanity_workspace_sources import hydrate


def legacy_hydrate(db, header):
    # Exact pre-cache read/check path for the same immutable header and bytes.
    records, size = [], 0
    for index, ref in enumerate(header['source_parts']):
        row = db.execute("SELECT * FROM {oa} WHERE record_type='WS_SOURCE_PART' AND workspace_key=%s",(ref['id'],)).fetchone()
        from oa.sanity_workspace import record_id
        assert row and row['workspace_project_ref'] == record_id('WS_PROJECT',header['project_id'])
        assert row['workspace_block_ref'] == record_id('WS_BLOCK',header['block_id'])
        value = SharedSourcePackets(db).restore_part(row)['payload']
        assert value['report_id'] == header['id'] and value['part_index'] == index
        assert digest(value) == ref['sha256'] and len(value['records']) == ref['count']
        records.extend(value['records']); size += len(canonical_json(value['records']))
    assert len(records) == header['record_count'] and size == header['source_bytes']
    return {**header,'records':records}


def measure(db, header):
    from unittest.mock import patch
    from common.sanity_privacy import SecretGuard
    from common.sanity_common import unpack_json
    runs, expected = [], None
    for label, operation in [('baseline',legacy_hydrate),('cold',hydrate),('warm',hydrate),('warm',hydrate),('warm',hydrate)]:
        if label != 'warm': CACHE.clear()
        counts = {'queries':0,'guard_scans':0,'gzip_decodes':0}
        execute, conn_execute = db.execute, db.conn.execute
        # db.execute delegates to conn.execute; count only the lower boundary.
        def counted_query(*args, **kwargs):
            counts['queries'] += 1; return conn_execute(*args, **kwargs)
        guard = SecretGuard.ensure_public if label == 'baseline' else SecretGuard.ensure_public_json
        def scan(instance, value):
            counts['guard_scans'] += 1; return guard(instance,value)
        def decode(*args, **kwargs):
            counts['gzip_decodes'] += 1; return unpack_json(*args, **kwargs)
        scan_name = 'ensure_public' if label == 'baseline' else 'ensure_public_json'
        decode_name = 'common.sanity_shared_sources.unpack_json' if label == 'baseline' else 'oa.sanity_source_reader.unpack_json'
        with patch.object(db.conn,'execute',side_effect=counted_query), patch.object(SecretGuard,scan_name,scan), patch(decode_name,decode):
            start=time.perf_counter(); value=operation(db,header); seconds=time.perf_counter()-start
        actual=hashlib.sha256(canonical_json(value)).hexdigest()
        if expected is None: expected=actual
        if actual != expected: raise RuntimeError('Hydration result changed')
        runs.append({'kind':label,'seconds':round(seconds,6),**counts})
    return {'parts':len(header['source_parts']),'records':header['record_count'],'source_bytes':header['source_bytes'],
            'complete_result_sha256':expected,'identical_results':True,'runs':runs,
            'cold_target_seconds':3,'warm_target_seconds':1,
            'cold_target_met':runs[1]['seconds']<=3,
            'warm_target_met':max(r['seconds'] for r in runs[2:])<=1,
            'warm_median_seconds':statistics.median(r['seconds'] for r in runs[2:]),
            'cache_body_bytes':CACHE.bytes,'cache_entries':len(CACHE.values),
            'scope':'complete hydration on one connected local PostgreSQL fixture; excludes HTTP/RBAC setup/network'}
