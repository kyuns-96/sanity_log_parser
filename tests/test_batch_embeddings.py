"""Tests for batch-then-slice embedding optimization."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sanity_log_parser.clustering.ai.clusterer import (
    AIClusterer,
    _EMBED_BATCH_SIZE,
)
from sanity_log_parser.gca.config import GcaConfig, GcaRuleConfig, VariableConfig


def _make_logic_group(
    rule_id: str,
    template: str,
    pattern: str,
    count: int = 1,
) -> dict:
    return {
        "rule_id": rule_id,
        "template": template,
        "pattern": pattern,
        "count": count,
        "members": [{"raw_log": f"log for {template}"}],
    }


def _fake_embed(texts: list[str]) -> np.ndarray:
    """Return deterministic embeddings based on text hash — 4-dim vectors."""
    rng = np.random.default_rng(42)
    embs = []
    for t in texts:
        seed = hash(t) % (2**31)
        embs.append(rng.standard_normal(4) + seed * 0.001)
    result = np.array(embs)
    norms = np.linalg.norm(result, axis=1, keepdims=True)
    return result / norms


def _make_clusterer(
    gca_config: GcaConfig | None = None,
) -> AIClusterer:
    """Build an AIClusterer with mocked internals so no real model loads."""
    with patch(
        "sanity_log_parser.clustering.ai.clusterer.load_embeddings_config"
    ) as mock_cfg:
        mock_cfg.return_value = MagicMock(backend="none", openai_compatible=None)
        clusterer = AIClusterer(gca_config=gca_config)
    # Force AI available so run() doesn't bail out
    clusterer.ai_available = True
    return clusterer


class TestBatchWeighted:
    """Tests for batch-then-slice in _run_weighted()."""

    def test_batch_single_rule_two_groups(self) -> None:
        """1 rule, 2 groups: verify output matches and slicing works."""
        gca = GcaConfig(
            default_eps=0.5,
            default_template_weight=0.3,
            default_variable_weight=0.7,
        )
        clusterer = _make_clusterer(gca_config=gca)
        clusterer._compute_embeddings = MagicMock(side_effect=_fake_embed)

        groups = [
            _make_logic_group("R1", "tmpl_a", "'var1' rest", count=5),
            _make_logic_group("R1", "tmpl_b", "'var2' rest", count=3),
        ]

        result = clusterer.run(groups)

        assert len(result) > 0
        assert all(g["rule_id"] == "R1" for g in result)
        total = sum(g["total_count"] for g in result)
        assert total == 8

    def test_batch_multi_rule(self) -> None:
        """3 rules with different variable counts: each gets correct slices."""
        gca = GcaConfig(
            default_eps=0.5,
            default_template_weight=0.3,
            default_variable_weight=0.7,
            rules={
                "R1": GcaRuleConfig(
                    eps=0.5,
                    template_weight=0.3,
                    variables={0: VariableConfig(weight=0.7)},
                ),
            },
        )
        clusterer = _make_clusterer(gca_config=gca)
        clusterer._compute_embeddings = MagicMock(side_effect=_fake_embed)

        groups = [
            _make_logic_group("R1", "t1", "'v1a' rest", count=5),
            _make_logic_group("R1", "t2", "'v1b' rest", count=3),
            _make_logic_group("R2", "t3", "'v2a' rest", count=4),
            _make_logic_group("R2", "t4", "'v2b' rest", count=2),
            _make_logic_group("R3", "t5", "'v3a/x' stuff", count=1),
            _make_logic_group("R3", "t6", "'v3b/y' stuff", count=1),
        ]

        result = clusterer.run(groups)

        rule_ids = {g["rule_id"] for g in result}
        assert rule_ids == {"R1", "R2", "R3"}
        total = sum(g["total_count"] for g in result)
        assert total == 16

    def test_batch_embed_call_count(self) -> None:
        """Verify _compute_embeddings is called ceil(N/512) times, not R*(1+V)."""
        gca = GcaConfig(
            default_eps=0.5,
            default_template_weight=0.3,
            default_variable_weight=0.7,
        )
        clusterer = _make_clusterer(gca_config=gca)
        clusterer._compute_embeddings = MagicMock(side_effect=_fake_embed)

        # 3 rules x 2 groups, each with 1 variable → old code would make
        # 3*(1+1) = 6 calls. New code collects into 1 batch of 12 texts → 1 call.
        groups = []
        for i in range(3):
            groups.append(
                _make_logic_group(f"R{i}", f"t{i}a", f"'v{i}a' rest", count=2)
            )
            groups.append(
                _make_logic_group(f"R{i}", f"t{i}b", f"'v{i}b' rest", count=1)
            )

        clusterer.run(groups)

        # All 12 texts fit in one batch (< 512)
        call_count = clusterer._compute_embeddings.call_count
        assert call_count == 1, f"Expected 1 embed call, got {call_count}"

    def test_batch_embed_failure_falls_back(self) -> None:
        """When _compute_embeddings returns None, all groups returned unclustered."""
        gca = GcaConfig(default_eps=0.5)
        clusterer = _make_clusterer(gca_config=gca)
        clusterer._compute_embeddings = MagicMock(return_value=None)

        groups = [
            _make_logic_group("R1", "t1", "'v1' rest", count=5),
            _make_logic_group("R1", "t2", "'v2' rest", count=3),
            _make_logic_group("R2", "t3", "'v3' rest", count=2),
            _make_logic_group("R2", "t4", "'v4' rest", count=1),
        ]

        result = clusterer.run(groups)

        # All groups should come back unclustered (merged_variants_count == 1)
        assert len(result) == 4
        assert all(g["merged_variants_count"] == 1 for g in result)

    def test_batch_empty_prepared(self) -> None:
        """No rules with >= 2 groups: embed never called, single groups returned."""
        gca = GcaConfig(default_eps=0.5)
        clusterer = _make_clusterer(gca_config=gca)
        clusterer._compute_embeddings = MagicMock(side_effect=_fake_embed)

        groups = [
            _make_logic_group("R1", "t1", "'v1' rest", count=5),
            _make_logic_group("R2", "t2", "'v2' rest", count=3),
        ]

        result = clusterer.run(groups)

        assert len(result) == 2
        assert all(g["merged_variants_count"] == 1 for g in result)
        clusterer._compute_embeddings.assert_not_called()


class TestBatchTemplateOnly:
    """Tests for batch-then-slice in _run_template_only()."""

    def test_batch_template_only_batched(self) -> None:
        """_run_template_only() makes ceil(N/512) embed calls, not R calls."""
        clusterer = _make_clusterer(gca_config=None)
        clusterer._compute_embeddings = MagicMock(side_effect=_fake_embed)

        # 5 rules x 2 groups → old code = 5 calls, new code = 1 call (10 texts < 512)
        groups = []
        for i in range(5):
            groups.append(
                _make_logic_group(f"R{i}", f"tmpl_{i}_a", f"'v{i}a' rest", count=2)
            )
            groups.append(
                _make_logic_group(f"R{i}", f"tmpl_{i}_b", f"'v{i}b' rest", count=1)
            )

        result = clusterer.run(groups)

        call_count = clusterer._compute_embeddings.call_count
        assert call_count == 1, f"Expected 1 embed call, got {call_count}"
        total = sum(g["total_count"] for g in result)
        assert total == 15


class TestBatchChunking:
    """Test that large batches get split at _EMBED_BATCH_SIZE boundary."""

    def test_batch_large_batch_chunked(self) -> None:
        """600 texts with batch_size=512: verify 2 embed calls, results concatenated."""
        clusterer = _make_clusterer(gca_config=None)
        clusterer._compute_embeddings = MagicMock(side_effect=_fake_embed)

        # Create 300 rules x 2 groups = 600 template texts
        groups = []
        for i in range(300):
            groups.append(
                _make_logic_group(f"R{i}", f"tmpl_{i}_a", f"'v{i}a' rest", count=2)
            )
            groups.append(
                _make_logic_group(f"R{i}", f"tmpl_{i}_b", f"'v{i}b' rest", count=1)
            )

        result = clusterer.run(groups)

        expected_calls = math.ceil(600 / _EMBED_BATCH_SIZE)
        call_count = clusterer._compute_embeddings.call_count
        assert call_count == expected_calls, (
            f"Expected {expected_calls} embed calls for 600 texts, got {call_count}"
        )
        total = sum(g["total_count"] for g in result)
        assert total == 300 * 3
