"""Instance-level candidate selection, scale-coupled damping, roll-up, and attribution."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, field_validator

from common.area_power.analysis import known_metadata, total

D = Decimal
ENGINE = 'instance-triage-v1'
PROMPT_VERSION = 'instance-triage-1'
BATCH_SIZE = 20
MAX_ITEMS = 400
ROLLUP_SHARE = D('0.8')
CONCENTRATED_ROLLUP_SHARE = D('0.75')
DEFAULT_NOISE_SHARE_PCT = D('0.1')
TOP_K_RANKING = 10
VERDICTS = ('expected', 'unexpected', 'needs_review', 'minor')
CAUSES = ('note', 'vth_shift', 'drive_shift', 'new_instance', 'unknown')
AREA, POWER_TOTAL = 'logic_area', 'pwr_all_total_mw'
# Run payload keys an explicit rerun must carry over unchanged.
RUN_KEYS = (
    'engine', 'retry_limit', 'thresholds', 'notes', 'note_warnings', 'same_rtl', 'watched_paths', 'process',
    'labels', 'comparison_kind', 'requested_by', 'approved_expectations', 'tracking_slot_id', 'tracking_generation',
    'comparison_condition', 'ai_model', 'attribution',
)


class Thresholds(BaseModel):
    model_config = ConfigDict(extra='forbid')
    area_increase_pct: Decimal = D(10)
    power_increase_pct: Decimal = D(10)
    min_area: Decimal = D(50)
    noise_share_pct: Decimal = DEFAULT_NOISE_SHARE_PCT

    @field_validator('area_increase_pct', 'power_increase_pct', 'min_area', 'noise_share_pct')
    @classmethod
    def finite_nonnegative(cls, value):
        if not value.is_finite() or value < 0:
            raise ValueError('A finite number >= 0 is required')
        return value

    @field_validator('noise_share_pct')
    @classmethod
    def valid_share(cls, value):
        if value > D(100):
            raise ValueError('noise_share_pct must be <= 100')
        return value


@dataclass
class Item:
    position: int
    group_id: str
    reasons: list[str]
    watched: bool = False
    removed: bool = False
    rolled_up: list[str] = field(default_factory=list)
    ranking_tags: list[str] = field(default_factory=list)
    distributed_metrics: list[str] = field(default_factory=list)
    depth1_block: str | None = None


@dataclass
class Selection:
    items: list[Item]
    missing_watched: list[str]
    counts: dict[str, int]
    over_limit: bool
    attribution: dict[str, dict] = field(default_factory=dict)


def _number(value):
    try:
        return None if value is None else D(str(value)).normalize()
    except InvalidOperation:
        return str(value)


def _corner(upload):
    return (upload.get('process_label'), _number(upload.get('voltage_v')), _number(upload.get('temp_c')), upload.get('pex'))


def side_context(view, current_process=None, baseline_process=None) -> dict:
    """Which metrics this comparison can analyse and whether both sides share a library.

    The comparison's displayed comparability is untouched; this only decides
    how the triage treats unconfirmed conditions. Library rule (user): same
    process and same corner mean the same library.
    """
    cu, bu, cm, bm = view.cu, view.bu, view.cm, view.bm
    if bu is None or view.baseline is None:
        missing = {'analyzed': False, 'unconfirmed': False, 'reason': 'baseline_missing'}
        return {'same_project': False, 'same_corner': False, 'area': dict(missing), 'power': dict(missing), 'library': 'unknown'}
    same_project = str(cu['project_id']) == str(bu['project_id'])
    same_corner = _corner(cu) == _corner(bu)
    complete = bool(view.current.conditions_complete and view.baseline.conditions_complete)
    same_key = bytes(view.current.condition_key) == bytes(view.baseline.condition_key)
    if not same_corner:
        power = {'analyzed': False, 'unconfirmed': False, 'reason': 'corner_different'}
    elif complete and not same_key:
        power = {'analyzed': False, 'unconfirmed': False, 'reason': 'analysis_conditions_different'}
    else:
        power = {'analyzed': True, 'unconfirmed': not (complete and same_key), 'reason': None}
    if known_metadata(cm.area_unit) and known_metadata(bm.area_unit) and cm.area_unit != bm.area_unit:
        area = {'analyzed': False, 'unconfirmed': False, 'reason': 'area_unit_different'}
    else:
        checks = ('design_stage', 'constraint_context', 'classification', 'library', 'area_unit', 'area_basis')
        unconfirmed = any(not known_metadata(getattr(cm, key)) or not known_metadata(getattr(bm, key))
                          or getattr(cm, key) != getattr(bm, key) for key in checks)
        area = {'analyzed': True, 'unconfirmed': unconfirmed, 'reason': None}
    left, right = (current_process or '').strip(), (baseline_process or '').strip()
    if not same_corner:
        library = 'different'
    elif same_project:
        library = 'same'
    elif left and right:
        library = 'same' if left == right else 'different'
    else:
        library = 'unknown'
    return {'same_project': same_project, 'same_corner': same_corner, 'area': area, 'power': power, 'library': library}


def _sides(view, group, key):
    baseline = total(view.baseline.rows, group.baseline_paths, key) if view.baseline is not None and group.baseline_paths else None
    current = total(view.current.rows, group.current_paths, key) if group.current_paths else None
    return baseline, current


def _sides_with_new_zero(view, group, key):
    baseline, current = _sides(view, group, key)
    if baseline is None and not group.baseline_paths and view.baseline is not None and current is not None:
        baseline = D(0)
    return baseline, current


def _change(baseline, current):
    if baseline is None or current is None:
        return None, None
    delta = current - baseline
    return delta, (delta / baseline * D(100) if baseline else None)


def _pct(value):
    return None if value is None else value.quantize(D('0.01'))


def _group_label(group) -> str:
    return ' + '.join(group.current_paths or group.baseline_paths) or group.id


def _root_sides(view, key) -> tuple[Decimal | None, Decimal | None]:
    roots = view.children.get(None, [])
    if not roots:
        return None, None
    b_vals, c_vals = [], []
    for pos in roots:
        b, c = _sides(view, view.groups[pos], key)
        if b is not None:
            b_vals.append(b)
        if c is not None:
            c_vals.append(c)
    return (sum(b_vals, D(0)) if b_vals else None), (sum(c_vals, D(0)) if c_vals else None)


def _root_reference(view, key) -> Decimal:
    """Symmetric positive root reference used for noise floor and root-share calculations."""
    target = AREA if key in ('logic_area', 'cts_area', 'others_area') else (
        'total_area' if key.endswith('_area') else (POWER_TOTAL if key.startswith('pwr_') else 'total_cells')
    )
    b_root, c_root = _root_sides(view, target)
    if target == AREA and not ((b_root and b_root > 0) or (c_root and c_root > 0)):
        b_root, c_root = _root_sides(view, 'total_area')
    positives = [v for v in (b_root, c_root) if v is not None and v > 0]
    if len(positives) == 2:
        return (positives[0] + positives[1]) / D(2)
    return positives[0] if positives else D(0)


def _noise_params(root_val: Decimal, noise_share_pct: Decimal, threshold_pct: Decimal, *, min_floor: Decimal = D(0)) -> tuple[Decimal, Decimal]:
    """Return (delta_0, epsilon) for scale-coupled change rate Δ / (B + ε)."""
    if noise_share_pct <= 0 or root_val <= 0:
        return D(0), D(0)
    s = noise_share_pct / D(100)
    delta_0 = max(min_floor, root_val * s)
    p = (threshold_pct if threshold_pct > 0 else D(10)) / D(100)
    return delta_0, delta_0 / p


def _damped_pct(baseline: Decimal | None, current: Decimal | None, eps: Decimal) -> tuple[Decimal | None, Decimal | None]:
    if baseline is None or current is None:
        return None, None
    delta = current - baseline
    denom = baseline + eps
    if denom <= 0:
        return delta, (D(100) if delta > 0 else (D(-100) if delta < 0 else D(0)))
    return delta, delta / denom * D(100)


def _max_area_size(view, group) -> Decimal | None:
    """Max of (total_area, logic_area) across both sides so SRAM macros (logic_area=0) are preserved."""
    vals = []
    for k in ('total_area', AREA):
        b, c = _sides(view, group, k)
        if b is not None:
            vals.append(b)
        if c is not None:
            vals.append(c)
    return max(vals) if vals else None


def _depth1_ancestor_map(view) -> dict[int, str | None]:
    """Map every group position to its Depth-1 sub-block display label (None for roots and Depth-1 itself)."""
    roots = set(view.children.get(None, []))
    depth1_ids: dict[str, str] = {}
    for r in roots:
        for d1 in view.children.get(view.groups[r].id, []):
            g = view.groups[d1]
            depth1_ids[g.id] = _group_label(g)
    result: dict[int, str | None] = {}
    for pos, group in enumerate(view.groups):
        if pos in roots or group.id in depth1_ids:
            result[pos] = None
            continue
        cur = view.parents.get(group.id)
        seen = set()
        found = None
        while cur is not None and cur not in seen:
            if cur in depth1_ids:
                found = depth1_ids[cur]
                break
            seen.add(cur)
            cur = view.parents.get(cur)
        result[pos] = found
    return result


def dominant_subblocks(view, context) -> dict[str, dict]:
    """Level-1 direct sub-block attribution breakdown (Section 5.1), independent of thresholds and roll-up."""
    roots = view.children.get(None, [])
    depth1 = [child for r in roots for child in view.children.get(view.groups[r].id, [])]
    out: dict[str, dict] = {}
    for key, part in ((AREA, 'area'), (POWER_TOTAL, 'power')):
        if not context[part]['analyzed']:
            out[part] = {'analyzed': False, 'reason': context[part]['reason'], 'items': []}
            continue
        b_root, c_root = _root_sides(view, key)
        root_delta, root_pct = _change(b_root, c_root)
        entry = {
            'analyzed': True,
            'metric': key,
            'root_baseline': b_root,
            'root_current': c_root,
            'root_delta': root_delta,
            'root_delta_pct': _pct(root_pct),
            'items': [],
            'rest_count': 0,
            'rest_delta': D(0),
            'rest_share_pct': None,
        }
        if root_delta is not None and root_delta > 0 and depth1:
            contribs = []
            for pos in depth1:
                g = view.groups[pos]
                if not g.current_paths:
                    continue
                b, c = _sides_with_new_zero(view, g, key)
                d, raw_p = _change(b, c)
                if d is not None and d > 0:
                    contribs.append((d, pos, g, b, c, raw_p))
            contribs.sort(key=lambda row: (-row[0], row[1]))
            top_rows, rest_rows = contribs[:5], contribs[5:]
            entry['items'] = [
                {
                    'path': _group_label(g),
                    'baseline': b,
                    'current': c,
                    'delta': d,
                    'delta_pct': _pct(raw_p),
                    'share_of_increase_pct': _pct(d / root_delta * D(100)),
                }
                for d, _pos, g, b, c, raw_p in top_rows
            ]
            if rest_rows:
                rest_delta = sum((row[0] for row in rest_rows), D(0))
                entry['rest_count'] = len(rest_rows)
                entry['rest_delta'] = rest_delta
                entry['rest_share_pct'] = _pct(rest_delta / root_delta * D(100))
        out[part] = entry
    return out


def select_items(view, thresholds: Thresholds, watched_paths, context) -> Selection:
    groups = view.groups
    index = view.index
    roots = set(view.children.get(None, []))
    depth1_map = _depth1_ancestor_map(view)
    attribution = dominant_subblocks(view, context)

    large = lambda position: (size := _max_area_size(view, groups[position])) is not None and size >= thresholds.min_area
    new_instances = {
        position for position, group in enumerate(groups)
        if group.current_paths and not group.baseline_paths and view.baseline is not None and large(position)
    }
    nested_new = {
        position for position in new_instances
        if (parent := view.parents.get(groups[position].id)) is not None and not groups[index[parent]].baseline_paths
    }

    increases: dict[str, dict[int, tuple[Decimal, Decimal]]] = {AREA: {}, POWER_TOTAL: {}}
    metric_specs = (
        (AREA, thresholds.area_increase_pct, 'area'),
        (POWER_TOTAL, thresholds.power_increase_pct, 'power'),
    )
    params_by_key: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for key, threshold, part in metric_specs:
        if not context[part]['analyzed']:
            continue
        root_ref = _root_reference(view, key)
        delta_0, eps = _noise_params(root_ref, thresholds.noise_share_pct, threshold)
        params_by_key[key] = (threshold, delta_0, eps)
        for position, group in enumerate(groups):
            if not group.current_paths:
                continue
            if group.baseline_paths and group.reason:
                continue
            if not group.baseline_paths and position in nested_new:
                continue
            if key == AREA and not large(position):
                continue
            b, c = _sides_with_new_zero(view, group, key)
            delta, damped = _damped_pct(b, c, eps)
            if delta is None or delta <= 0 or damped is None:
                continue
            if key == POWER_TOTAL and not large(position) and delta < delta_0:
                continue
            increases[key][position] = (delta, damped)

    absorbed: dict[str, dict[int, list[int]]] = {AREA: {}, POWER_TOTAL: {}}
    candidate_positions: dict[str, set[int]] = {AREA: set(), POWER_TOTAL: set()}
    distributed_parents: dict[str, set[int]] = {AREA: set(), POWER_TOTAL: set()}
    selected_by_key: dict[str, set[int]] = {AREA: set(), POWER_TOTAL: set()}
    ranking_tags_by_pos: dict[int, list[str]] = {}

    for key, threshold, part in metric_specs:
        if key not in params_by_key:
            continue
        _thr, delta_0, _eps = params_by_key[key]
        inc = increases[key]
        track1_damped_floor = threshold * D('0.5')
        is_cand = lambda pos: (inc[pos][0] >= delta_0 and inc[pos][1] >= track1_damped_floor) or inc[pos][1] >= threshold
        candidate_positions[key] = {pos for pos in inc if is_cand(pos)}

        for position, (delta, _damped) in inc.items():
            pos_children = []
            for child in view.children.get(groups[position].id, []):
                if child in inc:
                    pos_children.append((inc[child][0], child))
            if not pos_children:
                continue
            pos_children.sort(key=lambda row: (-row[0], row[1]))
            total_child_sum = sum((row[0] for row in pos_children), D(0))
            top2_sum = sum((row[0] for row in pos_children[:2]), D(0))

            if position in roots:
                qual_children = [row for row in pos_children if is_cand(row[1])]
                qual_sum = sum((row[0] for row in qual_children), D(0))
                if qual_children and qual_sum >= CONCENTRATED_ROLLUP_SHARE * delta:
                    absorbed[key][position] = [row[1] for row in qual_children]
            elif top2_sum >= CONCENTRATED_ROLLUP_SHARE * delta:
                if pos_children[0][0] >= CONCENTRATED_ROLLUP_SHARE * delta:
                    if is_cand(pos_children[0][1]):
                        absorbed[key][position] = [pos_children[0][1]]
                elif len(pos_children) >= 2 and (is_cand(pos_children[0][1]) or is_cand(pos_children[1][1])):
                    absorbed[key][position] = [c for _, c in pos_children[:2] if is_cand(c)]
            elif len(pos_children) >= 3 and total_child_sum >= CONCENTRATED_ROLLUP_SHARE * delta:
                distributed_parents[key].add(position)

        dist_ancestor: dict[int, int] = {}
        for position in inc:
            if position in distributed_parents[key]:
                continue
            cur = view.parents.get(groups[position].id)
            seen = set()
            while cur is not None and cur not in seen:
                p_pos = index[cur]
                if p_pos in distributed_parents[key] and p_pos not in absorbed[key]:
                    dist_ancestor[position] = p_pos
                    break
                seen.add(cur)
                cur = view.parents.get(cur)

        def pick_tracks():
            t1_pool = [
                (delta, damped, pos)
                for pos, (delta, damped) in inc.items()
                if pos not in absorbed[key]
                and pos not in dist_ancestor
                and delta >= delta_0
                and damped >= track1_damped_floor
            ]
            t1_pool.sort(key=lambda row: (-row[0], -row[1], row[2]))
            t1_win = [row[2] for row in t1_pool[:TOP_K_RANKING]]
            t1_set = set(t1_win)

            t2_pool = []
            for pos, (delta, damped) in inc.items():
                if pos in absorbed[key] or pos in t1_set or damped < threshold:
                    continue
                if pos in dist_ancestor:
                    anc_damped = inc[dist_ancestor[pos]][1]
                    if damped < max(threshold * D(2), anc_damped * D('1.5')):
                        continue
                t2_pool.append((damped, delta, pos))
            t2_pool.sort(key=lambda row: (-row[0], -row[1], row[2]))
            t2_win = [row[2] for row in t2_pool[:TOP_K_RANKING]]
            return t1_win, t2_win

        track1_winners, track2_winners = pick_tracks()
        chosen = set(track1_winners) | set(track2_winners)

        def has_chosen_descendant(pos: int, seen_nodes: set[int]) -> bool:
            for child in absorbed[key].get(pos, []):
                if child in seen_nodes:
                    continue
                seen_nodes.add(child)
                if child in chosen or has_chosen_descendant(child, seen_nodes):
                    return True
            return False

        orphaned = [
            pos for pos in list(absorbed[key])
            if pos not in roots and pos in candidate_positions[key] and not has_chosen_descendant(pos, set())
        ]
        if orphaned:
            for pos in orphaned:
                del absorbed[key][pos]
            track1_winners, track2_winners = pick_tracks()

        for pos in track1_winners:
            selected_by_key[key].add(pos)
            ranking_tags_by_pos.setdefault(pos, []).append(f'{part}_abs_top')
        for pos in track2_winners:
            selected_by_key[key].add(pos)
            ranking_tags_by_pos.setdefault(pos, []).append(f'{part}_damped_top')

    by_current = {path: position for position, group in enumerate(groups) for path in group.current_paths}
    by_baseline = {path: position for position, group in enumerate(groups) if not group.current_paths for path in group.baseline_paths}
    watched, removed, missing = set(), set(), []
    for path in dict.fromkeys(watched_paths):
        if path in by_current:
            watched.add(by_current[path])
        elif path in by_baseline:
            watched.add(by_baseline[path])
            removed.add(by_baseline[path])
        else:
            missing.append(path)

    names = {AREA: 'area_increase', POWER_TOTAL: 'power_increase'}
    part_names = {AREA: 'area', POWER_TOTAL: 'power'}
    items: dict[int, Item] = {}
    for position in view.order:
        reasons = [names[key] for key in (AREA, POWER_TOTAL) if position in selected_by_key[key]]
        if position in new_instances and position not in nested_new:
            if reasons or (not context['area']['analyzed'] and not context['power']['analyzed']):
                reasons.append('new_instance')
            else:
                for key in (AREA, POWER_TOTAL):
                    if key in params_by_key and position in increases[key] and increases[key][position][1] >= params_by_key[key][0]:
                        if position not in absorbed[key]:
                            reasons.append('new_instance')
                            break
        if reasons or position in watched:
            dist_list = [
                part_names[key] for key in (AREA, POWER_TOTAL)
                if position in distributed_parents[key] and position in selected_by_key[key]
            ]
            items[position] = Item(
                position,
                groups[position].id,
                list(dict.fromkeys(reasons)),
                position in watched,
                position in removed,
                ranking_tags=list(ranking_tags_by_pos.get(position, [])),
                distributed_metrics=dist_list,
                depth1_block=depth1_map.get(position),
            )

    def survivors(position, key, seen):
        for child in absorbed[key].get(position, []):
            if child in seen:
                continue
            seen.add(child)
            if child in items and names[key] in items[child].reasons:
                yield child
            else:
                yield from survivors(child, key, seen)

    rolled = set()
    for key in absorbed:
        for position in absorbed[key]:
            targets = list(survivors(position, key, set()))
            if not targets:
                continue
            if position in candidate_positions[key]:
                rolled.add(position)
                for target in targets:
                    if groups[position].id not in items[target].rolled_up:
                        items[target].rolled_up.append(groups[position].id)

    ordered = list(items.values())
    counts = {name: sum(name in item.reasons for item in ordered) for name in ('area_increase', 'power_increase', 'new_instance')}
    counts.update(
        area_abs_top=sum('area_abs_top' in item.ranking_tags for item in ordered),
        area_damped_top=sum('area_damped_top' in item.ranking_tags for item in ordered),
        power_abs_top=sum('power_abs_top' in item.ranking_tags for item in ordered),
        power_damped_top=sum('power_damped_top' in item.ranking_tags for item in ordered),
        watched=sum(item.watched for item in ordered),
        rolled_up=len(rolled - set(items)),
        total=len(ordered),
    )
    return Selection(ordered, missing, counts, len(ordered) > MAX_ITEMS, attribution=attribution)
