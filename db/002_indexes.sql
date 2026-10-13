-- 4개 테이블 준비 후 owner/DBA가 전체 파일을 한 번 실행한다.
-- 추가 테이블/스키마/확장 모듈/시퀀스는 만들지 않는다.
-- public이 아닌 스키마에 신청했다면 모든 public. 참조를 실제 스키마로 바꾼다.
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.sanity_vwp_data ADD CONSTRAINT sanity_vwp_parent_fk
    FOREIGN KEY (analysis_id, parent_id) REFERENCES public.sanity_vwp_data(analysis_id, record_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX sanity_vwp_unit_idx ON public.sanity_vwp_data(analysis_id, review_unit_id, record_type);
CREATE INDEX sanity_vwp_parent_idx ON public.sanity_vwp_data(analysis_id, parent_id);

CREATE UNIQUE INDEX sanity_oa_received_uq ON public.sanity_oa_data(bundle_id) WHERE record_type = 'RECEIVED_BUNDLE';
CREATE UNIQUE INDEX sanity_oa_unit_uq ON public.sanity_oa_data(bundle_id, review_unit_id) WHERE record_type = 'REVIEW_UNIT';
CREATE UNIQUE INDEX sanity_oa_event_uq ON public.sanity_oa_data(bundle_id, review_revision) WHERE record_type = 'REVIEW_EVENT';
CREATE UNIQUE INDEX sanity_oa_report_uq ON public.sanity_oa_data(bundle_id, review_revision) WHERE record_type = 'REPORT';
CREATE UNIQUE INDEX sanity_oa_request_uq ON public.sanity_oa_data(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX sanity_oa_bundle_idx ON public.sanity_oa_data(bundle_id, record_type, review_revision);

CREATE UNIQUE INDEX sanity_vwp_bundle_uq ON public.sanity_vwp_to_oa(bundle_id) WHERE message_type = 'ANALYSIS_BUNDLE';
CREATE UNIQUE INDEX sanity_vwp_response_uq ON public.sanity_vwp_to_oa(correlation_id) WHERE message_type = 'EVIDENCE_RESPONSE';
ALTER TABLE public.sanity_oa_to_vwp ADD CONSTRAINT sanity_oa_request_scope_uq
    UNIQUE (message_id, project, run_id, bundle_id);
ALTER TABLE public.sanity_vwp_to_oa ADD CONSTRAINT sanity_vwp_response_scope_fk
    FOREIGN KEY (correlation_id, project, run_id, bundle_id)
    REFERENCES public.sanity_oa_to_vwp(message_id, project, run_id, bundle_id);
CREATE INDEX sanity_vwp_ready_idx ON public.sanity_vwp_to_oa(next_attempt_at, message_id)
    WHERE status IN ('READY', 'RETRY');
CREATE INDEX sanity_vwp_lease_idx ON public.sanity_vwp_to_oa(lease_expires_at) WHERE status = 'PROCESSING';
CREATE INDEX sanity_vwp_run_idx ON public.sanity_vwp_to_oa(project, run_id, created_at);
CREATE INDEX sanity_oa_bridge_ready_idx ON public.sanity_oa_to_vwp(next_attempt_at, message_id)
    WHERE status IN ('READY', 'RETRY');
CREATE INDEX sanity_oa_bridge_lease_idx ON public.sanity_oa_to_vwp(lease_expires_at) WHERE status = 'PROCESSING';
CREATE INDEX sanity_oa_bridge_bundle_idx ON public.sanity_oa_to_vwp(bundle_id, created_at);

ALTER TABLE public.sanity_vwp_to_oa ALTER COLUMN payload_gzip SET STORAGE EXTERNAL;
ALTER TABLE public.sanity_vwp_data ALTER COLUMN raw_bytes SET STORAGE EXTERNAL;
ALTER TABLE public.sanity_oa_data ALTER COLUMN payload_bytes SET STORAGE EXTERNAL;
COMMIT;
