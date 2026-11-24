"""Candidate selection and comparison-engine verification for proposed 1:1 rules."""
from bisect import bisect_left
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from itertools import groupby
import re

from common.area_power.analysis import resolve_groups
from common.area_power.errors import DomainError
from common.area_power.models import CORRESPONDENCE_POLICY


class Tree:
    def __init__(self, items, metadata):
        self.metadata = metadata
        self.sep = metadata.hierarchy_separator
        self.rows = {row['path']: row for row in items}
        self.root_path = next(path for path, row in self.rows.items() if row['depth'] == 0)
        self.paths = sorted(self.rows)
        self.children = defaultdict(set)
        for path, row in self.rows.items():
            if row['parent_path'] is not None:
                self.children[row['parent_path']].add(self.leaf(path))

    def leaf(self, path):
        return path.rsplit(self.sep, 1)[-1]

    def subtree(self, path):
        yield path
        prefix = path + self.sep
        index = bisect_left(self.paths, prefix)
        while index < len(self.paths) and self.paths[index].startswith(prefix):
            yield self.paths[index]
            index += 1

    def features(self, path):
        name = self.leaf(path).lower()
        yield ('name', name)
        for token in re.findall(r'[^\W_]+', name):
            yield ('token', token)
        for pos in range(len(name) - 2):
            yield ('gram', name[pos:pos + 3])
        for child in self.children[path]:
            yield ('child', child)


def groups(before, current, rules):
    return resolve_groups(current, before, current.metadata, before.metadata, {'payload': {'rules': rules}},
                          correspondence=CORRESPONDENCE_POLICY)


def matched(groups_):
    return {(tuple(g.baseline_paths), tuple(g.current_paths)) for g in groups_ if not g.reason}


def numbers(path, tree):
    return re.findall(r'\d+', path[len(tree.root_path):])


def unmatched(before, current, rules):
    for rule in rules:
        if (any(p not in before.rows for p in rule['baseline_paths'])
                or any(p not in current.rows for p in rule['current_paths'])):
            raise DomainError('An existing mapping path is missing.', 'mapping_path_missing')
    initial = groups(before, current, rules)
    if any(g.reason in ('mapping_equal_precedence_conflict', 'mapping_parent_child_overlap', 'mapping_cardinality_invalid') for g in initial):
        raise DomainError('Resolve existing mapping conflicts first.', 'mapping_conflict')
    anchors = {g.baseline_paths[0]: g.current_paths[0] for g in initial if not g.reason and len(g.baseline_paths) == len(g.current_paths) == 1}
    protected = matched(initial)
    used_b = {p for b, _ in protected for p in b}
    used_c = {p for _, c in protected for p in c}
    # Added/removed and individual split/merge members belong to the user's rules.
    for rule in rules:
        used_b.update(rule['baseline_paths'])
        used_c.update(rule['current_paths'])
    free_b, free_c = before.rows.keys() - used_b, current.rows.keys() - used_c
    return protected, free_b, free_c, anchors


def rename_patterns(before, current, anchors):
    # Reuse a confirmed rename with the same numeric slots across other lanes.
    renames = defaultdict(set)
    for left, right in anchors.items():
        old, new = before.leaf(left).lower(), current.leaf(right).lower()
        if old != new and '#' not in old + new and re.findall(r'\d+', old) == re.findall(r'\d+', new):
            renames[re.sub(r'\d+', '#', old)].add(re.sub(r'\d+', '#', new))
    return renames


def build_index(current, free_c):
    index = defaultdict(list)
    for path in sorted(free_c):
        parent = current.rows[path]['parent_path']
        index['parent', parent].append(path)
        index['under', parent, current.leaf(path).lower()].append(path)
        digits = tuple(re.findall(r'\d+', current.leaf(path)))
        if digits:
            index['indices', parent, digits].append(path)
        for key in set(current.features(path)):
            index[key].append(path)

    return index


def candidates(before, current, paths, anchors, renames, index, covered_c):
    cases, held = [], []
    for path in paths:
        parent = before.rows[path]['parent_path']
        anchor = anchors.get(parent)
        name = before.leaf(path).lower()
        digits = re.findall(r'\d+', name)
        names = renames.get(re.sub(r'\d+', '#', name), ())
        predicted = None
        if len(names) == 1:
            slots = iter(digits)
            template = next(iter(names))
            if template.count('#') == len(digits):
                predicted = re.sub('#', lambda _: next(slots), template)
        keys = set(before.features(path))
        if predicted:
            keys.add(('name', predicted))
            keys.add(('under', anchor, predicted))
        if anchor is not None:
            keys.add(('parent', anchor))
            if digits:
                keys.add(('indices', anchor, tuple(digits)))
        votes = Counter()
        for key in keys:
            bucket = index.get(key, ())
            if len(bucket) <= 64:
                votes.update(bucket)
        old_indices = numbers(path, before)
        def rank(dest):
            same_parent = anchor is not None and current.rows[dest]['parent_path'] == anchor
            leaf = current.leaf(dest).lower()
            same_indices = digits == re.findall(r'\d+', leaf) if same_parent else old_indices == numbers(dest, current)
            return (same_indices, predicted == leaf, votes[dest] + 2 * same_parent)

        top = sorted(sorted(votes.keys() - covered_c), key=rank, reverse=True)
        if len(top) > 32 and rank(top[0]) == rank(top[32]):
            held.append({'baseline': path, 'alternatives': top[:3], 'reason': 'ambiguous'})
            continue
        options = []
        for dest in top[:32]:
            same_parent = anchor is not None and current.rows[dest]['parent_path'] == anchor
            left_name, right_name = before.leaf(path).lower(), current.leaf(dest).lower()
            similarity = SequenceMatcher(None, left_name, right_name, autojunk=False).ratio()
            left, right = before.children[path], current.children[dest]
            overlap = len(left & right) / len(left | right) if left or right else 0
            known_rename = predicted == right_name
            if not (similarity >= .65 or overlap >= .5 or known_rename):
                continue
            # A lane renumbering needs independent human evidence even when it looks similar.
            indices_match = digits == re.findall(r'\d+', right_name) if same_parent else old_indices == numbers(dest, current)
            score = similarity + .55 * same_parent + .85 * overlap + known_rename
            options.append({'path': dest, 'score': score, 'parent_match': same_parent, 'known_rename': known_rename,
                            'shared_children': sorted(left & right)[:6], 'indices_match': indices_match})
        options.sort(key=lambda o: (-o['score'], o['path']))
        eligible = [o for o in options if o['indices_match']]
        if not eligible or (len(eligible) > 1 and eligible[0]['score'] - eligible[1]['score'] < .12):
            alternatives = [o['path'] for o in (eligible or options)[:3]]
            evidence = [before.children[path] & current.children[dest] for dest in alternatives]
            possible_split = len(evidence) > 1 and len(set().union(*evidence)) > max(map(len, evidence))
            reason = 'possible_split' if possible_split else 'ambiguous' if options else 'unresolved'
            held.append({'baseline': path, 'alternatives': alternatives, 'reason': reason})
        else:
            cases.append({'baseline': path, 'options': eligible[:3]})
    # If two source instances favour the same destination, do not let batch order decide.
    demand = Counter(case['options'][0]['path'] for case in cases)
    unique = []
    for case in cases:
        if demand[case['options'][0]['path']] == 1:
            unique.append(case)
        else:
            held.append({'baseline': case['baseline'], 'alternatives': [o['path'] for o in case['options']], 'reason': 'possible_merge'})
    return unique, held


def suggest(before, current, rules, choose, *, max_depth: int | None = None):
    """Review parent candidates first; an approved subtree resolves its exact relative paths."""
    if max_depth is not None and (isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1):
        raise DomainError('Maximum mapping depth must be an integer >= 1.', 'invalid_mapping_depth')
    protected, free_b, free_c, anchors = unmatched(before, current, rules)
    candidate_c = free_c if max_depth is None else {p for p in free_c if current.rows[p]['depth'] <= max_depth}
    index = build_index(current, candidate_c)
    renames = rename_patterns(before, current, anchors)
    held = []
    covered_b, covered_c = set(), set()
    expected = set(protected)
    suggestions, additions = [], []
    paths = [p for p in sorted(free_b, key=lambda p: (before.rows[p]['depth'], p))
             if max_depth is None or before.rows[p]['depth'] <= max_depth]
    for _, layer in groupby(paths, key=lambda p: before.rows[p]['depth']):
        remaining, deferred = candidates(before, current, (p for p in layer if p not in covered_b),
                                          anchors, renames, index, covered_c)
        held.extend(deferred)
        decisions = []
        for start in range(0, len(remaining), 20):
            batch = remaining[start:start + 20]
            for case, (choice_index, reason) in zip(batch, choose(batch), strict=True):
                options = case['options']
                if choice_index is not None and any(abs(o['score'] - options[choice_index]['score']) < .12
                                                    for i, o in enumerate(options) if i != choice_index):
                    choice_index, reason = None, 'ambiguous'
                if choice_index is None:
                    held.append({'baseline': case['baseline'], 'alternatives': [o['path'] for o in options], 'reason': reason})
                else:
                    decisions.append((case['baseline'], options[choice_index]['path'], reason))
        demand = Counter(dest for _, dest, _ in decisions)
        for path, dest, reason in decisions:
            if demand[dest] > 1 or dest in covered_c:
                held.append({'baseline': path, 'alternatives': [dest], 'reason': 'ambiguous'})
                continue
            pairs = [(path, dest)]
            if before.children[path] and current.children[dest]:
                relatives = [(p, dest + p[len(path):].replace(before.sep, current.sep)) for p in before.subtree(path)]
                destinations = {c for _, c in relatives}
                if (destinations == set(current.subtree(dest)) and len(destinations) == len(relatives)
                        and all(b in free_b and c in free_c and c not in covered_c
                                and (c == dest or not demand[c]) for b, c in relatives)):
                    pairs = relatives
            scope = 'subtree' if len(pairs) > 1 else 'node'
            left = re.sub(r'\d+', '#', before.leaf(path))
            right = re.sub(r'\d+', '#', current.leaf(dest))
            suggestions.append({'baseline': path, 'current': dest, 'scope': scope, 'gain': len(pairs),
                                'reason': reason, 'pattern': f'{left} → {right}'})
            additions.append({'id': f'proposal-{len(additions)}', 'kind': 'one_to_one', 'scope': scope,
                              'baseline_paths': [path], 'current_paths': [dest]})
            anchors.update(pairs)
            expected.update(((b,), (c,)) for b, c in pairs)
            covered_b.update(b for b, _ in pairs)
            covered_c.update(c for _, c in pairs)
    if matched(groups(before, current, rules + additions)) != expected:
        raise DomainError('Proposed rules conflict with existing correspondence.', 'mapping_preview_conflict')
    return {'suggestions': suggestions, 'held': [h for h in held if h['baseline'] not in covered_b],
            'baseline_unmatched': len(free_b), 'current_unmatched': len(free_c),
            'max_depth': max_depth, 'scope_baseline_unmatched': len(paths), 'scope_current_unmatched': len(candidate_c),
            'max_tree_depth': max((r['depth'] for r in before.rows.values()), default=0)}
