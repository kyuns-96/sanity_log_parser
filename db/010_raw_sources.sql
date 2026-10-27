-- DBA migration after 009: raw report storage inside the existing private VWP table.
-- No new table, role, grant, DELETE privilege or queue message kind.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_vwp_data IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE expected TEXT; previous TEXT; raw_names TEXT[];
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.sanity_oa_data'::regclass
        AND conname='sanity_oa_workspace_scope_v7' AND contype='c' AND convalidated AND NOT connoinherit) THEN
        RAISE EXCEPTION 'Apply 009 before raw source storage; no migration committed';
    END IF;
    ALTER TABLE public.sanity_vwp_data ADD CONSTRAINT sanity_raw_kind_probe CHECK (record_type IN (
        'RUN','UNIT','FILE','GROUP','EVIDENCE','CLUSTER_JOB','CLUSTER_PART',
        'SUBMISSION','SOURCE_RUN','SOURCE_OUTBOX')) NOT VALID;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname='sanity_raw_kind_probe';
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.sanity_vwp_data'::regclass
        AND conname='sanity_vwp_kind_v3' AND contype='c' AND convalidated AND NOT connoinherit
        AND pg_get_expr(conbin,conrelid)=expected) THEN
        RAISE EXCEPTION 'Expected 008 VWP kind contract differs; no migration committed';
    END IF;
    ALTER TABLE public.sanity_vwp_data DROP CONSTRAINT sanity_raw_kind_probe,
        DROP CONSTRAINT sanity_vwp_kind_v3,
        ADD CONSTRAINT sanity_vwp_kind_v4 CHECK (record_type IN (
            'RUN','UNIT','FILE','GROUP','EVIDENCE','CLUSTER_JOB','CLUSTER_PART',
            'SUBMISSION','SOURCE_RUN','SOURCE_OUTBOX','RAW_SUBMISSION','RAW_FILE','RAW_CHUNK'));

    SELECT pg_get_expr(conbin,conrelid) INTO STRICT previous FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname='sanity_vwp_status_v3'
        AND contype='c' AND convalidated AND NOT connoinherit;
    EXECUTE format($status$
        ALTER TABLE public.sanity_vwp_data DROP CONSTRAINT sanity_vwp_status_v3,
        ADD CONSTRAINT sanity_vwp_status_v4 CHECK (
            (record_type NOT IN ('RAW_SUBMISSION','RAW_FILE','RAW_CHUNK') AND (%s))
            OR (record_type='RAW_SUBMISSION' AND status IN ('UPLOADING','VWP_UPLOADED'))
            OR (record_type IN ('RAW_FILE','RAW_CHUNK') AND status='RECORDED'))
    $status$,previous);

    SELECT pg_get_expr(conbin,conrelid) INTO STRICT previous FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname='sanity_vwp_submission_parent_v3'
        AND contype='c' AND convalidated AND NOT connoinherit;
    EXECUTE format($parent$
        ALTER TABLE public.sanity_vwp_data DROP CONSTRAINT sanity_vwp_submission_parent_v3,
        ADD CONSTRAINT sanity_vwp_submission_parent_v4 CHECK (
            (record_type NOT IN ('RAW_SUBMISSION','RAW_FILE','RAW_CHUNK') AND (%s))
            OR (record_type IN ('RAW_SUBMISSION','RAW_FILE','RAW_CHUNK')
                AND analysis_id IS NULL AND parent_id IS NULL AND review_unit_id IS NULL))
    $parent$,previous);

    -- Locate the original form CHECK by expression; GUI-assigned names may differ.
    ALTER TABLE public.sanity_vwp_data ADD CONSTRAINT sanity_raw_scope_probe CHECK (
        record_type='EVIDENCE' OR (raw_bytes IS NULL AND raw_sha256 IS NULL)) NOT VALID;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname='sanity_raw_scope_probe';
    SELECT array_agg(conname::text) INTO raw_names FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname<>'sanity_raw_scope_probe'
        AND contype='c' AND convalidated AND NOT connoinherit AND pg_get_expr(conbin,conrelid)=expected;
    IF coalesce(cardinality(raw_names),0)<>1 THEN
        RAISE EXCEPTION 'Expected VWP byte scope differs; no migration committed';
    END IF;
    EXECUTE format($raw$
        ALTER TABLE public.sanity_vwp_data DROP CONSTRAINT sanity_raw_scope_probe, DROP CONSTRAINT %I,
        ADD CONSTRAINT sanity_vwp_raw_scope_v4 CHECK (
            record_type IN ('EVIDENCE','RAW_CHUNK') OR (raw_bytes IS NULL AND raw_sha256 IS NULL))
    $raw$,raw_names[1]);
END
$migration$;

ALTER TABLE public.sanity_vwp_data
    ADD CONSTRAINT sanity_vwp_raw_immutable_v4 CHECK (
        record_type NOT IN ('RAW_FILE','RAW_CHUNK') OR
        (state='{}'::jsonb AND version=0 AND updated_at=created_at)),
    ADD CONSTRAINT sanity_vwp_raw_payload_v4 CHECK (
        CASE WHEN record_type IN ('RAW_SUBMISSION','RAW_FILE','RAW_CHUNK') THEN COALESCE(
            payload->'schema_version'='1'::jsonb AND payload->>'protocol'='raw-source-v1'
            AND CASE record_type
                WHEN 'RAW_SUBMISSION' THEN
                    state='{}'::jsonb
                    AND entity_key ~ '^raws[.][0-9a-f-]{36}$'
                    AND jsonb_typeof(payload->'node_id')='string' AND length(payload->>'node_id') BETWEEN 1 AND 100
                    AND jsonb_typeof(payload->'request')='object'
                    AND jsonb_typeof(payload->'request'->'submission')='object'
                    AND jsonb_typeof(payload->'request'->'manifest')='object'
                    AND entity_key='raws.'||(payload->'request'->'submission'->>'submission_id')
                    AND payload - ARRAY['schema_version','protocol','node_id','request']='{}'::jsonb
                WHEN 'RAW_FILE' THEN
                    jsonb_typeof(payload->'submission_id')='string'
                    AND payload->>'submission_id' ~ '^[0-9a-f-]{36}$'
                    AND jsonb_typeof(payload->'file_index')='number' AND payload->>'file_index' ~ '^[0-9]{1,5}$'
                    AND entity_key='rawf.'||(payload->>'submission_id')||'.'||lpad(payload->>'file_index',5,'0')
                    AND jsonb_typeof(payload->'file')='object'
                    AND jsonb_typeof(payload->'chunks')='array'
                    AND jsonb_array_length(payload->'chunks')<=16384
                    AND payload - ARRAY['schema_version','protocol','submission_id','file_index','file','chunks']='{}'::jsonb
                WHEN 'RAW_CHUNK' THEN
                    raw_bytes IS NOT NULL AND raw_sha256 IS NOT NULL
                    AND octet_length(raw_bytes) BETWEEN 1 AND 1048576
                    AND entity_key='rawc.'||raw_sha256 AND payload->>'sha256'=raw_sha256
                    AND jsonb_typeof(payload->'size_bytes')='number'
                    AND payload->'size_bytes'=to_jsonb(octet_length(raw_bytes))
                    AND payload - ARRAY['schema_version','protocol','sha256','size_bytes']='{}'::jsonb
                ELSE false END, false)
        ELSE true END);

CREATE UNIQUE INDEX sanity_vwp_raw_key_uq ON public.sanity_vwp_data(record_type,entity_key)
    WHERE record_type IN ('RAW_SUBMISSION','RAW_FILE','RAW_CHUNK');
CREATE INDEX sanity_vwp_raw_file_idx ON public.sanity_vwp_data((payload->>'submission_id'))
    WHERE record_type='RAW_FILE';
CREATE INDEX sanity_vwp_raw_pending_idx ON public.sanity_vwp_data((payload->>'node_id'),created_at)
    WHERE record_type='RAW_SUBMISSION';
COMMIT;
