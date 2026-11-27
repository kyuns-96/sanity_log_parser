-- AFTER di_design_artifact and 02_constraints.sql exist. Replace every <schema> token locally first.
-- One statement per physical line. Check existing indexes with 00_inspect.sql before submitting.
CREATE INDEX di_design_artifact_version_idx ON <schema>.di_design_artifact (project_id, block_id, version_id, artifact_kind, created_at DESC, id) WHERE state = 'active';
CREATE INDEX di_design_artifact_business_key_idx ON <schema>.di_design_artifact (project_id, block_id, net_ver, revision, eco_num, artifact_kind, created_at DESC) WHERE state = 'active';
CREATE INDEX di_design_artifact_area_version_idx ON <schema>.di_design_artifact (area_version_id, artifact_kind, created_at DESC) WHERE area_version_id IS NOT NULL AND state = 'active';
CREATE INDEX di_design_artifact_area_upload_idx ON <schema>.di_design_artifact (area_upload_id, artifact_kind) WHERE area_upload_id IS NOT NULL AND state = 'active';
CREATE INDEX di_design_artifact_sanity_report_idx ON <schema>.di_design_artifact (sanity_report_id) WHERE sanity_report_id IS NOT NULL AND state = 'active';
CREATE UNIQUE INDEX di_design_artifact_dedupe_uq ON <schema>.di_design_artifact (project_id, block_id, COALESCE(version_id, ''), artifact_kind, COALESCE(view_name, ''), sha256) WHERE state = 'active';
