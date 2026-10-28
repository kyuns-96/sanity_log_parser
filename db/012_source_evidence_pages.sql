-- Optional owner/DBA migration after 011. New V2 analyses may use bounded pages.
-- Keeps every old row, four tables, roles, grants, original bytes and wire JSON.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_vwp_data IN ACCESS EXCLUSIVE MODE;
DO $pages$
DECLARE expected TEXT; previous TEXT;
BEGIN
    IF (SELECT count(*) FROM pg_constraint WHERE conrelid='public.sanity_vwp_data'::regclass
        AND contype='c' AND convalidated AND NOT connoinherit AND conname IN (
            'sanity_vwp_kind_v4','sanity_vwp_status_v4','sanity_vwp_submission_parent_v4',
            'sanity_vwp_raw_scope_v4','sanity_vwp_raw_payload_v4','sanity_vwp_raw_immutable_v4',
            'sanity_vwp_payload_storage_v1')) <> 7 THEN
        RAISE EXCEPTION 'Validated 010 and 011 are required; no page migration committed';
    END IF;
    ALTER TABLE public.sanity_vwp_data ADD CONSTRAINT sanity_pages_kind_probe CHECK (record_type IN (
        'RUN','UNIT','FILE','GROUP','EVIDENCE','CLUSTER_JOB','CLUSTER_PART',
        'SUBMISSION','SOURCE_RUN','SOURCE_OUTBOX','RAW_SUBMISSION','RAW_FILE','RAW_CHUNK')) NOT VALID;
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname='sanity_pages_kind_probe';
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.sanity_vwp_data'::regclass
        AND conname='sanity_vwp_kind_v4' AND pg_get_expr(conbin,conrelid)=expected) THEN
        RAISE EXCEPTION 'Expected 010 VWP kind contract differs; no page migration committed';
    END IF;
    ALTER TABLE public.sanity_vwp_data DROP CONSTRAINT sanity_pages_kind_probe,
        DROP CONSTRAINT sanity_vwp_kind_v4,
        ADD CONSTRAINT sanity_vwp_kind_v5 CHECK (record_type IN (
            'RUN','UNIT','FILE','GROUP','EVIDENCE','CLUSTER_JOB','CLUSTER_PART',
            'SUBMISSION','SOURCE_RUN','SOURCE_OUTBOX','RAW_SUBMISSION','RAW_FILE','RAW_CHUNK',
            'SOURCE_EVIDENCE_PAGE'));
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT previous FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname='sanity_vwp_status_v4';
    EXECUTE format($status$
        ALTER TABLE public.sanity_vwp_data DROP CONSTRAINT sanity_vwp_status_v4,
        ADD CONSTRAINT sanity_vwp_status_v5 CHECK (
            (record_type<>'SOURCE_EVIDENCE_PAGE' AND (%s))
            OR (record_type='SOURCE_EVIDENCE_PAGE' AND status='RECORDED'))
    $status$,previous);
END
$pages$;
ALTER TABLE public.sanity_vwp_data
    ADD COLUMN page_bytes BYTEA,
    ALTER COLUMN page_bytes SET STORAGE EXTERNAL,
    ADD CONSTRAINT sanity_vwp_source_page_v1 CHECK (
        CASE WHEN record_type='SOURCE_EVIDENCE_PAGE' THEN COALESCE(
            page_bytes IS NOT NULL AND octet_length(page_bytes) BETWEEN 1 AND 4194304
            AND raw_bytes IS NULL AND raw_sha256 IS NULL AND payload_bytes IS NULL
            AND review_unit_id IS NOT NULL AND analysis_id IS NOT NULL AND parent_id IS NOT NULL
            AND state='{}'::jsonb AND version=0 AND updated_at=created_at
            AND payload->>'codec'='gzip-source-evidence-v1'
            AND payload->>'sha256'=encode(sha256(page_bytes),'hex')
            AND jsonb_typeof(payload->'uncompressed_bytes')='number'
            AND (payload->>'uncompressed_bytes') ~ '^[1-9][0-9]{0,6}$'
            AND (payload->>'uncompressed_bytes')::bigint BETWEEN 1 AND 4194304
            AND jsonb_typeof(payload->'record_index')='number'
            AND (payload->>'record_index') ~ '^[1-9][0-9]{0,17}$'
            AND jsonb_typeof(payload->'record_end')='number'
            AND (payload->>'record_end') ~ '^[1-9][0-9]{0,17}$'
            AND jsonb_typeof(payload->'record_count')='number'
            AND (payload->>'record_count') ~ '^[1-9][0-9]{0,2}$'
            AND (payload->>'record_count')::int BETWEEN 1 AND 256
            AND (payload->>'record_end')::bigint=(payload->>'record_index')::bigint
                +(payload->>'record_count')::int-1
            AND entity_key='sep.'||(payload->>'record_index')
            AND jsonb_typeof(payload->'status_counts')='object'
            AND (payload->'status_counts') - ARRAY['PASS','FAIL','UNKNOWN']='{}'::jsonb
            AND jsonb_typeof(payload#>'{status_counts,PASS}')='number'
            AND jsonb_typeof(payload#>'{status_counts,FAIL}')='number'
            AND jsonb_typeof(payload#>'{status_counts,UNKNOWN}')='number'
            AND (payload#>>'{status_counts,PASS}') ~ '^[0-9]{1,3}$'
            AND (payload#>>'{status_counts,FAIL}') ~ '^[0-9]{1,3}$'
            AND (payload#>>'{status_counts,UNKNOWN}') ~ '^[0-9]{1,3}$'
            AND (payload#>>'{status_counts,PASS}')::int+(payload#>>'{status_counts,FAIL}')::int
                +(payload#>>'{status_counts,UNKNOWN}')::int=(payload->>'record_count')::int
            AND payload - ARRAY['codec','sha256','uncompressed_bytes','record_index','record_end',
                                'record_count','status_counts']='{}'::jsonb,false)
        ELSE page_bytes IS NULL END);
CREATE UNIQUE INDEX sanity_vwp_source_page_start_uq ON public.sanity_vwp_data(
    analysis_id,((payload->>'record_index')::bigint)) WHERE record_type='SOURCE_EVIDENCE_PAGE';
COMMIT;
