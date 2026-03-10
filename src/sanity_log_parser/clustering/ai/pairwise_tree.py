from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .weights import select_levels

_SLOT_SPLIT_RE = re.compile(r"\s+/\s+")
_GENERIC_PATH_TOKENS = {
    "AF",
    "ALIVE",
    "BLK",
    "CORE",
    "CPU",
    "DOWN",
    "INST",
    "ISO",
    "ISP",
    "MEM",
    "NORMAL",
    "RAM",
    "REG",
    "ROM",
    "RTL",
    "SECU",
    "SECURE",
    "SENSOR",
    "SHUT",
    "TOP",
    "U",
}


def compute_pairwise_tree_distance_matrix(
    rule_groups: list[dict[str, Any]],
    pairwise_tree: dict[str, object],
) -> Any:
    """Compute an NxN 0/1 distance matrix from a rule-level pairwise tree."""
    nodes = pairwise_tree["nodes"]
    n = len(rule_groups)
    distances = np.zeros((n, n), dtype=np.float32)
    if n < 2:
        return distances

    upper_i, upper_j = np.triu_indices(n, 1)
    store = _PairFeatureStore(rule_groups, pairwise_tree["features"])
    values = _eval_tree_values_for_pairs(nodes, store, upper_i, upper_j)
    distances[upper_i, upper_j] = values
    distances[upper_j, upper_i] = values
    return distances


def compute_adaptive_eps_distance_matrix(
    rule_groups: list[dict[str, Any]],
    base_distances: Any,
    adaptive_eps_tree: dict[str, object],
) -> Any:
    """Normalize a base distance matrix by pair-specific epsilon values."""
    nodes = adaptive_eps_tree["nodes"]
    n = len(rule_groups)
    normalized = np.zeros((n, n), dtype=np.float32)
    if n < 2:
        return normalized

    upper_i, upper_j = np.triu_indices(n, 1)
    store = _PairFeatureStore(rule_groups, adaptive_eps_tree["features"])
    eps_values = _eval_tree_values_for_pairs(nodes, store, upper_i, upper_j)
    base = np.asarray(base_distances, dtype=np.float32)
    normalized[upper_i, upper_j] = base[upper_i, upper_j] / eps_values
    normalized[upper_j, upper_i] = normalized[upper_i, upper_j]
    return normalized


def _build_feature_matrices(
    rule_groups: list[dict[str, Any]],
    features: tuple[dict[str, object], ...],
) -> list[Any]:
    path_texts = [_extract_primary_path(group["pattern"]) for group in rule_groups]
    normalized_docs = [_normalize_path_doc(path) for path in path_texts]
    segment_sequences = [_segment_token_sequence(path) for path in path_texts]
    return [
        _compute_feature_matrix(
            feature,
            path_texts=path_texts,
            normalized_docs=normalized_docs,
            segment_sequences=segment_sequences,
        )
        for feature in features
    ]


def _eval_tree_values_for_pairs(
    nodes: tuple[dict[str, object], ...],
    store: "_PairFeatureStore",
    pair_i: np.ndarray,
    pair_j: np.ndarray,
) -> np.ndarray:
    values = np.empty(pair_i.shape[0], dtype=np.float32)
    active = np.arange(pair_i.shape[0], dtype=np.intp)
    _assign_tree_values(nodes, 0, store, pair_i, pair_j, active, values)
    return values


def _assign_tree_values(
    nodes: tuple[dict[str, object], ...],
    node_index: int,
    store: "_PairFeatureStore",
    pair_i: np.ndarray,
    pair_j: np.ndarray,
    active: np.ndarray,
    out: np.ndarray,
) -> None:
    if active.size == 0:
        return

    node = nodes[node_index]
    value = node.get("value")
    if value is not None:
        out[active] = float(value)
        return

    feature_idx = int(node["feature"])
    threshold = float(node["threshold"])
    feature_values = store.get_pair_values(
        feature_idx,
        pair_i[active],
        pair_j[active],
    )
    left_mask = feature_values <= threshold
    right_mask = ~left_mask
    if np.any(left_mask):
        _assign_tree_values(
            nodes,
            int(node["left"]),
            store,
            pair_i,
            pair_j,
            active[left_mask],
            out,
        )
    if np.any(right_mask):
        _assign_tree_values(
            nodes,
            int(node["right"]),
            store,
            pair_i,
            pair_j,
            active[right_mask],
            out,
        )


class _PairFeatureStore:
    def __init__(
        self,
        rule_groups: list[dict[str, Any]],
        features: tuple[dict[str, object], ...],
    ) -> None:
        self.features = tuple(features)
        self.path_texts = [_extract_primary_path(group["pattern"]) for group in rule_groups]
        self.normalized_docs = [_normalize_path_doc(path) for path in self.path_texts]
        self.segment_sequences = [_segment_token_sequence(path) for path in self.path_texts]
        self.path_lengths = np.asarray(
            [len(sequence) for sequence in self.segment_sequences],
            dtype=np.float32,
        )
        self._full_matrix_cache: dict[int, np.ndarray] = {}
        self._selected_text_cache: dict[int, np.ndarray] = {}
        self._selected_token_cache: dict[int, list[frozenset[str]]] = {}

    def get_pair_values(
        self,
        feature_idx: int,
        left_idx: np.ndarray,
        right_idx: np.ndarray,
    ) -> np.ndarray:
        feature = self.features[feature_idx]
        kind = feature["kind"]

        if kind == "path_tfidf_char_wb":
            matrix = self._full_matrix_cache.get(feature_idx)
            if matrix is None:
                ngram_range = tuple(feature.get("ngram_range", (3, 6)))
                vectorizer = TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=ngram_range,
                )
                tfidf = vectorizer.fit_transform(self.normalized_docs)
                matrix = cosine_similarity(tfidf).astype(np.float32, copy=False)
                self._full_matrix_cache[feature_idx] = matrix
            return matrix[left_idx, right_idx]

        if kind == "suffix_similarity":
            max_shift = int(feature.get("max_shift", 3))
            decay = float(feature.get("decay", 0.65))
            return np.fromiter(
                (
                    _suffix_similarity(
                        self.segment_sequences[i],
                        self.segment_sequences[j],
                        max_shift=max_shift,
                        decay=decay,
                    )
                    for i, j in zip(left_idx, right_idx, strict=True)
                ),
                dtype=np.float32,
                count=left_idx.size,
            )

        if kind == "path_length_equal":
            return (self.path_lengths[left_idx] == self.path_lengths[right_idx]).astype(
                np.float32
            )
        if kind == "path_length_diff":
            return np.abs(self.path_lengths[left_idx] - self.path_lengths[right_idx]).astype(
                np.float32
            )

        selected = self._selected_text_cache.get(feature_idx)
        if selected is None:
            levels = tuple(feature.get("levels", ()))
            selected = np.asarray(
                [select_levels(path, list(levels)) for path in self.path_texts],
                dtype=object,
            )
            self._selected_text_cache[feature_idx] = selected

        if kind == "level_exact":
            left = selected[left_idx]
            right = selected[right_idx]
            return ((left == right) & (left != "")).astype(np.float32)

        if kind == "level_jaccard":
            token_sets = self._selected_token_cache.get(feature_idx)
            if token_sets is None:
                token_sets = [frozenset(text.split()) for text in selected.tolist()]
                self._selected_token_cache[feature_idx] = token_sets
            return np.fromiter(
                (
                    _set_jaccard_similarity(token_sets[i], token_sets[j])
                    for i, j in zip(left_idx, right_idx, strict=True)
                ),
                dtype=np.float32,
                count=left_idx.size,
            )

        msg = f"Unsupported pairwise feature kind: {kind}"
        raise ValueError(msg)


def _eval_tree(nodes: tuple[dict[str, object], ...], features: list[float]) -> bool:
    return bool(_eval_tree_value(nodes, features))


def _eval_tree_value(nodes: tuple[dict[str, object], ...], features: list[float]) -> float:
    index = 0
    while True:
        node = nodes[index]
        value = node.get("value")
        if value is not None:
            return float(value)

        feature_idx = int(node["feature"])
        threshold = float(node["threshold"])
        if features[feature_idx] <= threshold:
            index = int(node["left"])
        else:
            index = int(node["right"])


def _compute_feature_matrix(
    feature: dict[str, object],
    *,
    path_texts: list[str],
    normalized_docs: list[str],
    segment_sequences: list[list[tuple[str, ...]]],
) -> Any:
    kind = feature["kind"]
    if kind == "path_tfidf_char_wb":
        ngram_range = tuple(feature.get("ngram_range", (3, 6)))
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=ngram_range)
        matrix = vectorizer.fit_transform(normalized_docs)
        return cosine_similarity(matrix)

    n = len(path_texts)
    result = np.zeros((n, n), dtype=np.float32)

    if kind == "suffix_similarity":
        max_shift = int(feature.get("max_shift", 3))
        decay = float(feature.get("decay", 0.65))
        for i in range(n):
            result[i, i] = 1.0
            for j in range(i + 1, n):
                sim = _suffix_similarity(
                    segment_sequences[i],
                    segment_sequences[j],
                    max_shift=max_shift,
                    decay=decay,
                )
                result[i, j] = sim
                result[j, i] = sim
        return result

    levels = tuple(feature.get("levels", ()))
    selected = [select_levels(path, list(levels)) for path in path_texts]
    for i in range(n):
        result[i, i] = 1.0
        for j in range(i + 1, n):
            if kind == "level_jaccard":
                sim = _text_jaccard_similarity(selected[i], selected[j])
            elif kind == "level_exact":
                sim = 1.0 if selected[i] and selected[i] == selected[j] else 0.0
            elif kind == "path_length_equal":
                sim = float(len(segment_sequences[i]) == len(segment_sequences[j]))
            elif kind == "path_length_diff":
                sim = float(abs(len(segment_sequences[i]) - len(segment_sequences[j])))
            else:
                msg = f"Unsupported pairwise feature kind: {kind}"
                raise ValueError(msg)
            result[i, j] = sim
            result[j, i] = sim

    return result


@lru_cache(maxsize=262144)
def _extract_primary_path(pattern: str) -> str:
    return _SLOT_SPLIT_RE.split(pattern.strip())[0].strip("'\" ")


@lru_cache(maxsize=262144)
def _normalize_path_doc(path: str) -> str:
    return " / ".join(" ".join(tokens) for tokens in _segment_token_sequence(path))


@lru_cache(maxsize=262144)
def _segment_token_sequence(path: str) -> tuple[tuple[str, ...], ...]:
    sequence: list[tuple[str, ...]] = []
    for raw_segment in path.split("/"):
        normalized = raw_segment.strip("'\" ").upper()
        if not normalized:
            continue
        if "__MBIT_" in normalized:
            normalized = normalized.split("__MBIT_", 1)[0]
        normalized = re.sub(r"\d+", "*", normalized)
        normalized = re.sub(r"[^A-Z0-9*]+", "_", normalized).strip("_")

        tokens: list[str] = []
        for token in normalized.split("_"):
            cleaned = token.strip("*")
            if len(cleaned) <= 1 or cleaned in _GENERIC_PATH_TOKENS:
                continue
            tokens.append(cleaned)
        if tokens:
            sequence.append(tuple(tokens))
    return tuple(sequence)


def _suffix_similarity(
    left: list[tuple[str, ...]],
    right: list[tuple[str, ...]],
    *,
    max_shift: int,
    decay: float,
) -> float:
    best = 0.0
    for shift in range(-max_shift, max_shift + 1):
        score = 0.0
        weight = 0.0
        for offset in range(max(len(left), len(right))):
            left_index = len(left) - 1 - offset
            right_index = len(right) - 1 - (offset + shift)
            if (
                left_index < 0
                or right_index < 0
                or left_index >= len(left)
                or right_index >= len(right)
            ):
                continue
            current_weight = decay**offset
            score += current_weight * _token_jaccard_similarity(
                left[left_index],
                right[right_index],
            )
            weight += current_weight
        if weight:
            best = max(best, score / weight)
    return best


def _text_jaccard_similarity(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    return _set_jaccard_similarity(left_tokens, right_tokens)


def _token_jaccard_similarity(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> float:
    return _set_jaccard_similarity(set(left), set(right))


def _set_jaccard_similarity(left: frozenset[str] | set[str], right: frozenset[str] | set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)
