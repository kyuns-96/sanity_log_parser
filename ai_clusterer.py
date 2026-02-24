from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol, cast
import os
import json
import re
from collections import defaultdict

from api_config import load_embeddings_config
from ai_weights import extract_variable_tail, apply_variable_position_weights
from openai_compat_embeddings import (
    EmbeddingsRequestError,
    OpenAICompatibleEmbeddingsClient,
)

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
    _VAR_PATTERN = re.compile(r"'(.*?)'")

    def __init__(
        self,
        model_path: str = 'all-MiniLM-L6-v2',
        config_file: str = 'rule_clustering_config.json',
        console: "_ConsoleLike | None" = None,
    ) -> None:
        # Initialize instance attributes
        self.model: _SentenceModelLike | None = None
        self.remote_embeddings_client: OpenAICompatibleEmbeddingsClient | None = None
        self.ai_available: bool = False
        self.console = console

        embeddings_config = load_embeddings_config(
            config_path="config.json",
            warn=self._warn if self.console is not None else None,
        )

        if embeddings_config.backend == "openai_compatible":
            if not dbscan_available:
                self._warn("⚠️  OpenAI-compatible embeddings selected, but scikit-learn is unavailable.")
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
                self._warn(f"⚠️  Failed to load SentenceTransformer model: {exc}")
                self.ai_available = False
        # Load rule-specific eps and tail_weight from config file
        self.rule_config = self._load_config(config_file)
        self.default_eps = 0.2
        self.default_tail_weight = 2

    def _load_config(self, config_file: str) -> dict[str, Any]:
        """Load rule-specific parameters from config file"""
        if not os.path.exists(config_file):
            self._warn(f"Config file '{config_file}' not found. Using default settings.")
            return {}

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self._success(f"Loaded rule config from '{config_file}'")
            return config.get('rules', {})
        except Exception as e:
            self._warn(f"Error loading config: {e}. Using default settings.")
            return {}

    def _info(self, message: str) -> None:
        if self.console is not None:
            self.console.info(message)

    def _warn(self, message: str) -> None:
        if self.console is not None:
            self.console.warn(message)

    def _success(self, message: str) -> None:
        if self.console is not None:
            self.console.success(message)

    def get_rule_config(self, rule_id: str) -> dict[str, Any]:
        """Get rule config, return default if not found"""
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
            'variable_tail_configs': None
        }

    def run(self, logic_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.ai_available or not logic_groups:
            return []
        if DBSCANFactory is None:
            return []

        self._info(f"🤖 Stage 2 - AI Clustering: analyzing {len(logic_groups)} logic groups...")

        # Classify groups by rule_id
        groups_by_rule = defaultdict(list)
        for logic_group in logic_groups:
            groups_by_rule[logic_group['rule_id']].append(logic_group)

        self._info(f"Grouping by rule_id: {len(groups_by_rule)} different rules")

        final_output = []
        ai_group_counter = 0

        # Perform AI Clustering separately for each rule
        for rule_id, rule_groups in groups_by_rule.items():
            config = self.get_rule_config(rule_id)
            eps = config['eps']
            variable_position_weights = config.get('variable_position_weights', None)
            variable_tail_configs = config.get('variable_tail_configs', None)

            if len(rule_groups) < 2:
                # No merging needed if only 1 group
                for logic_group in rule_groups:
                    ai_group_counter += 1
                    all_raw_logs = [m['raw_log'] for m in logic_group['members']]
                    final_output.append({
                        "type": "AISuperGroup",
                        "super_group_id": f"{rule_id}_SG_{ai_group_counter}",
                        "rule_id": rule_id,
                        "representative_template": logic_group['template'],
                        "representative_pattern": logic_group['pattern'],
                        "total_count": logic_group['count'],
                        "merged_variants_count": 1,
                        "original_logs": all_raw_logs
                    })
                continue

            # Perform embedding and clustering only within same rule_id
            embedding_inputs = []
            for logic_group in rule_groups:
                # Extract variables from pattern

                pattern_text = logic_group['pattern'].replace(' / ', ' ')
                variables = self._VAR_PATTERN.findall(pattern_text)

                # Handle position-based tail config if present
                if variable_tail_configs:
                    var_texts = []
                    for idx, var in enumerate(variables):
                        var_config = variable_tail_configs.get(str(idx), None)
                        if var_config:
                            tail_levels = var_config.get('tail_levels', 1)
                            tail_weights = var_config.get('tail_weights', [1])
                            # Restore variable in " / " format
                            var_with_sep = var.replace('/', ' / ')
                            tail_text = extract_variable_tail(var_with_sep, tail_levels, tail_weights, None)
                            var_texts.append(tail_text)
                        else:
                            # Use variable as-is if no config
                            var_texts.append(var)

                    # Apply position-based variable weights
                    if variable_position_weights:
                        var_texts = apply_variable_position_weights(var_texts, variable_position_weights)

                    embedding_input = f"{logic_group['template']} {' '.join(var_texts)}"
                else:
                    # Use variables as-is without tail config
                    if variable_position_weights:
                        var_texts = apply_variable_position_weights(variables, variable_position_weights)
                        embedding_input = f"{logic_group['template']} {' '.join(var_texts)}"
                    else:
                        embedding_input = f"{logic_group['template']} {' '.join(variables)}"

                embedding_inputs.append(embedding_input)

            if self.remote_embeddings_client is not None:
                try:
                    embeddings = self.remote_embeddings_client.embed(embedding_inputs)
                except EmbeddingsRequestError as exc:
                    self._warn(f"⚠️  Remote embeddings failed, disabling AI clustering: {exc}")
                    self.ai_available = False
                    return []
            else:
                if self.model is None:
                    return []
                embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)

            # Perform clustering with rule-specific eps
            clustering = DBSCANFactory(eps=eps, min_samples=1, metric='cosine').fit(embeddings)

            ai_grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"total_count": 0, "logic_subgroups": []})
            for label, logic_group in zip(clustering.labels_, rule_groups):
                cluster_key = f"{rule_id}_SG_{label}"
                ai_grouped[cluster_key]["total_count"] += logic_group['count']
                ai_grouped[cluster_key]["logic_subgroups"].append(logic_group)

            # Generate results
            for key, data in ai_grouped.items():
                ai_group_counter += 1
                main = max(data["logic_subgroups"], key=lambda group: group['count'])

                all_raw_logs = []
                for sub in data["logic_subgroups"]:
                    for member in sub["members"]:
                        all_raw_logs.append(member["raw_log"])

                final_output.append({
                    "type": "AISuperGroup",
                    "super_group_id": key,
                    "rule_id": rule_id,
                    "representative_template": main['template'],
                    "representative_pattern": main['pattern'],
                    "total_count": data["total_count"],
                    "merged_variants_count": len(data["logic_subgroups"]),
                    "original_logs": all_raw_logs
                })

        final_output.sort(key=lambda group: group['total_count'], reverse=True)
        return final_output


class _ConsoleLike(Protocol):
    def info(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def success(self, message: str) -> None: ...


class _SentenceModelLike(Protocol):
    def encode(
        self,
        sentences: list[str],
        batch_size: int,
        show_progress_bar: bool,
    ) -> Any: ...
