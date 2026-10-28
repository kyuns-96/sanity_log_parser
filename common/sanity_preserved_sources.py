"""Explicit preserved source evidence. Originals stay in the private RAW store.

Plan 3 declares files; only verified manifests yield PRESERVED metadata. No
parser/model inference, file I/O or implicit downgrade of unit reports here.
"""
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
        check(isinstance(item, dict) and set(item) == {'path', 'role', 'binding'}, 'INVALID_PRESERVED_SOURCE')
        relative_report(item['path'])
        check(item['path'] not in paths and isinstance(item['role'], str) and item['role'] in ROLES,
              'INVALID_PRESERVED_SOURCE')
        paths.add(item['path'])
        bind = item['binding']
        check(isinstance(bind, dict) and bind.get('kind') in ('unit', 'execution'), 'INVALID_PRESERVED_SOURCE')
        key = 'review_unit_id' if bind['kind'] == 'unit' else 'anchor_unit_id'
        check(set(bind) == {'kind', key} and isinstance(bind[key], str) and bind[key] in units,
              'INVALID_PRESERVED_BINDING')
    return items


def anchor(plan, item):
    bind = item['binding']
    key = bind.get('review_unit_id') if bind['kind'] == 'unit' else bind.get('anchor_unit_id')
    return next(u for u in plan['inventory']['units'] if u['review_unit_id'] == key)


def scope_declarations(plan):
    from common.sanity_submission import source_unit
    return sorted(({'path': p['path'], 'role': p['role'], 'binding_kind': p['binding']['kind'],
                    'unit_key': source_unit(plan['collection'], anchor(plan, p))}
                   for p in declarations(plan)), key=lambda p: p['path'])


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
        value = {**item, 'binding': dict(item['binding']), 'sha256': file['sha256'], 'size_bytes': file['size_bytes'],
                 'unit_key': source_unit(plan['collection'], unit),
                 'execution_id': source_execution(plan['collection'], unit),
                 'storage_status': 'PRESERVED', 'content_available_to_ai': False}
        result.append({'id': 'pf-' + digest(value), **value})
    return result


def validate_metadata(plan, values):
    from common.sanity_submission import HEX, check, relative_report
    check(isinstance(values, list) and len(values) == len(declarations(plan)), 'PRESERVED_SOURCE_MISMATCH')
    files = []
    for value in values:
        check(isinstance(value, dict) and set(value) == {'id', 'path', 'role', 'binding', 'sha256',
              'size_bytes', 'unit_key', 'execution_id', 'storage_status', 'content_available_to_ai'},
              'PRESERVED_SOURCE_MISMATCH')
        relative_report(value['path'])
        check(isinstance(value['sha256'], str) and HEX.fullmatch(value['sha256'])
              and type(value['size_bytes']) is int and value['size_bytes'] >= 0, 'PRESERVED_SOURCE_MISMATCH')
        files.append({k: value[k] for k in ('path', 'sha256', 'size_bytes')})
    check(len({f['path'] for f in files}) == len(files)
          and canonical_json(values) == canonical_json(metadata(plan, files)), 'PRESERVED_SOURCE_MISMATCH')
    return values


def for_unit(plan, unit, values):
    from common.sanity_submission import source_execution, source_unit
    key, execution = source_unit(plan['collection'], unit), source_execution(plan['collection'], unit)
    return [value for value in values if value['unit_key'] == key
            and (value['binding']['kind'] == 'unit' or value['execution_id'] == execution)]
