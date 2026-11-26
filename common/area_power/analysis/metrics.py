"""Metric aggregation, comparability evaluation, and policy threshold checking."""
from __future__ import annotations

from common.area_power.csv_input import UNKNOWN_METADATA_TOKENS
from .groups import (
    D,
    DISTRIBUTIONS,
    DRIVE,
    DRIVE_GROUP_MEMBERS,
    LEGACY_DRIVE,
    POWER,
    QUALITY,
    SUB1X_DRIVES,
    VT,
    nonoverlap,
)


def total(rows: dict, paths: list[str], metric: str):
    if not paths or any(p not in rows for p in paths):
        return None
    values = []
    for path in paths:
        row = rows[path]
        if metric in QUALITY:
            prefixes = VT if metric.startswith('vth') else tuple(v for v in DRIVE if f'{v}_cnt' in row)
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
        elif metric in DRIVE_GROUP_MEMBERS:
            members = DRIVE_GROUP_MEMBERS[metric]
            contributions = []
            missing = False
            for path in paths:
                area = D(rows[path]['logic_area'])
                if area == 0:
                    continue
                member_values = [rows[path].get(member) for member in members]
                if any(value is None for value in member_values):
                    missing = True
                    break
                contributions.append(sum((D(value) for value in member_values), D(0)) * area / D(100))
            n = None if missing else sum(contributions, D(0))
        else:
            contributions = [D(rows[path][metric]) * D(rows[path]['logic_area']) / D(100)
                             for path in paths if D(rows[path]['logic_area']) != 0 and rows[path].get(metric) is not None]
            n = None if any(D(rows[path]['logic_area']) != 0 and rows[path].get(metric) is None for path in paths) else sum(contributions, D(0))
        return (D(100) * n / d if n is not None and d else None), n, d
    return total(rows, paths, metric), None, None


def known_metadata(value):
    if value is None or value == '' or value == {}:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in UNKNOWN_METADATA_TOKENS
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
    baseline_paths = group.baseline_paths if not bm or nonoverlap(group.baseline_paths, bm.hierarchy_separator) else []
    current_paths = group.current_paths if nonoverlap(group.current_paths, cm.hierarchy_separator) else []
    bn = bd = cn = cd = None
    if metric in DISTRIBUTIONS:
        if metric.startswith('vth'):
            prefixes = VT
        else:
            has_sub1x = any(
                rows[path].get(f'{name}_pct') is not None
                for rows, paths in ((b_rows, baseline_paths), (current.rows, current_paths))
                for path in paths if path in rows
                for name in SUB1X_DRIVES
            )
            prefixes = DRIVE if has_sub1x else LEGACY_DRIVE
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
        candidate = False
    unit = 'mW' if metric in POWER else ('%' if metric.endswith('_pct') else (cm.area_unit if metric.endswith('_area') else ('ratio' if metric in DISTRIBUTIONS else 'cells')))
    return {'group_id': group.id, 'target_id': group.target_id, 'baseline_paths': group.baseline_paths, 'current_paths': group.current_paths, 'hier_name': ' + '.join(group.current_paths or group.baseline_paths) or 'Unresolved target', 'metric': metric, 'unit': unit, 'baseline': b, 'current': c, 'delta': delta, 'relative_change_pct': relative, 'pct_point_change': pp, 'baseline_numerator': bn, 'baseline_denominator': bd, 'current_numerator': cn, 'current_denominator': cd, 'comparability': state, 'reason': reason, 'is_candidate': bool(candidate), 'evidence': evidence, 'power_pair': pair}
