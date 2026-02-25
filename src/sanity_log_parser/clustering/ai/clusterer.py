from __future__ import annotations

import logging
from importlib import import_module
from typing import Any, Protocol, cast
from collections import defaultdict

from sanity_log_parser.config.embeddings import load_embeddings_config
from sanity_log_parser.config.rules import load_rule_config
from sanity_log_parser.patterns import VAR_PATTERN
from .weights import extract_variable_tail, apply_variable_position_weights
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
    SentenceTransformerFactory = import_module("sentence_transformers").SentenceTransformer
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
        model_path: str = 'all-MiniLM-L6-v2',
        config_file: str = 'rule_clustering_config.json',
        embeddings_config_file: str = 'config.json',
    ) -> None:
        self.model: _SentenceModelLike | None = None
        self.remote_embeddings_client: OpenAICompatibleEmbeddingsClient | None = None
        self.ai_available: bool = False

        embeddings_config = load_embeddings_config(
            config_path=embeddings_config_file,
            warn=lambda msg: logger.warning("%s", msg),
        )

        if embeddings_config.backend == "openai_compatible":
            if not dbscan_available:
                logger.warning("OpenAI-compatible embeddings selected, but scikit-learn is unavailable.")
                self.ai_available = False
            elif embeddings_config.openai_compatible is not None:
                openai_settings = embeddings_config.openai_compatible
                self.remote_embeddings_client = OpenAICompatibleEmbeddingsClient(
                    base_url=openai_settings.base_url,
                    model=openai_settings.model,
                    api_key=openai_settings.api_key,
                )
                self.ai_available = True
        elif sentence_transformers_available and dbscan_available and SentenceTransformerFactory is not None:
            try:
                self.model = cast(_SentenceModelLike, SentenceTransformerFactory(model_path))
                self.ai_available = True
            except (ImportError, OSError, RuntimeError) as exc:
                logger.warning("Failed to load SentenceTransformer model: %s", exc)
                self.ai_available = False

        self.rule_config = load_rule_config(config_file)
        self.default_eps = 0.2

    def get_rule_config(self, rule_id: str) -> dict[str, Any]:
        if rule_id in self.rule_config:
            config = self.rule_config[rule_id].copy()
            if 'eps' not in config:
                config['eps'] = self.default_eps
            if 'variable_position_weights' not in config:
                config['variable_position_weights'] = None
            if 'variable_tail_configs' not in config:
                config['variable_tail_configs'] = None
            return config
        return {
            'eps': self.default_eps,
            'variable_position_weights': None,
            'variable_tail_configs': None,
        }

    def run(self, logic_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.ai_available or not logic_groups or DBSCANFactory is None:
            return []

        logger.info("AI Clustering: analyzing %d logic groups...", len(logic_groups))

        groups_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for logic_group in logic_groups:
            groups_by_rule[logic_group['rule_id']].append(logic_group)

        logger.info("Grouping by rule_id: %d different rules", len(groups_by_rule))

        final_output: list[dict[str, Any]] = []
        group_counter = 0

        for rule_id, rule_groups in groups_by_rule.items():
            config = self.get_rule_config(rule_id)

            if len(rule_groups) < 2:
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(self._build_single_group(rule_id, lg, group_counter))
                continue

            embedding_inputs = self._prepare_embedding_inputs(rule_groups, config)
            embeddings = self._compute_embeddings(embedding_inputs)
            if embeddings is None:
                return []

            clustering = DBSCANFactory(
                eps=config['eps'], min_samples=1, metric='cosine',
            ).fit(embeddings)

            new_groups, group_counter = self._build_cluster_results(
                rule_id, clustering.labels_, rule_groups, group_counter,
            )
            final_output.extend(new_groups)

        final_output.sort(key=lambda g: g['total_count'], reverse=True)
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
            "representative_template": logic_group['template'],
            "representative_pattern": logic_group['pattern'],
            "total_count": logic_group['count'],
            "merged_variants_count": 1,
            "original_logs": [m['raw_log'] for m in logic_group['members']],
        }

    def _prepare_embedding_inputs(
        self,
        rule_groups: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[str]:
        variable_position_weights = config.get('variable_position_weights')
        variable_tail_configs = config.get('variable_tail_configs')

        inputs: list[str] = []
        for logic_group in rule_groups:
            pattern_text = logic_group['pattern'].replace(' / ', ' ')
            variables = VAR_PATTERN.findall(pattern_text)

            if variable_tail_configs:
                var_texts = self._apply_tail_configs(variables, variable_tail_configs)
            else:
                var_texts = list(variables)

            if variable_position_weights:
                var_texts = apply_variable_position_weights(var_texts, variable_position_weights)

            inputs.append(f"{logic_group['template']} {' '.join(var_texts)}")

        return inputs

    def _apply_tail_configs(
        self,
        variables: list[str],
        tail_configs: dict[str, Any],
    ) -> list[str]:
        var_texts: list[str] = []
        for idx, var in enumerate(variables):
            var_config = tail_configs.get(str(idx))
            if var_config:
                tail_levels = var_config.get('tail_levels', 1)
                tail_weights = var_config.get('tail_weights', [1])
                var_with_sep = var.replace('/', ' / ')
                var_texts.append(extract_variable_tail(var_with_sep, tail_levels, tail_weights, None))
            else:
                var_texts.append(var)
        return var_texts

    def _compute_embeddings(self, inputs: list[str]) -> Any | None:
        if self.remote_embeddings_client is not None:
            try:
                return self.remote_embeddings_client.embed(inputs)
            except EmbeddingsRequestError as exc:
                logger.warning("Remote embeddings failed, disabling AI clustering: %s", exc)
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
            grouped[cluster_key]["total_count"] += logic_group['count']
            grouped[cluster_key]["logic_subgroups"].append(logic_group)

        results: list[dict[str, Any]] = []
        for key, data in grouped.items():
            counter += 1
            main = max(data["logic_subgroups"], key=lambda g: g['count'])
            all_raw_logs = [
                member["raw_log"]
                for sub in data["logic_subgroups"]
                for member in sub["members"]
            ]
            results.append({
                "type": "AISuperGroup",
                "super_group_id": key,
                "rule_id": rule_id,
                "representative_template": main['template'],
                "representative_pattern": main['pattern'],
                "total_count": data["total_count"],
                "merged_variants_count": len(data["logic_subgroups"]),
                "original_logs": all_raw_logs,
            })

        return results, counter


class _SentenceModelLike(Protocol):
    def encode(
        self,
        sentences: list[str],
        batch_size: int,
        show_progress_bar: bool,
    ) -> Any: ...
