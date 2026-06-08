from __future__ import annotations

import numpy as np

from sanity_log_parser.clustering.ai.methods import fit_precomputed_distance_clusters


def test_agglomerative_complete_blocks_bridge_merge() -> None:
    distance_matrix = np.asarray(
        [
            [0.00, 0.10, 0.45, 0.80],
            [0.10, 0.00, 0.18, 0.75],
            [0.45, 0.18, 0.00, 0.70],
            [0.80, 0.75, 0.70, 0.00],
        ],
        dtype=np.float32,
    )

    labels = fit_precomputed_distance_clusters(
        distance_matrix,
        threshold=0.20,
        method="agglomerative_complete",
    )

    assert labels[0] == labels[1]
    assert labels[1] != labels[2]
    assert len(set(int(label) for label in labels)) == 3


def test_dbscan_keeps_single_link_bridge_behavior() -> None:
    distance_matrix = np.asarray(
        [
            [0.00, 0.10, 0.45],
            [0.10, 0.00, 0.18],
            [0.45, 0.18, 0.00],
        ],
        dtype=np.float32,
    )

    labels = fit_precomputed_distance_clusters(
        distance_matrix,
        threshold=0.20,
        method="dbscan",
    )

    assert len(set(int(label) for label in labels)) == 1
