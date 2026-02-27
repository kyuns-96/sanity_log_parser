from __future__ import annotations

import logging
import time
from importlib import import_module
from typing import Any, Protocol, cast
from collections import defaultdict

from sanity_log_parser.config.embeddings import load_embeddings_config
from sanity_log_parser.gca.config import (
    GcaConfig,
    GcaRuleConfig,
    VariableConfig,
    get_gca_rule_config,
)
from sanity_log_parser.patterns import VAR_PATTERN
from .weights import select_levels
from sanity_log_parser.embeddings.openai_compat import (
    EmbeddingsRequestError,
    OpenAICompatibleEmbeddingsClient,
)

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 512

SentenceTransformerFactory: Any | None = None
DBSCANFactory: Any | None = None
sentence_transformers_available = False
dbscan_available = False
try:
    SentenceTransformerFactory = import_module(
        "sentence_transformers"
    ).SentenceTransformer
except ImportError:
    pass
else:
    sentence_transformers_available = True

try:
    DBSCANFactory = import_module("sklearn.cluster").DBSCAN
except ImportError:
    pass
else:
    dbscan_available = True


class AIClusterer:
    def __init__(
        self,
        model_path: str = "all-MiniLM-L6-v2",
        embeddings_config_file: str = "config.json",
        gca_config: GcaConfig | None = None,
        embed_batch_size: int = _EMBED_BATCH_SIZE,
    ) -> None:
        self.model: _SentenceModelLike | None = None
        self.remote_embeddings_client: OpenAICompatibleEmbeddingsClient | None = None
        self.ai_available: bool = False
        self.gca_config = gca_config
        self.embed_batch_size = embed_batch_size

        embeddings_config = load_embeddings_config(
            config_path=embeddings_config_file,
            warn=lambda msg: logger.warning("%s", msg),
        )

        if embeddings_config.backend == "openai_compatible":
            if not dbscan_available:
                logger.warning(
                    "OpenAI-compatible embeddings selected, but scikit-learn is unavailable."
                )
                self.ai_available = False
            elif embeddings_config.openai_compatible is not None:
                openai_settings = embeddings_config.openai_compatible
                self.remote_embeddings_client = OpenAICompatibleEmbeddingsClient(
                    base_url=openai_settings.base_url,
                    model=openai_settings.model,
                    api_key=openai_settings.api_key,
                )
                self.ai_available = True
        elif (
            sentence_transformers_available
            and dbscan_available
            and SentenceTransformerFactory is not None
        ):
            try:
                self.model = cast(
                    _SentenceModelLike, SentenceTransformerFactory(model_path)
                )
                self.ai_available = True
            except (ImportError, OSError, RuntimeError) as exc:
                logger.warning("Failed to load SentenceTransformer model: %s", exc)
                self.ai_available = False

    def run(self, logic_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.ai_available or not logic_groups or DBSCANFactory is None:
            return []

        logger.info("AI Clustering: analyzing %d logic groups...", len(logic_groups))

        groups_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for logic_group in logic_groups:
            groups_by_rule[logic_group["rule_id"]].append(logic_group)

        logger.info("Grouping by rule_id: %d different rules", len(groups_by_rule))

        if self.gca_config is not None:
            return self._run_weighted(groups_by_rule)
        return self._run_template_only(groups_by_rule)

    def _run_template_only(
        self,
        groups_by_rule: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Template-only clustering path (no GCA config)."""
        final_output: list[dict[str, Any]] = []
        group_counter = 0

        # Phase 1: Handle single groups, collect multi-group rules
        multi_rules: dict[str, list[dict[str, Any]]] = {}
        for rule_id, rule_groups in groups_by_rule.items():
            if len(rule_groups) < 2:
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
                continue
            multi_rules[rule_id] = rule_groups

        if not multi_rules:
            final_output.sort(key=lambda g: g["total_count"], reverse=True)
            return final_output

        # Phase 2: Collect all templates into one flat batch
        batch_texts: list[str] = []
        template_index: dict[str, tuple[int, int]] = {}
        for rule_id, rule_groups in multi_rules.items():
            t_start = len(batch_texts)
            batch_texts.extend(lg["template"] for lg in rule_groups)
            template_index[rule_id] = (t_start, len(batch_texts))

        # Phase 3: Batch embed, then slice and cluster
        all_embs = self._compute_embeddings_batched(batch_texts)
        if all_embs is None:
            logger.warning(
                "Embeddings failed for %d rules; keeping all groups unclustered.",
                len(multi_rules),
            )
            for rule_id, rule_groups in multi_rules.items():
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
            final_output.sort(key=lambda g: g["total_count"], reverse=True)
            return final_output

        cluster_t0 = time.perf_counter()
        for rule_id, rule_groups in multi_rules.items():
            t_start, t_end = template_index[rule_id]
            embeddings = all_embs[t_start:t_end]

            dbscan_t0 = time.perf_counter()
            clustering = DBSCANFactory(
                eps=0.2,
                min_samples=1,
                metric="cosine",
            ).fit(embeddings)
            logger.info(
                "[timing] DBSCAN for '%s' (%d groups): %.3fs",
                rule_id,
                len(rule_groups),
                time.perf_counter() - dbscan_t0,
            )

            new_groups, group_counter = self._build_cluster_results(
                rule_id,
                clustering.labels_,
                rule_groups,
                group_counter,
            )
            final_output.extend(new_groups)

        logger.info(
            "[timing] clustering (all rules): %.3fs",
            time.perf_counter() - cluster_t0,
        )
        final_output.sort(key=lambda g: g["total_count"], reverse=True)
        return final_output

    def _run_weighted(
        self,
        groups_by_rule: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Multi-embedding weighted distance clustering path (GCA config present)."""
        assert self.gca_config is not None
        final_output: list[dict[str, Any]] = []
        group_counter = 0

        # Phase 1: Handle single groups, prepare components for multi-group rules
        prep_t0 = time.perf_counter()
        prepared: dict[str, tuple[GcaRuleConfig, list[dict[str, Any]]]] = {}
        for rule_id, rule_groups in groups_by_rule.items():
            if len(rule_groups) < 2:
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
                continue
            rule_config = get_gca_rule_config(self.gca_config, rule_id)
            components = _prepare_embedding_components(
                rule_groups, rule_config, self.gca_config.default_variable_weight
            )
            prepared[rule_id] = (rule_config, components)
        logger.info(
            "[timing] prepare components (%d rules): %.3fs",
            len(prepared),
            time.perf_counter() - prep_t0,
        )

        if not prepared:
            final_output.sort(key=lambda g: g["total_count"], reverse=True)
            return final_output

        # Phase 2: Collect all texts into one flat batch with index tracking
        batch_texts: list[str] = []
        template_index: dict[str, tuple[int, int]] = {}
        var_index: dict[str, list[tuple[int, int, list[bool]]]] = {}

        for rule_id, (rule_config, components) in prepared.items():
            # Templates
            t_start = len(batch_texts)
            batch_texts.extend(c["template"] for c in components)
            template_index[rule_id] = (t_start, len(batch_texts))

            # Variables per position
            max_vars = max(len(c["variables"]) for c in components)
            var_index[rule_id] = []
            for i in range(max_vars):
                v_start = len(batch_texts)
                mask: list[bool] = []
                for c in components:
                    if i < len(c["variables"]) and c["variables"][i].strip():
                        batch_texts.append(c["variables"][i])
                        mask.append(True)
                    else:
                        batch_texts.append("_")
                        mask.append(False)
                var_index[rule_id].append((v_start, len(batch_texts), mask))

        # Phase 3: Batch embed, then slice and cluster
        all_embs = self._compute_embeddings_batched(batch_texts)
        if all_embs is None:
            logger.warning(
                "Embeddings failed for %d rules; keeping all groups unclustered.",
                len(prepared),
            )
            for rule_id in prepared:
                for lg in groups_by_rule[rule_id]:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
            final_output.sort(key=lambda g: g["total_count"], reverse=True)
            return final_output

        cluster_t0 = time.perf_counter()
        for rule_id in prepared:
            rule_config, components = prepared[rule_id]
            n = len(components)

            t_start, t_end = template_index[rule_id]
            template_embs = all_embs[t_start:t_end]

            var_embeddings: list[tuple[Any, list[bool]]] = []
            for v_start, v_end, mask in var_index[rule_id]:
                var_embeddings.append((all_embs[v_start:v_end], mask))

            dm_t0 = time.perf_counter()
            distance_matrix = _compute_distance_matrix(
                n,
                template_embs,
                var_embeddings,
                rule_config,
                self.gca_config.default_variable_weight,
            )
            logger.info(
                "[timing] distance matrix for '%s' (%d groups): %.3fs",
                rule_id,
                n,
                time.perf_counter() - dm_t0,
            )

            dbscan_t0 = time.perf_counter()
            clustering = DBSCANFactory(
                eps=rule_config.eps,
                min_samples=1,
                metric="precomputed",
            ).fit(distance_matrix)
            logger.info(
                "[timing] DBSCAN for '%s': %.3fs",
                rule_id,
                time.perf_counter() - dbscan_t0,
            )

            new_groups, group_counter = self._build_cluster_results(
                rule_id,
                clustering.labels_,
                groups_by_rule[rule_id],
                group_counter,
            )
            final_output.extend(new_groups)

        logger.info(
            "[timing] clustering (all rules): %.3fs",
            time.perf_counter() - cluster_t0,
        )
        final_output.sort(key=lambda g: g["total_count"], reverse=True)
        return final_output

    def _build_single_group(
        self,
        rule_id: str,
        logic_group: dict[str, Any],
        counter: int,
    ) -> dict[str, Any]:
        return {
            "type": "AISuperGroup",
            "super_group_id": f"{rule_id}_SG_{counter}",
            "rule_id": rule_id,
            "representative_template": logic_group["template"],
            "representative_pattern": logic_group["pattern"],
            "total_count": logic_group["count"],
            "merged_variants_count": 1,
            "original_logs": [m["raw_log"] for m in logic_group["members"]],
        }

    def _compute_embeddings(self, inputs: list[str]) -> Any | None:
        if self.remote_embeddings_client is not None:
            try:
                return self.remote_embeddings_client.embed(inputs)
            except EmbeddingsRequestError as exc:
                logger.warning(
                    "Remote embeddings failed, disabling AI clustering: %s", exc
                )
                self.ai_available = False
                return None

        if self.model is None:
            return None
        return self.model.encode(inputs, batch_size=128, show_progress_bar=False)

    def _compute_embeddings_batched(self, texts: list[str]) -> Any | None:
        """Embed texts in bounded chunks, concatenate into one ndarray."""
        import numpy as np

        if not texts:
            return np.empty((0, 0))

        batch_size = self.embed_batch_size
        t0 = time.perf_counter()
        n_chunks = 0
        chunks: list[Any] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            chunk_t0 = time.perf_counter()
            result = self._compute_embeddings(chunk)
            logger.info(
                "[timing] embed chunk %d/%d (%d texts): %.3fs",
                start // batch_size + 1,
                -(-len(texts) // batch_size),  # ceil division
                len(chunk),
                time.perf_counter() - chunk_t0,
            )
            if result is None:
                return None
            chunks.append(np.asarray(result))
            n_chunks += 1

        result = np.vstack(chunks)
        logger.info(
            "[timing] embeddings total: %d texts in %d chunks, %.3fs",
            len(texts),
            n_chunks,
            time.perf_counter() - t0,
        )
        return result

    def _build_cluster_results(
        self,
        rule_id: str,
        labels: Any,
        rule_groups: list[dict[str, Any]],
        counter: int,
    ) -> tuple[list[dict[str, Any]], int]:
        grouped: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"total_count": 0, "logic_subgroups": []},
        )
        for label, logic_group in zip(labels, rule_groups):
            cluster_key = f"{rule_id}_SG_{label}"
            grouped[cluster_key]["total_count"] += logic_group["count"]
            grouped[cluster_key]["logic_subgroups"].append(logic_group)

        results: list[dict[str, Any]] = []
        for key, data in grouped.items():
            counter += 1
            main = max(data["logic_subgroups"], key=lambda g: g["count"])
            all_raw_logs = [
                member["raw_log"]
                for sub in data["logic_subgroups"]
                for member in sub["members"]
            ]
            results.append(
                {
                    "type": "AISuperGroup",
                    "super_group_id": key,
                    "rule_id": rule_id,
                    "representative_template": main["template"],
                    "representative_pattern": main["pattern"],
                    "total_count": data["total_count"],
                    "merged_variants_count": len(data["logic_subgroups"]),
                    "original_logs": all_raw_logs,
                }
            )

        return results, counter


def _prepare_embedding_components(
    rule_groups: list[dict[str, Any]],
    rule_config: GcaRuleConfig,
    default_variable_weight: float,
) -> list[dict[str, Any]]:
    """Prepare template + variable texts for each group."""
    components: list[dict[str, Any]] = []
    for lg in rule_groups:
        pattern_text = lg["pattern"].replace(" / ", " ")
        variables = VAR_PATTERN.findall(pattern_text)

        processed_vars: list[str] = []
        for idx, var_text in enumerate(variables):
            var_cfg = rule_config.variables.get(
                idx, VariableConfig(weight=default_variable_weight)
            )
            processed_vars.append(select_levels(var_text, var_cfg.levels))

        components.append(
            {
                "template": lg["template"],
                "variables": processed_vars,
            }
        )
    return components


def _cosine_distance_matrix(embs: Any) -> Any:
    """Compute NxN cosine distance matrix via vectorized dot product."""
    import numpy as np

    embs = np.asarray(embs, dtype=np.float64)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    normed = embs / norms
    return np.clip(1.0 - normed @ normed.T, 0.0, 2.0)


def _compute_distance_matrix(
    n: int,
    template_embs: Any,
    var_embeddings: list[tuple[Any, list[bool]]],
    rule_config: GcaRuleConfig,
    default_variable_weight: float,
) -> Any:
    """Build NxN distance matrix with per-pair renormalization.

    Uses vectorized numpy matrix ops instead of per-pair Python loops.
    """
    import numpy as np

    template_dist = _cosine_distance_matrix(template_embs)

    # Accumulate weighted and unweighted distance sums
    w_t = rule_config.template_weight
    weight_sum = np.full((n, n), w_t)
    weighted_dist = w_t * template_dist
    unweighted_dist = template_dist.copy()
    n_active = np.ones((n, n))

    for i, (embs_i, mask_i) in enumerate(var_embeddings):
        var_cfg = rule_config.variables.get(
            i, VariableConfig(weight=default_variable_weight)
        )
        w = var_cfg.weight
        mask_arr = np.asarray(mask_i, dtype=bool)
        active = np.outer(mask_arr, mask_arr)

        var_dist = _cosine_distance_matrix(embs_i)

        weight_sum += active * w
        weighted_dist += active * w * var_dist
        unweighted_dist += active * var_dist
        n_active += active

    # Per-pair normalization: use weighted when weight_sum > 0,
    # else uniform (mean of active distances)
    zero_mask = weight_sum == 0
    safe_weight_sum = np.where(zero_mask, 1.0, weight_sum)
    safe_n_active = np.where(n_active == 0, 1.0, n_active)

    d = np.where(
        zero_mask,
        unweighted_dist / safe_n_active,
        weighted_dist / safe_weight_sum,
    )
    np.fill_diagonal(d, 0.0)
    return d


class _SentenceModelLike(Protocol):
    def encode(
        self,
        sentences: list[str],
        batch_size: int,
        show_progress_bar: bool,
    ) -> Any: ...
