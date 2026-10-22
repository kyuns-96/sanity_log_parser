"""Immutable source page access; no source files or VWP private DB access on OA."""
from common.sanity_common import canonical_json, digest
from common.sanity_db import json_value
from common.sanity_submission import check

MAX_CONTEXT_BYTES = 256 * 1024 * 1024
MAX_CONTEXT_RECORDS = 1000000

# Source execution order is a verified source fact. Analysis creation order is
# from the VWP DB, never the arrival time / hash / lexical ECO name on OA.
CURRENT_SOURCES_SQL = """(payload->>'source_storage' IS DISTINCT FROM 'PARTS_V2' OR record_id IN (
    SELECT DISTINCT ON (payload->>'source_unit_key') record_id FROM {oa}
    WHERE record_type='WS_REPORT' AND payload->>'source_storage'='PARTS_V2'
    ORDER BY payload->>'source_unit_key',(payload->'source'->>'execution_order')::bigint DESC,
             payload->>'analysis_created_at' DESC,created_at DESC,workspace_key))"""


def delivery_status(db, header):
    value = db.execute("SELECT state->'collection_blockers' AS blockers FROM {oa} WHERE record_type='WS_SUBMISSION' "
        "AND state->>'delivery_state'='OA_STORED' AND state->'reports' @> %s",
        (json_value([header['id']]),)).fetchall()
    ready = bool(value) and any(not row['blockers'] for row in value)
    blockers = [] if ready else sorted({code for row in value for code in (row['blockers'] or [])}) or ['SOURCE_NOT_PUBLISHED']
    return {'delivery_ready':ready,'delivery_blockers':blockers}


def part(db, header, ref, index):
    row = db.execute("SELECT payload,workspace_project_ref,workspace_block_ref FROM {oa} "
        "WHERE record_type='WS_SOURCE_PART' AND workspace_key=%s",(ref['id'],)).fetchone()
    from oa.sanity_workspace import record_id
    check(row and row['workspace_project_ref']==record_id('WS_PROJECT',header['project_id'])
          and row['workspace_block_ref']==record_id('WS_BLOCK',header['block_id']),'STATE_CORRUPT')
    value = row['payload']
    check(value['report_id']==header['id'] and value['part_index']==index and digest(value)==ref['sha256']
          and len(value['records'])==ref['count'],'STATE_CORRUPT')
    return value['records']


def hydrate(db, header):
    check(header['record_count'] <= MAX_CONTEXT_RECORDS and header['source_bytes'] <= MAX_CONTEXT_BYTES,
          'SOURCE_CONTEXT_LIMIT','Complete review input exceeds the explicit context budget')
    records, size = [], 0
    for index,ref in enumerate(header['source_parts']):
        values = part(db,header,ref,index)
        size += len(canonical_json(values))
        records.extend(values)
    check(len(records)==header['record_count'] and size==header['source_bytes'],'STATE_CORRUPT')
    return {**header,'records':records}


def records_page(db, header, *, offset=0, limit=50, search='', member_ids=None):
    check(type(offset) is int and offset>=0 and type(limit) is int and 1<=limit<=100,'INVALID_REQUEST')
    needle, found, items, visited = search.casefold(), 0, [], 0
    counts = {'PASS':0,'FAIL':0,'UNKNOWN':0}
    for index,ref in enumerate(header['source_parts']):
        if not needle and member_ids is None and (visited+ref['count']<=offset or visited>=offset+limit):
            visited += ref['count']
            continue
        values = part(db,header,ref,index)
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


def submissions(db, user_id, block_id, *, offset=0, limit=25, workspace=None):
    from oa.sanity_workspace import WorkspaceStore, record_id
    workspace = WorkspaceStore(db) if workspace is None else workspace
    check(workspace.db is db,'AUTHORIZATION_CONFIG')
    workspace.access(user_id,block_id)
    check(type(offset) is int and offset>=0 and type(limit) is int and 1<=limit<=100,'INVALID_REQUEST')
    total = db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_block_ref=%s",
                       (record_id('WS_BLOCK',block_id),)).fetchone()['n']
    rows = db.execute("SELECT workspace_key,payload->'scope' AS scope,payload->'actor'->>'user_id' AS submitted_by,"
        "state->>'delivery_state' AS delivery_state,state->'receipt' AS receipt,created_at,updated_at "
        "FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_block_ref=%s "
        "ORDER BY created_at DESC,workspace_key LIMIT %s OFFSET %s",(record_id('WS_BLOCK',block_id),limit,offset)).fetchall()
    return {'items':[{'id':r['workspace_key'],'scope':r['scope'],'submitted_by':r['submitted_by'],
        'delivery_state':r['delivery_state'],'review_readiness':(r['receipt'] or {}).get('review_readiness','BLOCKED'),
        'blockers':(r['receipt'] or {}).get('blockers',[]),'workspace_links':(r['receipt'] or {}).get('workspace_links',[]),
        'created_at':r['created_at'].isoformat(),'updated_at':r['updated_at'].isoformat()} for r in rows],
        'total':total,'offset':offset,'limit':limit}
