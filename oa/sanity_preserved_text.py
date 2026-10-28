"""OA reads only published, authorized report references to masked source text."""
from common.sanity_submission import check
from common.sanity_preserved_text import verify_parts
from common.sanity_shared_sources import SharedSourcePackets
from oa.sanity_workspace import record_id


def read(db, report, source_id):
    file = next((f for f in report.get('preserved_sources', []) if f['id'] == source_id), None)
    check(file is not None, 'NOT_FOUND')
    check(file.get('content_available_to_ai') is True, 'PRESERVED_TEXT_NOT_AVAILABLE')
    shared = SharedSourcePackets(db)
    def part(ref):
        row = db.execute("SELECT * FROM {oa} WHERE record_type='WS_SOURCE_PART' AND workspace_key=%s", (ref['id'],)).fetchone()
        check(row and row['workspace_project_ref'] == record_id('WS_PROJECT', report['project_id'])
              and row['workspace_block_ref'] == record_id('WS_BLOCK', report['block_id']), 'PRESERVED_TEXT_CORRUPT')
        return shared.restore_part(row)['payload']
    return file, verify_parts(file, part)


def enrich(db, context):
    files = context['report'].get('preserved_sources', [])
    if any(f.get('content_available_to_ai') for f in files):
        context['preserved_evidence'] = [{'source_id': f['id'], 'segments': read(db, context['report'], f['id'])[1]}
                                         for f in files if f.get('content_available_to_ai')]
    return context
