-- AFTER BOTH TABLES EXIST. One complete SQL statement per physical line.
-- Replace every <schema> token locally with the actual schema; never share that value here.
-- Check existing definitions first: apply ONLY missing constraints with the intended definitions.
-- Do not recreate di_area_power_csv, which has already been requested.
-- ALTER TABLE needs table ownership or an authorized operator, not merely DML privileges.
-- No transaction/session continuity is assumed; all checks validate existing rows immediately.
-- If a statement fails, stop and resolve its cause locally. Do not drop data or hide the error.

-- Phase 1: keys. Existing equivalent PK/UNIQUE definitions need not be added again.
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_pkey PRIMARY KEY (id);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_pkey PRIMARY KEY (id);
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_scope_key UNIQUE (id, project_id, version_id, block_id, snapshot_id, condition_key);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_revision_key UNIQUE (record_type, entity_id, revision);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_dedupe_key UNIQUE (record_type, dedupe_key);

-- Phase 2: row-local validation, deferred from column-only creation requests.
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_text_ck CHECK (btrim(execution_name) <> '' AND btrim(original_filename) <> '' AND btrim(uploaded_by) <> '' AND btrim(process_label) <> '' AND btrim(pex) <> '' AND btrim(encoding) <> '');
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_numeric_ck CHECK (voltage_v > '-Infinity'::numeric AND voltage_v < 'Infinity'::numeric AND temp_c > '-Infinity'::numeric AND temp_c < 'Infinity'::numeric);
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_digest_ck CHECK (octet_length(condition_key) = 32 AND octet_length(original_sha256) = 32 AND octet_length(structure_digest) = 32);
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_sizes_ck CHECK (original_byte_length >= 0 AND compressed_byte_length >= 0 AND row_count >= 0 AND compressed_byte_length = octet_length(csv_gzip)::bigint);
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_json_ck CHECK (jsonb_typeof(analysis_conditions) = 'object' AND jsonb_typeof(input_contract) = 'object' AND jsonb_typeof(root_summary) = 'object' AND jsonb_typeof(validation_report) = 'object' AND jsonb_typeof(error_detail) = 'object' AND error_detail <> '{}'::jsonb);
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_conditions_ck CHECK (NOT conditions_complete OR (process_label IS NOT NULL AND voltage_v IS NOT NULL AND temp_c IS NOT NULL AND pex IS NOT NULL));
ALTER TABLE <schema>.di_area_power_csv ADD CONSTRAINT di_area_power_csv_state_ck CHECK (state IN ('pending', 'ready', 'failed') AND (state <> 'ready' OR (structure_digest IS NOT NULL AND root_summary IS NOT NULL AND encoding IS NOT NULL AND row_count IS NOT NULL AND row_count > 0 AND validation_report IS NOT NULL AND validated_at IS NOT NULL)) AND ((state = 'failed' AND error_detail IS NOT NULL) OR (state <> 'failed' AND error_detail IS NULL)));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_text_ck CHECK (btrim(record_type) <> '' AND btrim(selection_scope) <> '' AND btrim(state) <> '' AND btrim(created_by) <> '' AND btrim(updated_by) <> '' AND btrim(claim_owner) <> '' AND btrim(read_by) <> '' AND btrim(acknowledged_by) <> '' AND btrim(resolved_by) <> '');
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_versions_ck CHECK (revision > 0 AND payload_schema_version > 0 AND row_version > 0);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_digest_ck CHECK (octet_length(condition_key) = 32 AND octet_length(dedupe_key) = 32);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_json_ck CHECK (jsonb_typeof(payload) = 'object' AND jsonb_typeof(error_detail) = 'object' AND error_detail <> '{}'::jsonb);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_self_ck CHECK ((parent_id IS NULL OR parent_id <> id) AND (previous_id IS NULL OR previous_id <> id));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_times_ck CHECK (updated_at >= created_at AND (started_at IS NULL OR claimed_at IS NULL OR started_at >= claimed_at) AND (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_selection_ck CHECK ((record_type <> 'selection' OR (project_id IS NOT NULL AND version_id IS NOT NULL AND block_id IS NOT NULL AND snapshot_id IS NOT NULL AND condition_key IS NOT NULL AND selection_scope IS NOT NULL AND upload_id IS NOT NULL)) AND (record_type = 'selection' OR selection_scope IS NULL));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_job_fields_ck CHECK (job_kind IN ('ingest', 'compare', 'extract', 'judge', 'mail') AND consumer_server IN ('vwp_server', 'oa_server') AND (record_type = 'job' OR (job_kind IS NULL AND consumer_server IS NULL AND claim_owner IS NULL AND claimed_at IS NULL)) AND (record_type <> 'job' OR (job_kind IS NOT NULL AND consumer_server IS NOT NULL)));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_job_routing_ck CHECK (record_type <> 'job' OR (job_kind = 'mail' AND consumer_server = 'oa_server') OR (job_kind <> 'mail' AND consumer_server = 'vwp_server'));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_job_state_ck CHECK (record_type <> 'job' OR (state = 'pending' AND claim_owner IS NULL AND claimed_at IS NULL AND started_at IS NULL AND finished_at IS NULL AND error_detail IS NULL) OR (state = 'running' AND claim_owner IS NOT NULL AND claimed_at IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NULL AND error_detail IS NULL) OR (state = 'completed' AND claim_owner IS NOT NULL AND claimed_at IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL AND error_detail IS NULL) OR (state = 'failed' AND claim_owner IS NOT NULL AND claimed_at IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL AND error_detail IS NOT NULL));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_job_target_ck CHECK (record_type <> 'job' OR (job_kind = 'ingest' AND upload_id IS NOT NULL) OR (job_kind <> 'ingest' AND parent_id IS NOT NULL));
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_alert_fields_ck CHECK ((record_type = 'alert' OR (read_at IS NULL AND read_by IS NULL AND acknowledged_at IS NULL AND acknowledged_by IS NULL AND resolved_at IS NULL AND resolved_by IS NULL AND resolution_note IS NULL)) AND (read_by IS NULL OR read_at IS NOT NULL) AND (acknowledged_by IS NULL OR acknowledged_at IS NOT NULL) AND (resolved_by IS NULL OR resolved_at IS NOT NULL) AND (resolution_note IS NULL OR resolved_at IS NOT NULL));

-- Phase 3: foreign keys LAST, after both tables and all referenced keys exist.
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_parent_fk FOREIGN KEY (parent_id) REFERENCES <schema>.tracking_record (id);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_previous_fk FOREIGN KEY (previous_id) REFERENCES <schema>.tracking_record (id);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_upload_fk FOREIGN KEY (upload_id) REFERENCES <schema>.di_area_power_csv (id);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_baseline_upload_fk FOREIGN KEY (baseline_upload_id) REFERENCES <schema>.di_area_power_csv (id);
ALTER TABLE <schema>.tracking_record ADD CONSTRAINT tracking_record_upload_scope_fk FOREIGN KEY (upload_id, project_id, version_id, block_id, snapshot_id, condition_key) REFERENCES <schema>.di_area_power_csv (id, project_id, version_id, block_id, snapshot_id, condition_key);
