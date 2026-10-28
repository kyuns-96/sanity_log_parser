"""Current source-text provenance and model citations, separate from log/criterion IDs."""
from common.sanity_common import digest
from common.sanity_submission import check
from common.sanity_preserved_text import KIND, verify_parts, validate_extraction


def available(context):
    return [f for f in context['report'].get('preserved_sources', []) if f.get('content_available_to_ai') is True]


def evidence(context):
    files, values = available(context), context.get('preserved_evidence', [])
    check(isinstance(values, list) and len(values) == len(files), 'INVALID_AI_INPUT')
    ids = set()
    for file, value in zip(files, values):
        validate_extraction(file)
        check(isinstance(value, dict) and set(value) == {'source_id', 'segments'}
              and value['source_id'] == file['id'] and isinstance(value['segments'], list)
              and len(value['segments']) == sum(p['count'] for p in file['extraction']['parts']), 'INVALID_AI_INPUT')
        bodies, offset = {}, 0
        for index, ref in enumerate(file['extraction']['parts']):
            bodies[ref['id']] = {'kind': KIND, 'source_id': file['id'], 'part_index': index,
                                 'segments': value['segments'][offset:offset + ref['count']]}
            offset += ref['count']
        verified = verify_parts(file, lambda ref: bodies[ref['id']])
        current = {s['id'] for s in verified}
        check(len(current) == len(verified) and not current & ids, 'INVALID_AI_INPUT')
        ids.update(current)
    return ids


def validate_citations(citations, advice, context):
    ids = evidence(context)
    targets = {g['id'] for g in advice['groups']} if 'groups' in advice else {'review'}
    check(isinstance(citations, list) and len(citations) <= len(targets), 'INVALID_PRESERVED_CITATION')
    seen = set()
    for cite in citations:
        check(isinstance(cite, dict) and set(cite) == {'target_id', 'evidence_ids', 'reason'}
              and isinstance(cite['target_id'], str) and cite['target_id'] in targets and cite['target_id'] not in seen
              and isinstance(cite['reason'], str) and 1 <= len(cite['reason'].strip()) <= 2000,
              'INVALID_PRESERVED_CITATION')
        used = cite['evidence_ids']
        check(isinstance(used, list) and 1 <= len(used) <= 20 and all(isinstance(i, str) for i in used)
              and len(set(used)) == len(used) and set(used) <= ids, 'INVALID_PRESERVED_CITATION')
        seen.add(cite['target_id'])
    return citations


def citation_sources(citations, context):
    used = {key for cite in citations for key in cite['evidence_ids']}
    keys = ('id', 'source_id', 'line_start', 'line_end', 'source_sha256', 'redacted')
    return [{key: s[key] for key in keys} for f in context.get('preserved_evidence', [])
            for s in f['segments'] if s['id'] in used]


def receipt(metadata, advice, context):
    if available(context):
        check(metadata.get('preserved_input_sha256') == digest(context.get('preserved_evidence')),
              'INVALID_PRESERVED_CITATION')
        citations = validate_citations(metadata.get('preserved_citations'), advice, context)
        check(metadata.get('preserved_citation_sources') == citation_sources(citations, context),
              'INVALID_PRESERVED_CITATION')
    else:
        check('preserved_citations' not in metadata and 'preserved_input_sha256' not in metadata
              and 'preserved_citation_sources' not in metadata,
              'INVALID_PRESERVED_CITATION')


def split_reply(value, context):
    if not available(context):
        return value, None
    check(isinstance(value, dict) and 'preserved_citations' in value, 'INVALID_PRESERVED_CITATION')
    return {k: v for k, v in value.items() if k != 'preserved_citations'}, value['preserved_citations']


def model_metadata(metadata, citations, advice, context):
    if available(context):
        validate_citations(citations, advice, context)
        return {**metadata, 'preserved_citations': citations,
                'preserved_citation_sources': citation_sources(citations, context),
                'preserved_input_sha256': digest(context['preserved_evidence'])}
    return metadata
