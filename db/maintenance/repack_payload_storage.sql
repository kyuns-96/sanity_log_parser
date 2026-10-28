-- OPTIONAL OWNER/DBA maintenance after 011 and a verified payload_compaction.
-- NOT part of application startup or the empty-DB portal migration plan.
-- Stop writers, verify a recoverable backup and allow full-table/index/WAL space.
-- A full rewrite holds ACCESS EXCLUSIVE until commit. It never deletes logical
-- rows or changes IDs, source bytes, state, revisions or timestamps.
-- Replace all public. references for a custom schema. Verify logical hashes
-- before/after and measure pg_total_relation_size; app code never runs this SQL.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_vwp_data IN ACCESS EXCLUSIVE MODE;
DO $prior$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass
          AND conname='sanity_vwp_payload_storage_v1' AND contype='c'
          AND convalidated AND NOT connoinherit) THEN
        RAISE EXCEPTION 'Validated migration 011 is required; no rewrite committed';
    END IF;
END
$prior$;
ALTER TABLE public.sanity_vwp_data
    ALTER COLUMN payload TYPE JSONB USING (payload::text::jsonb),
    ALTER COLUMN payload_bytes TYPE BYTEA USING (payload_bytes || ''::bytea),
    ALTER COLUMN raw_bytes TYPE BYTEA USING (raw_bytes || ''::bytea),
    ALTER COLUMN payload_bytes SET STORAGE EXTERNAL,
    ALTER COLUMN raw_bytes SET STORAGE EXTENDED;
COMMIT;
