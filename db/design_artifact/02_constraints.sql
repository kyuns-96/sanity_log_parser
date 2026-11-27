-- AFTER di_design_artifact exists. One complete SQL statement per physical line.
-- Replace every <schema> token locally with the actual schema; never share that value here.
-- Check existing definitions with 00_inspect.sql first: apply ONLY missing constraints.
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_pkey PRIMARY KEY (id);
ALTER TABLE <schema>.di_design_artifact ALTER COLUMN artifact_bytes SET STORAGE EXTERNAL;
ALTER TABLE <schema>.di_design_artifact ALTER COLUMN thumbnail_bytes SET STORAGE EXTERNAL;
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_text_ck CHECK (btrim(project_id) <> '' AND btrim(block_id) <> '' AND btrim(original_filename) <> '' AND btrim(uploaded_by) <> '' AND (version_id IS NULL OR btrim(version_id) <> '') AND (net_ver IS NULL OR btrim(net_ver) <> '') AND (revision IS NULL OR btrim(revision) <> '') AND (eco_num IS NULL OR btrim(eco_num) <> '') AND (design_stage IS NULL OR btrim(design_stage) <> '') AND (tool_name IS NULL OR btrim(tool_name) <> '') AND (view_name IS NULL OR btrim(view_name) <> '') AND (process_label IS NULL OR btrim(process_label) <> '') AND (pex IS NULL OR btrim(pex) <> '') AND (sanity_report_id IS NULL OR btrim(sanity_report_id) <> ''));
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_layout_ck CHECK (layout IS NULL OR layout IN ('PRE', 'POST'));
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_kind_ck CHECK (artifact_kind IN ('floorplan', 'placement', 'congestion', 'clock_tree', 'routing', 'power_map', 'ir_drop', 'timing_map', 'density_map', 'drc_map', 'screenshot', 'other'));
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_mime_ck CHECK (mime_type IN ('image/png', 'image/webp', 'image/jpeg', 'image/svg+xml', 'application/pdf'));
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_state_ck CHECK (state IN ('active', 'archived'));
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_numeric_ck CHECK ((voltage_v IS NULL OR (voltage_v > '-Infinity'::numeric AND voltage_v < 'Infinity'::numeric)) AND (temp_c IS NULL OR (temp_c > '-Infinity'::numeric AND temp_c < 'Infinity'::numeric)) AND (width_px IS NULL OR width_px > 0) AND (height_px IS NULL OR height_px > 0));
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_bytes_ck CHECK (octet_length(sha256) = 32 AND byte_length BETWEEN 1 AND 16777216 AND byte_length = octet_length(artifact_bytes)::bigint AND sha256 = sha256(artifact_bytes) AND (thumbnail_bytes IS NULL OR octet_length(thumbnail_bytes) BETWEEN 1 AND 262144));
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_json_ck CHECK (jsonb_typeof(tags) = 'array' AND jsonb_typeof(metadata) = 'object');
ALTER TABLE <schema>.di_design_artifact ADD CONSTRAINT di_design_artifact_times_ck CHECK (updated_at >= created_at);
