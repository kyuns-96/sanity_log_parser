-- Owner/DBA migration after 008. No company IdP values, roles or new tables.
-- Portal users must use the generated single-ALTER bundle, not edit this file.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_oa_data IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE expected TEXT; old_scope TEXT;
BEGIN
    ALTER TABLE public.sanity_oa_data ADD CONSTRAINT sanity_sso_kind_probe CHECK (record_type IN (
        'RECEIVED_BUNDLE','REVIEW_UNIT','REVIEW_EVENT','REPORT',
        'CLUSTER_STATE','CLUSTER_JOB','CLUSTER_PART','CLUSTER_MEMBER','CLUSTER_EVENT','CLUSTER_RULE',
        'WS_CONFIG','WS_USER','WS_SESSION','WS_PROJECT','WS_BLOCK','WS_VERSION','WS_POR','WS_POR_EVENT','WS_REPORT',
        'WS_REVIEW','WS_REVIEW_EVENT','WS_REVIEW_PROPOSAL',
        'WS_CLUSTER','WS_CLUSTER_EVENT','WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL',
        'WS_SUBMISSION','WS_SOURCE_PART')) NOT VALID;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
        WHERE conrelid='public.sanity_oa_data'::regclass AND conname='sanity_sso_kind_probe';
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.sanity_oa_data'::regclass
                   AND conname='sanity_oa_kind_v6' AND contype='c' AND convalidated
                   AND NOT connoinherit AND pg_get_expr(conbin,conrelid)=expected) THEN
        RAISE EXCEPTION 'Expected 008 kind contract differs; no migration committed';
    END IF;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT old_scope FROM pg_constraint
        WHERE conrelid='public.sanity_oa_data'::regclass AND conname='sanity_oa_workspace_scope_v6'
              AND contype='c' AND convalidated AND NOT connoinherit;
    EXECUTE format($scope$
        ALTER TABLE public.sanity_oa_data
            DROP CONSTRAINT sanity_sso_kind_probe,
            DROP CONSTRAINT sanity_oa_kind_v6,
            DROP CONSTRAINT sanity_oa_workspace_scope_v6,
            ADD CONSTRAINT sanity_oa_kind_v7 CHECK (record_type IN (
                'RECEIVED_BUNDLE','REVIEW_UNIT','REVIEW_EVENT','REPORT',
                'CLUSTER_STATE','CLUSTER_JOB','CLUSTER_PART','CLUSTER_MEMBER','CLUSTER_EVENT','CLUSTER_RULE',
                'WS_CONFIG','WS_USER','WS_SESSION','WS_PROJECT','WS_BLOCK','WS_VERSION','WS_POR','WS_POR_EVENT','WS_REPORT',
                'WS_REVIEW','WS_REVIEW_EVENT','WS_REVIEW_PROPOSAL',
                'WS_CLUSTER','WS_CLUSTER_EVENT','WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL',
                'WS_SUBMISSION','WS_SOURCE_PART','SSO_IDENTITY')),
            ADD CONSTRAINT sanity_oa_workspace_scope_v7 CHECK (
                (record_type <> 'SSO_IDENTITY' AND (%s)) OR
                (record_type = 'SSO_IDENTITY'
                 AND workspace_key IS NOT NULL AND workspace_key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
                 AND project IS NULL AND run_id IS NULL AND bundle_id IS NULL
                 AND review_unit_id IS NULL AND review_revision IS NULL AND request_id IS NULL
                 AND entity_key IS NULL AND classification_revision IS NULL
                 AND payload_bytes IS NULL AND payload_sha256 IS NULL
                 AND workspace_project_ref IS NULL AND workspace_block_ref IS NULL AND workspace_version_ref IS NULL
                 AND workspace_review_report_ref IS NULL AND workspace_review_version_ref IS NULL
                 AND workspace_review_event_ref IS NULL
                 AND workspace_pipeline_report_ref IS NULL AND workspace_pipeline_version_ref IS NULL
                 AND workspace_pipeline_event_ref IS NULL AND workspace_pipeline_cluster_ref IS NULL))
    $scope$, old_scope);
END
$migration$;

-- CASE isolates timestamp casts from all legacy records; COALESCE rejects
-- missing/JSON-null mandatory fields instead of letting SQL UNKNOWN pass CHECK.
ALTER TABLE public.sanity_oa_data ADD CONSTRAINT sanity_oa_sso_identity_v7 CHECK (
    CASE WHEN record_type = 'SSO_IDENTITY' THEN COALESCE(
        payload ?& ARRAY['issuer','subject','display_name','first_seen_at']
        AND payload - ARRAY['issuer','subject','display_name','first_seen_at'] = '{}'::jsonb
        AND jsonb_typeof(payload->'issuer') = 'string'
        AND length(payload->>'issuer') BETWEEN 1 AND 512
        AND payload->>'issuer' = btrim(payload->>'issuer')
        AND payload->>'issuer' !~ '[[:cntrl:]]'
        AND jsonb_typeof(payload->'subject') = 'string'
        AND payload->>'subject' = workspace_key
        AND jsonb_typeof(payload->'display_name') = 'string'
        AND length(payload->>'display_name') BETWEEN 1 AND 100
        AND payload->>'display_name' = btrim(payload->>'display_name')
        AND payload->>'display_name' !~ '[[:cntrl:]]'
        AND jsonb_typeof(payload->'first_seen_at') = 'string'
        AND payload->>'first_seen_at' ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9][.][0-9]{6}Z$'
        AND state ?& ARRAY['enabled','last_seen_at']
        AND state - ARRAY['enabled','last_seen_at'] = '{}'::jsonb
        AND jsonb_typeof(state->'enabled') = 'boolean'
        AND jsonb_typeof(state->'last_seen_at') = 'string'
        AND state->>'last_seen_at' ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9][.][0-9]{6}Z$'
        AND (state->>'last_seen_at')::timestamptz >= (payload->>'first_seen_at')::timestamptz,
        false) ELSE true END);
COMMIT;
