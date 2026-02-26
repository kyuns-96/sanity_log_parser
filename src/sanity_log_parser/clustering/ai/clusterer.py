from __future__ import annotations

import logging
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
    ) -> None:
        self.model: _SentenceModelLike | None = None
        self.remote_embeddings_client: OpenAICompatibleEmbeddingsClient | None = None
        self.ai_available: bool = False
        self.gca_config = gca_config

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

        for rule_id, rule_groups in groups_by_rule.items():
            if len(rule_groups) < 2:
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
                continue

            templates = [lg["template"] for lg in rule_groups]
            embeddings = self._compute_embeddings(templates)
            if embeddings is None:
                logger.warning(
                    "Embeddings failed for rule '%s'; keeping unclustered groups.",
                    rule_id,
                )
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
                continue

            clustering = DBSCANFactory(
                eps=0.2,
                min_samples=1,
                metric="cosine",
            ).fit(embeddings)

            new_groups, group_counter = self._build_cluster_results(
                rule_id,
                clustering.labels_,
                rule_groups,
                group_counter,
            )
            final_output.extend(new_groups)

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

            # Batch-embed per component position
            n = len(rule_groups)
            template_texts = [c["template"] for c in components]
            template_embs = self._compute_embeddings(template_texts)
            if template_embs is None:
                logger.warning(
                    "Embeddings failed for rule '%s'; keeping unclustered groups.",
                    rule_id,
                )
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
                continue

            max_vars = max(len(c["variables"]) for c in components)
            var_embeddings: list[tuple[Any, list[bool]]] = []
            for i in range(max_vars):
                texts: list[str] = []
                active_mask: list[bool] = []
                for c in components:
                    if i < len(c["variables"]) and c["variables"][i].strip():
                        texts.append(c["variables"][i])
                        active_mask.append(True)
                    else:
                        texts.append("_")
                        active_mask.append(False)
                embs = self._compute_embeddings(texts)
                if embs is None:
                    logger.warning(
                        "Variable embeddings failed for rule '%s'; keeping unclustered.",
                        rule_id,
                    )
                    break
                var_embeddings.append((embs, active_mask))
            else:
                # All embeddings succeeded — compute distance matrix
                distance_matrix = _compute_distance_matrix(
                    n,
                    template_embs,
                    var_embeddings,
                    rule_config,
                    self.gca_config.default_variable_weight,
                )

                clustering = DBSCANFactory(
                    eps=rule_config.eps,
                    min_samples=1,
                    metric="precomputed",
                ).fit(distance_matrix)

                new_groups, group_counter = self._build_cluster_results(
                    rule_id,
                    clustering.labels_,
                    rule_groups,
                    group_counter,
                )
                final_output.extend(new_groups)
                continue

            # Fallback: embeddings failed partway
            for lg in rule_groups:
                group_counter += 1
                final_output.append(
                    self._build_single_group(rule_id, lg, group_counter)
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


def _compute_distance_matrix(
    n: int,
    template_embs: Any,
    var_embeddings: list[tuple[Any, list[bool]]],
    rule_config: GcaRuleConfig,
    default_variable_weight: float,
) -> Any:
    """Build NxN distance matrix with per-pair renormalization."""
    import numpy as np
    from scipy.spatial.distance import cosine as cosine_distance

    d = np.zeros((n, n))
    for a in range(n):
        for b in range(a + 1, n):
            active_weights: list[float] = []
            active_distances: list[float] = []

            # Template: always active
            active_weights.append(rule_config.template_weight)
            active_distances.append(
                float(cosine_distance(template_embs[a], template_embs[b]))
            )

            # Variables: active only if BOTH groups have non-empty text
            for i, (embs_i, mask_i) in enumerate(var_embeddings):
                if mask_i[a] and mask_i[b]:
                    var_cfg = rule_config.variables.get(
                        i, VariableConfig(weight=default_variable_weight)
                    )
                    active_weights.append(var_cfg.weight)
                    active_distances.append(
                        float(cosine_distance(embs_i[a], embs_i[b]))
                    )

            # Per-pair normalization
            total = sum(active_weights)
            if total == 0:
                n_active = len(active_weights)
                normalized = [1.0 / n_active] * n_active if n_active > 0 else []
            else:
                normalized = [w / total for w in active_weights]

            d[a][b] = d[b][a] = sum(
                w * dist for w, dist in zip(normalized, active_distances)
            )

    return d


class _SentenceModelLike(Protocol):
    def encode(
        self,
        sentences: list[str],
        batch_size: int,
        show_progress_bar: bool,
    ) -> Any: ...
