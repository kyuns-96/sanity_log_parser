-- Bind two-stage workspace clustering and group decisions to immutable sources.
-- Owner migration after 006. Four tables and application grants are retained.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_oa_data IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE expected TEXT; old_scope TEXT;
BEGIN
    ALTER TABLE public.sanity_oa_data ADD CONSTRAINT sanity_pipeline_probe CHECK (record_type IN (
        'RECEIVED_BUNDLE','REVIEW_UNIT','REVIEW_EVENT','REPORT',
        'CLUSTER_STATE','CLUSTER_JOB','CLUSTER_PART','CLUSTER_MEMBER','CLUSTER_EVENT','CLUSTER_RULE',
        'WS_CONFIG','WS_USER','WS_SESSION','WS_PROJECT','WS_BLOCK','WS_VERSION','WS_POR','WS_POR_EVENT','WS_REPORT',
        'WS_REVIEW','WS_REVIEW_EVENT','WS_REVIEW_PROPOSAL')) NOT VALID;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
        WHERE conrelid='public.sanity_oa_data'::regclass AND conname='sanity_pipeline_probe';
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.sanity_oa_data'::regclass
                   AND conname='sanity_oa_kind_v4' AND convalidated AND pg_get_expr(conbin,conrelid)=expected) THEN
        RAISE EXCEPTION 'Expected 006 kind constraint differs; no migration committed';
    END IF;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT old_scope FROM pg_constraint
        WHERE conrelid='public.sanity_oa_data'::regclass AND conname='sanity_oa_workspace_scope_v4' AND convalidated;
    ALTER TABLE public.sanity_oa_data DROP CONSTRAINT sanity_pipeline_probe, DROP CONSTRAINT sanity_oa_kind_v4,
        DROP CONSTRAINT sanity_oa_workspace_scope_v4;
    ALTER TABLE public.sanity_oa_data ADD CONSTRAINT sanity_oa_kind_v5 CHECK (record_type IN (
        'RECEIVED_BUNDLE','REVIEW_UNIT','REVIEW_EVENT','REPORT',
        'CLUSTER_STATE','CLUSTER_JOB','CLUSTER_PART','CLUSTER_MEMBER','CLUSTER_EVENT','CLUSTER_RULE',
        'WS_CONFIG','WS_USER','WS_SESSION','WS_PROJECT','WS_BLOCK','WS_VERSION','WS_POR','WS_POR_EVENT','WS_REPORT',
        'WS_REVIEW','WS_REVIEW_EVENT','WS_REVIEW_PROPOSAL',
        'WS_CLUSTER','WS_CLUSTER_EVENT','WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL'));
    EXECUTE format('ALTER TABLE public.sanity_oa_data ADD CONSTRAINT sanity_oa_workspace_scope_v5 CHECK (
        (record_type NOT IN (''WS_CLUSTER'',''WS_CLUSTER_EVENT'',''WS_GROUP_REVIEW'',''WS_GROUP_REVIEW_EVENT'',''WS_GROUP_REVIEW_PROPOSAL'') AND (%s)) OR
        (record_type IN (''WS_CLUSTER'',''WS_CLUSTER_EVENT'',''WS_GROUP_REVIEW'',''WS_GROUP_REVIEW_EVENT'',''WS_GROUP_REVIEW_PROPOSAL'')
         AND workspace_key IS NOT NULL AND workspace_key ~ ''^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$''
         AND project IS NOT NULL AND workspace_project_ref IS NOT NULL AND workspace_block_ref IS NOT NULL
         AND run_id IS NULL AND bundle_id IS NULL AND review_unit_id IS NULL AND review_revision IS NULL
         AND request_id IS NULL AND entity_key IS NULL AND classification_revision IS NULL AND payload_bytes IS NULL
         AND workspace_version_ref IS NULL))', old_scope);
END
$migration$;

ALTER TABLE public.sanity_oa_data
    ADD COLUMN workspace_pipeline_report_ref UUID GENERATED ALWAYS AS (
        CASE WHEN record_type IN ('WS_CLUSTER','WS_CLUSTER_EVENT','WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL')
             THEN (payload->>'report_record_id')::uuid END) STORED REFERENCES public.sanity_oa_data(record_id),
    ADD COLUMN workspace_pipeline_version_ref UUID GENERATED ALWAYS AS (
        CASE WHEN record_type IN ('WS_CLUSTER','WS_CLUSTER_EVENT','WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL')
             THEN (payload->>'version_record_id')::uuid END) STORED REFERENCES public.sanity_oa_data(record_id),
    ADD COLUMN workspace_pipeline_event_ref UUID GENERATED ALWAYS AS (
        CASE WHEN record_type IN ('WS_CLUSTER','WS_GROUP_REVIEW') THEN (state->>'event_record_id')::uuid END
    ) STORED REFERENCES public.sanity_oa_data(record_id),
    ADD COLUMN workspace_pipeline_cluster_ref UUID GENERATED ALWAYS AS (
        CASE WHEN record_type IN ('WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL')
             THEN (payload->>'cluster_event_record_id')::uuid END
    ) STORED REFERENCES public.sanity_oa_data(record_id),
    ADD CONSTRAINT sanity_workspace_pipeline_refs_v5 CHECK (
        record_type NOT IN ('WS_CLUSTER','WS_CLUSTER_EVENT','WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL')
        OR (workspace_pipeline_report_ref IS NOT NULL AND workspace_pipeline_version_ref IS NOT NULL
            AND (record_type IN ('WS_CLUSTER','WS_CLUSTER_EVENT') OR workspace_pipeline_cluster_ref IS NOT NULL))),
    ADD CONSTRAINT sanity_workspace_pipeline_immutable_v5 CHECK (
        record_type NOT IN ('WS_CLUSTER_EVENT','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL')
        OR (state='{}'::jsonb AND version=0 AND updated_at=created_at));

CREATE INDEX sanity_workspace_pipeline_report_idx
    ON public.sanity_oa_data(workspace_pipeline_report_ref,record_type,created_at DESC);
CREATE INDEX sanity_workspace_pipeline_cluster_idx ON public.sanity_oa_data(workspace_pipeline_cluster_ref,record_type);
COMMIT;
