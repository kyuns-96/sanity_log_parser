-- AFTER both tables and 03_constraints.sql exist. Replace every <schema> token locally first.
-- One statement per physical line. Check for existing equivalent indexes before submitting.
-- An already existing same-named index is NOT proof that its definition matches.
-- Selection uniqueness is REQUIRED, not merely a performance optimization.
CREATE INDEX di_area_power_csv_scope_idx ON <schema>.di_area_power_csv (project_id, version_id, block_id, created_at, id);
CREATE INDEX di_area_power_csv_condition_idx ON <schema>.di_area_power_csv (snapshot_id, condition_key, created_at, id);
CREATE INDEX di_area_power_csv_sha256_idx ON <schema>.di_area_power_csv (original_sha256);
CREATE INDEX di_area_power_tracking_record_parent_idx ON <schema>.di_area_power_tracking_record (parent_id, record_type, created_at, id);
CREATE INDEX di_area_power_tracking_record_previous_idx ON <schema>.di_area_power_tracking_record (previous_id) WHERE previous_id IS NOT NULL;
CREATE INDEX di_area_power_tracking_record_scope_idx ON <schema>.di_area_power_tracking_record (record_type, project_id, version_id, block_id, state, created_at, id);
CREATE INDEX di_area_power_tracking_record_snapshot_idx ON <schema>.di_area_power_tracking_record (snapshot_id, record_type, created_at, id) WHERE snapshot_id IS NOT NULL;
CREATE INDEX di_area_power_tracking_record_upload_idx ON <schema>.di_area_power_tracking_record (upload_id, record_type) WHERE upload_id IS NOT NULL;
CREATE INDEX di_area_power_tracking_record_baseline_idx ON <schema>.di_area_power_tracking_record (baseline_upload_id) WHERE baseline_upload_id IS NOT NULL;
CREATE UNIQUE INDEX di_area_power_tracking_record_selection_idx ON <schema>.di_area_power_tracking_record (selection_scope, snapshot_id, condition_key) WHERE record_type = 'selection';
CREATE INDEX di_area_power_tracking_record_pending_job_idx ON <schema>.di_area_power_tracking_record (consumer_server, created_at, id) WHERE record_type = 'job' AND state = 'pending';
