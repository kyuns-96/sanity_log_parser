"""Exact, snapshot-frozen hierarchy comparisons; no provider calls or DDL."""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from functools import cached_property
from typing import Any
from hashlib import sha256

from psycopg import sql

from area_power.common.errors import DomainError
from area_power.common.jsonutil import digest
from area_power.common.models import CALCULATION_CONTRACT, COMPARISON_FORMAT, CORRESPONDENCE_POLICY, ComparisonRequest, MappingPayload, UploadMetadata
from area_power.common.csv_input import OBSERVED_EXTRA, ObservedCSV, parse_csv, read_observations

D = Decimal
CATEGORIES = ('total', 'logic', 'cts', 'sram', 'io', 'others')
VT = tuple(f'vt_{v}{suffix}' for v in ('slvt', 'lvt', 'rvt', 'hvt') for suffix in ('', '_llp'))
DRIVE = tuple(f'drv_{v}' for v in ('D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D10', 'D12', 'D14', 'D16', 'other'))
POWER = tuple(f'pwr_{c}_{p}_mw' for c in ('all', 'logic', 'sram', 'io', 'others') for p in ('internal', 'switching', 'leakage', 'total'))
STRUCTURAL = tuple(f'{c}_{s}' for c in CATEGORIES for s in ('cells', 'area')) + ('cts_area_pct',) + tuple(f'{c}_{s}' for c in VT + DRIVE for s in ('cnt', 'pct'))
QUALITY = ('vth_unclassified_cnt', 'drive_unclassified_cnt')
DISTRIBUTIONS = ('vth_distribution_change', 'drive_distribution_change')
METRICS = STRUCTURAL + QUALITY + DISTRIBUTIONS + POWER
SCOPE_FIELDS = ('project_id', 'version_id', 'block_id', 'snapshot_id', 'condition_key')


def scope_fields(upload: dict) -> dict:
    return {key: upload[key] for key in SCOPE_FIELDS}


def all_records(db, conn, record_type: str, **filters) -> list[dict]:
    rows, offset = [], 0
    while True:
        page = db.records(conn, record_type, limit=1000, offset=offset, **filters)
        rows.extend(page)
        if len(page) < 1000:
            return rows
        offset += len(page)


def existing_dedupe(db, conn, kind: str, key: bytes, *, lock=False):
    return conn.execute(sql.SQL('SELECT * FROM {} WHERE record_type=%s AND dedupe_key=%s' + (' FOR UPDATE' if lock else '')).format(db.table('di_area_power_tracking_record')), (kind, key)).fetchone()


def advisory_lock(conn, key: bytes) -> None:
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (int.from_bytes(key[:8], 'big', signed=True),))


def parse_upload(upload: dict, settings, *, observations=False):
    if upload['state'] != 'ready':
        raise DomainError('Selected upload is not ready', code='upload_not_ready')
    metadata = UploadMetadata.model_validate(upload['input_contract']['metadata'])
    if observations:
        return read_observations(upload, metadata, settings.max_uncompressed_bytes), metadata
    parsed = parse_csv(bytes(upload['csv_gzip']), upload['original_filename'], metadata, settings.max_uncompressed_bytes)
    if (bytes(parsed.structure_digest) != bytes(upload['structure_digest'])
            or bytes(parsed.condition_key) != bytes(upload['condition_key'])
            or sha256(parsed.original_bytes).digest() != bytes(upload['original_sha256'])
            or len(parsed.original_bytes) != upload['original_byte_length']
            or parsed.row_count != upload['row_count']):
        raise DomainError('Stored upload no longer matches validated snapshot', code='stored_upload_mismatch')
    return parsed, metadata


def descendant(path: str, parent: str, separator: str) -> bool:
    return path == parent or path.startswith(parent + separator)


def nonoverlap(paths: list[str], separator: str) -> bool:
    return len(set(paths)) == len(paths) and not any(a != b and descendant(a, b, separator) for a in paths for b in paths)


def version_chain(db, conn, version_id) -> list[str]:
    chain, seen = [], set()
    while version_id:
        sid = str(version_id)
        if sid in seen:
            raise DomainError('Version ancestry has a cycle', code='invalid_version_interval')
        seen.add(sid)
        row = db.record(conn, version_id, kind='version')
        chain.append(sid)
        version_id = row['payload'].get('previous_version_id')
    return chain


def interval_reason(payload: dict, current: dict, baseline: dict | None, chain: list[str]) -> str | None:
    if not baseline:
        return 'baseline_missing'
    cross = str(current['project_id']) != str(baseline['project_id'])
    if cross:
        if str(payload.get('baseline_project_id')) != str(baseline['project_id']):
            return 'project_bridge_unresolved'
    elif str(baseline['version_id']) not in chain:
        return 'version_ancestry_unresolved'
    start = payload.get('applies_from_version_id') or payload.get('valid_from_version_id')
    end = payload.get('applies_through_version_id') or payload.get('valid_through_version_id')
    if not start:
        return 'source_interval_unspecified'
    if str(start) not in chain:
        return 'source_interval_outside_ancestry'
    if end and (str(end) not in chain or chain.index(str(end)) > chain.index(str(start))):
        return 'source_interval_ambiguous'
    if not cross and chain.index(str(start)) >= chain.index(str(baseline['version_id'])):
        if end and chain.index(str(end)) < chain.index(str(baseline['version_id'])):
            return 'source_interval_overlaps_baseline'
        return 'change_already_in_baseline'
    if end and chain.index(str(current['version_id'])) > chain.index(str(end)):
        return 'source_interval_not_reached'
    return None


@dataclass
class Group:
    baseline_paths: list[str]
    current_paths: list[str]
    reason: str | None = None
    target_id: Any = None
    metrics: tuple[str, ...] = METRICS
    mapping_ids: list[str] = field(default_factory=list)
    declared_kind: str | None = None

    @cached_property
    def id(self) -> str:
        # Paths are fixed once a group exists. The plain-string form below is
        # byte-identical to the canonical digest (checked over every Unicode
        # code point) and about four times faster for large hierarchies.
        if self.target_id is None and all(isinstance(path, str) for path in self.baseline_paths + self.current_paths):
            encode = lambda paths: json.dumps(sorted(paths), ensure_ascii=False, separators=(',', ':'))
            return sha256(('{"baseline":%s,"current":%s,"target":null}' % (encode(self.baseline_paths), encode(self.current_paths))).encode()).hexdigest()
        return digest({'baseline': sorted(self.baseline_paths), 'current': sorted(self.current_paths), 'target': self.target_id}).hex()


def resolve_groups(current, baseline, cm, bm, mapping: dict | None, mapping_reason: str | None = None,
                   correspondence: str | None = None) -> list[Group]:
    cr, br = current.rows, baseline.rows if baseline else {}
    same = bm is not None and cm.project_id == bm.project_id
    cs, bs = cm.hierarchy_separator, bm.hierarchy_separator if bm else cm.hierarchy_separator
    # Same project: identical paths. Different projects (only when frozen with
    # the policy): design root to design root, then identical relative paths.
    relative = not same and baseline is not None and correspondence == CORRESPONDENCE_POLICY

    def implied(path: str) -> str | None:
        if same:
            return path
        if relative:
            return baseline.root_path + path[len(current.root_path):].replace(cs, bs)
        return None
    rules = mapping['payload'].get('rules', []) if mapping and not mapping_reason else []
    exact_current, exact_baseline, subtree = {}, {}, []
    rule_groups = []
    reservations = [(r['baseline_paths'][0], r['current_paths'][0]) for r in rules if r.get('scope') == 'subtree' and len(r['baseline_paths']) == len(r['current_paths']) == 1]
    for raw in rules:
        b, c = list(raw['baseline_paths']), list(raw['current_paths'])
        kind, scope = raw['kind'], raw.get('scope', 'node')
        cardinality = {'one_to_one': len(b) == len(c) == 1, 'split': len(b) == 1 and len(c) >= 2, 'merge': len(b) >= 2 and len(c) == 1, 'added': not b and bool(c), 'removed': bool(b) and not c}
        reason = None
        if not cardinality.get(kind) or (scope == 'subtree' and kind != 'one_to_one'):
            reason = 'mapping_cardinality_invalid'
        elif not nonoverlap(b, bs) or not nonoverlap(c, cs):
            reason = 'mapping_parent_child_overlap'
        elif any(p not in br for p in b) or any(p not in cr for p in c):
            reason = 'mapping_path_missing'
        g = Group(b, c, reason, mapping_ids=[str(raw['id'])], declared_kind=kind)
        if scope == 'subtree' and not reason:
            subtree.append((b[0], c[0], g))
        else:
            rule_groups.append(g)
            for p in c:
                exact_current.setdefault(p, []).append(g)
            for p in b:
                exact_baseline.setdefault(p, []).append(g)
    for g in rule_groups:
        if any(len(exact_current[p]) > 1 for p in g.current_paths) or any(len(exact_baseline[p]) > 1 for p in g.baseline_paths):
            g.reason = 'mapping_equal_precedence_conflict'
    groups, consumed_b, consumed_c, emitted = [], set(), set(), set()
    for g in rule_groups:
        if g.id not in emitted:
            groups.append(g)
            emitted.add(g.id)
        consumed_b.update(g.baseline_paths)
        consumed_c.update(g.current_paths)
        if len(g.current_paths) > 1:
            groups.extend(Group([], [p], 'individual_group_baseline_unavailable', mapping_ids=g.mapping_ids) for p in g.current_paths)
        if len(g.baseline_paths) > 1:
            groups.extend(Group([p], [], 'individual_group_current_unavailable', mapping_ids=g.mapping_ids) for p in g.baseline_paths)
    for path in sorted(cr):
        if path in consumed_c:
            continue
        matches = [(c.count(cs), b, c, g) for b, c, g in subtree if not g.reason and descendant(path, c, cs)]
        if matches:
            depth = max(m[0] for m in matches)
            best = [m for m in matches if m[0] == depth]
            possibilities = {b + path[len(c):].replace(cs, bs) for _, b, c, _ in best}
            bp = sorted(possibilities)
            conflict = len(best) != 1 or any(p in exact_baseline or p in consumed_b for p in bp)
            if not conflict:
                inverse = [(b.count(bs), b, c) for b, c, sg in subtree if not sg.reason and descendant(bp[0], b, bs)]
                if inverse:
                    inverse_depth = max(item[0] for item in inverse)
                    destinations = {c + bp[0][len(b):].replace(bs, cs) for depth, b, c in inverse if depth == inverse_depth}
                    conflict = destinations != {path}
            reason = 'mapping_equal_precedence_conflict' if conflict else (None if bp[0] in br else 'mapping_path_missing')
            g = Group(bp, [path], reason, mapping_ids=[m[3].mapping_ids[0] for m in best])
        elif (not mapping_reason and (bp := implied(path)) is not None and bp in br and bp not in exact_baseline
              and bp not in consumed_b and not any(descendant(bp, b, bs) or descendant(path, c, cs) for b, c in reservations)):
            g = Group([bp], [path])
        else:
            reason = mapping_reason or ('baseline_missing' if baseline is None else 'mapping_unresolved')
            # Missing correspondence is not an implicit pairing of design roots.
            # Keep each side's observed value, without inventing a counterpart.
            g = Group([], [path], reason)
        groups.append(g)
        consumed_c.add(path)
        consumed_b.update(g.baseline_paths)
    for path in sorted(set(br) - consumed_b):
        groups.append(Group([path], [], mapping_reason or 'mapping_unresolved'))
    unique = {}
    for group in groups:
        old = unique.get(group.id)
        if old:
            old.mapping_ids = sorted(set(old.mapping_ids + group.mapping_ids))
            if old.reason != group.reason or old.declared_kind != group.declared_kind:
                old.reason = 'mapping_equal_precedence_conflict'
        else:
            unique[group.id] = group
    return list(unique.values())


def expand_metrics(requested: list[str]) -> tuple[str, ...]:
    if not requested:
        return METRICS
    families = {'area': tuple(m for m in STRUCTURAL if '_area' in m), 'count': tuple(m for m in STRUCTURAL if m.endswith(('_cnt', '_cells'))), 'vth': tuple(m for m in STRUCTURAL + QUALITY + DISTRIBUTIONS if m.startswith(('vt_', 'vth_'))), 'drive': tuple(m for m in STRUCTURAL + QUALITY + DISTRIBUTIONS if m.startswith(('drv_', 'drive_'))), 'power': POWER}
    result = []
    for name in requested:
        for metric in families.get(name, (name,)):
            if metric not in METRICS:
                raise DomainError('Target requests unknown metric', code='unknown_metric')
            if metric not in result:
                result.append(metric)
    return tuple(result)


def select_groups(groups: list[Group], request, targets: list[dict], current_upload, baseline_upload, cm, bm) -> list[Group]:
    if request.scope.kind == 'all':
        return groups
    indexed = {str(t['entity_id']): t for t in targets}
    selected = []
    for tid in request.scope.target_ids:
        target = indexed.get(str(tid))
        if target is None:
            selected.append(Group([], [], 'target_missing', target_id=tid))
            continue
        p = target['payload']
        metrics = expand_metrics(p.get('metrics', []))
        def binding(upload):
            if upload is None:
                return []
            found = [b['paths'] for b in p.get('bindings', []) if str(b['snapshot_id']) == str(upload['snapshot_id'])]
            if len(found) > 1:
                return []
            if found:
                return found[0]
            if str(p.get('snapshot_id') or target.get('snapshot_id')) == str(upload['snapshot_id']):
                return p.get('paths', [])
            if not p.get('snapshot_id') and not target.get('snapshot_id') and all(target.get(k) is not None and str(target[k]) == str(upload[k]) for k in ('project_id', 'version_id', 'block_id')):
                return p.get('paths', [])
            return []
        cp, bp = binding(current_upload), binding(baseline_upload)
        if not cp and not bp:
            selected.append(Group([], [], 'target_binding_unresolved', tid, metrics))
            continue
        matched = [g for g in groups if (cp and any(x in cp for x in g.current_paths)) or (bp and any(x in bp for x in g.baseline_paths))]
        exact_matches = [g for g in matched if (not cp or set(cp) == set(g.current_paths)) and (not bp or set(bp) == set(g.baseline_paths))]
        if exact_matches:
            matched = exact_matches
        if not matched:
            selected.append(Group(bp, cp, 'target_path_missing', tid, metrics))
            continue
        cb = sorted({x for g in matched for x in g.baseline_paths})
        cc = sorted({x for g in matched for x in g.current_paths})
        reason = next((g.reason for g in matched if g.reason), None)
        if (cp and set(cp) != set(cc)) or (bp and set(bp) != set(cb)):
            reason = reason or 'target_group_binding_incomplete'
        if not nonoverlap(cc, cm.hierarchy_separator) or (bm and not nonoverlap(cb, bm.hierarchy_separator)):
            reason = 'target_parent_child_overlap'
        kinds = {g.declared_kind for g in matched}
        group = Group(cb, cc, reason, tid, metrics, sorted({x for g in matched for x in g.mapping_ids}), next(iter(kinds)) if len(kinds) == 1 else None)
        selected.append(group)
        if request.scope.include_descendants or p.get('scope') == 'subtree':
            for g in groups:
                if any(x != root and descendant(x, root, cm.hierarchy_separator) for x in g.current_paths for root in cp) or (bm and any(x != root and descendant(x, root, bm.hierarchy_separator) for x in g.baseline_paths for root in bp)):
                    selected.append(Group(g.baseline_paths, g.current_paths, g.reason, tid, metrics, g.mapping_ids, g.declared_kind))
    return list({g.id: g for g in selected}.values())


def total(rows: dict, paths: list[str], metric: str):
    if not paths or any(p not in rows for p in paths):
        return None
    values = []
    for path in paths:
        row = rows[path]
        if metric in QUALITY:
            prefixes = VT if metric.startswith('vth') else DRIVE
            value = D(row['logic_cells']) - sum((D(row[f'{v}_cnt']) for v in prefixes), D(0))
        else:
            value = row.get(metric)
        if value is None:
            return None
        values.append(D(value))
    return sum(values, D(0))


def metric_value(rows, paths, metric):
    if metric.endswith('_pct'):
        d = total(rows, paths, 'logic_area')
        if metric == 'cts_area_pct':
            n = total(rows, paths, 'cts_area')
        elif d is None:
            n = None
        else:
            # A zero-area row contributes no area even when its ratio is unavailable.
            contributions = [D(rows[path][metric]) * D(rows[path]['logic_area']) / D(100)
                             for path in paths if D(rows[path]['logic_area']) != 0 and rows[path].get(metric) is not None]
            n = None if any(D(rows[path]['logic_area']) != 0 and rows[path].get(metric) is None for path in paths) else sum(contributions, D(0))
        return (D(100) * n / d if n is not None and d else None), n, d
    return total(rows, paths, metric), None, None


def known_metadata(value):
    if value is None or value == '' or value == {}:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in ('unknown', 'unavailable', 'unspecified', 'n/a')
    return True


def comparability(metric, group, cm, bm, current, baseline, cu, bu):
    if group.reason:
        return 'unavailable', group.reason
    if not group.baseline_paths or not group.current_paths:
        return 'unavailable', 'declared_' + (group.declared_kind or 'missing_side')
    if baseline is None or bu is None:
        return 'unavailable', 'baseline_missing'
    if metric in POWER:
        if not current.conditions_complete or not baseline.conditions_complete:
            return 'contextual', 'power_conditions_incomplete'
        if bytes(current.condition_key) != bytes(baseline.condition_key):
            return 'contextual', 'power_conditions_different'
        return 'comparable', None
    if metric.startswith(('vt_', 'drv_', 'vth_', 'drive_')):
        if not known_metadata(cm.library) or not known_metadata(bm.library) or cm.library != bm.library or not known_metadata(cm.classification) or not known_metadata(bm.classification) or cm.classification != bm.classification:
            return 'unavailable', 'library_or_classification_mapping_unconfirmed'
    checks = ('design_stage', 'constraint_context', 'classification', 'library')
    if '_area' in metric or metric.endswith('_pct') or metric in DISTRIBUTIONS:
        checks += ('area_unit', 'area_basis')
    if any(not known_metadata(getattr(cm, k)) or not known_metadata(getattr(bm, k)) or getattr(cm, k) != getattr(bm, k) for k in checks):
        return 'contextual', 'structural_metadata_unknown_or_different'
    return 'comparable', None


def expected_bounds(extractions, group, metric):
    for extraction in extractions:
        if extraction.get('applicability_reason') or extraction.get('unresolved'):
            continue
        for item in extraction.get('expected', []):
            if item.get('metric') != metric or item.get('unresolved'):
                continue
            paths = item.get('resolved_paths', extraction.get('resolved_paths', []))
            tids = [str(v) for v in (item.get('target_ids') or extraction.get('target_ids', []))]
            if not paths and not tids:
                continue
            if (paths and set(paths) != set(group.current_paths)) or (tids and group.target_id is not None and str(group.target_id) not in tids) or (not paths and tids and group.target_id is None):
                continue
            yield item


def evaluate_metric(metric, group, current, baseline, cm, bm, cu, bu, policy, pair=None):
    b_rows = baseline.rows if baseline else {}
    state, reason = comparability(metric, group, cm, bm, current, baseline, cu, bu)
    # Invalid groups never expose a double-counted sum, even as a contextual value.
    baseline_paths = group.baseline_paths if not bm or nonoverlap(group.baseline_paths, bm.hierarchy_separator) else []
    current_paths = group.current_paths if nonoverlap(group.current_paths, cm.hierarchy_separator) else []
    bn = bd = cn = cd = None
    if metric in DISTRIBUTIONS:
        prefixes = VT if metric.startswith('vth') else DRIVE
        bd, cd = total(b_rows, baseline_paths, 'logic_area'), total(current.rows, current_paths, 'logic_area')
        b, c = None, None
        if bd and cd:
            before = [metric_value(b_rows, baseline_paths, p + '_pct')[0] for p in prefixes]
            after = [metric_value(current.rows, current_paths, p + '_pct')[0] for p in prefixes]
            if all(value is not None for value in before + after):
                differences = [abs(cv - bv) for bv, cv in zip(before, after)]
                differences.append(abs(max(D(0), D(100) - sum(after)) - max(D(0), D(100) - sum(before))))
                c = sum(differences, D(0)) / D(200)
        delta = c
        relative = pp = None
    else:
        b, bn, bd = metric_value(b_rows, baseline_paths, metric)
        c, cn, cd = metric_value(current.rows, current_paths, metric)
        delta = c - b if c is not None and b is not None else None
        relative = delta / b * D(100) if delta is not None and b else None
        pp = delta if metric.endswith('_pct') else None
    if c is None or (b is None and metric not in DISTRIBUTIONS):
        state, reason = 'unavailable', reason or ('zero_or_missing_denominator' if metric.endswith('_pct') else 'metric_or_path_missing')
    if metric in DISTRIBUTIONS and c is None:
        state, reason = 'unavailable', reason or 'zero_or_missing_denominator'
    if state == 'unavailable':
        delta = relative = pp = None
    evidence = {'mapping_ids': group.mapping_ids, 'declared_kind': group.declared_kind, 'threshold_exceeded': [], 'operating_limit_exceeded': [], 'expected_range_exceeded': [], 'policy_unconfigured': not bool(policy.metrics or policy.operating_limits)}
    evidence['hierarchy_separator'] = cm.hierarchy_separator
    threshold = policy.metrics.get(metric)
    tiny = False
    if threshold:
        if threshold.min_denominator is not None:
            denominators = [v for v in (bd, cd) if v is not None] or [v for v in (b, c) if v is not None]
            tiny = any(abs(v) < threshold.min_denominator for v in denominators)
        for key, value in (('absolute_change', delta), ('relative_change_pct', relative), ('pct_point_change', pp)):
            bound = getattr(threshold, key)
            if bound is not None and value is not None and abs(value) > bound and not (tiny and key == 'relative_change_pct'):
                evidence['threshold_exceeded'].append(key)
    evidence['tiny_denominator'] = tiny
    limits = policy.operating_limits.get(metric, {})
    for key, violation in (('lower', c is not None and limits.get('lower') is not None and c < D(limits['lower'])), ('upper', c is not None and limits.get('upper') is not None and c > D(limits['upper'])), ('min_denominator', limits.get('min_denominator') is not None and any(x is not None and x < D(limits['min_denominator']) for x in (bd, cd)))):
        if violation:
            evidence['operating_limit_exceeded'].append(key)
    evidence['data_quality'] = metric in QUALITY and ((b is not None and b > 0) or (c is not None and c > 0))
    evidence['new_nonzero_value'] = b == 0 and c is not None and c != 0
    candidate = state != 'comparable' or tiny or evidence['data_quality'] or evidence['new_nonzero_value'] or any(evidence[k] for k in ('threshold_exceeded', 'operating_limit_exceeded'))
    if metric in DISTRIBUTIONS and state == 'comparable':
        candidate = False  # Auxiliary distribution distance never independently triggers an alert.
    unit = 'mW' if metric in POWER else ('%' if metric.endswith('_pct') else (cm.area_unit if metric.endswith('_area') else ('ratio' if metric in DISTRIBUTIONS else 'cells')))
    return {'group_id': group.id, 'target_id': group.target_id, 'baseline_paths': group.baseline_paths, 'current_paths': group.current_paths, 'hier_name': ' + '.join(group.current_paths or group.baseline_paths) or 'Unresolved target', 'metric': metric, 'unit': unit, 'baseline': b, 'current': c, 'delta': delta, 'relative_change_pct': relative, 'pct_point_change': pp, 'baseline_numerator': bn, 'baseline_denominator': bd, 'current_numerator': cn, 'current_denominator': cd, 'comparability': state, 'reason': reason, 'is_candidate': bool(candidate), 'evidence': evidence, 'power_pair': pair}


def _validate_frozen_upload(snapshot, selected):
    if snapshot is None or selected is None:
        if snapshot is not None or selected is not None:
            raise DomainError('Selected upload differs from frozen comparison input', code='frozen_input_mismatch')
        return
    required = ('id', 'project_id', 'version_id', 'block_id', 'snapshot_id', 'condition_key', 'structure_digest', 'original_sha256')
    if (not isinstance(snapshot, dict) or any(key not in snapshot for key in required)
            or ('input_contract' not in snapshot and snapshot.get('input_contract_sha256') is None)):
        raise DomainError('Frozen upload identity or contract is incomplete', code='frozen_input_mismatch')
    keys = ('id', 'project_id', 'version_id', 'block_id', 'snapshot_id', 'condition_key', 'structure_digest', 'original_sha256', 'input_contract', 'analysis_conditions')
    if any(key in snapshot and digest(snapshot[key]) != digest(selected[key]) for key in keys):
        raise DomainError('Selected upload metadata differs from frozen comparison input', code='frozen_input_mismatch')
    if snapshot.get('input_contract_sha256') is not None and snapshot['input_contract_sha256'] != digest(selected['input_contract']).hex():
        raise DomainError('Selected upload contract differs from frozen comparison input', code='frozen_input_mismatch')


def load_inputs(db, settings, comparison, *, observations=False):
    request = ComparisonRequest.model_validate(comparison['payload']['request'])
    unknown_policy = (set(request.policy.metrics) | set(request.policy.operating_limits)) - set(METRICS)
    if unknown_policy:
        raise DomainError('Detection policy contains unsupported metric keys: ' + ', '.join(sorted(unknown_policy)), code='unknown_policy_metric')
    with db.connect() as conn:
        cu = db.upload(conn, request.current_upload_id, include_blob=True)
        bu = db.upload(conn, request.baseline_upload_id, include_blob=True) if request.baseline_upload_id else None
        chain = version_chain(db, conn, cu['version_id'])
        if bu and request.comparison_kind == 'previous_version' and (str(bu['project_id']) != str(cu['project_id']) or str(bu['version_id']) not in chain):
            raise DomainError('Previous-version baseline must be on the selected explicit project ancestry', code='invalid_baseline_interval')
        if bu and request.comparison_kind == 'previous_project' and str(bu['project_id']) == str(cu['project_id']):
            raise DomainError('Previous-project baseline requires a different explicit project', code='invalid_baseline_project')
        frozen = comparison['payload'].get('frozen', {})
        mapping = frozen.get('mapping')
        if request.mapping_id and mapping is None:
            mapping = db.record(conn, request.mapping_id, kind='mapping')
        if 'scope_target_ids' in frozen:
            targets = [db.record(conn, identifier, kind='target') for identifier in frozen['scope_target_ids']]
        else:
            targets = frozen.get('scope_targets', frozen.get('targets', []))
        for name, selected in (('current_upload', cu), ('baseline_upload', bu)):
            if name in frozen:
                _validate_frozen_upload(frozen[name], selected)
    current, cm = parse_upload(cu, settings, observations=observations)
    baseline, bm = parse_upload(bu, settings, observations=observations) if bu else (None, None)
    mapping_reason = None
    if mapping:
        payload = mapping['payload']
        MappingPayload.model_validate({k: v for k, v in payload.items() if k not in ('state', 'path_validation')})
        for side, upload in (('current', cu), ('baseline', bu)):
            keys = ('project_id', 'block_id') if side == 'current' else ('project_id', 'version_id', 'block_id')
            if upload is None or any(str(payload[f'{side}_{k}']) != str(upload[k]) for k in keys):
                mapping_reason = 'mapping_selected_scope_mismatch'
        explicit_range = payload.get('valid_from_version_id') or payload.get('valid_through_version_id')
        if not explicit_range and str(payload['current_version_id']) != str(cu['version_id']):
            mapping_reason = 'mapping_selected_scope_mismatch'
        start = str(payload.get('valid_from_version_id') or payload['current_version_id'])
        if start not in chain:
            mapping_reason = 'mapping_version_interval_unresolved'
        if payload.get('valid_through_version_id'):
            with db.connect() as conn:
                through_chain = version_chain(db, conn, payload['valid_through_version_id'])
            if str(cu['version_id']) not in through_chain or start not in through_chain or through_chain.index(str(cu['version_id'])) > through_chain.index(start):
                mapping_reason = 'mapping_version_interval_unresolved'
    full_groups = resolve_groups(current, baseline, cm, bm, mapping, mapping_reason, comparison['payload'].get('correspondence'))
    groups = select_groups(full_groups, request, targets, cu, bu, cm, bm)
    return request, cu, bu, current, baseline, cm, bm, groups, chain, full_groups


def _reference_contract(comparison):
    if comparison['payload'].get('result_format') != COMPARISON_FORMAT:
        raise DomainError('Create a new comparison to use the current result format.', code='comparison_format_upgrade_required')
    if comparison['payload'].get('calculation_contract') != CALCULATION_CONTRACT:
        raise DomainError('This comparison calculation contract is unsupported.', code='comparison_contract_unsupported')


def _power_pairs(db, settings, comparison, request, cu, bu, current, baseline, cm, bm, *, observations=False):
    pairs = [(cu, bu, current, baseline, cm, bm, None)]
    pair_ids = {(str(cu['id']), str(bu['id']) if bu else None)}
    frozen = comparison['payload'].get('frozen', {}).get('power_uploads', [])
    if len(frozen) != len(request.power_pairs):
        raise DomainError('Power uploads differ from frozen comparison input', code='frozen_input_mismatch')
    for index, spec in enumerate(request.power_pairs):
        with db.connect() as conn:
            pc = db.upload(conn, spec['current_upload_id'], include_blob=True)
            pb = db.upload(conn, spec['baseline_upload_id'], include_blob=True) if spec.get('baseline_upload_id') else None
        _validate_frozen_upload(frozen[index].get('current_upload'), pc)
        _validate_frozen_upload(frozen[index].get('baseline_upload'), pb)
        pair_id = (str(pc['id']), str(pb['id']) if pb else None)
        if pair_id in pair_ids:
            raise DomainError('The same power execution pair was requested more than once', code='duplicate_power_pair')
        pair_ids.add(pair_id)
        if str(pc['snapshot_id']) != str(cu['snapshot_id']) or (pb and (not bu or str(pb['snapshot_id']) != str(bu['snapshot_id']))):
            raise DomainError('Additional power runs must belong to selected snapshot sides', code='power_snapshot_mismatch')
        cp, cmeta = parse_upload(pc, settings, observations=observations)
        bp, bmeta = parse_upload(pb, settings, observations=observations) if pb else (None, None)
        pairs.append((pc, pb, cp, bp, cmeta, bmeta, spec))
    return pairs


def _pair_precision(current, baseline):
    highest, lowest = 0, 0
    for parsed in (current, baseline):
        if parsed:
            if isinstance(parsed, ObservedCSV):
                highest = max(highest, parsed.numeric_highest)
                lowest = min(lowest, parsed.numeric_lowest)
                continue
            for row in parsed.rows.values():
                for value in row.values():
                    if isinstance(value, (D, int)):
                        decimal = D(value)
                        highest = max(highest, decimal.adjusted())
                        lowest = min(lowest, decimal.as_tuple().exponent)
    return max(80, highest - lowest + len(str(max(len(current.rows), len(baseline.rows) if baseline else 0))) + 40)


def _power_pair(pc, pb, current, baseline, spec):
    return {'current_upload_id': pc['id'], 'baseline_upload_id': pb['id'] if pb else None,
            'current_conditions': current.conditions, 'baseline_conditions': baseline.conditions if baseline else None,
            'label': spec.get('label') if spec else None}


def comparison_coverage(db, settings, comparison):
    _reference_contract(comparison)
    view = observation_view(db, settings, comparison)
    request, cu, bu, current, baseline, cm, bm, groups = view.request, view.cu, view.bu, view.current, view.baseline, view.cm, view.bm, view.groups
    pairs = _power_pairs(db, settings, comparison, request, cu, bu, current, baseline, cm, bm, observations=True)
    expected = sum(len(group.metrics) + sum(1 for _ in pairs[1:] for metric in group.metrics if metric in POWER) for group in groups)
    return {'scope_validation': 'complete', 'metric_evaluation': 'on_demand',
            'expected_scopes': len(groups), 'processed_scopes': len(groups),
            'unresolved_scopes': sum(group.reason is not None for group in groups),
            'expected_metrics': expected, 'processed_metrics': 0, 'stored_result_rows': 0,
            'power_pairs': len(pairs)}


def compute_comparison(db, settings, comparison_id):
    with db.connect() as conn:
        comparison = db.record(conn, comparison_id, lock=True, kind='comparison')
        if comparison['state'] == 'completed':
            return comparison
        _reference_contract(comparison)
        db.update_record(conn, comparison_id, 'vwp-worker', state='running', error_detail=None)
    coverage = comparison_coverage(db, settings, comparison)
    with db.connect() as conn:
        row = db.record(conn, comparison_id, lock=True, kind='comparison')
        if row['state'] == 'completed':
            return row
        _reference_contract(row)
        payload = {**row['payload'], 'coverage': coverage}
        return db.update_record(conn, comparison_id, 'vwp-worker', state='completed', payload=payload, error_detail=None)


def iter_comparison_metrics(db, settings, comparison):
    _reference_contract(comparison)
    request, cu, bu, current, baseline, cm, bm, groups, chain, full_groups = load_inputs(db, settings, comparison)
    pairs = _power_pairs(db, settings, comparison, request, cu, bu, current, baseline, cm, bm)
    for index, (pc, pb, cp, bp, cmeta, bmeta, spec) in enumerate(pairs):
        pair = _power_pair(pc, pb, cp, bp, spec)
        precision = _pair_precision(cp, bp)
        for group in groups:
            for metric in group.metrics:
                if index and metric not in POWER:
                    continue
                with localcontext() as context:
                    context.prec = precision
                    result = evaluate_metric(metric, group, cp, bp, cmeta, bmeta, pc, pb, request.policy, pair if metric in POWER else None)
                yield pc, pb, result


def group_tree(groups: list[Group], cs: str, bs: str) -> dict[str, str | None]:
    """Display parent of each group: the nearest ancestor path owned by another group.

    The current side decides placement; baseline-only groups use the baseline
    side. Correspondence and values are unchanged. Explicit mappings may invert
    nesting across sides, so display cycles are broken at the root.
    """
    owner = ({}, {})
    for group in groups:
        for index, paths in enumerate((group.current_paths, group.baseline_paths)):
            for path in paths:
                owner[index].setdefault(path, group.id)
    parents: dict[str, str | None] = {}
    for group in groups:
        side = 0 if group.current_paths else 1
        paths = group.current_paths or group.baseline_paths
        separator = cs if side == 0 else bs
        parent = None
        path = paths[0] if paths else ''
        while parent is None and separator in path:
            path = path.rsplit(separator, 1)[0]
            candidate = owner[side].get(path)
            if candidate is not None and candidate != group.id:
                parent = candidate
        parents.setdefault(group.id, parent)
    for start in list(parents):
        trail, node = [], start
        while node is not None and node not in trail:
            trail.append(node)
            node = parents.get(node)
        if node is not None:
            parents[node] = None
    return parents


@dataclass
class ObservationView:
    """Immutable inputs of one frozen comparison, prepared once per process."""
    request: Any
    cu: dict
    bu: dict | None
    current: Any
    baseline: Any
    cm: Any
    bm: Any
    groups: list[Group]
    order: list[int]
    children: dict[str | None, list[int]]
    child_count: dict[str, int]
    index: dict[str, int]
    sizes: list[Decimal | None]


_VIEW_CACHE: OrderedDict[tuple[str, str], ObservationView] = OrderedDict()
_VIEW_LOCK = threading.Lock()
_VIEW_BUILDING: dict[tuple[str, str], threading.Lock] = {}
VIEW_CACHE_SIZE = 4
FROZEN_KEYS = ('request', 'frozen', 'result_format', 'calculation_contract', 'correspondence')


# Metrics an observation page may evaluate: totals, areas, cells, the CTS
# share and reported Vth/drive shares, and power. Requests name a subset.
DEFAULT_OBSERVED = ('total_area', 'pwr_all_total_mw')
OBSERVABLE = tuple(metric for metric in METRICS if metric in DEFAULT_OBSERVED + OBSERVED_EXTRA + ('cts_area_pct',))
MAX_OBSERVED = 16


def _side_area(rows, paths):
    values = [rows[path]['total_area'] for path in paths if path in rows and rows[path].get('total_area') is not None]
    return sum(values, D(0)) if values and len(values) == len(paths) else None


def _area_key(group: Group, current, baseline):
    """Largest instance first: current total area, else the baseline's."""
    value = _side_area(current.rows, group.current_paths)
    if value is None:
        value = _side_area(baseline.rows if baseline else {}, group.baseline_paths)
    return (value is None, -(value or D(0)), (group.current_paths or group.baseline_paths or [''])[0])


def _group_size(group: Group, current, baseline):
    """The larger side's total area, so a shrunken or removed instance stays visible."""
    values = [value for value in (_side_area(current.rows, group.current_paths), _side_area(baseline.rows if baseline else {}, group.baseline_paths)) if value is not None]
    return max(values) if values else None


def observation_view(db, settings, comparison) -> ObservationView:
    """Parse, correspond and index a frozen comparison once.

    The key covers every frozen input and stored originals are immutable and
    checksum-verified, so a cached view can only differ from a fresh parse by
    being older than nothing. Authorization is still checked per request by
    the caller.
    """
    key = (str(comparison['id']), digest({name: comparison['payload'].get(name) for name in FROZEN_KEYS}).hex())
    with _VIEW_LOCK:
        if key in _VIEW_CACHE:
            _VIEW_CACHE.move_to_end(key)
            return _VIEW_CACHE[key]
        building = _VIEW_BUILDING.setdefault(key, threading.Lock())
    with building:  # concurrent expands wait for one build instead of parsing again
        with _VIEW_LOCK:
            if key in _VIEW_CACHE:
                return _VIEW_CACHE[key]
        request, cu, bu, current, baseline, cm, bm, groups, chain, full_groups = load_inputs(db, settings, comparison, observations=True)
        parents = group_tree(groups, cm.hierarchy_separator, bm.hierarchy_separator if bm else cm.hierarchy_separator)
        order = sorted(range(len(groups)), key=lambda position: _area_key(groups[position], current, baseline))
        children: dict[str | None, list[int]] = {}
        for position in order:
            children.setdefault(parents[groups[position].id], []).append(position)
        view = ObservationView(request, cu, bu, current, baseline, cm, bm, groups, order, children,
                               {identifier: len(rows) for identifier, rows in children.items() if identifier is not None},
                               {group.id: position for position, group in enumerate(groups)},
                               [_group_size(group, current, baseline) for group in groups])
        with _VIEW_LOCK:
            _VIEW_CACHE[key] = view
            while len(_VIEW_CACHE) > VIEW_CACHE_SIZE:
                _VIEW_CACHE.popitem(last=False)
            _VIEW_BUILDING.pop(key, None)
        return view


def comparison_observations(db, settings, comparison, offset=0, limit=200, search='', parent=None, metrics=None, min_area=None):
    _reference_contract(comparison)
    if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or limit < 1 or limit > 500:
        raise DomainError('Observation pagination requires offset >= 0 and limit 1..500.', code='invalid_pagination')
    metrics = tuple(dict.fromkeys(metrics or DEFAULT_OBSERVED))
    unknown = [metric for metric in metrics if metric not in OBSERVABLE]
    if unknown or len(metrics) > MAX_OBSERVED:
        raise DomainError(f'Observation metrics must be at most {MAX_OBSERVED} of: ' + ', '.join(OBSERVABLE), code='invalid_observation_metrics')
    if min_area is not None and (not isinstance(min_area, Decimal) or not min_area.is_finite() or min_area < 0):
        raise DomainError('Area threshold must be a finite number >= 0.', code='invalid_area_threshold')
    view = observation_view(db, settings, comparison)
    # Rows whose larger side is below the threshold are hidden, not removed:
    # values and correspondence are unchanged and the hidden count is reported.
    shown = (lambda position: True) if not min_area else (lambda position: view.sizes[position] is None or view.sizes[position] >= min_area)
    request, cu, bu, current, baseline, cm, bm = view.request, view.cu, view.bu, view.current, view.baseline, view.cm, view.bm
    needle = search.strip().casefold()
    nodes = None
    if needle:
        candidates = [position for position in view.order if any(needle in path.casefold() for path in view.groups[position].baseline_paths + view.groups[position].current_paths)]
    elif parent is not None:
        if parent and parent not in view.index:
            raise DomainError('The requested hierarchy node is not part of this comparison.', code='unknown_hierarchy_node', status_code=404)
        candidates = view.children.get(parent or None, [])
        nodes = True
    else:
        candidates = view.order
    # Design roots stay visible so a threshold never empties the tree.
    selected = candidates if nodes and not parent else [position for position in candidates if shown(position)]
    groups = [view.groups[position] for position in selected[offset:offset + limit]]
    total = len(selected)
    pair = _power_pair(cu, bu, current, baseline, None)
    precision = _pair_precision(current, baseline)
    items = []
    with localcontext() as context:
        context.prec = precision
        for group in groups:
            for metric in metrics:
                items.append(evaluate_metric(metric, group, current, baseline, cm, bm, cu, bu, request.policy, pair if metric in POWER else None))
    page = {'items': items, 'total_groups': total, 'hidden_groups': len(candidates) - total, 'offset': offset, 'limit': limit, 'search': search,
            'metrics': list(metrics), 'min_area': min_area}
    if nodes:
        page['parent'] = parent
        page['nodes'] = [{'group_id': group.id, 'parent_id': parent or None,
                          'child_count': sum(1 for position in view.children.get(group.id, []) if shown(position)),
                          'hidden_child_count': sum(1 for position in view.children.get(group.id, []) if not shown(position))} for group in groups]
    return page
