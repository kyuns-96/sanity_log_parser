"""Frozen input validation, cached observation views, and paginated comparison pages."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, localcontext
import threading
from typing import Any

from common.area_power.csv_input import OBSERVED_EXTRA, ObservedCSV
from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest
from common.area_power.models import (
    CALCULATION_CONTRACT,
    COMPARISON_FORMAT,
    CORRESPONDENCE_POLICY,
    ComparisonRequest,
    MappingPayload,
)
from .groups import (
    D,
    DRIVE_GROUP_METRICS,
    Group,
    METRICS,
    POWER,
    group_tree,
    parse_upload,
    resolve_groups,
    select_groups,
    version_chain,
)
from .metrics import evaluate_metric


def frozen_upload_reference(upload: dict | None) -> dict[str, Any] | None:
    if upload is None:
        return None
    fields = ('id', 'project_id', 'block_id', 'version_id', 'snapshot_id', 'condition_key', 'structure_digest', 'original_sha256')
    return {**{field: upload[field] for field in fields}, 'input_contract_sha256': digest(upload['input_contract']).hex()}


def build_comparison_dedupe_and_payload(
    request: dict[str, Any],
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    targets: list[dict[str, Any]] | None = None,
    power_uploads: list[dict[str, Any]] | None = None,
    designation: dict[str, Any] | None = None,
    policy_unconfigured: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    targets = targets or []
    power_uploads = power_uploads or []
    numeric = {key: value for key, value in request.items() if key not in ('change_ids', 'recipients')}
    numeric['result_format'] = COMPARISON_FORMAT
    numeric['calculation_contract'] = CALCULATION_CONTRACT
    numeric['target_revisions'] = sorted(str(target['id']) for target in targets)
    if designation is not None:
        numeric['baseline_project_id'] = designation['baseline_project_id']
    correspondence = CORRESPONDENCE_POLICY if baseline and baseline['project_id'] != current['project_id'] else None
    if correspondence:
        numeric['correspondence'] = correspondence
    key = digest(numeric)
    frozen: dict[str, Any] = {
        'current_upload': frozen_upload_reference(current),
        'baseline_upload': frozen_upload_reference(baseline),
        'scope_target_ids': [target['id'] for target in targets],
        'power_uploads': [{side: frozen_upload_reference(upload) for side, upload in pair.items()} for pair in power_uploads],
    }
    if designation is not None:
        frozen['baseline_designation'] = designation
    payload: dict[str, Any] = {
        'request': request,
        'result_format': COMPARISON_FORMAT,
        'calculation_contract': CALCULATION_CONTRACT,
        'frozen': frozen,
        'coverage': {},
        **({'correspondence': correspondence} if correspondence else {}),
        'policy_unconfigured': policy_unconfigured,
        'ai_state': 'not_requested',
    }
    return key, payload


def validate_frozen_upload(snapshot, selected):
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
                validate_frozen_upload(frozen[name], selected)
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
        validate_frozen_upload(frozen[index].get('current_upload'), pc)
        validate_frozen_upload(frozen[index].get('baseline_upload'), pb)
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
    parents: dict[str, str | None]


_VIEW_CACHE: OrderedDict[tuple[str, str], ObservationView] = OrderedDict()
_VIEW_LOCK = threading.Lock()
_VIEW_BUILDING: dict[tuple[str, str], threading.Lock] = {}
VIEW_CACHE_SIZE = 4
FROZEN_KEYS = ('request', 'frozen', 'result_format', 'calculation_contract', 'correspondence')
DEFAULT_OBSERVED = ('total_area', 'pwr_all_total_mw')
OBSERVABLE = tuple(metric for metric in METRICS if metric in DEFAULT_OBSERVED + OBSERVED_EXTRA + ('cts_area_pct',) + DRIVE_GROUP_METRICS)
MAX_OBSERVED = 16


def _side_area(rows, paths):
    values = [rows[path]['total_area'] for path in paths if path in rows and rows[path].get('total_area') is not None]
    return sum(values, D(0)) if values and len(values) == len(paths) else None


def _area_key(group: Group, current, baseline):
    value = _side_area(current.rows, group.current_paths)
    if value is None:
        value = _side_area(baseline.rows if baseline else {}, group.baseline_paths)
    return (value is None, -(value or D(0)), (group.current_paths or group.baseline_paths or [''])[0])


def _group_size(group: Group, current, baseline):
    values = [value for value in (_side_area(current.rows, group.current_paths), _side_area(baseline.rows if baseline else {}, group.baseline_paths)) if value is not None]
    return max(values) if values else None


def observation_view(db, settings, comparison) -> ObservationView:
    """Parse, correspond and index a frozen comparison once."""
    key = (str(comparison['id']), digest({name: comparison['payload'].get(name) for name in FROZEN_KEYS}).hex())
    with _VIEW_LOCK:
        if key in _VIEW_CACHE:
            _VIEW_CACHE.move_to_end(key)
            return _VIEW_CACHE[key]
        building = _VIEW_BUILDING.setdefault(key, threading.Lock())
    with building:
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
                               [_group_size(group, current, baseline) for group in groups], parents)
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
    selected = candidates if nodes and not parent else [position for position in candidates if shown(position)]
    groups = [view.groups[position] for position in selected[offset:offset + limit]]
    total_count = len(selected)
    pair = _power_pair(cu, bu, current, baseline, None)
    precision = _pair_precision(current, baseline)
    items = []
    with localcontext() as context:
        context.prec = precision
        for group in groups:
            for metric in metrics:
                items.append(evaluate_metric(metric, group, current, baseline, cm, bm, cu, bu, request.policy, pair if metric in POWER else None))
    page = {'items': items, 'total_groups': total_count, 'hidden_groups': len(candidates) - total_count, 'offset': offset, 'limit': limit, 'search': search,
            'metrics': list(metrics), 'min_area': min_area}
    if nodes:
        page['parent'] = parent
        page['nodes'] = [{'group_id': group.id, 'parent_id': parent or None,
                          'child_count': sum(1 for position in view.children.get(group.id, []) if shown(position)),
                          'hidden_child_count': sum(1 for position in view.children.get(group.id, []) if not shown(position))} for group in groups]
    return page
