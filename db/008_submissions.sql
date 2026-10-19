-- Owner/DBA migration after 007. Retains four tables and restricted app grants.
-- Run only in an approved maintenance window; application code never runs DDL.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE public.sanity_vwp_data, public.sanity_oa_data,
    public.sanity_vwp_to_oa, public.sanity_oa_to_vwp IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE change RECORD; target REGCLASS; expected TEXT; old_scope TEXT;
BEGIN
    FOR change IN SELECT * FROM (VALUES
      ('public.sanity_vwp_data','record_type','sanity_vwp_kind_v2','sanity_vwp_kind_v3',
       $old$'RUN','UNIT','FILE','GROUP','EVIDENCE','CLUSTER_JOB','CLUSTER_PART'$old$,
       $new$'SUBMISSION','SOURCE_RUN','SOURCE_OUTBOX'$new$),
      ('public.sanity_oa_data','record_type','sanity_oa_kind_v5','sanity_oa_kind_v6',
       $old$'RECEIVED_BUNDLE','REVIEW_UNIT','REVIEW_EVENT','REPORT',
       'CLUSTER_STATE','CLUSTER_JOB','CLUSTER_PART','CLUSTER_MEMBER','CLUSTER_EVENT','CLUSTER_RULE',
       'WS_CONFIG','WS_USER','WS_SESSION','WS_PROJECT','WS_BLOCK','WS_VERSION','WS_POR','WS_POR_EVENT','WS_REPORT',
       'WS_REVIEW','WS_REVIEW_EVENT','WS_REVIEW_PROPOSAL',
       'WS_CLUSTER','WS_CLUSTER_EVENT','WS_GROUP_REVIEW','WS_GROUP_REVIEW_EVENT','WS_GROUP_REVIEW_PROPOSAL'$old$,
       $new$'WS_SUBMISSION','WS_SOURCE_PART'$new$),
      ('public.sanity_vwp_to_oa','message_type','sanity_vwp_message_kind_v2','sanity_vwp_message_kind_v3',
       $old$'PING','ANALYSIS_BUNDLE','EVIDENCE_RESPONSE','CLUSTER_PART','CLUSTER_RESULT'$old$,
       $new$'SOURCE_MANIFEST','SOURCE_PART','SOURCE_COMMIT'$new$),
      ('public.sanity_oa_to_vwp','message_type','sanity_oa_message_kind_v2','sanity_oa_message_kind_v3',
       $old$'PING','EVIDENCE_REQUEST','CLUSTER_REQUEST'$old$,
       $new$'SOURCE_RECEIPT'$new$)
    ) AS changes(table_name,column_name,old_name,new_name,old_values,new_values)
    LOOP
        target := change.table_name::regclass;
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT sanity_submission_probe CHECK (%I IN (%s)) NOT VALID',
                       target,change.column_name,change.old_values);
        SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
            WHERE conrelid=target AND conname='sanity_submission_probe';
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=target AND conname=change.old_name
                       AND convalidated AND pg_get_expr(conbin,conrelid)=expected) THEN
            RAISE EXCEPTION 'Expected prior kind contract differs on %; no migration committed',target;
        END IF;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT sanity_submission_probe, DROP CONSTRAINT %I,
                        ADD CONSTRAINT %I CHECK (%I IN (%s,%s))',
                       target,change.old_name,change.new_name,change.column_name,change.old_values,change.new_values);
    END LOOP;

    -- Preserve every prior scope/status predicate while isolating new control records.
    FOR change IN SELECT * FROM (VALUES
      ('public.sanity_vwp_data','sanity_vwp_status_v2','sanity_vwp_status_v3','record_type',
       $k$'SUBMISSION','SOURCE_RUN','SOURCE_OUTBOX'$k$,
       $s$(record_type='SUBMISSION' AND status IN ('VWP_ACCEPTED','PARSING','DB_QUEUED','OA_STORED','REJECTED'))
          OR (record_type IN ('SOURCE_RUN','SOURCE_OUTBOX') AND status='RECORDED')$s$),
      ('public.sanity_oa_data','sanity_oa_workspace_scope_v5','sanity_oa_workspace_scope_v6','record_type',
       $k$'WS_SUBMISSION','WS_SOURCE_PART'$k$,
       $s$workspace_key IS NOT NULL AND workspace_key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$'
          AND project IS NOT NULL AND workspace_project_ref IS NOT NULL AND workspace_block_ref IS NOT NULL
          AND run_id IS NULL AND bundle_id IS NULL AND review_unit_id IS NULL AND review_revision IS NULL
          AND request_id IS NULL AND entity_key IS NULL AND classification_revision IS NULL
          AND payload_bytes IS NULL AND workspace_version_ref IS NULL$s$),
      ('public.sanity_vwp_to_oa','sanity_vwp_message_scope_v2','sanity_vwp_message_scope_v3','message_type',
       $k$'SOURCE_MANIFEST','SOURCE_PART','SOURCE_COMMIT'$k$,
       $s$run_id IS NULL AND bundle_id IS NULL AND correlation_id IS NOT NULL AND payload_gzip IS NOT NULL$s$),
      ('public.sanity_oa_to_vwp','sanity_oa_message_scope_v2','sanity_oa_message_scope_v3','message_type',
       $k$'SOURCE_RECEIPT'$k$,
       $s$run_id IS NULL AND bundle_id IS NULL AND expires_at IS NULL$s$)
    ) AS changes(table_name,old_name,new_name,column_name,new_values,new_scope)
    LOOP
        target := change.table_name::regclass;
        SELECT pg_get_expr(conbin,conrelid) INTO STRICT old_scope FROM pg_constraint
            WHERE conrelid=target AND conname=change.old_name AND convalidated;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I, ADD CONSTRAINT %I CHECK (
            (%I NOT IN (%s) AND (%s)) OR (%I IN (%s) AND (%s)))',
            target,change.old_name,change.new_name,change.column_name,change.new_values,old_scope,
            change.column_name,change.new_values,change.new_scope);
    END LOOP;
END
$migration$;

-- The original form's parent CHECK may have a GUI-assigned name.
DO $parent$
DECLARE expected TEXT; names TEXT[]; old_expression TEXT;
BEGIN
    old_expression := $expr$(record_type='RUN' AND record_id=analysis_id AND parent_id IS NULL AND review_unit_id IS NULL)
        OR (record_type<>'RUN' AND parent_id IS NOT NULL AND parent_id<>record_id)$expr$;
    EXECUTE format('ALTER TABLE public.sanity_vwp_data ADD CONSTRAINT sanity_submission_parent_probe CHECK (%s) NOT VALID',old_expression);
    SELECT pg_get_expr(conbin,conrelid) INTO STRICT expected FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND conname='sanity_submission_parent_probe';
    SELECT array_agg(conname::text) INTO names FROM pg_constraint
        WHERE conrelid='public.sanity_vwp_data'::regclass AND contype='c' AND convalidated
        AND conname<>'sanity_submission_parent_probe' AND pg_get_expr(conbin,conrelid)=expected;
    IF coalesce(cardinality(names),0)<>1 THEN
        RAISE EXCEPTION 'Original VWP parent contract differs; no migration committed';
    END IF;
    EXECUTE format('ALTER TABLE public.sanity_vwp_data DROP CONSTRAINT sanity_submission_parent_probe,
        DROP CONSTRAINT %I, ADD CONSTRAINT sanity_vwp_submission_parent_v3 CHECK (
        (record_type NOT IN (''SUBMISSION'',''SOURCE_RUN'',''SOURCE_OUTBOX'') AND analysis_id IS NOT NULL AND (%s))
        OR (record_type IN (''SUBMISSION'',''SOURCE_RUN'',''SOURCE_OUTBOX'') AND analysis_id IS NULL
            AND parent_id IS NULL AND review_unit_id IS NULL))',names[1],old_expression);
END
$parent$;

ALTER TABLE public.sanity_vwp_data ALTER COLUMN analysis_id DROP NOT NULL,
    ADD CONSTRAINT sanity_vwp_source_immutable_v3 CHECK (
        record_type NOT IN ('SOURCE_RUN','SOURCE_OUTBOX') OR (state='{}'::jsonb AND version=0 AND updated_at=created_at)),
    ADD CONSTRAINT sanity_vwp_source_identity_v3 CHECK (record_type<>'SOURCE_RUN' OR coalesce(
        payload->>'execution_id'=entity_key AND payload->>'unit_key' ~ '^su-[0-9a-f]{64}$'
        AND payload->>'raw_sha256' ~ '^[0-9a-f]{64}$'
        AND (payload->'source'->>'execution_order')::bigint>=0,false));
CREATE UNIQUE INDEX sanity_vwp_submission_key_uq ON public.sanity_vwp_data(record_type,entity_key)
    WHERE record_type IN ('SUBMISSION','SOURCE_RUN','SOURCE_OUTBOX');
CREATE UNIQUE INDEX sanity_vwp_source_order_uq ON public.sanity_vwp_data(
    (payload->>'unit_key'),((payload->'source'->>'execution_order')::bigint)) WHERE record_type='SOURCE_RUN';
CREATE INDEX sanity_vwp_source_outbox_idx ON public.sanity_vwp_data((payload->>'submission_id'),(payload->>'position'))
    WHERE record_type='SOURCE_OUTBOX';
CREATE INDEX sanity_vwp_source_record_idx ON public.sanity_vwp_data(analysis_id,((payload->>'record_index')::bigint))
    WHERE record_type='EVIDENCE';

ALTER TABLE public.sanity_oa_data ADD CONSTRAINT sanity_workspace_source_immutable_v6 CHECK (
    record_type<>'WS_SOURCE_PART' OR (state='{}'::jsonb AND version=0 AND updated_at=created_at));
CREATE INDEX sanity_workspace_source_part_idx ON public.sanity_oa_data((payload->>'report_id'),
    ((payload->>'part_index')::bigint)) WHERE record_type='WS_SOURCE_PART';
CREATE INDEX sanity_workspace_submission_idx ON public.sanity_oa_data(workspace_block_ref,created_at DESC)
    WHERE record_type='WS_SUBMISSION';
CREATE INDEX sanity_workspace_current_source_idx ON public.sanity_oa_data((payload->>'source_unit_key'),
    ((payload->'source'->>'execution_order')::bigint) DESC,(payload->>'analysis_created_at') DESC)
    WHERE record_type='WS_REPORT' AND payload->>'source_storage'='PARTS_V2';

ALTER TABLE public.sanity_vwp_to_oa ALTER COLUMN run_id DROP NOT NULL,
    ADD CONSTRAINT sanity_vwp_source_legacy_run_v3 CHECK (
        message_type IN ('SOURCE_MANIFEST','SOURCE_PART','SOURCE_COMMIT') OR run_id IS NOT NULL);
ALTER TABLE public.sanity_oa_to_vwp ALTER COLUMN run_id DROP NOT NULL,
    ADD CONSTRAINT sanity_oa_source_legacy_run_v3 CHECK (message_type='SOURCE_RECEIPT' OR run_id IS NOT NULL);
COMMIT;
