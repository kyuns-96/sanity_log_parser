-- DBA opt-in after 009 (independent of optional 010..013 storage extensions).
-- Two new queue kinds; four tables, existing roles/ACLs and old predicates stay.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_vwp_to_oa, public.sanity_oa_to_vwp IN ACCESS EXCLUSIVE MODE;
DO $migration$
DECLARE change RECORD; target REGCLASS; old_scope TEXT; expected TEXT;
BEGIN
    FOR change IN SELECT * FROM (VALUES
      ('public.sanity_vwp_to_oa','sanity_vwp_message_kind_v3','sanity_vwp_message_kind_v4',
       $k$'PING','ANALYSIS_BUNDLE','EVIDENCE_RESPONSE','CLUSTER_PART','CLUSTER_RESULT',
           'SOURCE_MANIFEST','SOURCE_PART','SOURCE_COMMIT'$k$, 'CLI_REVIEW_REQUEST'),
      ('public.sanity_oa_to_vwp','sanity_oa_message_kind_v3','sanity_oa_message_kind_v4',
       $k$'PING','EVIDENCE_REQUEST','CLUSTER_REQUEST','SOURCE_RECEIPT'$k$, 'CLI_REVIEW_RESPONSE')
    ) AS changes(table_name,old_name,new_name,old_values,new_value)
    LOOP
        target := change.table_name::regclass;
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT sanity_cli_probe CHECK (message_type IN (%s)) NOT VALID',
                       target,change.old_values);
        SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
            WHERE conrelid=target AND conname='sanity_cli_probe';
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=target AND conname=change.old_name
                       AND convalidated AND pg_get_expr(conbin,conrelid)=expected) THEN
            RAISE EXCEPTION 'Expected queue kind contract differs; no CLI migration committed';
        END IF;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT sanity_cli_probe, DROP CONSTRAINT %I,
            ADD CONSTRAINT %I CHECK (message_type IN (%s,%L))',target,change.old_name,
            change.new_name,change.old_values,change.new_value);
    END LOOP;
    FOR change IN SELECT * FROM (VALUES
      ('public.sanity_vwp_to_oa','sanity_vwp_message_scope_v3','sanity_vwp_message_scope_v4',
       'CLI_REVIEW_REQUEST',
       $s$run_id IS NULL AND bundle_id IS NULL AND correlation_id=payload->>'session_id'
          AND correlation_id IS NOT NULL AND payload_gzip IS NULL AND project='__cli_review__'
          AND payload->>'protocol'='human-cli-review-v1'
          AND message_id='cli-request-'||(payload->>'request_id')
          AND octet_length(payload::text)<=524288$s$),
      ('public.sanity_oa_to_vwp','sanity_oa_message_scope_v3','sanity_oa_message_scope_v4',
       'CLI_REVIEW_RESPONSE',
       $s$run_id IS NULL AND bundle_id IS NULL AND expires_at IS NOT NULL AND project='__cli_review__'
          AND payload->>'protocol'='human-cli-review-v1'
          AND message_id='cli-response-'||(payload->>'request_id')
          AND octet_length(payload::text)<=4194304$s$),
      ('public.sanity_vwp_to_oa','sanity_vwp_source_legacy_run_v3','sanity_vwp_source_legacy_run_v4',
       'CLI_REVIEW_REQUEST', 'run_id IS NULL'),
      ('public.sanity_oa_to_vwp','sanity_oa_source_legacy_run_v3','sanity_oa_source_legacy_run_v4',
       'CLI_REVIEW_RESPONSE', 'run_id IS NULL')
    ) AS changes(table_name,old_name,new_name,new_value,new_scope)
    LOOP
        target := change.table_name::regclass;
        SELECT pg_get_expr(conbin,conrelid) INTO STRICT old_scope FROM pg_constraint
            WHERE conrelid=target AND conname=change.old_name AND convalidated;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I, ADD CONSTRAINT %I CHECK (
            (message_type<>%L AND (%s)) OR (message_type=%L AND COALESCE((%s),false)))',
            target,change.old_name,change.new_name,change.new_value,old_scope,change.new_value,change.new_scope);
    END LOOP;
END
$migration$;
COMMIT;
