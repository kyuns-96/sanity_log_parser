-- DBA가 sanity_vwp_app / sanity_oa_app 역할을 먼저 준비한 뒤 실행한다.
-- 두 역할은 서로 다른 앱 로그인에 연결하며 owner/슈퍼유저/BYPASSRLS/상대 역할을 상속하지 않는다.
-- public이 아닌 스키마라면 모든 public. 참조와 GRANT USAGE 대상을 함께 바꾼다.
-- 예상 밖 직접 권한이 남으면 자동 회수하지 않고 중단한다. DBA가 기존 테스트 권한을 검토한다.
BEGIN;
DO $$
DECLARE target REGCLASS; item RECORD;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sanity_vwp_app')
        OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sanity_oa_app') THEN
        RAISE EXCEPTION 'DBA must create sanity_vwp_app and sanity_oa_app first';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('sanity_vwp_app', 'sanity_oa_app')
               AND (rolsuper OR rolcreaterole OR rolbypassrls))
        OR pg_has_role('sanity_vwp_app', 'sanity_oa_app', 'MEMBER')
        OR pg_has_role('sanity_oa_app', 'sanity_vwp_app', 'MEMBER') THEN
        RAISE EXCEPTION 'Application roles must be unprivileged and must not inherit each other';
    END IF;
    FOREACH target IN ARRAY ARRAY['public.sanity_vwp_data'::regclass, 'public.sanity_oa_data'::regclass,
        'public.sanity_vwp_to_oa'::regclass, 'public.sanity_oa_to_vwp'::regclass] LOOP
        IF EXISTS (SELECT 1 FROM pg_class c CROSS JOIN pg_roles r
                   WHERE c.oid = target AND r.rolname IN ('sanity_vwp_app', 'sanity_oa_app')
                     AND pg_has_role(r.oid, c.relowner, 'MEMBER')) THEN
            RAISE EXCEPTION 'Application role must not own or inherit the owner of %', target;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE c.oid = target AND a.grantee <> c.relowner AND a.grantee <> 0
              AND a.grantee NOT IN (SELECT oid FROM pg_roles WHERE rolname IN ('sanity_vwp_app', 'sanity_oa_app'))
        ) OR EXISTS (
            SELECT 1 FROM pg_attribute col JOIN pg_class c ON c.oid = col.attrelid
            CROSS JOIN LATERAL aclexplode(col.attacl) a
            WHERE c.oid = target AND a.grantee <> c.relowner AND a.grantee <> 0
              AND a.grantee NOT IN (SELECT oid FROM pg_roles WHERE rolname IN ('sanity_vwp_app', 'sanity_oa_app'))
        ) THEN
            RAISE EXCEPTION 'Unexpected existing grants on %; DBA must review/revoke old test grants first', target;
        END IF;
        EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC, sanity_vwp_app, sanity_oa_app', target);
        FOR item IN SELECT attname FROM pg_attribute WHERE attrelid = target AND attnum > 0 AND NOT attisdropped LOOP
            EXECUTE format('REVOKE ALL (%I) ON TABLE %s FROM PUBLIC, sanity_vwp_app, sanity_oa_app', item.attname, target);
        END LOOP;
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA public TO sanity_vwp_app, sanity_oa_app;
GRANT SELECT, INSERT ON public.sanity_vwp_data TO sanity_vwp_app;
GRANT UPDATE (state, status, version, updated_at) ON public.sanity_vwp_data TO sanity_vwp_app;
GRANT SELECT, INSERT ON public.sanity_oa_data TO sanity_oa_app;
GRANT UPDATE (state, status, version, updated_at)
    ON public.sanity_oa_data TO sanity_oa_app;

GRANT SELECT ON public.sanity_vwp_to_oa, public.sanity_oa_to_vwp TO sanity_vwp_app, sanity_oa_app;
GRANT INSERT (message_id, message_type, payload, project, run_id, bundle_id,
    correlation_id, payload_gzip, payload_sha256, uncompressed_bytes)
    ON public.sanity_vwp_to_oa TO sanity_vwp_app;
GRANT UPDATE (status, error_message, attempt_count, next_attempt_at, lease_token,
    lease_owner, lease_expires_at, completed_at, updated_at)
    ON public.sanity_vwp_to_oa TO sanity_oa_app;
GRANT INSERT (message_id, message_type, payload, project, run_id, bundle_id, expires_at)
    ON public.sanity_oa_to_vwp TO sanity_oa_app;
GRANT UPDATE (status, error_message, attempt_count, next_attempt_at, lease_token,
    lease_owner, lease_expires_at, completed_at, updated_at)
    ON public.sanity_oa_to_vwp TO sanity_vwp_app;
COMMIT;
