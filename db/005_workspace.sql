-- OA management records, after 004. Four existing tables are retained.
-- Owner/DBA migration; application code never runs DDL. Existing forms/002/003/004 stay unchanged.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_oa_data IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE expected TEXT;
BEGIN
    ALTER TABLE public.sanity_oa_data ADD CONSTRAINT sanity_workspace_probe CHECK (record_type IN
        ('RECEIVED_BUNDLE', 'REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT',
         'CLUSTER_STATE', 'CLUSTER_JOB', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE')) NOT VALID;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
        WHERE conrelid='public.sanity_oa_data'::regclass AND conname='sanity_workspace_probe';
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.sanity_oa_data'::regclass
                   AND conname='sanity_oa_kind_v2' AND convalidated AND pg_get_expr(conbin,conrelid)=expected) THEN
        RAISE EXCEPTION 'Expected 004 OA kind constraint differs; no migration committed';
    END IF;
END
$migration$;

ALTER TABLE public.sanity_oa_data
    DROP CONSTRAINT sanity_workspace_probe,
    DROP CONSTRAINT sanity_oa_kind_v2,
    ALTER COLUMN project DROP NOT NULL,
    ALTER COLUMN run_id DROP NOT NULL,
    ALTER COLUMN bundle_id DROP NOT NULL,
    ADD COLUMN workspace_key VARCHAR(100),
    ADD COLUMN workspace_project_ref UUID REFERENCES public.sanity_oa_data(record_id),
    ADD COLUMN workspace_block_ref UUID REFERENCES public.sanity_oa_data(record_id),
    ADD COLUMN workspace_version_ref UUID GENERATED ALWAYS AS (
        CASE WHEN record_type = 'WS_POR' THEN (state->>'version_record_id')::uuid
             WHEN record_type IN ('WS_REPORT', 'WS_POR_EVENT') THEN (payload->>'version_record_id')::uuid END
    ) STORED REFERENCES public.sanity_oa_data(record_id),
    ADD CONSTRAINT sanity_oa_kind_v3 CHECK (record_type IN (
        'RECEIVED_BUNDLE', 'REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT',
        'CLUSTER_STATE', 'CLUSTER_JOB', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE',
        'WS_CONFIG', 'WS_USER', 'WS_SESSION', 'WS_PROJECT', 'WS_BLOCK', 'WS_VERSION', 'WS_POR', 'WS_POR_EVENT', 'WS_REPORT')),
    ADD CONSTRAINT sanity_oa_workspace_scope_v3 CHECK (
        (record_type NOT IN ('WS_CONFIG', 'WS_USER', 'WS_SESSION', 'WS_PROJECT', 'WS_BLOCK', 'WS_VERSION', 'WS_POR', 'WS_POR_EVENT', 'WS_REPORT')
         AND project IS NOT NULL AND run_id IS NOT NULL AND bundle_id IS NOT NULL
         AND workspace_key IS NULL AND workspace_project_ref IS NULL AND workspace_block_ref IS NULL AND workspace_version_ref IS NULL)
        OR
        (record_type IN ('WS_CONFIG', 'WS_USER', 'WS_SESSION', 'WS_PROJECT', 'WS_BLOCK', 'WS_VERSION', 'WS_POR', 'WS_POR_EVENT', 'WS_REPORT')
         AND workspace_key IS NOT NULL AND workspace_key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
         AND run_id IS NULL AND bundle_id IS NULL AND review_unit_id IS NULL AND review_revision IS NULL
         AND request_id IS NULL AND entity_key IS NULL AND classification_revision IS NULL AND payload_bytes IS NULL
         AND ((record_type IN ('WS_CONFIG', 'WS_USER', 'WS_SESSION') AND project IS NULL)
              OR (record_type NOT IN ('WS_CONFIG', 'WS_USER', 'WS_SESSION') AND project IS NOT NULL))
         AND ((record_type IN ('WS_BLOCK', 'WS_VERSION', 'WS_POR', 'WS_POR_EVENT', 'WS_REPORT')) = (workspace_project_ref IS NOT NULL))
         AND ((record_type IN ('WS_VERSION', 'WS_POR', 'WS_POR_EVENT', 'WS_REPORT')) = (workspace_block_ref IS NOT NULL))
         AND (record_type NOT IN ('WS_REPORT', 'WS_POR_EVENT') OR workspace_version_ref IS NOT NULL))
    ),
    ADD CONSTRAINT sanity_oa_workspace_immutable_v3 CHECK (
        record_type NOT IN ('WS_CONFIG', 'WS_PROJECT', 'WS_BLOCK', 'WS_VERSION', 'WS_POR_EVENT', 'WS_REPORT')
        OR (state = '{}'::jsonb AND version = 0 AND updated_at = created_at));

CREATE UNIQUE INDEX sanity_workspace_key_uq ON public.sanity_oa_data(record_type, workspace_key)
    WHERE workspace_key IS NOT NULL;
CREATE UNIQUE INDEX sanity_workspace_username_uq ON public.sanity_oa_data((payload->>'username'))
    WHERE record_type = 'WS_USER';
CREATE INDEX sanity_workspace_project_idx ON public.sanity_oa_data(workspace_project_ref, record_type);
CREATE INDEX sanity_workspace_block_idx ON public.sanity_oa_data(workspace_block_ref, record_type, created_at);
CREATE INDEX sanity_workspace_version_idx ON public.sanity_oa_data(workspace_version_ref, record_type);
CREATE INDEX sanity_workspace_audit_idx ON public.sanity_oa_data(workspace_block_ref, version DESC, created_at DESC)
    WHERE record_type = 'WS_POR_EVENT';
COMMIT;
