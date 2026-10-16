#!/usr/bin/env python3
"""Parse synthetic public-reference reports through a fresh isolated PostgreSQL DB.

Export a read-only dashboard snapshot, then stop the disposable DB. No existing
.env, company adapter, company DB or live AI is used. Generated data is not Git input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def build(output, count, pg_bin):
    runner = module("public_preview_pg_runner", ROOT / "db/tests/run_mock.py")
    database = runner.MockDatabase(runner.pg_binaries(pg_bin))
    print("artifacts=" + str(database.root), flush=True)
    staged = database.root / "source"
    runner.stage_source(ROOT, staged)
    try:
        database.start()
        database.setup(staged)
        environment = database.test_environment()
        # All processing occurs in the credential-free code snapshot. Secrets stay
        # in a private runtime file, never command arguments, stdout or exported JSON.
        config = database.root / "preview-env.json"
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({key: value for key, value in environment.items() if key.startswith("SANITY_")}, stream)
        runtime = database.root / "process_preview.py"
        runtime.write_text(PROCESS_SCRIPT, encoding="utf-8")
        log = database.root / "process-preview.log"
        try:
            with log.open("wb") as stream:
                result = subprocess.run([sys.executable, str(runtime), str(staged), str(config),
                                         str(output.resolve()), str(count)], cwd=staged,
                                        env=database.env, stdout=stream, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError("Synthetic preview processing failed; inspect the local process-preview.log")
        finally:
            config.unlink(missing_ok=True)
        summary = json.loads((database.root / "dataset-summary.json").read_text())
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        return summary
    finally:
        database.stop()


PROCESS_SCRIPT = r'''
import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import sys

root, config, output, count = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
sys.path.insert(0, str(root))
os.environ.update(json.loads(config.read_text()))
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result
from sanity_common import atomic_json, coverage, digest
from sanity_db import Database
from sanity_oa_store import OAStore
from sanity_sample_reports import generate_library
from site_integration.public_parsers import create_public_registry
collector = load('preview_collector', root / 'vwp-dev/main.py')
bridge = load('preview_bridge', root / 'vwp-bridge/main.py')
oa = load('preview_oa', root / 'oa-bridge/main.py')
library = generate_library(root.parent / 'reports', count)
parsed, stored_records, bundle_count = {}, 0, 0
for key, run in sorted(library['runs'].items()):
    collected = run['directory'] / 'snapshot'
    collector.collect(run['directory'] / 'plan.json', run['directory'] / 'reports', collected)
    payload, receipt = bridge.analyze(collected / 'run.json', parser_registry=create_public_registry())
    received = oa.receive(argparse.Namespace(message_id=receipt['message_id'], lease_seconds=300))
    if received['status'] != 'RECEIVED':
        raise RuntimeError('Synthetic OA receipt mismatch')
    with Database('oa') as db, db.transaction(readonly=True):
        stored, decisions, context, revision = OAStore(db).load(payload['bundle_id'])
        if stored != payload:
            raise RuntimeError('Stored payload differs from parser output')
    ready = {u['review_unit_id']: u for u in coverage(payload)['units']}
    with Database('vwp') as db, db.transaction(readonly=True):
        rows = db.execute("SELECT payload FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE' ORDER BY review_unit_id,(payload->>'record_index')::int", (receipt['analysis_id'],)).fetchall()
    by_unit = {}
    for row in rows:
        record = row['payload']
        by_unit.setdefault(record['review_unit_id'], []).append({
            'id': record['record_id'], 'fields': record['fields'], 'check_status': record['check_status'],
            'line_start': record['line_start'], 'line_end': record['line_end'],
            'raw': record['example']['raw'], 'reasons': record['reasons']})
        stored_records += 1
    for unit in payload['review_units']:
        parsed[unit['review_unit_id']] = {**unit, 'bundle_id': payload['bundle_id'],
                                         'analysis_ready': ready[unit['review_unit_id']]['analysis_ready'],
                                         'records': by_unit.get(unit['review_unit_id'], [])}
    bundle_count += 1
items = []
for item in library['items']:
    report = Path(item['report_path'])
    items.append({**{key: value for key, value in item.items() if key not in ('report_path', 'case')},
                  **parsed[item['id']], 'raw_report': report.read_text() if report.exists() else None})
status = Counter(item['check_status'] for item in items)
parsing = Counter(item['parse_status'] for item in items)
families = Counter(item['family'] for item in items)
summary = {'items': len(items), 'users': len(library['users']), 'projects': len(library['projects']),
           'blocks': len(library['blocks']), 'bundles': bundle_count, 'stored_records': stored_records,
           'status_counts': dict(status), 'parse_counts': dict(parsing), 'tool_counts': dict(families),
           'origin': 'SYNTHETIC_PUBLIC_REFERENCE', 'actual_tools_run': False, 'real_ai_called': False,
           'db_ingestion_verified': True}
data = {'schema_version': 1, 'summary': summary, 'users': library['users'], 'projects': library['projects'],
        'blocks': library['blocks'], 'sources': library['sources'], 'items': items}
data['dataset_sha256'] = digest(data)
atomic_json(output, data)
atomic_json(root.parent / 'dataset-summary.json', summary)
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--pg-bin")
    parser.add_argument("--out", type=Path, default=ROOT / "oa-bridge/dashboard/public/sample-data/reports-library.json")
    args = parser.parse_args(argv)
    if not 27 <= args.count <= 10000:
        parser.error("Choose 27 to 10000 sample items")
    try:
        build(args.out, args.count, args.pg_bin)
    except Exception as exc:
        print("code=PUBLIC_PREVIEW_BUILD_FAILED type=" + type(exc).__name__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
