"""Preserved files: scope, exact bytes, immutable context and OA/AI boundaries."""


from common.sanity_submission import scope_digest


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
