from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Any

VALID_CLUSTERING_METHODS = {"dbscan", "agglomerative_complete"}
DEFAULT_CLUSTERING_METHOD = "dbscan"


@lru_cache(maxsize=1)
def get_dbscan_factory() -> Any | None:
    try:
        return import_module("sklearn.cluster").DBSCAN
    except ImportError:
        return None


@lru_cache(maxsize=1)
def get_agglomerative_factory() -> Any | None:
    try:
        return import_module("sklearn.cluster").AgglomerativeClustering
    except ImportError:
        return None


def fit_precomputed_distance_clusters(
    distance_matrix: Any,
    *,
    threshold: float,
    method: str = DEFAULT_CLUSTERING_METHOD,
    dbscan_factory: Any | None = None,
    agglomerative_factory: Any | None = None,
) -> Any:
    """Cluster an NxN precomputed distance matrix."""
    import numpy as np

    matrix = np.asarray(distance_matrix, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        msg = "distance_matrix must be a square matrix"
        raise ValueError(msg)

    n = matrix.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.int32)
    if n == 1:
        return np.zeros(1, dtype=np.int32)

    if method == "dbscan":
        factory = dbscan_factory or get_dbscan_factory()
        if factory is None:
            msg = "scikit-learn DBSCAN is required for dbscan clustering"
            raise RuntimeError(msg)
        return factory(
            eps=float(threshold),
            min_samples=1,
            metric="precomputed",
        ).fit(matrix).labels_

    if method == "agglomerative_complete":
        factory = agglomerative_factory or get_agglomerative_factory()
        if factory is None:
            msg = (
                "scikit-learn AgglomerativeClustering is required for "
                "agglomerative_complete clustering"
            )
            raise RuntimeError(msg)
        model = _build_complete_link_model(factory, threshold=float(threshold))
        return model.fit_predict(matrix)

    msg = f"Unsupported clustering method: {method}"
    raise ValueError(msg)


def _build_complete_link_model(factory: Any, *, threshold: float) -> Any:
    kwargs = {
        "n_clusters": None,
        "distance_threshold": threshold,
        "linkage": "complete",
        "compute_full_tree": True,
    }
    try:
        return factory(metric="precomputed", **kwargs)
    except TypeError:
        return factory(affinity="precomputed", **kwargs)
