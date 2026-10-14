-- 이미 신청한 db/form/의 4개 테이블용 증분 변경. form/002/003은 변경하지 않는다.
-- owner/DBA가 기존 002/003 적용 후, bridge 작업자를 중지한 점검 시간에 한 번 실행한다.
-- public이 아닌 스키마라면 이 파일의 모든 public. 참조를 실제 스키마로 바꾼다.
-- 테이블/기존 행을 삭제하거나 다시 만들지 않는다. 실패하면 전체 ROLLBACK.
-- 재실행용 IF NOT EXISTS로 다른 스키마를 묵인하지 않는다. 이미 적용했다면 실행하지 않는다.
BEGIN;
SET LOCAL lock_timeout = '5s';

LOCK TABLE public.sanity_vwp_data, public.sanity_oa_data,
    public.sanity_vwp_to_oa, public.sanity_oa_to_vwp IN ACCESS EXCLUSIVE MODE;

ALTER TABLE public.sanity_oa_data
    ADD COLUMN entity_key VARCHAR(256),
    ADD COLUMN classification_revision BIGINT
        CHECK (classification_revision IS NULL OR classification_revision > 0);

-- GUI가 생성한 CHECK 이름은 가정하지 않는다.
-- 원래 form의 식을 PostgreSQL 자신에게 파싱시켜 기존 식과 정확히 비교한다.
-- 일치하는 검증 완료 CHECK가 정확히 하나가 아니면 자동 보정하지 않고 중단한다.
-- probe는 동일 테이블의 임시 CHECK일 뿐 추가 테이블/함수/확장 모듈이 아니다.
DO $migration$
DECLARE
    change RECORD;
    target REGCLASS;
    old_expression TEXT;
    old_names TEXT[];
BEGIN
    FOR change IN SELECT * FROM (VALUES
        ('public.sanity_vwp_data', 'sanity_vwp_kind_v2',
         $old$record_type IN ('RUN', 'UNIT', 'FILE', 'GROUP', 'EVIDENCE')$old$,
         $new$record_type IN ('RUN', 'UNIT', 'FILE', 'GROUP', 'EVIDENCE', 'CLUSTER_JOB', 'CLUSTER_PART')$new$),
        ('public.sanity_vwp_data', 'sanity_vwp_status_v2',
         $old$(record_type = 'RUN' AND status IN ('BUILDING', 'SEALED', 'FAILED'))
            OR (record_type = 'UNIT' AND status IN ('PENDING', 'COMPLETE', 'PARTIAL', 'MISSING', 'UNSUPPORTED', 'NOT_APPLICABLE'))
            OR (record_type IN ('FILE', 'GROUP', 'EVIDENCE') AND status = 'RECORDED')$old$,
         $new$(record_type = 'RUN' AND status IN ('BUILDING', 'SEALED', 'FAILED'))
            OR (record_type = 'UNIT' AND status IN ('PENDING', 'COMPLETE', 'PARTIAL', 'MISSING', 'UNSUPPORTED', 'NOT_APPLICABLE'))
            OR (record_type IN ('FILE', 'GROUP', 'EVIDENCE', 'CLUSTER_PART') AND status = 'RECORDED')
            OR (record_type = 'CLUSTER_JOB' AND status IN ('WORKING', 'COMPLETE', 'FAILED'))$new$),
        ('public.sanity_vwp_data', 'sanity_vwp_immutable_v2',
         $old$record_type NOT IN ('FILE', 'EVIDENCE') OR (state = '{}'::jsonb AND version = 0 AND updated_at = created_at)$old$,
         $new$record_type NOT IN ('FILE', 'EVIDENCE', 'CLUSTER_PART') OR (state = '{}'::jsonb AND version = 0 AND updated_at = created_at)$new$),
        ('public.sanity_oa_data', 'sanity_oa_kind_v2',
         $old$record_type IN ('RECEIVED_BUNDLE', 'REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT')$old$,
         $new$record_type IN ('RECEIVED_BUNDLE', 'REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT',
             'CLUSTER_STATE', 'CLUSTER_JOB', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE')$new$),
        ('public.sanity_oa_data', 'sanity_oa_unit_scope_v2',
         $old$(record_type = 'REVIEW_UNIT' AND review_unit_id IS NOT NULL)
            OR (record_type <> 'REVIEW_UNIT' AND review_unit_id IS NULL)$old$,
         $new$(record_type IN ('REVIEW_UNIT', 'CLUSTER_STATE', 'CLUSTER_JOB', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE') AND review_unit_id IS NOT NULL)
            OR (record_type NOT IN ('REVIEW_UNIT', 'CLUSTER_STATE', 'CLUSTER_JOB', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE') AND review_unit_id IS NULL)$new$),
        ('public.sanity_oa_data', 'sanity_oa_request_v2',
         $old$(record_type = 'REVIEW_EVENT' AND
             ((review_revision = 0 AND request_id IS NULL) OR (review_revision > 0 AND request_id IS NOT NULL)))
            OR (record_type <> 'REVIEW_EVENT' AND request_id IS NULL)$old$,
         $new$(record_type = 'REVIEW_EVENT' AND
             ((review_revision = 0 AND request_id IS NULL) OR (review_revision > 0 AND request_id IS NOT NULL)))
            OR (record_type = 'CLUSTER_EVENT' AND request_id IS NOT NULL)
            OR (record_type NOT IN ('REVIEW_EVENT', 'CLUSTER_EVENT') AND request_id IS NULL)$new$),
        ('public.sanity_oa_data', 'sanity_oa_immutable_v2',
         $old$record_type NOT IN ('REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT')
            OR (state = '{}'::jsonb AND version = 0 AND updated_at = created_at)$old$,
         $new$record_type NOT IN ('REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE')
            OR (state = '{}'::jsonb AND version = 0 AND updated_at = created_at)$new$),
        ('public.sanity_oa_to_vwp', 'sanity_oa_message_kind_v2',
         $old$message_type IN ('PING', 'EVIDENCE_REQUEST')$old$,
         $new$message_type IN ('PING', 'EVIDENCE_REQUEST', 'CLUSTER_REQUEST')$new$),
        ('public.sanity_oa_to_vwp', 'sanity_oa_message_scope_v2',
         $old$(message_type = 'PING' AND bundle_id IS NULL AND expires_at IS NULL)
            OR (message_type = 'EVIDENCE_REQUEST' AND bundle_id IS NOT NULL AND expires_at IS NOT NULL AND expires_at > created_at)$old$,
         $new$(message_type = 'PING' AND bundle_id IS NULL AND expires_at IS NULL)
            OR (message_type IN ('EVIDENCE_REQUEST', 'CLUSTER_REQUEST') AND bundle_id IS NOT NULL AND expires_at IS NOT NULL AND expires_at > created_at)$new$),
        ('public.sanity_vwp_to_oa', 'sanity_vwp_message_kind_v2',
         $old$message_type IN ('PING', 'ANALYSIS_BUNDLE', 'EVIDENCE_RESPONSE')$old$,
         $new$message_type IN ('PING', 'ANALYSIS_BUNDLE', 'EVIDENCE_RESPONSE', 'CLUSTER_PART', 'CLUSTER_RESULT')$new$),
        ('public.sanity_vwp_to_oa', 'sanity_vwp_message_scope_v2',
         $old$(message_type = 'PING' AND bundle_id IS NULL AND correlation_id IS NULL AND payload_gzip IS NULL)
            OR (message_type = 'ANALYSIS_BUNDLE' AND bundle_id IS NOT NULL AND correlation_id IS NULL AND payload_gzip IS NOT NULL)
            OR (message_type = 'EVIDENCE_RESPONSE' AND bundle_id IS NOT NULL AND correlation_id IS NOT NULL
                AND COALESCE(payload->>'result' IN ('FULFILLED', 'REJECTED', 'EXPIRED'), false)
                AND ((payload->>'result' = 'FULFILLED') = (payload_gzip IS NOT NULL)))$old$,
         $new$(message_type = 'PING' AND bundle_id IS NULL AND correlation_id IS NULL AND payload_gzip IS NULL)
            OR (message_type = 'ANALYSIS_BUNDLE' AND bundle_id IS NOT NULL AND correlation_id IS NULL AND payload_gzip IS NOT NULL)
            OR (message_type = 'EVIDENCE_RESPONSE' AND bundle_id IS NOT NULL AND correlation_id IS NOT NULL
                AND COALESCE(payload->>'result' IN ('FULFILLED', 'REJECTED', 'EXPIRED'), false)
                AND ((payload->>'result' = 'FULFILLED') = (payload_gzip IS NOT NULL)))
            OR (message_type = 'CLUSTER_PART' AND bundle_id IS NOT NULL AND correlation_id IS NOT NULL AND payload_gzip IS NOT NULL)
            OR (message_type = 'CLUSTER_RESULT' AND bundle_id IS NOT NULL AND correlation_id IS NOT NULL AND payload_gzip IS NULL)$new$)
    ) AS changes(table_name, new_name, old_check, new_check)
    LOOP
        target := change.table_name::regclass;
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT sanity_migration_probe CHECK (%s) NOT VALID', target, change.old_check);
        SELECT pg_get_expr(conbin, conrelid) INTO STRICT old_expression
            FROM pg_constraint WHERE conrelid = target AND conname = 'sanity_migration_probe';
        SELECT array_agg(conname::text) INTO old_names
            FROM pg_constraint
            WHERE conrelid = target AND contype = 'c' AND convalidated AND NOT connoinherit
                AND conname <> 'sanity_migration_probe' AND pg_get_expr(conbin, conrelid) = old_expression;
        IF coalesce(cardinality(old_names), 0) <> 1 THEN
            RAISE EXCEPTION 'Original form CHECK mismatch on %, replacement %; no changes committed', target, change.new_name;
        END IF;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT sanity_migration_probe, DROP CONSTRAINT %I, ADD CONSTRAINT %I CHECK (%s)',
            target, old_names[1], change.new_name, change.new_check);
    END LOOP;
END
$migration$;

ALTER TABLE public.sanity_vwp_data ADD CONSTRAINT sanity_vwp_cluster_unit_v2
    CHECK (record_type NOT IN ('CLUSTER_JOB', 'CLUSTER_PART') OR review_unit_id IS NOT NULL);
ALTER TABLE public.sanity_oa_data
    ADD CONSTRAINT sanity_oa_cluster_key_v2 CHECK (
        (record_type IN ('CLUSTER_STATE', 'CLUSTER_JOB', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE')
            AND entity_key IS NOT NULL AND btrim(entity_key) <> '')
        OR (record_type NOT IN ('CLUSTER_STATE', 'CLUSTER_JOB', 'CLUSTER_PART', 'CLUSTER_MEMBER', 'CLUSTER_EVENT', 'CLUSTER_RULE')
            AND entity_key IS NULL)),
    ADD CONSTRAINT sanity_oa_cluster_revision_v2
        CHECK ((record_type = 'CLUSTER_EVENT') = (classification_revision IS NOT NULL));

CREATE UNIQUE INDEX sanity_cluster_entity_uq ON public.sanity_oa_data(bundle_id, review_unit_id, record_type, entity_key)
    WHERE entity_key IS NOT NULL;
CREATE UNIQUE INDEX sanity_cluster_revision_uq ON public.sanity_oa_data(bundle_id, review_unit_id, classification_revision)
    WHERE record_type = 'CLUSTER_EVENT';
CREATE INDEX sanity_cluster_members_idx ON public.sanity_oa_data(bundle_id, review_unit_id, record_type,
    (payload->>'job_id'), (payload->>'group_id'), entity_key);
CREATE INDEX sanity_cluster_rules_idx ON public.sanity_oa_data((payload->>'scope_hash'), created_at)
    WHERE record_type = 'CLUSTER_RULE';
CREATE UNIQUE INDEX sanity_cluster_result_uq ON public.sanity_vwp_to_oa(correlation_id) WHERE message_type = 'CLUSTER_RESULT';
CREATE UNIQUE INDEX sanity_cluster_part_uq ON public.sanity_vwp_to_oa(correlation_id, (payload->>'part'))
    WHERE message_type = 'CLUSTER_PART';

-- 003의 테이블 SELECT/INSERT 권한은 추가 컬럼에도 적용된다.
-- UPDATE 허용 컬럼은 변하지 않으므로 별도 GRANT나 003 재실행은 필요 없다.
COMMIT;
