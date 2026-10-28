#!/usr/bin/env python3
"""Developer-only: derive a portal plan in a new local socket-only mock database."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from db.portal.runbook import TABLES, advance, fingerprint, identifier, inspection_sql, table_at

MIGRATIONS = ('002_indexes.sql', '004_clustering.sql', '005_workspace.sql',
              '006_workspace_reviews.sql', '007_workspace_pipeline.sql', '008_submissions.sql',
              '009_sso_identity.sql')


def parenthesized(source, opening):
    depth, quoted, i = 1, False, opening + 1
    while i < len(source):
        c = source[i]
        if c == "'":
            if quoted and i + 1 < len(source) and source[i + 1] == "'":
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            if c == '(': depth += 1
            if c == ')':
                depth -= 1
                if not depth: return source[opening + 1:i]
        i += 1
    raise ValueError('Unclosed CHECK in immutable reference source')


def check_sources(source, before, phase):
    """Compile only these pinned migrations, not arbitrary SQL or user input.

    Keep the original parser input: pg_get_expr is a decompiler, and reparsing it
    can change array-coercion trees despite equivalent truth values.
    """
    expressions = {}
    for match in re.finditer(r'ADD CONSTRAINT ([A-Za-z_][A-Za-z0-9_]*)\s+CHECK\s*\(', source):
        if match[1].endswith('_probe'):
            continue
        expression = parenthesized(source, match.end() - 1)
        if match[1] in ('sanity_oa_workspace_scope_v4', 'sanity_oa_workspace_scope_v5', 'sanity_vwp_submission_parent_v3'):
            expression = expression.replace("''", "'")
        if '%s' in expression:
            if phase in ('006', '007') and match[1].startswith('sanity_oa_workspace_scope_'):
                name = 'sanity_oa_workspace_scope_v' + ('3' if phase == '006' else '4')
                old = next(c['definition'] for c in table_at(before, 'sanity_oa_data')['constraints'] if c['name'] == name)
            elif phase == '008' and match[1] == 'sanity_vwp_submission_parent_v3':
                old = re.search(r'old_expression := \$expr\$(.*?)\$expr\$', source, re.S)[1]
            elif phase == '009' and match[1] == 'sanity_oa_workspace_scope_v7':
                old = next(c['definition'] for c in table_at(before, 'sanity_oa_data')['constraints']
                           if c['name'] == 'sanity_oa_workspace_scope_v6')
            elif phase == '010' and match[1] in ('sanity_vwp_status_v4','sanity_vwp_submission_parent_v4'):
                name = {'sanity_vwp_status_v4':'sanity_vwp_status_v3',
                        'sanity_vwp_submission_parent_v4':'sanity_vwp_submission_parent_v3'}[match[1]]
                old = next(c['definition'] for c in table_at(before,'sanity_vwp_data')['constraints'] if c['name'] == name)
            elif phase == '012' and match[1] == 'sanity_vwp_status_v5':
                old = next(c['definition'] for c in table_at(before,'sanity_vwp_data')['constraints']
                           if c['name'] == 'sanity_vwp_status_v4')
            else:
                raise ValueError('Unrecognized format in pinned CHECK')
            expression = expression.replace('%s', old)
        expressions[match[1]] = expression
    if phase == '004':
        for match in re.finditer(r"\('public\.\w+',\s*'(\w+)',\s*\$old\$.*?\$old\$,\s*\$new\$(.*?)\$new\$\)", source, re.S):
            expressions[match[1]] = match[2]
        match = re.search(r'classification_revision BIGINT\s*CHECK\s*\(', source)
        expressions['sanity_oa_data_classification_revision_check'] = parenthesized(source, match.end() - 1)
    if phase == '008':
        pattern = r"\('public\.(\w+)',\s*'(\w+)',\s*'\w+',\s*'(\w+)',\s*\$old\$(.*?)\$old\$,\s*\$new\$(.*?)\$new\$\)"
        for match in re.finditer(pattern, source, re.S):
            expressions[match[3]] = match[2] + ' IN (' + match[4] + ',' + match[5] + ')'
        pattern = r"\('public\.(\w+)',\s*'(\w+)',\s*'(\w+)',\s*'(\w+)',\s*\$k\$(.*?)\$k\$,\s*\$s\$(.*?)\$s\$\)"
        for match in re.finditer(pattern, source, re.S):
            old = next(c['definition'] for c in table_at(before, match[1])['constraints'] if c['name'] == match[2])
            expressions[match[3]] = (f'({match[4]} NOT IN ({match[5]}) AND ({old})) OR '
                                     f'({match[4]} IN ({match[5]}) AND ({match[6]}))')
    return expressions


def snapshot(connection, schema):
    with connection.cursor() as cursor:
        cursor.execute(inspection_sql(schema))
        return cursor.fetchone()[0]


def create_forms(connection, schema):
    with connection.cursor() as cursor:
        cursor.execute('CREATE SCHEMA ' + identifier(schema))
        for table in TABLES:
            columns = (ROOT / 'db/form' / (table + '.columns.sql')).read_text()
            cursor.execute('CREATE TABLE ' + identifier(schema) + '.' + identifier(table) + ' (' + columns + ')')


def derive_steps(before, after, phase):
    steps = []
    # The referenced OA request UNIQUE must exist before the VWP response FK.
    for table in TABLES:
        old, new = table_at(before, table), table_at(after, table)
        old_columns = {c['name']: c for c in old['columns']}
        new_columns = {c['name']: c for c in new['columns']}
        if not old_columns.keys() <= new_columns.keys():
            raise ValueError('Reference migrations must not drop columns')
        old_constraints = {c['name']: c for c in old['constraints']}
        new_constraints = {c['name']: c for c in new['constraints']}
        delta = {
            'phase': phase, 'table': table, 'kind': 'alter',
            'add_columns': [c for c in new['columns'] if c['name'] not in old_columns],
            'change_columns': [c for c in new['columns'] if c['name'] in old_columns and c != old_columns[c['name']]],
            'drop_constraints': [c for c in old['constraints'] if new_constraints.get(c['name']) != c],
            'add_constraints': [c for c in new['constraints'] if old_constraints.get(c['name']) != c],
        }
        if any(delta[k] for k in ('add_columns', 'change_columns', 'drop_constraints', 'add_constraints')):
            steps.append(delta)
        old_indexes = {i['name']: i for i in old['indexes']}
        new_indexes = {i['name']: i for i in new['indexes']}
        if not old_indexes.keys() <= new_indexes.keys() or any(new_indexes[k] != v for k, v in old_indexes.items()):
            raise ValueError('Reference migrations must not remove/change indexes')
        for index in new['indexes']:
            if index['name'] not in old_indexes:
                steps.append({'phase': phase, 'table': table, 'kind': 'index', 'index': index})
    return steps


def capture(connection, schema='portal_reference', *, raw_sources=False, payload_storage=False, evidence_pages=False,
            shared_packets=False):
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        if not cursor.fetchone()[0].startswith('sanity_test_'):
            raise ValueError('Reference capture requires a disposable sanity_test_* database')
    create_forms(connection, schema)
    initial = snapshot(connection, schema)
    state = deepcopy(initial)
    steps = []
    migrations = (MIGRATIONS + (('010_raw_sources.sql',) if raw_sources or payload_storage or evidence_pages or shared_packets else ())
                  + (('011_payload_storage.sql',) if payload_storage or evidence_pages or shared_packets else ())
                  + (('012_source_evidence_pages.sql',) if evidence_pages or shared_packets else ())
                  + (('013_shared_source_packets.sql',) if shared_packets else ()))
    for filename in migrations:
        source = (ROOT / 'db' / filename).read_text(encoding='utf-8')
        expressions = check_sources(source, state, filename[:3])
        generations = {m[1]: parenthesized(source, m.end() - 1) for m in re.finditer(
            r'ADD COLUMN (\w+) UUID GENERATED ALWAYS AS\s*\(', source)}
        with connection.cursor() as cursor:
            cursor.execute(source.replace('public.', identifier(schema) + '.'))
        after = snapshot(connection, schema)
        for step in derive_steps(state, after, filename[:3]):
            if step['kind'] == 'alter':
                step['check_sql'] = {c['name']: expressions[c['name']] for c in step['add_constraints'] if c['type'] == 'c'}
                step['generation_sql'] = {c['name']: generations[c['name']] for c in step['add_columns'] if c['generated']}
            state = advance(state, step)
            steps.append(step)
        if fingerprint(state) != fingerprint(after):
            raise ValueError('Reference diff does not cover the entire catalog')
    sources = [*(f'db/form/{name}.columns.sql' for name in TABLES),
               *(f'db/{name}' for name in migrations), 'db/003_grants.sql']
    return {'format': 1, 'sources': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
            'initial': initial, 'steps': steps}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--raw-sources',action='store_true',help='include the opt-in 010 raw-storage extension')
    parser.add_argument('--payload-storage',action='store_true',help='include 010 and opt-in 011 lossless payload storage')
    parser.add_argument('--evidence-pages',action='store_true',help='include 010/011/012 source evidence pages')
    parser.add_argument('--shared-source-packets',action='store_true',help='include 010..013 shared source packets')
    args = parser.parse_args()
    import psycopg2
    from db.tests.run_mock import MockDatabase, pg_binaries
    database = MockDatabase(pg_binaries(args.pg_bin))
    try:
        database.start()
        database.setup(ROOT)
        connection = psycopg2.connect(**database.parameters('admin'))
        connection.autocommit = True
        try:
            plan = capture(connection,raw_sources=args.raw_sources,payload_storage=args.payload_storage,
                           evidence_pages=args.evidence_pages, shared_packets=args.shared_source_packets)
        finally:
            connection.close()
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(plan, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print('status=REFERENCE_CAPTURED steps=' + str(len(plan['steps'])))
    finally:
        database.stop()


if __name__ == '__main__':
    main()
