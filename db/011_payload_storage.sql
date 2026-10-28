-- Optional, owner/DBA-only migration after 010; PostgreSQL 12.1+.
-- Keeps four tables, original raw bytes, all IDs/FKs/UNIQUE/ACLs and wire JSON.
-- Existing rows stay plaintext. New V2 source writers may store a lossless gzip
-- payload behind indexed JSON metadata. Deploy the compatible VWP code first.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_vwp_data IN ACCESS EXCLUSIVE MODE;
DO $prior$
BEGIN
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND contype='c'
          AND convalidated AND NOT connoinherit AND conname IN (
            'sanity_vwp_kind_v4','sanity_vwp_status_v4','sanity_vwp_submission_parent_v4',
            'sanity_vwp_raw_scope_v4','sanity_vwp_raw_payload_v4','sanity_vwp_raw_immutable_v4')) <> 6 THEN
        RAISE EXCEPTION 'Validated migration 010 is required; no storage change committed';
    END IF;
END
$prior$;
ALTER TABLE public.sanity_vwp_data
    ADD COLUMN payload_bytes BYTEA,
    ALTER COLUMN payload_bytes SET STORAGE EXTERNAL,
    ALTER COLUMN raw_bytes SET STORAGE EXTENDED,
    ADD CONSTRAINT sanity_vwp_payload_storage_v1 CHECK (
        (payload_bytes IS NULL AND NOT (payload ? '__vwp_payload__'))
        OR COALESCE(
            payload_bytes IS NOT NULL AND record_type IN ('EVIDENCE','SOURCE_OUTBOX')
            AND octet_length(payload_bytes) BETWEEN 1 AND 67108864
            AND jsonb_typeof(payload->'__vwp_payload__')='object'
            AND payload#>>'{__vwp_payload__,codec}' IN ('gzip-json-v1','gzip-evidence-v1')
            AND (payload#>>'{__vwp_payload__,codec}' <> 'gzip-evidence-v1'
                 OR (record_type='EVIDENCE' AND raw_bytes IS NULL
                     AND payload->>'local_evidence_ref'='db-gzip-v1:'||entity_key))
            AND payload#>>'{__vwp_payload__,sha256}'=encode(sha256(payload_bytes),'hex')
            AND jsonb_typeof(payload#>'{__vwp_payload__,uncompressed_bytes}')='number'
            AND (payload#>>'{__vwp_payload__,uncompressed_bytes}')::numeric BETWEEN 1 AND 67108864
            AND (payload#>>'{__vwp_payload__,uncompressed_bytes}')::numeric =
                trunc((payload#>>'{__vwp_payload__,uncompressed_bytes}')::numeric),false));
COMMIT;
