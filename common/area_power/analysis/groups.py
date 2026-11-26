"""Hierarchy correspondence groups, scope selection, and display tree layout."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from hashlib import sha256
import json
from typing import Any

from common.area_power.csv_input import (
    CELL_GROUPS as CATEGORIES,
    DRIVE_NAMES as DRIVE,
    LEGACY_DRIVE_NAMES as LEGACY_DRIVE,
    POWER_FIELDS as POWER,
    SUB1X_DRIVE_NAMES as SUB1X_DRIVES,
    VTH_NAMES as VT,
    parse_csv,
)
from common.area_power.layered_csv import (
    is_power_layer_upload,
    parse_power_only_against_base,
    read_layered_observations,
)
from common.area_power.db import Database
from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest
from common.area_power.models import CORRESPONDENCE_POLICY, UploadMetadata

D = Decimal
DRIVE_GROUP_NAMES = ('drv_sub1x', 'drv_d1_d3', 'drv_d4_plus')
DRIVE_GROUP_MEMBERS = {
    'drv_sub1x_pct': ('drv_D0P5_pct', 'drv_D0P7_pct'),
    'drv_d1_d3_pct': ('drv_D1_pct', 'drv_D2_pct', 'drv_D3_pct'),
    'drv_d4_plus_pct': ('drv_D4_pct', 'drv_D5_pct', 'drv_D6_pct', 'drv_D7_pct', 'drv_D8_pct', 'drv_D10_pct', 'drv_D12_pct', 'drv_D14_pct', 'drv_D16_pct'),
}
DRIVE_GROUP_METRICS = tuple(f'{name}_pct' for name in DRIVE_GROUP_NAMES)
STRUCTURAL = tuple(f'{c}_{s}' for c in CATEGORIES for s in ('cells', 'area')) + ('cts_area_pct',) + tuple(f'{c}_{s}' for c in VT + DRIVE for s in ('cnt', 'pct'))
QUALITY = ('vth_unclassified_cnt', 'drive_unclassified_cnt')
DISTRIBUTIONS = ('vth_distribution_change', 'drive_distribution_change')
METRICS = STRUCTURAL + DRIVE_GROUP_METRICS + QUALITY + DISTRIBUTIONS + POWER
SCOPE_FIELDS = ('project_id', 'version_id', 'block_id', 'snapshot_id', 'condition_key')


def scope_fields(upload: dict) -> dict:
    return {key: upload[key] for key in SCOPE_FIELDS}


def all_records(db, conn, record_type: str, **filters) -> list[dict]:
    return db.all_records(conn, record_type, **filters)


def existing_dedupe(db, conn, kind: str, key: bytes, *, lock=False):
    return db.raw.existing_dedupe(conn, kind, key, lock=lock)


def advisory_lock(conn, key: bytes) -> None:
    Database.advisory_lock(conn, key)


def parse_upload(upload: dict, settings, *, observations=False, base_upload: dict | None = None):
    if upload['state'] != 'ready':
        raise DomainError('Selected upload is not ready', code='upload_not_ready')
    metadata = UploadMetadata.model_validate(upload['input_contract']['metadata'])
    if observations:
        return read_layered_observations(upload, metadata, settings.max_uncompressed_bytes, base_upload=base_upload), metadata
    if is_power_layer_upload(upload):
        if base_upload is None:
            raise DomainError('Area base upload is required to read a power-layer upload.', code='stored_upload_mismatch')
        base_meta = UploadMetadata.model_validate(base_upload['input_contract']['metadata'])
        parsed = parse_power_only_against_base(
            bytes(upload['csv_gzip']), upload['original_filename'], metadata,
            base_upload, base_meta, settings.max_uncompressed_bytes,
            raw_original_sha256=(upload.get('input_contract') or {}).get('raw_original_sha256'),
        )
    else:
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
        if self.target_id is None and all(isinstance(path, str) for path in self.baseline_paths + self.current_paths):
            encode = lambda paths: json.dumps(sorted(paths), ensure_ascii=False, separators=(',', ':'))
            return sha256(('{"baseline":%s,"current":%s,"target":null}' % (encode(self.baseline_paths), encode(self.current_paths))).encode()).hexdigest()
        return digest({'baseline': sorted(self.baseline_paths), 'current': sorted(self.current_paths), 'target': self.target_id}).hex()


def resolve_groups(current, baseline, cm, bm, mapping: dict | None, mapping_reason: str | None = None,
                   correspondence: str | None = None) -> list[Group]:
    cr, br = current.rows, baseline.rows if baseline else {}
    same = bm is not None and cm.project_id == bm.project_id
    cs, bs = cm.hierarchy_separator, bm.hierarchy_separator if bm else cm.hierarchy_separator
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
    families = {'area': tuple(m for m in STRUCTURAL if '_area' in m), 'count': tuple(m for m in STRUCTURAL if m.endswith(('_cnt', '_cells'))), 'vth': tuple(m for m in STRUCTURAL + QUALITY + DISTRIBUTIONS if m.startswith(('vt_', 'vth_'))), 'drive': tuple(m for m in DRIVE_GROUP_METRICS + STRUCTURAL + QUALITY + DISTRIBUTIONS if m.startswith(('drv_', 'drive_'))), 'power': POWER}
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


def group_tree(groups: list[Group], cs: str, bs: str) -> dict[str, str | None]:
    """Display parent of each group: the nearest ancestor path owned by another group."""
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
