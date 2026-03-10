from __future__ import annotations

import logging
import re
import time
from functools import lru_cache
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
from .pairwise_tree import (
    compute_adaptive_eps_distance_matrix,
    compute_pairwise_tree_distance_matrix,
)
from sanity_log_parser.embeddings.openai_compat import (
    EmbeddingsRequestError,
    OpenAICompatibleEmbeddingsClient,
)

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 512
_REMOTE_EMBED_MAX_ATTEMPTS = 3
_REMOTE_EMBED_RETRY_BASE_SECONDS = 0.5


@lru_cache(maxsize=1)
def _get_sentence_transformer_factory() -> Any | None:
    try:
        return import_module("sentence_transformers").SentenceTransformer
    except ImportError:
        return None


@lru_cache(maxsize=1)
def _get_dbscan_factory() -> Any | None:
    try:
        return import_module("sklearn.cluster").DBSCAN
    except ImportError:
        return None


class AIClusterer:
    def __init__(
        self,
        model_path: str = "nomic-ai/nomic-embed-text-v1.5",
        embeddings_config_file: str = "config.json",
        gca_config: GcaConfig | None = None,
        embed_batch_size: int = _EMBED_BATCH_SIZE,
    ) -> None:
        self.model: _SentenceModelLike | None = None
        self.remote_embeddings_client: OpenAICompatibleEmbeddingsClient | None = None
        self.ai_available: bool = False
        self.gca_config = gca_config
        self.embed_batch_size = embed_batch_size
        self.dbscan_factory = _get_dbscan_factory()

        embeddings_config = load_embeddings_config(
            config_path=embeddings_config_file,
            warn=lambda msg: logger.warning("%s", msg),
        )

        if embeddings_config.backend == "openai_compatible":
            if self.dbscan_factory is None:
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
        elif self.dbscan_factory is not None:
            sentence_transformer_factory = _get_sentence_transformer_factory()
            if sentence_transformer_factory is None:
                return
            try:
                self.model = cast(
                    _SentenceModelLike,
                    sentence_transformer_factory(
                        model_path, trust_remote_code=True
                    ),
                )
                self.ai_available = True
            except (ImportError, OSError, RuntimeError) as exc:
                logger.warning("Failed to load SentenceTransformer model: %s", exc)
                self.ai_available = False

    def run(
        self,
        logic_groups: list[dict[str, Any]],
        *,
        strict: bool = False,
    ) -> list[dict[str, Any]]:
        if not self.ai_available or not logic_groups or self.dbscan_factory is None:
            return []

        logger.info("AI Clustering: analyzing %d logic groups...", len(logic_groups))

        groups_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for logic_group in logic_groups:
            groups_by_rule[logic_group["rule_id"]].append(logic_group)

        logger.info("Grouping by rule_id: %d different rules", len(groups_by_rule))

        if self.gca_config is not None:
            return self._run_weighted(groups_by_rule, strict=strict)
        return self._run_template_only(groups_by_rule, strict=strict)

    def _run_template_only(
        self,
        groups_by_rule: dict[str, list[dict[str, Any]]],
        *,
        strict: bool = False,
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
            if strict:
                raise RuntimeError("AI clustering failed during embedding computation.")
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
            clustering = self.dbscan_factory(
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
        *,
        strict: bool = False,
    ) -> list[dict[str, Any]]:
        """Multi-embedding weighted distance clustering path (GCA config present)."""
        assert self.gca_config is not None
        final_output: list[dict[str, Any]] = []
        group_counter = 0

        # Phase 1: Handle single groups, prepare components for multi-group rules
        prep_t0 = time.perf_counter()
        prepared: dict[str, tuple[GcaRuleConfig, list[dict[str, Any]], list[float], list[str]]] = {}
        for rule_id, rule_groups in groups_by_rule.items():
            rule_config = get_gca_rule_config(self.gca_config, rule_id)
            if len(rule_groups) < 2:
                for lg in rule_groups:
                    group_counter += 1
                    final_output.append(
                        self._build_single_group(rule_id, lg, group_counter)
                    )
                continue
            if rule_config.pairwise_tree is not None:
                distance_matrix = compute_pairwise_tree_distance_matrix(
                    rule_groups,
                    rule_config.pairwise_tree,
                )
                clustering = self.dbscan_factory(
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
            components, vw, vm = _prepare_embedding_components(
                rule_groups, rule_config, self.gca_config.default_variable_weight
            )
            prepared[rule_id] = (rule_config, components, vw, vm)
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
        template_index: dict[str, tuple[int, int, list[str]]] = {}
        # var slots: (v_start, v_end, mask, v_keys, mode)
        var_index: dict[str, list[tuple[int, int, list[bool], list[str], str]]] = {}

        for rule_id, (rule_config, components, _vw, vm) in prepared.items():
            # Templates
            t_start = len(batch_texts)
            t_keys: list[str] = [c["template"] for c in components]
            batch_texts.extend(t_keys)
            template_index[rule_id] = (t_start, len(batch_texts), t_keys)

            # Variables per position
            max_vars = max(len(c["variables"]) for c in components)
            var_index[rule_id] = []
            for i in range(max_vars):
                mode = vm[i] if i < len(vm) else "embedding"
                mask: list[bool] = []
                v_keys: list[str] = []
                for c in components:
                    if i < len(c["variables"]) and c["variables"][i].strip():
                        text = c["variables"][i]
                        mask.append(True)
                        v_keys.append(text)
                    else:
                        mask.append(False)
                        v_keys.append("_")
                if mode != "jaccard":
                    v_start = len(batch_texts)
                    batch_texts.extend(v_keys)
                    var_index[rule_id].append((v_start, len(batch_texts), mask, v_keys, mode))
                else:
                    var_index[rule_id].append((-1, -1, mask, v_keys, mode))

        # Phase 3: Batch embed, then slice and cluster
        all_embs = self._compute_embeddings_batched(batch_texts)
        if all_embs is None:
            if strict:
                raise RuntimeError("AI clustering failed during embedding computation.")
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
            rule_config, components, vw, vm = prepared[rule_id]
            n = len(components)

            t_start, t_end, t_keys = template_index[rule_id]
            template_embs = all_embs[t_start:t_end]

            var_embeddings: list[tuple[Any, list[bool], list[str]]] = []
            for v_start, v_end, mask, v_keys, mode in var_index[rule_id]:
                if mode != "jaccard":
                    var_embeddings.append((all_embs[v_start:v_end], mask, v_keys))
                else:
                    var_embeddings.append((None, mask, v_keys))

            dm_t0 = time.perf_counter()
            distance_matrix = _compute_distance_matrix(
                n,
                template_embs,
                t_keys,
                var_embeddings,
                rule_config,
                self.gca_config.default_variable_weight,
                var_weights=vw,
                var_modes=vm,
            )
            if rule_config.adaptive_eps_tree is not None:
                distance_matrix = compute_adaptive_eps_distance_matrix(
                    groups_by_rule[rule_id],
                    distance_matrix,
                    rule_config.adaptive_eps_tree,
                )
                dbscan_eps = 1.0
            else:
                dbscan_eps = rule_config.eps
            logger.info(
                "[timing] distance matrix for '%s' (%d groups): %.3fs",
                rule_id,
                n,
                time.perf_counter() - dm_t0,
            )

            dbscan_t0 = time.perf_counter()
            clustering = self.dbscan_factory(
                eps=dbscan_eps,
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
            return self.remote_embeddings_client.embed(inputs)

        if self.model is None:
            return None
        return self.model.encode(inputs, batch_size=128, show_progress_bar=False)

    def _compute_embeddings_batched(self, texts: list[str]) -> Any | None:
        """Embed texts in bounded chunks, concatenate into one ndarray."""
        import numpy as np

        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        batch_size = self.embed_batch_size
        t0 = time.perf_counter()
        n_chunks = 0
        unique_texts: list[str] = []
        row_to_unique = np.empty(len(texts), dtype=np.intp)
        unique_index_by_text: dict[str, int] = {}
        for index, text in enumerate(texts):
            unique_index = unique_index_by_text.get(text)
            if unique_index is None:
                unique_index = len(unique_texts)
                unique_index_by_text[text] = unique_index
                unique_texts.append(text)
            row_to_unique[index] = unique_index

        chunks: list[Any] = []
        for start in range(0, len(unique_texts), batch_size):
            chunk = unique_texts[start : start + batch_size]
            chunk_t0 = time.perf_counter()
            result = self._embed_chunk_resilient(chunk)
            logger.info(
                "[timing] embed chunk %d/%d (%d texts): %.3fs",
                start // batch_size + 1,
                -(-len(unique_texts) // batch_size),  # ceil division
                len(chunk),
                time.perf_counter() - chunk_t0,
            )
            if result is None:
                return None
            chunks.append(np.asarray(result, dtype=np.float32))
            n_chunks += 1

        unique_embeddings = np.vstack(chunks)
        result = unique_embeddings[row_to_unique]
        logger.info(
            "[timing] embeddings total: %d texts (%d unique) in %d chunks, %.3fs",
            len(texts),
            len(unique_texts),
            n_chunks,
            time.perf_counter() - t0,
        )
        return result

    def _embed_chunk_resilient(self, chunk: list[str]) -> Any | None:
        import numpy as np

        if self.remote_embeddings_client is None:
            try:
                return self._compute_embeddings(chunk)
            except EmbeddingsRequestError as exc:
                logger.warning(
                    "Remote embeddings failed, disabling AI clustering: %s", exc
                )
                self.ai_available = False
                return None

        last_exc: EmbeddingsRequestError | None = None
        for attempt in range(1, _REMOTE_EMBED_MAX_ATTEMPTS + 1):
            try:
                return self._compute_embeddings(chunk)
            except EmbeddingsRequestError as exc:
                last_exc = exc
                if (
                    attempt >= _REMOTE_EMBED_MAX_ATTEMPTS
                    or not _is_retryable_remote_embeddings_error(exc)
                ):
                    break
                delay = _REMOTE_EMBED_RETRY_BASE_SECONDS * attempt
                logger.warning(
                    "Remote embeddings chunk attempt %d/%d failed for %d texts: %s. Retrying in %.1fs.",
                    attempt,
                    _REMOTE_EMBED_MAX_ATTEMPTS,
                    len(chunk),
                    exc,
                    delay,
                )
                time.sleep(delay)

        if len(chunk) > 1:
            split_at = len(chunk) // 2
            logger.warning(
                "Remote embeddings chunk failed for %d texts: %s. Retrying as split chunks (%d + %d).",
                len(chunk),
                last_exc,
                split_at,
                len(chunk) - split_at,
            )
            left = self._embed_chunk_resilient(chunk[:split_at])
            right = self._embed_chunk_resilient(chunk[split_at:])
            if left is None or right is None:
                self.ai_available = False
                return None
            return np.vstack(
                [
                    np.asarray(left, dtype=np.float32),
                    np.asarray(right, dtype=np.float32),
                ]
            )

        logger.warning(
            "Remote embeddings failed, disabling AI clustering: %s", last_exc
        )
        self.ai_available = False
        return None

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
            patterns = [sub["pattern"] for sub in data["logic_subgroups"]]
            results.append(
                {
                    "type": "AISuperGroup",
                    "super_group_id": key,
                    "rule_id": rule_id,
                    "representative_template": main["template"],
                    "representative_pattern": _merge_patterns(patterns),
                    "total_count": data["total_count"],
                    "merged_variants_count": len(data["logic_subgroups"]),
                    "original_logs": all_raw_logs,
                }
            )

        return results, counter


_SLOT_SPLIT_RE = re.compile(r"\s+/\s+")
_MAX_ALT_SEG = 4
_MAX_ALT_SLOT = 6


def _merge_patterns(patterns: list[str]) -> str:
    """Create a representative pattern from multiple logic group patterns.

    Performs structural merge at the path-segment level to avoid
    double-wildcarding already-wildcarded patterns.

    Examples:
        ["'u_top/clk_gen_*' / 'master_*'",
         "'u_top/sig_out_*' / 'master_*'"]
        → "'u_top/{clk_gen_*|sig_out_*}' / 'master_*'"
    """
    if len(patterns) == 1:
        return patterns[0]

    pats = [p for p in patterns if p and p != "NO_VAR"]
    if not pats:
        return "NO_VAR"

    split = [_SLOT_SPLIT_RE.split(p.strip()) for p in pats]
    slot_count = max(len(s) for s in split)
    split = [s + ["NO_VAR"] * (slot_count - len(s)) for s in split]

    merged_slots: list[str] = []
    for i in range(slot_count):
        vals = [s[i] for s in split if s[i] != "NO_VAR"]
        if not vals:
            merged_slots.append("NO_VAR")
        else:
            merged_slots.append(_merge_slot(vals))

    return " / ".join(merged_slots)


def _merge_slot(values: list[str]) -> str:
    """Merge a single variable-position slot across subgroups."""
    cores: list[str] = []
    quotes: list[str] = []
    for v in values:
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            cores.append(v[1:-1])
            quotes.append(v[0])
        else:
            cores.append(v)

    quote = quotes[0] if quotes and len(set(quotes)) == 1 else ""

    unique = list(dict.fromkeys(cores))  # dedupe, preserve order
    if len(unique) == 1:
        merged_core = unique[0]
    else:
        seg_lists = [u.split("/") for u in unique]
        if all(len(s) == len(seg_lists[0]) for s in seg_lists):
            merged_core = "/".join(
                _merge_atom(list(col)) for col in zip(*seg_lists)
            )
        else:
            merged_core = _merge_atom(unique, max_alt=_MAX_ALT_SLOT)

    return f"{quote}{merged_core}{quote}" if quote else merged_core


def _merge_atom(values: list[str], max_alt: int = _MAX_ALT_SEG) -> str:
    """Merge a single path segment or slot into one representative value."""
    unique = list(dict.fromkeys(values))
    if len(unique) == 1:
        return unique[0]
    if len(unique) <= max_alt:
        return "{" + "|".join(unique) + "}"
    return "*"


def _prepare_embedding_components(
    rule_groups: list[dict[str, Any]],
    rule_config: GcaRuleConfig,
    default_variable_weight: float,
) -> tuple[list[dict[str, Any]], list[float], list[str]]:
    """Prepare template + variable texts for each group.

    Returns (components, var_weights, var_modes) where var_weights[i] is the
    weight for expanded variable slot i, and var_modes[i] is ``"embedding"``
    or ``"jaccard"``.
    """
    # Determine max original variable count across all groups
    split_variables = [_split_pattern_slots(lg["pattern"]) for lg in rule_groups]
    max_orig_vars = max((len(variables) for variables in split_variables), default=0)

    # Build expansion plan: list of (orig_var_idx, levels_arg_for_select_levels)
    slot_specs: list[tuple[int, tuple[int, ...] | None]] = []
    var_weights: list[float] = []
    var_modes: list[str] = []
    for idx in range(max_orig_vars):
        var_cfg = rule_config.variables.get(
            idx, VariableConfig(weight=default_variable_weight)
        )
        if var_cfg.level_weights is not None:
            for level_key in sorted(var_cfg.level_weights.keys()):
                level_weight = var_cfg.level_weights[level_key]
                if level_weight == 0:
                    continue
                slot_specs.append((idx, (level_key,)))
                var_weights.append(level_weight)
                var_modes.append(var_cfg.match_mode)
        else:
            if var_cfg.weight == 0:
                continue
            levels_key = tuple(var_cfg.levels) if var_cfg.levels is not None else None
            slot_specs.append((idx, levels_key))
            var_weights.append(var_cfg.weight)
            var_modes.append(var_cfg.match_mode)

    # Process each group using the expansion plan
    components: list[dict[str, Any]] = []
    for lg, variables in zip(rule_groups, split_variables, strict=True):
        processed_vars: list[str] = []
        for orig_idx, levels in slot_specs:
            if orig_idx < len(variables):
                processed_vars.append(
                    _select_levels_cached(variables[orig_idx], levels)
                )
            else:
                processed_vars.append("")

        components.append(
            {
                "template": lg["template"],
                "variables": processed_vars,
            }
        )
    return components, var_weights, var_modes


@lru_cache(maxsize=131072)
def _split_pattern_slots(pattern: str) -> tuple[str, ...]:
    return tuple(_SLOT_SPLIT_RE.split(pattern.strip()))


@lru_cache(maxsize=262144)
def _select_levels_cached(
    path: str,
    levels: tuple[int, ...] | None,
) -> str:
    return select_levels(path, list(levels) if levels is not None else None)


def _is_retryable_remote_embeddings_error(exc: EmbeddingsRequestError) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "response size mismatch",
            "missing indices",
            "network error",
            "i/o error",
            "timeout",
            "timed out",
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        )
    )


def _cosine_distance_matrix_unique(
    embeddings: Any, text_keys: list[str]
) -> Any:
    """Compute NxN cosine distance, deduplicating identical texts.

    Groups sharing the same text get the same embedding row, so we only
    compute the unique-by-unique matmul and expand back to NxN via indexing.
    """
    import numpy as np

    n = len(text_keys)

    # Deduplicate: map each row to a unique index
    unique_map: dict[str, int] = {}
    row_to_unique = np.empty(n, dtype=np.intp)
    for i, key in enumerate(text_keys):
        if key not in unique_map:
            unique_map[key] = len(unique_map)
        row_to_unique[i] = unique_map[key]

    n_unique = len(unique_map)
    if n_unique == n:
        # No duplicates — compute full NxN directly
        return _cosine_distance_matrix_raw(np.asarray(embeddings, dtype=np.float32))

    # Gather one embedding per unique text
    unique_indices = np.empty(n_unique, dtype=np.intp)
    for idx, uid in enumerate(unique_map.values()):
        unique_indices[uid] = 0  # placeholder
    for i, uid in enumerate(row_to_unique):
        unique_indices[uid] = i  # last occurrence wins (all same)

    X_unique = np.asarray(embeddings, dtype=np.float32)[unique_indices]
    unique_dist = _cosine_distance_matrix_raw(X_unique)

    # Expand unique distances back to NxN via fancy indexing
    return unique_dist[np.ix_(row_to_unique, row_to_unique)]


def _cosine_distance_matrix_raw(X: Any) -> Any:
    """Cosine distance NxN via normalized matmul (BLAS, float32)."""
    import numpy as np

    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X_norm = X / norms
    sim = X_norm @ X_norm.T
    np.clip(sim, -1.0, 1.0, out=sim)
    dist = 1.0 - sim
    np.fill_diagonal(dist, 0.0)
    return dist


def _jaccard_distance_matrix(keys: list[str]) -> Any:
    """NxN Jaccard distance on token sets.

    Each key is split on whitespace into a set of tokens (quotes stripped).
    Jaccard distance = 1 - |intersection| / |union|.
    Identical keys → 0, completely disjoint → 1.
    """
    import numpy as np

    n = len(keys)
    sets: list[frozenset[str]] = []
    for k in keys:
        tokens = frozenset(t.strip("'\" ") for t in k.split() if t.strip("'\" "))
        sets.append(tokens)

    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            si, sj = sets[i], sets[j]
            if not si and not sj:
                d = 0.0
            elif not si or not sj:
                d = 1.0
            else:
                d = 1.0 - len(si & sj) / len(si | sj)
            dist[i, j] = d
            dist[j, i] = d
    return dist


def _compute_distance_matrix(
    n: int,
    template_embs: Any,
    template_keys: list[str],
    var_embeddings: list[tuple[Any, list[bool], list[str]]],
    rule_config: GcaRuleConfig,
    default_variable_weight: float,
    var_weights: list[float] | None = None,
    var_modes: list[str] | None = None,
) -> Any:
    """Build NxN distance matrix with per-pair renormalization (vectorized).

    When *var_weights* is provided, ``var_weights[i]`` is used as the weight
    for variable slot *i* instead of looking up from *rule_config*.

    When *var_modes* is provided, ``var_modes[i]`` selects the distance metric
    for slot *i*: ``"embedding"`` (cosine) or ``"jaccard"``.
    """
    import numpy as np

    tw = float(rule_config.template_weight)

    # Template: NxN cosine distance (skip if weight is 0)
    if tw > 0:
        template_dist = _cosine_distance_matrix_unique(template_embs, template_keys)
    else:
        template_dist = np.zeros((n, n), dtype=np.float32)

    # Variable components: skip zero-weight variables entirely
    comp_dists: list[Any] = []
    comp_weights: list[float] = []
    comp_pair_masks: list[Any] = []
    for i, (embs_i, mask_i, keys_i) in enumerate(var_embeddings):
        if var_weights is not None:
            w = float(var_weights[i])
        else:
            var_cfg = rule_config.variables.get(
                i, VariableConfig(weight=default_variable_weight)
            )
            w = float(var_cfg.weight)
        if w == 0:
            continue
        mode = var_modes[i] if var_modes is not None and i < len(var_modes) else "embedding"
        if mode == "jaccard":
            dist_i = _jaccard_distance_matrix(keys_i)
        else:
            dist_i = _cosine_distance_matrix_unique(embs_i, keys_i)
        mask_arr = np.array(mask_i, dtype=np.float32)
        pair_mask = np.outer(mask_arr, mask_arr)  # 1.0 where both active
        comp_dists.append(dist_i)
        comp_weights.append(w)
        comp_pair_masks.append(pair_mask)

    # Accumulate: weighted numerator and weight denominator
    weight_sum = np.full((n, n), tw, dtype=np.float32)
    numerator = tw * template_dist

    for dist_i, w_i, pm_i in zip(comp_dists, comp_weights, comp_pair_masks):
        weight_sum += w_i * pm_i
        numerator += w_i * dist_i * pm_i

    # Uniform fallback: count active components per pair
    n_active = np.ones((n, n), dtype=np.float32)  # template always counts
    uniform_num = template_dist.copy()
    for dist_i, pm_i in zip(comp_dists, comp_pair_masks):
        n_active += pm_i
        uniform_num += dist_i * pm_i

    # Per-pair normalization: weighted if total > 0, else uniform
    zero_weight = weight_sum == 0
    safe_weight = np.where(zero_weight, 1.0, weight_sum)
    safe_active = np.maximum(n_active, 1.0)
    result = np.where(zero_weight, uniform_num / safe_active, numerator / safe_weight)
    np.fill_diagonal(result, 0.0)

    return result


class _SentenceModelLike(Protocol):
    def encode(
        self,
        sentences: list[str],
        batch_size: int,
        show_progress_bar: bool,
    ) -> Any: ...
