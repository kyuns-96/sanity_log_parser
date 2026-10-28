-- Owner/DBA opt-in after 012: retain one immutable masked SOURCE_PART body.
-- Both private stores reference the existing shared queue row. No data deletion,
-- new table/role/GRANT, cross-private read, or change to the logical packet JSON.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_vwp_data,public.sanity_oa_data,public.sanity_vwp_to_oa IN ACCESS EXCLUSIVE MODE;
DO $prior$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.sanity_vwp_data'::regclass
        AND conname='sanity_vwp_source_page_v1' AND contype='c' AND convalidated AND NOT connoinherit) THEN
        RAISE EXCEPTION 'Validated 012 is required; no shared packet storage committed';
    END IF;
END
$prior$;
ALTER TABLE public.sanity_vwp_data
    ADD COLUMN source_packet_ref VARCHAR(128),
    ADD CONSTRAINT sanity_vwp_source_packet_fk FOREIGN KEY (source_packet_ref)
        REFERENCES public.sanity_vwp_to_oa(message_id),
    ADD CONSTRAINT sanity_vwp_shared_source_v1 CHECK (
        CASE WHEN payload ? '__source_packet__' THEN COALESCE(
            record_type='SOURCE_OUTBOX' AND payload->>'kind'='SOURCE_PART'
            AND source_packet_ref IS NOT NULL AND source_packet_ref=payload->>'message_id'
            AND source_packet_ref ~ '^s-[0-9a-f]{64}$'
            AND payload_bytes IS NULL AND raw_bytes IS NULL AND raw_sha256 IS NULL
            AND jsonb_typeof(payload->'__source_packet__')='object'
            AND (payload#>>'{__source_packet__,packet_sha256}') ~ '^[0-9a-f]{64}$'
            AND (payload->'__source_packet__')-ARRAY['packet_sha256']='{}'::jsonb
            AND payload-ARRAY['submission_id','position','kind','message_id','packet','__source_packet__']='{}'::jsonb
            AND jsonb_typeof(payload->'packet')='object'
            AND (payload->'packet')-ARRAY['collection_id']='{}'::jsonb,false)
        ELSE source_packet_ref IS NULL END);
ALTER TABLE public.sanity_oa_data
    ADD COLUMN source_packet_ref VARCHAR(128),
    ADD CONSTRAINT sanity_oa_source_packet_fk FOREIGN KEY (source_packet_ref)
        REFERENCES public.sanity_vwp_to_oa(message_id),
    ADD CONSTRAINT sanity_oa_shared_source_v1 CHECK (
        CASE WHEN payload ? '__source_packet__' THEN COALESCE(
            record_type='WS_SOURCE_PART' AND source_packet_ref IS NOT NULL
            AND source_packet_ref=payload#>>'{__source_packet__,message_id}'
            AND source_packet_ref ~ '^s-[0-9a-f]{64}$'
            AND payload_bytes IS NULL AND payload_sha256 IS NULL
            AND jsonb_typeof(payload->'__source_packet__')='object'
            AND (payload#>>'{__source_packet__,packet_sha256}') ~ '^[0-9a-f]{64}$'
            AND (payload#>>'{__source_packet__,body_sha256}') ~ '^[0-9a-f]{64}$'
            AND workspace_key='sp-'||(payload#>>'{__source_packet__,body_sha256}')
            AND jsonb_typeof(payload#>'{__source_packet__,record_count}')='number'
            AND (payload#>>'{__source_packet__,record_count}') ~ '^[1-9][0-9]{0,3}$'
            AND (payload#>>'{__source_packet__,record_count}')::int BETWEEN 1 AND 1000
            AND (payload->'__source_packet__')-ARRAY['message_id','packet_sha256','body_sha256','record_count']='{}'::jsonb
            AND payload-ARRAY['report_id','part_index','__source_packet__']='{}'::jsonb,false)
        ELSE source_packet_ref IS NULL END);
ALTER TABLE public.sanity_vwp_to_oa
    ADD CONSTRAINT sanity_vwp_source_gate_v1 CHECK (
        NOT (payload ? '__source_commit_gate__') OR COALESCE(
            message_type='SOURCE_PART' AND payload->'__source_commit_gate__'='true'::jsonb
            AND payload->'schema_version'='2'::jsonb AND payload->>'protocol'='source-collection-v2'
            AND payload->>'submission_id'=correlation_id
            AND (payload->>'content_sha256') ~ '^[0-9a-f]{64}$'
            AND (payload->>'collection_id') ~ '^sc-[0-9a-f]{64}$'
            AND payload-ARRAY['schema_version','protocol','submission_id','content_sha256','collection_id',
                              '__source_commit_gate__']='{}'::jsonb,false));
CREATE INDEX sanity_vwp_source_commit_gate_idx ON public.sanity_vwp_to_oa(correlation_id,project)
    WHERE message_type='SOURCE_COMMIT';
COMMIT;
