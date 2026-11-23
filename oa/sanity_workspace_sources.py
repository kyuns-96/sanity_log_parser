"""Immutable source page access; no source files or VWP private DB access on OA."""
from common.sanity_db import json_value
from common.sanity_submission import check
from oa.sanity_source_reader import SourceReader

MAX_CONTEXT_BYTES = 256 * 1024 * 1024
MAX_CONTEXT_RECORDS = 1000000

# New submissions are distinct runs ordered by their VWP acceptance time.
# Legacy reports retain their verified execution/analysis ordering.
CURRENT_SOURCES_SQL = """(payload->>'source_storage' IS DISTINCT FROM 'PARTS_V2' OR record_id IN (
    SELECT DISTINCT ON (payload->>'source_unit_key') record_id FROM {oa}
    WHERE record_type='WS_REPORT' AND payload->>'source_storage'='PARTS_V2'
    ORDER BY payload->>'source_unit_key',payload->>'submitted_at' DESC NULLS LAST,
             (payload->'source'->>'execution_order')::bigint DESC,
             payload->>'analysis_created_at' DESC,created_at DESC,workspace_key))"""


def delivery_status(db, header):
    value = db.execute("SELECT state->'collection_blockers' AS blockers FROM {oa} WHERE record_type='WS_SUBMISSION' "
        "AND state->>'delivery_state'='OA_STORED' AND state->'reports' @> %s",
        (json_value([header['id']]),)).fetchall()
    ready = bool(value) and any(not row['blockers'] for row in value)
    blockers = [] if ready else sorted({code for row in value for code in (row['blockers'] or [])}) or ['SOURCE_NOT_PUBLISHED']
    return {'delivery_ready':ready,'delivery_blockers':blockers}


def _records(value, header, index):
    check(value.owner == header['id'] and value.index == index and value.records_bytes is not None, 'STATE_CORRUPT')
    return value.decode()['records']


def part(db, header, ref, index):
    value = next(SourceReader(db).parts(header, [ref]))
    return _records(value, header, index)


def hydrate(db, header):
    check(header['record_count'] <= MAX_CONTEXT_RECORDS and header['source_bytes'] <= MAX_CONTEXT_BYTES,
          'SOURCE_CONTEXT_LIMIT','Complete review input exceeds the explicit context budget')
    records, size = [], 0
    reader = SourceReader(db)
    for index, value in enumerate(reader.parts(header, header['source_parts'])):
        values = _records(value, header, index)
        size += value.records_bytes
        records.extend(values)
    check(len(records)==header['record_count'] and size==header['source_bytes'],'STATE_CORRUPT')
    return {**header,'records':records}


def records_page(db, header, *, offset=0, limit=50, search='', member_ids=None):
    check(type(offset) is int and offset>=0 and type(limit) is int and 1<=limit<=100,'INVALID_REQUEST')
    needle, found, items, visited = search.casefold(), 0, [], 0
    counts = {'PASS':0,'FAIL':0,'UNKNOWN':0}
    selected, indexes, positions = [], [], []
    for index, ref in enumerate(header['source_parts']):
        if needle or member_ids is not None or visited + ref['count'] > offset and visited < offset + limit:
            selected.append(ref); indexes.append(index); positions.append(visited)
        visited += ref['count']
    visited = 0
    for index, position, value in zip(indexes, positions, SourceReader(db).parts(header, selected)):
        if not needle and member_ids is None: visited = position
        values = _records(value, header, index)
        for record in values:
            if member_ids is not None and record['id'] not in member_ids:
                continue
            if needle and needle not in (record['id']+' '+record['raw']+' '+str(record['fields'])).casefold():
                continue
            counts[record['check_status']] += 1
            position = visited if not needle and member_ids is None else found
            if offset<=position<offset+limit:
                items.append({**record,'raw':record['raw'][:4096],'raw_display_truncated':len(record['raw'])>4096})
            found += 1
            visited += 1
    total = header['record_count'] if not needle and member_ids is None else found
    return {'items':items,'total':total,'offset':offset,'limit':limit,'counts':counts}
