"""Small synthetic checks for stable project/block scope on legacy bundles."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.sanity_clustering import assignment, scope_for
from common.sanity_common import ReviewError, atomic_json, digest, read_json, validate_content_id, validate_payload
from common.sanity_db import stable_uuid
from common.sanity_scope import run_scope, validate_scope
from oa.sanity_cluster_store import ClusterStore
from tests.test_common import pass_payload
from vwp.main import collect, validate_plan
from vwp_bridge.main import validate_manifest
from vwp_bridge.sanity_evidence import PgEvidence, PRODUCER_VERSION


def explicit_scope(project="project-1", block="project-1-cpu"):
    return {"schema_version": 1, "project_id": project, "block_id": block}


def source_plan(scope=None):
    plan = {"schema_version": 1, "run": copy.deepcopy(pass_payload()["run"]),
            "inventory": {"confirmed": True, "source": "synthetic-plan", "version": "1", "units": []}}
    if scope is not None:
        plan["run"]["scope"] = scope
    return plan


def source_manifest(scope=None):
    manifest = source_plan(scope)
    manifest["inventory"]["sha256"] = digest(manifest["inventory"])
    manifest.update(ready=True, files=[], unexpected_files=[])
    return manifest


class ScopeContractTests(unittest.TestCase):
    def test_optional_scope_is_distinct_from_present_null_and_required_scope(self):
        self.assertIsNone(run_scope({}))
        for run in ({}, {"scope": None}):
            with self.subTest(run=run), self.assertRaises(ReviewError) as error:
                run_scope(run, required=True, code="SCOPE_REQUIRED")
            self.assertEqual("SCOPE_REQUIRED", error.exception.code)

    def test_scope_accepts_workspace_identifiers_and_returns_a_copy(self):
        value = explicit_scope("A" + "x" * 99, "9_block.foo-bar")
        copied = validate_scope(value)
        self.assertEqual(value, copied)
        copied["block_id"] = "other"
        self.assertEqual("9_block.foo-bar", value["block_id"])

    def test_scope_rejects_extra_missing_wrong_types_and_noncanonical_identifiers(self):
        valid = explicit_scope()
        malformed = [None, [], "project-1", {}, {**valid, "extra": "ignored"},
                     {k: v for k, v in valid.items() if k != "block_id"},
                     {**valid, "schema_version": True}, {**valid, "schema_version": 1.0},
                     {**valid, "schema_version": 2}]
        for key in ("project_id", "block_id"):
            for value in (None, True, 1, "", "a" * 101, "项目", "-first", " a", "a ",
                          "a/b", "a\\b", "a\n", "a\r", "a\t", "a\x00", "a\x7f"):
                malformed.append({**valid, key: value})
        for value in malformed:
            with self.subTest(scope=value), self.assertRaises(ReviewError) as error:
                validate_scope(value)
            self.assertEqual("INVALID_SCOPE", error.exception.code)

    def test_plan_manifest_and_payload_all_validate_the_scope_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for value, accepted in ((explicit_scope(), True),
                                    ({**explicit_scope(), "schema_version": True}, False), (None, False)):
                plan, manifest, payload = source_plan(), source_manifest(), pass_payload()
                for item in (plan, manifest, payload):
                    item["run"]["scope"] = value
                for validator, item, args, code in (
                    (validate_plan, plan, (), "INVALID_PLAN"),
                    (validate_manifest, manifest, (root,), "BAD_MANIFEST"),
                    (validate_payload, payload, (), "INVALID_SCHEMA"),
                ):
                    with self.subTest(validator=validator.__name__, scope=value):
                        if accepted:
                            validator(item, *args)
                        else:
                            with self.assertRaises(ReviewError) as error:
                                validator(item, *args)
                            self.assertEqual(code, error.exception.code)

    def test_collection_preserves_explicit_scope_in_the_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            reports.mkdir()
            plan = source_plan(explicit_scope())
            atomic_json(root / "plan.json", plan)
            collected = collect(root / "plan.json", reports, root / "snapshot")
            self.assertEqual(plan["run"], collected["run"])
            self.assertEqual(explicit_scope(), read_json(root / "snapshot/run.json")["run"]["scope"])
            validate_manifest(collected, root / "snapshot")

    def test_scope_changes_bundle_identity_and_cannot_modify_a_sealed_bundle(self):
        payload = pass_payload()
        del payload["bundle_id"]
        original = "b-" + digest(payload)
        payload["run"]["scope"] = explicit_scope()
        scoped = "b-" + digest(payload)
        self.assertNotEqual(original, scoped)
        payload["bundle_id"] = scoped
        validate_content_id(payload)
        payload["run"]["scope"]["block_id"] = "project-1-gpu"
        with self.assertRaises(ReviewError):
            validate_content_id(payload)

    def test_analysis_identity_binds_scope_and_preserves_unscoped_identity(self):
        def analysis_id(manifest):
            db = Mock()
            db.conn.execute.return_value.fetchone.return_value = {"acquired": True}
            db.execute.return_value.fetchone.return_value = None
            with patch.object(PgEvidence, "stage"), patch.object(PgEvidence, "flush"):
                return PgEvidence(db, manifest).analysis_id

        old = source_manifest()
        contract = {key: old[key] for key in ("run", "inventory", "files", "unexpected_files")}
        contract["producer_version"] = PRODUCER_VERSION
        old_id = analysis_id(old)
        self.assertEqual(stable_uuid("analysis", digest(contract)), old_id)
        cpu_id = analysis_id(source_manifest(explicit_scope()))
        gpu_id = analysis_id(source_manifest(explicit_scope(block="project-1-gpu")))
        self.assertEqual(3, len({old_id, cpu_id, gpu_id}))

    def test_clustering_preserves_legacy_scope_and_separates_registered_blocks(self):
        payload = pass_payload()
        run, unit = payload["run"], payload["review_units"][0]
        legacy = {**{key: run[key] for key in ("project", "design", "stage", "tool", "tool_version")},
                  **{key: unit.get(key) for key in ("item_id", "parser", "rule_version", "condition")}}
        self.assertEqual(legacy, scope_for(run, unit))
        cpu_run = {**run, "scope": explicit_scope()}
        cpu = scope_for(cpu_run, unit)
        gpu = scope_for({**run, "scope": explicit_scope(block="project-1-gpu")}, unit)
        foreign = scope_for({**run, "scope": explicit_scope(project="project-2")}, unit)
        scopes = [legacy, cpu, gpu, foreign]
        self.assertEqual(4, len({digest(scope) for scope in scopes}))
        fields = {"object": "top/A", "code": "C1", "severity": "ERROR"}
        rule = {"identity_fields": ["object"], "where": {}, "reason": "Synthetic", "evidence_ids": ["r-1"]}
        self.assertEqual(4, len({assignment(fields, scope, [rule])["group_id"] for scope in scopes}))
        cpu["scope"]["block_id"] = "changed"
        self.assertEqual("project-1-cpu", cpu_run["scope"]["block_id"])


class ScopeReferenceTests(unittest.TestCase):
    def setUp(self):
        payload = pass_payload()
        self.scope = scope_for({**payload["run"], "scope": explicit_scope()}, payload["review_units"][0])
        self.store = ClusterStore.__new__(ClusterStore)
        self.store.scope = self.scope
        self.store.bundle_id, self.store.unit_id = "b-current", "u-current"
        self.store.db = Mock()

    def reference(self, suffix):
        return {"source_bundle": "b-" + suffix, "source_unit": "u-" + suffix,
                "source_job": "job-" + suffix, "group_revision": 1}

    def head(self, ref, scope):
        return {"state": {"confirmed": True, "confirmed_revision": ref["group_revision"],
                          "active_job": ref["source_job"]}, "payload": {"scope_hash": digest(scope)}}

    def rows(self, *values):
        self.store.db.execute.side_effect = [Mock(fetchone=Mock(return_value=value)) for value in values]

    def test_same_scope_dependency_is_current_for_both_legacy_and_scoped_runs(self):
        for scope in (self.scope, {key: value for key, value in self.scope.items() if key != "scope"}):
            self.store.scope = scope
            ref = self.reference("same")
            self.rows(self.head(ref, scope), {"payload": {"scope": scope, "known_groups": []}})
            self.assertIsNone(self.store.reference_problem({"known_groups": [ref]}, lock=True))

    def test_cross_block_or_unscoped_reference_is_rejected_even_with_matching_legacy_metadata(self):
        for other in ({**self.scope, "scope": explicit_scope(block="project-1-gpu")},
                      {key: value for key, value in self.scope.items() if key != "scope"}):
            ref = self.reference("foreign")
            self.rows(self.head(ref, other))
            self.assertEqual("REFERENCE_SCOPE_MISMATCH", self.store.reference_problem({"known_groups": [ref]}))

    def test_matching_head_hash_cannot_hide_a_foreign_source_job(self):
        ref = self.reference("foreign")
        self.rows(self.head(ref, self.scope), {"payload": {"scope": {}, "known_groups": []}})
        self.assertEqual("REFERENCE_SCOPE_MISMATCH", self.store.reference_problem({"known_groups": [ref]}))

    def test_transitive_reference_is_checked_for_cross_scope_reuse(self):
        first, second = self.reference("first"), self.reference("second")
        foreign = {**self.scope, "scope": explicit_scope(block="project-1-gpu")}
        self.rows(self.head(first, self.scope), {"payload": {"scope": self.scope, "known_groups": [second]}},
                  self.head(second, foreign))
        self.assertEqual("REFERENCE_SCOPE_MISMATCH", self.store.reference_problem({"known_groups": [first]}))


if __name__ == "__main__":
    unittest.main()
