#!/usr/bin/env python3
"""Run pure unit tests and configured PostgreSQL integration tests (never a local DB fallback)."""
import argparse
import json
import os
from pathlib import Path
import time
import unittest


ROOT = Path(__file__).resolve().parent
PROFILES = ("quick", "full")
# A short regression set for everyday edits. Keep expensive datasets and the
# complete portal migration replay in full, with their original assertions.
# Pure contracts are cheap. Exercise TLS/OWNER intake with the plain and shared
# layouts here; the compressed/page variants and legacy HTTPS replay stay in
# full rather than repeating the same workflow in every everyday run.
QUICK_TESTS = (
    "tests.test_vwp_inference", "tests.test_vwp_worker", "tests.test_vwp_personal_credentials",
    "tests.test_ai_http", "tests.test_ai_retry_resume",
    "tests.test_retry_policy", "tests.test_ai_resume_cancellation", "tests.test_clustering.ClusterUnitTests",
    "tests.test_ai_connections",
    "tests.test_ai_routing", "tests.test_ai_workbench_api",
    "tests.test_ai_batches.BatchPureTests", "tests.test_ai_batches.BatchDatabaseTests",
    "tests.test_ai_evidence_batches.EvidenceBatchPureTests", "tests.test_ai_evidence_batches.EvidenceBatchDatabaseTests",
    "tests.test_ai_evidence_batches.EvidenceTextDatabaseTests",
    "tests.test_source_read_cache",
    "tests.test_preserved_sources", "tests.test_preserved_text",
    "tests.test_payload_storage.PayloadStorageDatabaseTests.test_restore_plain_includes_committed_evidence_from_unfinished_analyses",
    "tests.test_shared_source_packets.SharedSourceSubmissionFlows.test_collection_only_owner_intake_and_human_review_with_shared_bodies",
    "tests.test_shared_source_packets.SharedSourceSubmissionFlows.test_collection_only_marker_tampering_is_rejected_with_shared_bodies",
    "tests.test_repository_layout.RepositoryLayoutTests.test_shared_code_and_each_server_use_their_own_namespace",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_one_retained_queue_body_serves_both_private_stores_after_ack",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_staged_parts_are_not_claimable_until_commit_even_by_exact_id_and_resume_without_duplicates",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_a_mismatched_commit_cannot_release_an_unfinished_source",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_foreign_keys_prevent_purging_acknowledged_bodies_and_apps_cannot_mutate_them",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_changed_or_truncated_queue_body_is_not_replaced_by_partial_logs",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_a_valid_packet_from_another_block_cannot_supply_a_private_part",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_equivalent_gzip_idempotence_fingerprint_and_repack_keep_every_record",
    "tests.test_shared_source_packets.SharedSourceDatabaseTests.test_partial_or_cascading_reference_contract_fails_closed",
    "tests.test_shared_source_packets.SharedSourceSubmissionFlows.test_tokenless_sso_owner_confirmation_keeps_full_shared_evidence",
    "tests.test_shared_source_packets.SharedSourceSubmissionFlows.test_group_confirmation_human_judgment_and_next_run_references_with_shared_bodies",
    "tests.test_shared_source_migration.SharedPacketPortalTests",
    "tests.test_shared_source_migration.SharedPacketMigrationTests.test_checkpoint_62_runs_exact_original_013_catalog",
    "tests.test_evidence_pages.EvidencePageCodecTests",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_new_pages_reduce_rows_and_preserve_every_record_and_outbox_packet",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_preexisting_row_analyses_keep_their_layout_after_012",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_replay_compares_logical_records_across_equivalent_gzip_encodings",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_crash_after_committed_full_page_resumes_without_missing_or_duplicate_logs",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_missing_first_middle_and_final_pages_never_return_a_complete_report",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_fingerprint_repack_and_restore_guard_preserve_pages_and_raw_sources",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_011_restore_cannot_claim_old_reader_compatibility_after_012_even_before_new_pages",
    "tests.test_evidence_pages.EvidencePageDatabaseTests.test_app_cannot_update_page_bytes_or_apply_012_and_reapplication_is_atomic",
    "tests.test_evidence_pages_migration.EvidencePagesPortalTests",
    "tests.test_evidence_pages_migration.EvidencePagesMigrationTests.test_checkpoint_60_applies_only_012_with_exact_original_catalog",
    "tests.test_payload_storage.PayloadCodecTests",
    "tests.test_payload_storage.PayloadStorageDatabaseTests.test_old_schema_plain_and_new_schema_compressed_sources_share_the_same_reader",
    "tests.test_payload_storage.PayloadStorageDatabaseTests.test_dry_run_backfill_restart_and_restore_preserve_all_logical_columns",
    "tests.test_payload_storage.PayloadStorageDatabaseTests.test_repack_keeps_every_byte_hash_id_revision_timestamp_and_payload",
    "tests.test_payload_storage.PayloadStorageDatabaseTests.test_app_roles_cannot_modify_blobs_or_use_owner_maintenance",
    "tests.test_payload_storage_migration.PayloadStoragePortalTests",
    "tests.test_payload_storage_migration.PayloadStorageMigrationTests.test_checkpoint_59_prepares_and_verifies_the_exact_original_011_catalog",
    "tests.test_collection_only",
    "tests.test_submission_tokenless.TokenlessSubmissionTests.test_collection_only_cannot_bypass_numeric_policy_or_publish_an_analysis",
    "tests.test_submission_tokenless.TokenlessSubmissionTests.test_collection_only_truncated_report_stays_blocked_after_owner_confirmation",
    "tests.test_submission_tokenless.InternalIntakeContracts",
    "tests.test_submission_tokenless.TokenlessSubmissionTests.test_real_tls_without_token_reaches_pending_then_sso_owner_publishes",
    "tests.test_submission_tokenless.TokenlessSubmissionTests.test_only_current_owner_can_confirm_and_digest_revision_and_request_are_bound",
    "tests.test_submission_tokenless.TokenlessSubmissionTests.test_owner_revocation_and_unconfigured_sso_do_not_allow_confirmation",
    "tests.test_submission_tokenless.TokenlessSubmissionTests.test_tokenless_routes_cannot_read_or_modify_a_legacy_authenticated_upload",
    "tests.test_submission_tokenless.TokenlessSubmissionTests.test_confirmation_revalidates_staged_parts_and_rolls_back_partial_publication",
    "tests.test_auth", "tests.test_db_config", "tests.test_db_single_account", "tests.test_common", "tests.test_parsers",
    "tests.test_scope_contract", "tests.test_access_store.AccessConfigurationTests", "tests.test_access_api.AccessApiBoundaryTests",
    "tests.test_access_store.AccessStoreTests.test_top_and_owner_have_sql_scoped_lists_and_exact_bundle_permissions",
    "tests.test_scoped_dashboard.ScopedDashboardTests.test_admin_starts_without_business_grants_and_can_approve_own_top_request",
    "tests.test_scoped_dashboard.ScopedDashboardTests.test_response_id_alias_cannot_expose_another_blocks_evidence",
    "tests.test_schema", "tests.test_submission_contracts", "tests.test_submission_https",
    "tests.test_submission_https_database.HttpsSubmissionDatabaseTests.test_worker_rechecks_remote_identity_and_current_block_assignment_before_analysis",
    "tests.test_normalization", "tests.test_integration_contracts",
    "tests.test_raw_sources.RawSourcesDatabaseTests.test_incomplete_submission_is_not_parsed_or_published",
    "tests.test_raw_sources.RawSourcesDatabaseTests.test_analysis_crash_removes_scratch_and_restarts_from_database_originals",
    "tests.test_raw_sources.RawSourcesDatabaseTests.test_private_raw_bytes_and_payloads_keep_restricted_database_permissions",
    "tests.test_raw_sources.RawSourcesDatabaseTests.test_missing_raw_migration_fails_closed_without_creating_an_archive",
    "tests.test_raw_sources_migration.RawSourcesPortalTests",
    "tests.test_raw_sources_migration.RawSourcesMigrationTests.test_009_checkpoint_executes_four_portal_statements_with_the_original_010_catalog",
    "tests.test_review_exceptions",
    "tests.test_submission_database.SubmissionDatabaseTests.test_summary_numeric_plan_is_rejected_without_publication",
    "tests.test_submission_database.SubmissionDatabaseTests.test_invalid_utf8_is_private_and_public_unknown_evidence_is_masked",
    "tests.test_submission_database.SubmissionDatabaseTests.test_json_pointers_survive_the_full_roundtrip_and_records_api",
    "tests.test_submission_database.SubmissionDatabaseTests.test_old_sensitive_outbox_is_blocked_without_rewriting_or_publication",
    "tests.test_submission_database.SubmissionDatabaseTests.test_old_numeric_summary_outbox_is_rejected_without_blocking_later_submissions",
    "tests.test_submission_database.SubmissionDatabaseTests.test_partial_outbox_from_another_revision_is_rejected_and_preserved",
    "tests.test_submission_database.SubmissionDatabaseTests.test_normalization_revision_preserves_previous_sources_and_human_decisions",
    "tests.test_submission_database.SubmissionDatabaseTests.test_receiver_rechecks_staged_numeric_manifest_before_commit",
    "tests.test_submission_database.SubmissionDatabaseTests.test_receiver_rechecks_staged_sensitive_parts_before_commit",
    "tests.test_ai_model.ReviewV2Tests.test_first_judgment_uses_direct_evidence_and_an_approved_criterion",
    "tests.test_oa_ai_feedback.OAFeedbackCycleTests.test_human_corrections_and_counterexamples_reach_the_next_eco_model_calls",
    "tests.test_mock_runner", "tests.test_test_runner", "oa.test_auth_config", "oa.test_sso",
    "tests.test_sso_identity_migration.SSOIdentityMigrationTests.test_008_checkpoint_runs_one_portal_statement_with_the_exact_original_009_catalog",
    "tests.test_sso_identity_migration.SSOIdentityMigrationTests.test_conflicting_insert_preserves_original_issuer_disabled_state_and_role_permissions",
    "tests.test_database.DatabaseTests.test_missing_configuration_and_admin_login_fail_closed",
    "tests.test_database.DatabaseTests.test_excess_column_rights_are_detected",
    "tests.test_database.DatabaseTests.test_destructive_and_ddl_related_rights_are_rejected_in_restricted_mode",
    "tests.test_database.DatabaseTests.test_private_payload_and_sender_ack_cannot_be_modified",
    "tests.test_database.DatabaseTests.test_message_idempotence_and_conflict",
    "tests.test_database.DatabaseTests.test_psycopg2_outer_rollback_and_readonly_do_not_leak",
    "oa.test_workspace_pipeline.WorkspacePipelineTests.test_only_a_confirmed_group_accepts_a_judgment_and_it_does_not_become_a_report_judgment",
    "oa.test_workspace_pipeline.WorkspacePipelineTests.test_top_scope_csrf_forged_actor_and_unconfigured_models_remain_guarded",
    "tests.test_portal_runbook.PortalRunbookTests.test_offline_cli_needs_only_standard_library_and_never_loads_a_db_driver",
    "tests.test_portal_runbook.PortalDatabaseTests.test_optional_pg15_nulls_flag_is_read_on_new_servers_and_false_on_pg12",
)


def build_suite(profile):
    loader = unittest.TestLoader()
    if profile == "quick":
        return loader.loadTestsFromNames(QUICK_TESTS)
    if profile != "full":
        raise ValueError("Unknown test profile")
    suite = unittest.TestSuite()
    for directory in ("tests", "vwp", "vwp_bridge", "oa"):
        suite.addTests(loader.discover(str(ROOT / directory), pattern="test_*.py", top_level_dir=str(ROOT)))
    return suite


def test_cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_cases(test)
        else:
            yield test


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="quick",
                        help="quick: core regressions (default); full: all tests including 100,000-record workloads")
    parser.add_argument("--list", action="store_true", help="list selected test IDs without executing them")
    parser.add_argument("--require-db", action="store_true", help="fail rather than skip PostgreSQL integration tests")
    parser.add_argument("--report-json", type=Path, help="write a new machine-readable result file (will not overwrite)")
    args = parser.parse_args(argv)
    if args.require_db and not all(os.environ.get(key) for key in ("SANITY_TEST_ADMIN_DSN", "SANITY_VWP_DSN", "SANITY_OA_DSN")):
        parser.error("--require-db needs SANITY_TEST_ADMIN_DSN, SANITY_VWP_DSN and SANITY_OA_DSN")
    if args.require_db:
        try:
            import psycopg2
        except ImportError:
            parser.error("--require-db needs psycopg2; install requirements.txt")
    suite = build_suite(args.profile)
    selected = suite.countTestCases()
    if not selected:
        parser.error("No tests selected")
    print(f"profile={args.profile} tests_selected={selected} full_suite={str(args.profile == 'full').lower()}", flush=True)
    if args.list:
        for test in test_cases(suite):
            print(test.id())
        return 0
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.report_json:
        report = {"profile": args.profile, "full_suite": args.profile == "full", "tests_selected": selected,
                  "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                  "skipped": len(result.skipped), "successful": result.wasSuccessful(),
                  "seconds": round(time.monotonic() - started, 3),
                  "failed_tests": [test.id() for test, _ in result.failures + result.errors]}
        with args.report_json.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
