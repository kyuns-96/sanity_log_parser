#!/usr/bin/env python3
"""Offline portal DDL preparation. Never connects to a DB or executes SQL."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PLAN = Path(__file__).with_name('plan.json')
TABLES = ('sanity_vwp_data', 'sanity_oa_data', 'sanity_oa_to_vwp', 'sanity_vwp_to_oa')
PHASES = {'002': '002_indexes.sql', '004': '004_clustering.sql', '005': '005_workspace.sql',
          '006': '006_workspace_reviews.sql', '007': '007_workspace_pipeline.sql', '008': '008_submissions.sql',
          '009': '009_sso_identity.sql', '010':'010_raw_sources.sql'}


class PortalError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not value or len(value.encode()) > 63 or any(ord(c) < 32 for c in value):
        raise PortalError('PORTAL_INVALID_IDENTIFIER')
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "E'" + value.replace('\\', '\\\\').replace("'", "''") + "'"


def inspection_sql(schema):
    """One read-only SELECT, including on PG12 where the PG15 flag is absent.

    Read the optional catalog field from the row JSON rather than naming a
    nonexistent column. Before PG15, unique indexes always treat nulls as
    distinct; newer servers must retain their actual flag in the snapshot.
    """
    tables = []
    for table in TABLES:
        rel = literal(identifier(schema) + '.' + identifier(table)) + '::regclass'
        tables.append(f"""SELECT jsonb_build_object(
 'name', {literal(table)}, 'rows', (SELECT count(*) FROM {identifier(schema)}.{identifier(table)}),
 'columns', (SELECT jsonb_agg(jsonb_build_object(
   'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),'not_null',a.attnotnull,
   'storage',a.attstorage,'generated',a.attgenerated,'identity',a.attidentity,
   'expression',pg_get_expr(d.adbin,d.adrelid,false),'collation',cl.collname) ORDER BY a.attnum)
   FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
   LEFT JOIN pg_collation cl ON cl.oid=a.attcollation
   WHERE a.attrelid={rel} AND a.attnum>0 AND NOT a.attisdropped),
 'constraints', COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'name',c.conname,'type',c.contype,'validated',c.convalidated,'no_inherit',c.connoinherit,
   'index_nulls_not_distinct',(SELECT COALESCE((to_jsonb(i)->>'indnullsnotdistinct')::boolean,false) FROM pg_index i
       WHERE i.indexrelid=c.conindid AND c.contype IN ('p','u')),
   'deferrable',c.condeferrable,'deferred',c.condeferred,
   'definition',CASE WHEN c.contype='c' THEN pg_get_expr(c.conbin,c.conrelid,false) END,
   'columns',COALESCE((SELECT jsonb_agg(a.attname ORDER BY k.pos) FROM unnest(c.conkey)
      WITH ORDINALITY k(num,pos) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num),'[]'::jsonb),
   'ref_table',(SELECT relname FROM pg_class WHERE oid=c.confrelid),
   'ref_schema',CASE WHEN c.confrelid=0 THEN NULL WHEN (SELECT relnamespace FROM pg_class WHERE oid=c.confrelid)
       ={literal(identifier(schema))}::regnamespace THEN 'SELF' ELSE 'OTHER' END,
   'ref_columns',COALESCE((SELECT jsonb_agg(a.attname ORDER BY k.pos) FROM unnest(c.confkey)
      WITH ORDINALITY k(num,pos) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num),'[]'::jsonb),
   'match',c.confmatchtype,'on_update',c.confupdtype,'on_delete',c.confdeltype) ORDER BY c.conname)
   FROM pg_constraint c WHERE c.conrelid={rel}),'[]'::jsonb),
 'indexes', COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'name',ic.relname,'method',am.amname,'unique',i.indisunique,'valid',i.indisvalid,
   'ready',i.indisready,'live',i.indislive,'immediate',i.indimmediate,
   'nulls_not_distinct',COALESCE((to_jsonb(i)->>'indnullsnotdistinct')::boolean,false),'include_count',i.indnatts-i.indnkeyatts,
   'options',to_jsonb(i.indoption::smallint[]),
   'opclasses',(SELECT jsonb_agg(jsonb_build_array(n.nspname,op.opcname) ORDER BY k.pos)
       FROM unnest(i.indclass) WITH ORDINALITY k(id,pos) JOIN pg_opclass op ON op.oid=k.id
       JOIN pg_namespace n ON n.oid=op.opcnamespace),
   'collations',(SELECT jsonb_agg(CASE WHEN k.id=0 THEN NULL ELSE jsonb_build_array(n.nspname,co.collname) END ORDER BY k.pos)
       FROM unnest(i.indcollation) WITH ORDINALITY k(id,pos) LEFT JOIN pg_collation co ON co.oid=k.id
       LEFT JOIN pg_namespace n ON n.oid=co.collnamespace),
   'keys',(SELECT jsonb_agg(pg_get_indexdef(i.indexrelid,k,false) ORDER BY k)
           FROM generate_series(1,i.indnkeyatts) k),
   'predicate',pg_get_expr(i.indpred,i.indrelid,false)) ORDER BY ic.relname)
   FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_am am ON am.oid=ic.relam
   WHERE i.indrelid={rel} AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid=i.indexrelid)),
   '[]'::jsonb)) AS value""")
    return ("SELECT jsonb_build_object('format',1,'server_version_num',current_setting('server_version_num')::int,"
            "'encoding',current_setting('server_encoding'),"
            f"'schema',{literal(schema)},'tables',jsonb_agg(value ORDER BY value->>'name')) AS snapshot\nFROM (\n"
            + '\nUNION ALL\n'.join(tables) + '\n) inventory;\n')


def read_json(path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise PortalError('PORTAL_INPUT_TOO_LARGE')
    return json.loads(path.read_text(encoding='utf-8'))


def load_plan(*, raw_sources=False):
    plan = read_json(PLAN.with_name('raw-plan.json') if raw_sources else PLAN)
    for name, expected in plan['sources'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise PortalError('PORTAL_REFERENCE_STALE')
    return plan


def signature(item):
    return canonical({k: v for k, v in item.items() if k != 'name'})


def normalized(snapshot):
    version = snapshot.get('server_version_num')
    if (snapshot.get('format') != 1 or type(version) is not int or version < 120001
            or snapshot.get('encoding') != 'UTF8'):
        raise PortalError('PORTAL_UNSUPPORTED_SNAPSHOT')
    identifier(snapshot['schema'])
    tables = deepcopy(snapshot['tables'])
    if sorted(t['name'] for t in tables) != sorted(TABLES):
        raise PortalError('PORTAL_TABLE_SET_MISMATCH')
    for table in tables:
        if type(table['rows']) is not int or table['rows'] != 0:
            raise PortalError('PORTAL_REQUIRES_EMPTY_TABLES')
        for key in ('constraints', 'indexes'):
            values = [signature(item) for item in table[key]]
            if len(values) != len(set(values)):
                raise PortalError('PORTAL_AMBIGUOUS_DEFINITIONS')
            table[key] = sorted(values)
    return sorted(tables, key=lambda t: t['name'])


def fingerprint(snapshot):
    return sha(canonical(normalized(snapshot)))


def table_at(snapshot, name):
    return next(t for t in snapshot['tables'] if t['name'] == name)


def matching(items, expected):
    values = [item for item in items if signature(item) == signature(expected)]
    if len(values) != 1:
        raise PortalError('PORTAL_PRIOR_DEFINITION_MISMATCH')
    return values[0]


def advance(snapshot, step):
    result = deepcopy(snapshot)
    table = table_at(result, step['table'])
    if step['kind'] == 'index':
        table['indexes'].append(deepcopy(step['index']))
    else:
        table['columns'].extend(deepcopy(step['add_columns']))
        for change in step['change_columns']:
            i = next(i for i, c in enumerate(table['columns']) if c['name'] == change['name'])
            table['columns'][i] = deepcopy(change)
        for old in step['drop_constraints']:
            table['constraints'].remove(matching(table['constraints'], old))
        table['constraints'].extend(deepcopy(step['add_constraints']))
    return result


def constraint_sql(c, schema, expression=None):
    columns = lambda names: ', '.join(identifier(n) for n in names)
    if not c['validated'] or (c['type'] == 'c' and c['no_inherit']):
        raise PortalError('PORTAL_UNSUPPORTED_REFERENCE_CONSTRAINT')
    if c['type'] == 'c':
        body = 'CHECK (' + (expression if expression is not None else c['definition']) + ')'
    elif c['type'] in ('p', 'u'):
        body = ('PRIMARY KEY' if c['type'] == 'p' else 'UNIQUE') + ' (' + columns(c['columns']) + ')'
        if c['index_nulls_not_distinct']:
            raise PortalError('PORTAL_UNSUPPORTED_REFERENCE_CONSTRAINT')
    elif c['type'] == 'f':
        if c['ref_table'] not in TABLES or c['ref_schema'] != 'SELF' or c['match'] != 's':
            raise PortalError('PORTAL_UNSUPPORTED_REFERENCE_FK')
        actions = {'a': 'NO ACTION', 'r': 'RESTRICT', 'c': 'CASCADE', 'n': 'SET NULL', 'd': 'SET DEFAULT'}
        body = ('FOREIGN KEY (' + columns(c['columns']) + ') REFERENCES '
                + identifier(schema) + '.' + identifier(c['ref_table']) + ' (' + columns(c['ref_columns']) + ')'
                + ' ON UPDATE ' + actions[c['on_update']] + ' ON DELETE ' + actions[c['on_delete']])
    else:
        raise PortalError('PORTAL_UNSUPPORTED_REFERENCE_CONSTRAINT')
    if c['deferrable']:
        body += ' DEFERRABLE INITIALLY ' + ('DEFERRED' if c['deferred'] else 'IMMEDIATE')
    return 'ADD CONSTRAINT ' + identifier(c['name']) + ' ' + body


def statement(step, schema, snapshot):
    table = table_at(snapshot, step['table'])
    target = identifier(schema) + '.' + identifier(step['table'])
    if step['kind'] == 'index':
        index = step['index']
        source = (ROOT / 'db' / PHASES[step['phase']]).read_text(encoding='utf-8')
        matches = re.findall(r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+' + re.escape(index['name']) + r'\s+ON\s+[^;]+;', source)
        if len(matches) != 1:
            raise PortalError('PORTAL_REFERENCE_INDEX_MISSING')
        return matches[0].replace('public.', identifier(schema) + '.') + '\n'
    actions = []
    for c in step['add_columns']:
        if c['identity'] or c['collation'] not in (None, 'default'):
            raise PortalError('PORTAL_UNSUPPORTED_REFERENCE_COLUMN')
        body = 'ADD COLUMN ' + identifier(c['name']) + ' ' + c['type']
        if c['generated']:
            if c['generated'] != 's':
                raise PortalError('PORTAL_UNSUPPORTED_REFERENCE_COLUMN')
            body += ' GENERATED ALWAYS AS (' + step['generation_sql'][c['name']] + ') STORED'
        elif c['expression'] is not None:
            body += ' DEFAULT ' + c['expression']
        if c['not_null']:
            body += ' NOT NULL'
        actions.append(body)
    for c in step['change_columns']:
        old = next(x for x in table['columns'] if x['name'] == c['name'])
        if {k: v for k, v in c.items() if k not in ('not_null', 'storage')} != {
                k: v for k, v in old.items() if k not in ('not_null', 'storage')}:
            raise PortalError('PORTAL_UNSUPPORTED_REFERENCE_ALTER')
        if old['not_null'] != c['not_null']:
            actions.append('ALTER COLUMN ' + identifier(c['name']) + (' SET' if c['not_null'] else ' DROP') + ' NOT NULL')
        if old['storage'] != c['storage']:
            storage = {'p': 'PLAIN', 'e': 'EXTERNAL', 'm': 'MAIN', 'x': 'EXTENDED'}[c['storage']]
            actions.append('ALTER COLUMN ' + identifier(c['name']) + ' SET STORAGE ' + storage)
    for c in step['drop_constraints']:
        actions.append('DROP CONSTRAINT ' + identifier(matching(table['constraints'], c)['name']))
    actions.extend(constraint_sql(c, schema, step['check_sql'][c['name']] if c['type'] == 'c' else None)
                   for c in step['add_constraints'])
    if not actions:
        raise PortalError('PORTAL_EMPTY_REFERENCE_STEP')
    return 'ALTER TABLE ' + target + '\n    ' + ',\n    '.join(actions) + ';\n'


def checkpoints(plan):
    state = deepcopy(plan['initial'])
    yield state
    for step in plan['steps']:
        state = advance(state, step)
        yield state


def contents(snapshot, plan):
    actual = fingerprint(snapshot)
    positions = [i for i, state in enumerate(checkpoints(plan)) if fingerprint(state) == actual]
    if len(positions) != 1:
        raise PortalError('PORTAL_SCHEMA_NOT_A_CHECKPOINT')
    start = positions[0]
    manifest = {'format': 1, 'schema': snapshot['schema'], 'plan_sha256': sha(canonical(plan)),
                'start_checkpoint': start, 'total_steps': len(plan['steps']), 'steps': [],
                'operational_ready': False, 'requires_separate_dba_grants': 'db/003_grants.sql'}
    state = deepcopy(snapshot)
    files = {'inspect.sql': inspection_sql(snapshot['schema'])}
    for number, step in enumerate(plan['steps'][start:], start=start + 1):
        sql = statement(step, snapshot['schema'], state)
        before = fingerprint(state)
        state = advance(state, step)
        filename = f'{number:03d}-{step["phase"]}-{step["table"]}.sql'
        files[filename] = sql
        manifest['steps'].append({'number': number, 'phase': step['phase'], 'file': filename,
                                  'sha256': sha(sql), 'before': before, 'after': fingerprint(state)})
    manifest['final'] = fingerprint(state)
    return manifest, files


def prepare(snapshot, output, plan=None):
    plan = plan or load_plan()
    if output.exists():
        raise PortalError('PORTAL_OUTPUT_EXISTS')
    manifest, files = contents(snapshot, plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='portal-', dir=output.parent) as temporary:
        directory = Path(temporary) / 'bundle'
        directory.mkdir()
        for name, sql in files.items():
            (directory / name).write_text(sql, encoding='utf-8')
        (directory / 'start-snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        directory.rename(output)
    return manifest


def verify(snapshot, bundle, step_number=None, when='after'):
    manifest = read_json(bundle / 'manifest.json')
    plan = load_plan()
    if manifest.get('plan_sha256') != sha(canonical(plan)):
        plan = load_plan(raw_sources=True)
    expected_manifest, files = contents(read_json(bundle / 'start-snapshot.json'), plan)
    if manifest != expected_manifest or snapshot['schema'] != manifest['schema']:
        raise PortalError('PORTAL_BUNDLE_MISMATCH')
    for name, sql in files.items():
        if (bundle / name).read_bytes() != sql.encode():
            raise PortalError('PORTAL_SQL_MODIFIED')
    expected = manifest['final']
    if step_number is not None:
        found = [s for s in manifest['steps'] if s['number'] == step_number]
        if len(found) != 1:
            raise PortalError('PORTAL_UNKNOWN_STEP')
        expected = found[0][when]
    if fingerprint(snapshot) != expected:
        raise PortalError('PORTAL_VERIFICATION_FAILED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--schema', required=True)
    inspect.add_argument('--out', type=Path, required=True)
    build = commands.add_parser('prepare')
    build.add_argument('--snapshot', type=Path, required=True)
    build.add_argument('--out', type=Path, required=True)
    build.add_argument('--raw-sources',action='store_true',help='prepare the opt-in 010 DB original-file storage steps')
    check = commands.add_parser('verify')
    check.add_argument('--snapshot', type=Path, required=True)
    check.add_argument('--bundle', type=Path, required=True)
    check.add_argument('--step', type=int)
    check.add_argument('--when', choices=('before', 'after'), default='after')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            with args.out.open('x', encoding='utf-8') as stream:
                stream.write(inspection_sql(args.schema))
            print('status=INSPECTION_SQL_CREATED database_access=NONE')
        elif args.command == 'prepare':
            manifest = prepare(read_json(args.snapshot), args.out,load_plan(raw_sources=args.raw_sources))
            print(f'status=PREPARED checkpoint={manifest["start_checkpoint"]} remaining={len(manifest["steps"])} operational_ready=false')
        else:
            verify(read_json(args.snapshot), args.bundle, args.step, args.when)
            print('status=SCHEMA_VERIFIED operational_ready=false')
    except (PortalError, OSError, ValueError, KeyError, TypeError, StopIteration):
        # Never print snapshot contents, schema names, paths, or raw exception messages.
        import sys
        error = sys.exc_info()[1]
        code = str(error) if isinstance(error, PortalError) else 'PORTAL_INVALID_INPUT'
        print('status=STOP code=' + code)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
