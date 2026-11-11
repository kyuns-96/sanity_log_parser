"""Exact, snapshot-frozen hierarchy comparisons; no provider calls or DDL."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Any
from hashlib import sha256

from psycopg import sql

from area_power.common.errors import DomainError
from area_power.common.jsonutil import digest
from area_power.common.models import ComparisonRequest, MappingPayload, UploadMetadata
from area_power.vwp_server.csv_input import parse_csv

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
    return conn.execute(sql.SQL('SELECT * FROM {} WHERE record_type=%s AND dedupe_key=%s' + (' FOR UPDATE' if lock else '')).format(db.table('tracking_record')), (kind, key)).fetchone()


def advisory_lock(conn, key: bytes) -> None:
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (int.from_bytes(key[:8], 'big', signed=True),))


def parse_upload(upload: dict, settings):
    if upload['state'] != 'ready':
        raise DomainError('Selected upload is not ready', code='upload_not_ready')
    metadata = UploadMetadata.model_validate(upload['input_contract']['metadata'])
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

    @property
    def id(self) -> str:
        return digest({'baseline': sorted(self.baseline_paths), 'current': sorted(self.current_paths), 'target': self.target_id}).hex()


def resolve_groups(current, baseline, cm, bm, mapping: dict | None, mapping_reason: str | None = None) -> list[Group]:
    cr, br = current.rows, baseline.rows if baseline else {}
    same = bm is not None and cm.project_id == bm.project_id
    cs, bs = cm.hierarchy_separator, bm.hierarchy_separator if bm else cm.hierarchy_separator
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
        elif same and not mapping_reason and path in br and path not in exact_baseline and path not in consumed_b and not any(descendant(path, b, bs) or descendant(path, c, cs) for b, c in reservations):
            g = Group([path], [path])
        else:
            reason = mapping_reason or ('baseline_missing' if baseline is None else 'mapping_unresolved')
            # A renamed root is an unresolved correspondence, never two declarations of addition/deletion.
            bp = [baseline.root_path] if baseline and path == current.root_path and baseline.root_path not in consumed_b else []
            g = Group(bp, [path], reason)
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
        numerator = 'cts_area' if metric == 'cts_area_pct' else metric[:-4] + '_cnt'
        denominator = 'logic_area' if metric == 'cts_area_pct' else 'logic_cells'
        n, d = total(rows, paths, numerator), total(rows, paths, denominator)
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
    if '_area' in metric and not metric.endswith('_pct'):
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
        bd, cd = total(b_rows, baseline_paths, 'logic_cells'), total(current.rows, current_paths, 'logic_cells')
        b, c = None, None
        if bd and cd:
            differences = [abs(total(current.rows, current_paths, p + '_cnt') / cd - total(b_rows, baseline_paths, p + '_cnt') / bd) for p in prefixes]
            quality = QUALITY[0 if metric.startswith('vth') else 1]
            differences.append(abs(total(current.rows, current_paths, quality) / cd - total(b_rows, baseline_paths, quality) / bd))
            c = sum(differences, D(0)) / D(2)
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


def load_inputs(db, settings, comparison):
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
        targets = frozen.get('scope_targets', frozen.get('targets', []))
        for name, selected in (('current_upload', cu), ('baseline_upload', bu)):
            snapshot = frozen.get(name)
            if snapshot and selected:
                keys = ('id', 'project_id', 'version_id', 'block_id', 'snapshot_id', 'condition_key', 'structure_digest', 'original_sha256', 'input_contract', 'analysis_conditions')
                if any(key in snapshot and digest(snapshot[key]) != digest(selected[key]) for key in keys):
                    raise DomainError('Selected upload metadata differs from frozen comparison input', code='frozen_input_mismatch')
    current, cm = parse_upload(cu, settings)
    baseline, bm = parse_upload(bu, settings) if bu else (None, None)
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
    full_groups = resolve_groups(current, baseline, cm, bm, mapping, mapping_reason)
    groups = select_groups(full_groups, request, targets, cu, bu, cm, bm)
    return request, cu, bu, current, baseline, cm, bm, groups, chain, full_groups


def compute_comparison(db, settings, comparison_id):
    with db.connect() as conn:
        comparison = db.record(conn, comparison_id, lock=True, kind='comparison')
        if comparison['state'] == 'completed':
            return comparison
        payload = dict(comparison['payload'])
        db.update_record(conn, comparison_id, 'vwp-worker', state='running', payload=payload, error_detail=None)
    request, cu, bu, current, baseline, cm, bm, groups, chain, full_groups = load_inputs(db, settings, comparison)
    pairs = [(cu, bu, current, baseline, cm, bm, None)]
    pair_ids = {(str(cu['id']), str(bu['id']) if bu else None)}
    for spec in request.power_pairs:
        with db.connect() as conn:
            pc = db.upload(conn, spec['current_upload_id'], include_blob=True)
            pb = db.upload(conn, spec['baseline_upload_id'], include_blob=True) if spec.get('baseline_upload_id') else None
        pair_id = (str(pc['id']), str(pb['id']) if pb else None)
        if pair_id in pair_ids:
            raise DomainError('The same power execution pair was requested more than once', code='duplicate_power_pair')
        pair_ids.add(pair_id)
        if str(pc['snapshot_id']) != str(cu['snapshot_id']) or (pb and (not bu or str(pb['snapshot_id']) != str(bu['snapshot_id']))):
            raise DomainError('Additional power runs must belong to selected snapshot sides', code='power_snapshot_mismatch')
        cp, cmeta = parse_upload(pc, settings)
        bp, bmeta = parse_upload(pb, settings) if pb else (None, None)
        pairs.append((pc, pb, cp, bp, cmeta, bmeta, spec))
    expected = sum(len(g.metrics) + sum(1 for _ in pairs[1:] for m in g.metrics if m in POWER) for g in groups)
    processed = candidates = 0
    for index, (pc, pb, cp, bp, cmeta, bmeta, spec) in enumerate(pairs):
        pair = {'current_upload_id': pc['id'], 'baseline_upload_id': pb['id'] if pb else None, 'current_conditions': cp.conditions, 'baseline_conditions': bp.conditions if bp else None, 'label': spec.get('label') if spec else None}
        highest, lowest = 0, 0
        for parsed in (cp, bp):
            if parsed:
                for row in parsed.rows.values():
                    for value in row.values():
                        if isinstance(value, (D, int)):
                            decimal = D(value)
                            highest = max(highest, decimal.adjusted())
                            lowest = min(lowest, decimal.as_tuple().exponent)
        precision = max(80, highest - lowest + len(str(max(len(cp.rows), len(bp.rows) if bp else 0))) + 40)
        for group in groups:
            results = []
            for metric in group.metrics:
                if index and metric not in POWER:
                    continue
                with localcontext() as context:
                    context.prec = precision
                    result = evaluate_metric(metric, group, cp, bp, cmeta, bmeta, pc, pb, request.policy, pair if metric in POWER else None)
                key = digest({'comparison': comparison_id, 'group': group.id, 'metric': metric, 'power_pair': {'current': pc['id'], 'baseline': pb['id'] if pb else None} if metric in POWER else None})
                results.append((key, result))
            with db.connect() as conn:
                for key, result in results:
                    advisory_lock(conn, key)
                    old = existing_dedupe(db, conn, 'metric_result', key)
                    if old is None:
                        db.insert_record(conn, 'metric_result', result, 'vwp-worker', parent_id=comparison_id, upload_id=pc['id'], baseline_upload_id=pb['id'] if pb else None, state=result['comparability'], dedupe_key=key, **scope_fields(pc))
                    elif digest(old['payload']) != digest(result):
                        raise DomainError('Immutable comparison inputs produced different metrics', code='immutable_result_conflict')
                    processed += 1
                    candidates += int(result['is_candidate'])
    if processed != expected:
        raise DomainError('Comparison metric coverage mismatch', code='coverage_mismatch')
    with db.connect() as conn:
        row = db.record(conn, comparison_id, lock=True, kind='comparison')
        payload = dict(row['payload'])
        payload['selected_inputs'] = {'current_upload': {k: v for k, v in cu.items() if k != 'csv_gzip'}, 'baseline_upload': {k: v for k, v in bu.items() if k != 'csv_gzip'} if bu else None,
                                      'power_pairs': [{'current_upload': {k: v for k, v in pc.items() if k != 'csv_gzip'}, 'baseline_upload': {k: v for k, v in pb.items() if k != 'csv_gzip'} if pb else None} for pc, pb, *_ in pairs]}
        if request.scope.kind == 'targets':
            payload['mapping_context'] = [{'baseline_paths': g.baseline_paths, 'current_paths': g.current_paths, 'reason': g.reason, 'mapping_ids': g.mapping_ids} for g in full_groups]
        payload.update({'coverage': {'expected_scopes': len(groups), 'processed_scopes': len(groups), 'expected_metrics': expected, 'processed_metrics': processed, 'candidate_metrics': candidates, 'power_pairs': len(pairs)}, 'policy_unconfigured': not bool(request.policy.metrics or request.policy.operating_limits), 'resolved_mapping': [{'group_id': g.id, 'baseline_paths': g.baseline_paths, 'current_paths': g.current_paths, 'target_id': g.target_id, 'reason': g.reason, 'mapping_ids': g.mapping_ids} for g in groups], 'version_ancestry': chain, 'ai_state': payload.get('ai_state', 'pending')})
        return db.update_record(conn, comparison_id, 'vwp-worker', state='completed', payload=payload, error_detail=None)
