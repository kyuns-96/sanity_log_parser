from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sanity_log_parser.clustering.ai.clusterer import AIClusterer
from sanity_log_parser.clustering.logic import LogicClusterer
from sanity_log_parser.gca import GCA_DEFAULT_CONFIG_PATH
from sanity_log_parser.gca.config import load_gca_config
from sanity_log_parser.gca.eval import evaluate
from sanity_log_parser.parsing.template_manager import RuleTemplateManager
from sanity_log_parser.patterns import VAR_PATTERN


def _load_restored_clusters() -> dict[str, list[str]]:
    expected_path = (
        Path(__file__).resolve().parents[1]
        / "labeling"
        / "DES_0001"
        / "expected_clusters.py"
    )
    spec = importlib.util.spec_from_file_location(
        "des_0001_expected_clusters",
        expected_path,
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load {expected_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.clusters


def _build_logic_groups() -> tuple[list[dict[str, object]], dict[str, str]]:
    clusters = _load_restored_clusters()
    template_manager = RuleTemplateManager(template_file=None)
    parsed_logs: list[dict[str, object]] = []
    raw_to_human: dict[str, str] = {}

    for human_gid, raw_logs in clusters.items():
        for raw_log in raw_logs:
            variables = VAR_PATTERN.findall(raw_log)
            parsed_logs.append(
                {
                    "rule_id": "DES_0001",
                    "variables": tuple(variables) if variables else ("NO_VAR",),
                    "template": template_manager.get_pure_template(raw_log),
                    "raw_log": raw_log,
                }
            )
            raw_to_human[raw_log] = human_gid

    logic_groups = LogicClusterer().run(parsed_logs)
    logic_groups.sort(key=lambda group: (group["pattern"], group["template"]))
    return logic_groups, raw_to_human


def _write_json(path: Path, name: str, data: object) -> Path:
    target = path / name
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def test_restored_des_0001_reaches_perfect_f1_without_embeddings() -> None:
    logic_groups, raw_to_human = _build_logic_groups()

    clusterer = object.__new__(AIClusterer)
    clusterer.gca_config = load_gca_config(str(GCA_DEFAULT_CONFIG_PATH), strict=True)

    def _unexpected_embeddings(texts: list[str]) -> object:
        raise AssertionError(f"Embeddings should not be used for DES_0001: {len(texts)}")

    clusterer._compute_embeddings_batched = _unexpected_embeddings  # type: ignore[attr-defined]

    ai_groups = AIClusterer._run_weighted(
        clusterer,
        {"DES_0001": logic_groups},
        strict=True,
    )

    logic_json = {"schema_version": 2, "run": {}, "groups": []}
    gt_clusters: dict[str, list[str]] = {}
    for index, group in enumerate(logic_groups, 1):
        group_id = f"DES_0001::logic::{index:06d}"
        original_logs = [member["raw_log"] for member in group["members"]]
        human_gid = raw_to_human[original_logs[0]]
        gt_clusters.setdefault(human_gid, []).append(group_id)
        logic_json["groups"].append(
            {
                "group_type": "logic",
                "group_id": group_id,
                "rule_id": "DES_0001",
                "representative_template": group["template"],
                "representative_pattern": group["pattern"],
                "total_count": group["count"],
                "merged_variants_count": 1,
                "original_logs": original_logs,
            }
        )

    ai_json = {
        "schema_version": 2,
        "run": {},
        "groups": [
            {
                "rule_id": group["rule_id"],
                "original_logs": group["original_logs"],
            }
            for group in ai_groups
        ],
    }
    gt_json = {"DES_0001": list(gt_clusters.values())}

    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        logic_path = _write_json(tmp_path, "logic.json", logic_json)
        ai_path = _write_json(tmp_path, "ai.json", ai_json)
        gt_path = _write_json(tmp_path, "gt.json", gt_json)

        results = evaluate(logic_path, ai_path, gt_path, f1_threshold=1.0)

    assert len(ai_groups) == 13
    assert results == [
        {
            "rule_id": "DES_0001",
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "tp": 105,
            "fp": 0,
            "fn": 0,
            "gt_clusters": 13,
            "ai_clusters": 13,
            "status": "PASS",
        }
    ]
