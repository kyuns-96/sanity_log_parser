-- owner/관리자로 전용 테스트 DB에서 실행. fixture bytes는 gzip이 아닌 DDL용 합성 데이터.
-- payload 의미 검증·실제 사내 접속은 검사하지 않는다. 모든 테스트 행은 ROLLBACK.
BEGIN;
CREATE FUNCTION pg_temp.assert_true(ok BOOLEAN, label TEXT) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF ok IS DISTINCT FROM true THEN RAISE EXCEPTION 'ASSERT FAILED: %', label; END IF;
END $$;
CREATE FUNCTION pg_temp.expect_error(statement TEXT, expected_state TEXT, label TEXT)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    BEGIN EXECUTE statement;
    EXCEPTION WHEN OTHERS THEN
        IF SQLSTATE = expected_state THEN RETURN; END IF;
        RAISE EXCEPTION 'Wrong error for %: % / %', label, SQLSTATE, SQLERRM;
    END;
    RAISE EXCEPTION 'Expected error missing: %', label;
END $$;

SET LOCAL ROLE sanity_vwp_app;
INSERT INTO public.sanity_vwp_data(record_id, record_type, analysis_id, entity_key, payload, status)
VALUES ('00000000-0000-0000-0000-000000000001', 'RUN', '00000000-0000-0000-0000-000000000001', 'run-1', '{}', 'BUILDING');
INSERT INTO public.sanity_vwp_data(record_id, record_type, analysis_id, entity_key, review_unit_id, parent_id, payload, status)
VALUES ('00000000-0000-0000-0000-000000000002', 'UNIT', '00000000-0000-0000-0000-000000000001', 'unit-1', 'unit-1',
    '00000000-0000-0000-0000-000000000001', '{}', 'PENDING');
INSERT INTO public.sanity_vwp_data(record_id, record_type, analysis_id, entity_key, review_unit_id, parent_id,
    payload, status, raw_bytes, raw_sha256)
VALUES ('00000000-0000-0000-0000-000000000003', 'EVIDENCE', '00000000-0000-0000-0000-000000000001', 'record-1', 'unit-1',
    '00000000-0000-0000-0000-000000000002', '{"check_status":"PASS"}', 'RECORDED', decode('010203', 'hex'),
    encode(sha256(decode('010203', 'hex')), 'hex'));
SET CONSTRAINTS ALL IMMEDIATE;
SELECT pg_temp.expect_error($q$UPDATE public.sanity_vwp_data SET payload = '{}' WHERE record_type = 'EVIDENCE'$q$,
    '42501', 'original payload immutable');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_vwp_data SET state = '{"changed":true}' WHERE record_type = 'EVIDENCE'$q$,
    '23514', 'evidence state immutable');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_vwp_data SET updated_at = clock_timestamp() WHERE record_type = 'EVIDENCE'$q$,
    '23514', 'evidence timestamp immutable');
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_data(record_id, record_type, analysis_id, entity_key, review_unit_id, parent_id, payload, status)
    VALUES ('00000000-0000-0000-0000-000000000004','UNIT','00000000-0000-0000-0000-000000000099','wrong','unit-1',
        '00000000-0000-0000-0000-000000000001','{}','PENDING')
$q$, '23503', 'cross-analysis parent rejected');

INSERT INTO public.sanity_vwp_to_oa(message_id, project, run_id, payload)
VALUES ('ping-vwp', 'test', 'run-1', '{"message":"연결 테스트"}');
INSERT INTO public.sanity_vwp_to_oa(message_id, project, run_id, message_type, bundle_id,
    payload_gzip, payload_sha256, uncompressed_bytes)
VALUES ('bundle-1', 'test', 'run-1', 'ANALYSIS_BUNDLE', 'b-' || repeat('a',64), decode('010203','hex'),
    encode(sha256(decode('010203','hex')), 'hex'), 3);
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,payload_gzip,payload_sha256,uncompressed_bytes)
    VALUES ('bundle-duplicate','test','run-1','ANALYSIS_BUNDLE','b-'||repeat('a',64),decode('010203','hex'),
        encode(sha256(decode('010203','hex')),'hex'),3)
$q$, '23505', 'bundle id unique');
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,payload_gzip,payload_sha256,uncompressed_bytes)
    VALUES ('bundle-bad-hash','test','run-1','ANALYSIS_BUNDLE','b-'||repeat('b',64),decode('01','hex'),repeat('0',64),1)
$q$, '23514', 'binary hash checked');
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,payload_gzip,payload_sha256,uncompressed_bytes)
    VALUES ('bundle-bad-size','test','run-1','ANALYSIS_BUNDLE','b-'||repeat('b',64),decode('01','hex'),
        encode(sha256(decode('01','hex')),'hex'),67108865)
$q$, '23514', 'uncompressed size limit');
SELECT pg_temp.expect_error($q$INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,status)
    VALUES ('inject','test','run-1','RECEIVED')$q$, '42501', 'sender cannot inject status');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_vwp_to_oa SET status = 'RETRY'$q$, '42501', 'sender cannot acknowledge');
SELECT pg_temp.expect_error('SELECT * FROM public.sanity_oa_data', '42501', 'VWP cannot read OA data');
SELECT pg_temp.expect_error($q$INSERT INTO public.sanity_oa_to_vwp(message_id,project,run_id)
    VALUES ('wrong-way','test','run-1')$q$, '42501', 'VWP cannot impersonate OA');

RESET ROLE;
SET LOCAL ROLE sanity_oa_app;
SELECT pg_temp.expect_error('SELECT * FROM public.sanity_vwp_data', '42501', 'OA cannot read private evidence');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_vwp_to_oa SET payload = '{}' $q$, '42501', 'receiver cannot rewrite payload');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_vwp_to_oa SET status = 'PROCESSING' WHERE message_id = 'bundle-1'$q$,
    '23514', 'processing requires lease');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_vwp_to_oa SET status = 'RECEIVED' WHERE message_id = 'bundle-1'$q$,
    '23514', 'receipt requires timestamp');
WITH picked AS (
    SELECT message_id FROM public.sanity_vwp_to_oa WHERE message_id = 'bundle-1' AND status = 'READY'
    FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE public.sanity_vwp_to_oa m SET status = 'PROCESSING', attempt_count = attempt_count + 1,
    lease_token = '00000000-0000-0000-0000-000000000010', lease_owner = 'oa-worker',
    lease_expires_at = clock_timestamp() - interval '1 second', updated_at = clock_timestamp()
FROM picked WHERE m.message_id = picked.message_id;
UPDATE public.sanity_vwp_to_oa SET lease_token = '00000000-0000-0000-0000-000000000011',
    attempt_count = attempt_count + 1, lease_expires_at = clock_timestamp() + interval '1 minute'
WHERE message_id = 'bundle-1' AND status = 'PROCESSING' AND lease_expires_at <= clock_timestamp();
DO $$
DECLARE affected INTEGER;
BEGIN
    UPDATE public.sanity_vwp_to_oa SET status = 'RECEIVED', completed_at = clock_timestamp(),
        lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL
    WHERE message_id = 'bundle-1' AND status = 'PROCESSING'
      AND lease_token = '00000000-0000-0000-0000-000000000010' AND lease_expires_at > clock_timestamp();
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 0 THEN RAISE EXCEPTION 'Stale lease accepted'; END IF;
    UPDATE public.sanity_vwp_to_oa SET status = 'RECEIVED', completed_at = clock_timestamp(),
        lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL
    WHERE message_id = 'bundle-1' AND status = 'PROCESSING'
      AND lease_token = '00000000-0000-0000-0000-000000000011' AND lease_expires_at > clock_timestamp();
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN RAISE EXCEPTION 'Valid receipt missing'; END IF;
END $$;

INSERT INTO public.sanity_oa_to_vwp(message_id,project,run_id,message_type,bundle_id,expires_at,payload)
VALUES ('request-1','test','run-1','EVIDENCE_REQUEST','b-'||repeat('a',64),
    CURRENT_TIMESTAMP + interval '1 hour','{"review_unit_id":"unit-1","record_ids":["record-1"]}');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_oa_to_vwp SET status = 'RETRY'$q$,
    '42501', 'OA cannot acknowledge own request');
INSERT INTO public.sanity_oa_data(record_id,record_type,project,run_id,bundle_id,payload)
VALUES ('10000000-0000-0000-0000-000000000001','RECEIVED_BUNDLE','test','run-1','b-'||repeat('a',64),'{}');
INSERT INTO public.sanity_oa_data(record_id,record_type,project,run_id,bundle_id,review_revision,request_id,payload)
SELECT ('20000000-0000-0000-0000-'||lpad(n::text,12,'0'))::uuid,'REVIEW_EVENT','test','run-1','b-'||repeat('a',64),n,
    CASE WHEN n = 0 THEN NULL ELSE ('30000000-0000-0000-0000-'||lpad(n::text,12,'0'))::uuid END,
    jsonb_build_object('conclusion', CASE WHEN n IN (1,3) THEN 'FAIL' WHEN n = 2 THEN 'PASS' ELSE 'UNREVIEWED' END)
FROM generate_series(0,3) n;
SELECT pg_temp.assert_true((SELECT count(*) = 4 FROM public.sanity_oa_data WHERE record_type = 'REVIEW_EVENT'),
    'A-B-A keeps all review revisions');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_oa_data SET payload = '{}' WHERE record_type = 'REVIEW_EVENT'$q$,
    '42501', 'review event payload immutable');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_oa_data SET version = 1 WHERE record_type = 'REVIEW_EVENT'$q$,
    '23514', 'review event version immutable');
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_oa_data(record_id,record_type,project,run_id,bundle_id,review_revision,request_id,payload)
    VALUES ('20000000-0000-0000-0000-000000000099','REVIEW_EVENT','test','run-1','b-'||repeat('a',64),3,
        '30000000-0000-0000-0000-000000000099','{}')
$q$, '23505', 'review revision unique');
INSERT INTO public.sanity_oa_data(record_id,record_type,project,run_id,bundle_id,review_revision,payload,payload_bytes,payload_sha256)
VALUES ('40000000-0000-0000-0000-000000000001','REPORT','test','run-1','b-'||repeat('a',64),3,'{}',
    convert_to('테스트 보고서','UTF8'),encode(sha256(convert_to('테스트 보고서','UTF8')),'hex'));
SELECT pg_temp.expect_error($q$UPDATE public.sanity_oa_data SET payload_bytes = convert_to('changed','UTF8') WHERE record_type = 'REPORT'$q$,
    '42501', 'report bytes immutable');
SELECT pg_temp.expect_error($q$UPDATE public.sanity_oa_data SET status = 'READY' WHERE record_type = 'RECEIVED_BUNDLE'$q$,
    '23514', 'OA records cannot become publishing tasks');

RESET ROLE;
SET LOCAL ROLE sanity_vwp_app;
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,correlation_id,payload)
    VALUES ('response-bad','test','run-1','EVIDENCE_RESPONSE','b-'||repeat('a',64),'request-missing','{"result":"REJECTED"}')
$q$, '23503', 'response must reference real request');
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,correlation_id,payload)
    VALUES ('response-bad-result','test','run-1','EVIDENCE_RESPONSE','b-'||repeat('a',64),'request-1','{}')
$q$, '23514', 'missing response result cannot bypass CHECK with NULL');
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,correlation_id,payload)
    VALUES ('response-bad-scope','test','other-run','EVIDENCE_RESPONSE','b-'||repeat('a',64),'request-1','{"result":"REJECTED"}')
$q$, '23503', 'response cannot reference request from another run');
SELECT pg_temp.expect_error($q$
    INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,correlation_id,payload)
    VALUES ('response-null-bytes','test','run-1','EVIDENCE_RESPONSE','b-'||repeat('a',64),'request-1','{"result":"FULFILLED"}')
$q$, '23514', 'fulfilled response needs bytes');
INSERT INTO public.sanity_vwp_to_oa(message_id,project,run_id,message_type,bundle_id,correlation_id,payload)
VALUES ('response-1','test','run-1','EVIDENCE_RESPONSE','b-'||repeat('a',64),'request-1','{"result":"REJECTED","reason":"not approved"}');
UPDATE public.sanity_oa_to_vwp SET status = 'RECEIVED', completed_at = clock_timestamp()
WHERE message_id = 'request-1' AND status = 'READY';
SELECT pg_temp.expect_error('DELETE FROM public.sanity_vwp_to_oa', '42501', 'no app deletes');
RESET ROLE;
SELECT pg_temp.assert_true((SELECT count(*) = 4 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'), 'exactly four public tables');
SELECT 'PASS: four-table DDL and role smoke checks' AS result;
ROLLBACK;
