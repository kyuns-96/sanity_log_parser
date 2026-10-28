"""Explicit preserved source evidence. Originals stay in the private RAW store.

Plan 3 declares files; only verified manifests yield PRESERVED metadata. No
parser/model inference, file I/O or implicit downgrade of unit reports here.
"""
import copy

from common.sanity_common import canonical_json, digest

PRODUCER = 'postgresql-source-2.3'
MAX_FILES = 256
ROLES = frozenset(('canonical_companion', 'constraint_input'))


def declarations(plan):
    return plan['inventory'].get('preserved_files', [])


def validate_declarations(plan):
    from common.sanity_submission import check, relative_report
    items = declarations(plan)
    check(isinstance(items, list) and 1 <= len(items) <= MAX_FILES, 'INVALID_PRESERVED_SOURCE')
    units = {u['review_unit_id']: u for u in plan['inventory']['units']}
    paths = {u['report'] for u in units.values()}
    for item in items:
        keys = {'path', 'role', 'binding'} | ({'content'} if plan['schema_version'] == 4 else set())
        check(isinstance(item, dict) and set(item) == keys, 'INVALID_PRESERVED_SOURCE')
        if plan['schema_version'] == 4:
            from common.sanity_preserved_text import FORMAT
            check(item['content'] in (None, FORMAT), 'INVALID_PRESERVED_SOURCE')
        relative_report(item['path'])
        check(item['path'] not in paths and isinstance(item['role'], str) and item['role'] in ROLES,
              'INVALID_PRESERVED_SOURCE')
        paths.add(item['path'])
        bind = item['binding']
        kinds = ('unit', 'execution', 'units', 'executions') if plan['schema_version'] == 4 else ('unit', 'execution')
        check(isinstance(bind, dict) and bind.get('kind') in kinds, 'INVALID_PRESERVED_SOURCE')
        key = binding_key(bind)
        check(set(bind) == {'kind', key}, 'INVALID_PRESERVED_BINDING')
        ids = binding_ids(bind)
        check(isinstance(ids, list) and 1 <= len(ids) <= len(units)
              and all(isinstance(i, str) and i in units for i in ids)
              and len(set(ids)) == len(ids), 'INVALID_PRESERVED_BINDING')
    return items


def binding_key(bind):
    return {'unit': 'review_unit_id', 'execution': 'anchor_unit_id',
            'units': 'review_unit_ids', 'executions': 'anchor_unit_ids'}[bind['kind']]


def binding_ids(bind):
    value = bind[binding_key(bind)]
    return value if bind['kind'] in ('units', 'executions') else [value]


def anchors(plan, item):
    ids = binding_ids(item['binding'])
    return [u for u in plan['inventory']['units'] if u['review_unit_id'] in ids]


def anchor(plan, item):
    return anchors(plan, item)[0]


def scope_declarations(plan):
    from common.sanity_submission import source_unit
    values = []
    for p in declarations(plan):
        value = {'path': p['path'], 'role': p['role'], 'binding_kind': p['binding']['kind']}
        if plan['schema_version'] == 4:
            value.update(content=p['content'], unit_keys=sorted(source_unit(plan['collection'], u) for u in anchors(plan, p)))
        else:
            value['unit_key'] = source_unit(plan['collection'], anchor(plan, p))
        values.append(value)
    return sorted(values, key=lambda p: p['path'])


def declared_paths(plan):
    return {p['path'] for p in declarations(plan)}


def metadata(plan, files):
    """Resolve verified file descriptors into immutable, scoped references."""
    from common.sanity_submission import check, source_execution, source_unit
    by_path = {f['path']: f for f in files}
    result = []
    for item in sorted(declarations(plan), key=lambda p: p['path']):
        check(item['path'] in by_path, 'PRESERVED_FILE_MISSING')
        file, unit = by_path[item['path']], anchor(plan, item)
        value = {**item, 'binding': copy.deepcopy(item['binding']), 'sha256': file['sha256'], 'size_bytes': file['size_bytes'],
                 'unit_key': source_unit(plan['collection'], unit),
                 'execution_id': source_execution(plan['collection'], unit),
                 'storage_status': 'PRESERVED', 'content_available_to_ai': False}
        if plan['schema_version'] == 4:
            value.pop('unit_key'); value.pop('execution_id')
            value['targets'] = [{'unit_key': source_unit(plan['collection'], u),
                                 'execution_id': source_execution(plan['collection'], u)} for u in anchors(plan, item)]
        result.append({'id': 'pf-' + digest(value), **value})
    return result


def validate_metadata(plan, values, *, transferred=False):
    from common.sanity_submission import HEX, check, relative_report
    check(isinstance(values, list) and len(values) == len(declarations(plan)), 'PRESERVED_SOURCE_MISMATCH')
    files, normalized = [], []
    for original in values:
        check(isinstance(original, dict), 'PRESERVED_SOURCE_MISMATCH')
        value = dict(original)
        keys = {'id', 'path', 'role', 'binding', 'sha256', 'size_bytes', 'storage_status', 'content_available_to_ai'}
        if plan['schema_version'] == 4:
            keys |= {'targets', 'content'}
            if transferred and value.get('content') is not None:
                from common.sanity_preserved_text import validate_extraction
                check(value['content_available_to_ai'] is True, 'PRESERVED_TEXT_CORRUPT')
                validate_extraction(value); value.pop('extraction')
                value['content_available_to_ai'] = False
        else:
            keys |= {'unit_key', 'execution_id'}
        check(set(value) == keys, 'PRESERVED_SOURCE_MISMATCH')
        relative_report(value['path'])
        check(isinstance(value['sha256'], str) and HEX.fullmatch(value['sha256'])
              and type(value['size_bytes']) is int and value['size_bytes'] >= 0, 'PRESERVED_SOURCE_MISMATCH')
        files.append({k: value[k] for k in ('path', 'sha256', 'size_bytes')})
        normalized.append(value)
    check(len({f['path'] for f in files}) == len(files)
          and canonical_json(normalized) == canonical_json(metadata(plan, files)), 'PRESERVED_SOURCE_MISMATCH')
    return values


def for_unit(plan, unit, values):
    from common.sanity_submission import source_execution, source_unit
    key, execution = source_unit(plan['collection'], unit), source_execution(plan['collection'], unit)
    if plan['schema_version'] == 4:
        return [value for value in values if {'unit_key': key, 'execution_id': execution} in value['targets']]
    return [value for value in values if value['unit_key'] == key
            and (value['binding']['kind'] == 'unit' or value['execution_id'] == execution)]
