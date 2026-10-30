import type { PreservedSource, PreservedCitation, PreservedCitationSource } from './PreservedSources'
export type WorkspaceUser = { id: string; name: string; username: string; role: string }
export type WorkspaceSession = { user: WorkspaceUser; csrf: string; expires_at: string; environment: string; is_admin?: boolean }
export type WorkspaceVersion = { id: string; project_id: string; block_id: string; net_ver: string; revision: string; layout: 'PRE' | 'POST'; source_pre_id: string | null; ecos: (string | null)[] }
export type WorkspacePor = { revision: number; version: WorkspaceVersion | null; assigned_by: WorkspaceUser | null; assigned_at: string | null }
export type WorkspaceBlock = { id: string; project_id: string; name: string; owners: WorkspaceUser[]; can_assign_por: boolean; por: WorkspacePor; report_version: WorkspaceVersion | null; counts: Record<string, number>; total: number }
export type WorkspaceProject = { id: string; name: string; description: string; tops: WorkspaceUser[]; blocks: WorkspaceBlock[] }
export type WorkspaceOverview = { user: WorkspaceUser; projects: WorkspaceProject[]; data_origin: string }
export type WorkspaceVersions = { items: WorkspaceVersion[]; por: WorkspacePor; can_assign_por: boolean }
export type WorkspaceRecord = { evaluation_status?: 'NOT_PERFORMED'; id: string; fields: Record<string, unknown>; check_status: string; raw: string; raw_display_truncated?: boolean; line_start: number; line_end: number; reasons?: string[] }
export type WorkspaceReport = { preserved_sources?: PreservedSource[]; evaluation_mode?: 'none'; not_evaluated_count?: number; id: string; item_id?: string; project_id: string; block_id: string; version_id: string; eco: string | null; family: string; rule: string; check_status: string; parse_status: string; parser: string; raw_report?: string | null; record_count: number; records?: WorkspaceRecord[]; blockers: string[]; analysis_ready: boolean; source_storage?: 'PARTS_V2'; delivery_ready?: boolean; delivery_blockers?: string[]; human_review?: { conclusion: string; actor: WorkspaceUser; at: string; revision: number; reusable: boolean } | null; group_review_summary?: PipelineSummary | null }
export type WorkspaceItemGroup = { item_id: string; total: number; counts: Record<string, number>; parse_counts: Record<string, number>; families: string[] }
export type WorkspaceReports = { items: WorkspaceReport[]; total: number; offset: number; limit: number; counts: Record<string, number>; groups: WorkspaceItemGroup[]; version: WorkspaceVersion }
export type WorkspaceEvent = { id: string; previous_version_id: string | null; version: WorkspaceVersion; actor: WorkspaceUser; reason: string; at: string; revision: number }
export type Judgment = 'PASS' | 'FAIL' | 'DEFER'
export type ReviewException = { rule: string; reason: string; conditions: string }
export type CaseSourceMetadata = { evidence_mode?: 'SHARED_CONFIRMED_KNOWLEDGE'; example_complete?: boolean; retrieval_comparison?: { different_attributes: string[]; attributes?: Record<string, { current: unknown; case: unknown }> }; project_id?: string; block_id?: string; version_id?: string; eco?: string | null; scope?: { project_id?: string; block_id?: string; eco?: string | null; layout?: string; item_id?: string; family?: string; parser?: string; rule_version?: string }; source_version?: WorkspaceVersion }
export type ReviewCase = CaseSourceMetadata & { id: string; report_id: string; version_id: string; conclusion: Judgment; reason: string; criteria: string; conditions: Record<string, string>; reusable: boolean; compatible_versions: boolean; exception?: ReviewException | null; revision: number; actor: WorkspaceUser; at: string; source_sha256: string; source_version: WorkspaceVersion; proposal_id: string | null; proposal_conclusion: Judgment | null; proposal_agreed: boolean | null; group_id?: string; cluster_revision?: number; group_snapshot?: { id: string; label: string; record_count?: number; record_ids?: string[] } }
export type ReviewProposal = { id: string; conclusion: Judgment; reason: string; references: ReviewCase[]; advisor: string; model_called: boolean; at: string; stale?: boolean; schema_version?: 2; evidence_ids?: string[]; criterion_ids?: string[]; model_metadata?: ClusterModelMetadata }
export type ReportReview = { current: ReviewCase | null; revision: number; history: ReviewCase[]; facts: Record<string, string>; ready: boolean; can_review: boolean; cases: ReviewCase[]; case_count: number; reusable_count: number; cases_displayed: number; proposal: ReviewProposal | null; corporate_ai_connected: boolean; current_stale?: boolean; blocked_reason?: string | null; ai?: CodexCapability }
export type PipelineGroup = { id: string; label: string; record_ids: string[] }
export type RationaleStatus = 'MISSING' | 'DRAFT' | 'CONFIRMED' | 'STALE'
export type RationaleContent = { reason: string; evidence_ids: string[]; reference_groups: { case_id: string; group_id: string; reason: string }[] }
export type GroupRationale = { content: RationaleContent; proposal?: { content: RationaleContent; source: string; event_id: string }; source: string; status: RationaleStatus; binding: { source_sha256: string; cluster_event_id: string; cluster_revision: number; group_id: string; label: string; membership_sha256: string }; reviewed_by: WorkspaceUser | null; reviewed_at: string | null; change_reason: string; previous_event_id: string | null; edited_by?: WorkspaceUser; edited_at?: string }
export type RationaleDetail = { group_id: string; cluster_revision: number; event_id: string; status: RationaleStatus; rationale: GroupRationale | null; samples: WorkspaceRecord[]; references: (CaseSourceMetadata & { id: string; report_id: string; revision: number; group: { id: string; label: string }; rationale: GroupRationale | null })[]; history: PageResult<{ id: string; revision: number; at: string; actor: WorkspaceUser; rationale: GroupRationale }> }
export type PipelineSummary = { cluster_revision: number; confirmed: boolean; total: number; reviewed: number; stale: number; groups: Record<string, { conclusion: Judgment | null; stale: boolean; revision: number }>; removed: { id: string; label: string; conclusion: Judgment }[] }
export type ClusterModelMetadata = { connection_id?: string; connection_label?: string; connection_revision?: number; preserved_citations?: PreservedCitation[]; preserved_citation_sources?: PreservedCitationSource[]; model: string; prompt_version: string; input_sha256: string; usage: Record<string, number>; seconds: number; input_case_count: number; synthetic_only: boolean }
export type ClusterEvent = { id: string; report_id: string; revision: number; action: 'PROPOSE' | 'EDIT' | 'CONFIRM' | 'WITHDRAW'; confirmed: boolean; reusable: boolean; compatible_versions: boolean; criteria: string; reason: string; identity_fields: string[]; groups: PipelineGroup[]; at: string; actor: WorkspaceUser; references: (CaseSourceMetadata & { id: string; report_id: string; revision: number; criteria: string; source_sha256: string })[]; advisor: string; model_called: boolean; model_metadata?: ClusterModelMetadata; initial_ai_event_id?: string }
export type PipelineView = { current: ClusterEvent | null; revision: number; ready: boolean; can_edit: boolean; stale: boolean; history: ClusterEvent[]; case_count: number; reusable_count: number; summary: PipelineSummary | null; corporate_ai_connected: boolean; record_count: number }
export type ClusterBrief = Omit<ClusterEvent, 'groups'> & { group_count: number; record_count: number; rationale_counts: Record<RationaleStatus, number> }
export type PipelineTask = { id: string; status: 'PENDING' | 'RUNNING' | 'FAILED' | 'STALE' | 'CANCELLED' | 'COMPLETED'; record_count: number; processed: number; next_chunk: number; error: string | null; chunks: { index: number; start: number; stop: number; record_count: number; input_sha256: string; status: string }[] }
export type UnknownRecovery = { event_sha256: string; step_sha256: string; call_token: string; phase: string; round: number; index: number; total: number; input_bytes?: number }
export type CodexJob = { can_retry_unknown?: boolean; unknown_recovery?: UnknownRecovery; unknown_recovery_error?: string; unknown_reason?: string; worker_site?: 'oa' | 'vwp'; preparation_phase?: string; total_records?: number; call_seconds?: number; retry_count?: number; retry_limit?: number; retry_after?: string | null; resume_count?: number; resumable?: boolean; started_at?: string; connection_id?: string; connection_label?: string; id: string; status: 'PENDING' | 'RUNNING' | 'RESULT_READY' | 'COMPLETED' | 'FAILED' | 'STALE' | 'UNKNOWN' | 'CANCELLED' | 'SAVE_FAILED'; error: string | null; model: string; created_at: string; finished_at?: string; expected_revision: number; input_case_count: number; event_id?: string; progress?: { mode: 'BATCH'; phase: 'TEXT' | 'TEXT_MERGE' | 'CLASSIFY' | 'MERGE' | 'REVIEW' | 'REVIEW_MERGE'; completed_records: number; total_records: number; completed_calls: number; round: number; waiting: boolean; round_completed?: number; round_total?: number; average_call_seconds?: number | null; seconds_per_record?: number | null; stage_eta_seconds?: number | null; retry_count?: number; child_retries?: number; retry_limit?: number; retry_error?: string | null; retry_http_status?: number | null; retry_after?: string | null } }
export type AIConnectionChoice = { id: string; label: string; execution_site?: 'vwp'; model: string; models: string[]; available: boolean; enabled: boolean; credential_required?: boolean; credential_ready?: boolean; credential_error?: string | null }
export type CodexCapability = { connection_id?: string; connection_label?: string; execution_site?: 'vwp'; connections?: AIConnectionChoice[]; available: boolean; supported: boolean; enabled: boolean; model: string; models?: string[]; remaining: number | null; job: CodexJob | null; provider?: string; synthetic_only?: boolean; credential_required?: boolean; credential_ready?: boolean; credential_error?: string | null }
export type WorkbenchView = { report: WorkspaceReport; version: WorkspaceVersion; current: ClusterBrief | null; revision: number; ready: boolean; can_edit: boolean; stale: boolean; task: PipelineTask | null; ai?: CodexCapability; record_count: number; raw_line_count: number; source_sha256: string; case_count: number; reusable_count: number; removed_count: number; corporate_ai_connected: boolean; summary: { confirmed: boolean; total: number; reviewed: number; pending: number; complete: boolean; rationale_counts: Record<RationaleStatus, number>; counts: Record<string, number> } }
export type GroupBrief = { id: string; ordinal?: number; label: string; record_count: number; status: string; revision: number; example: Record<string, unknown>; rationale: { status: RationaleStatus; reason: string; reason_truncated: boolean; reason_omitted_chars: number; source: string | null; evidence_count: number } }
export type PageResult<T> = { items: T[]; total: number; offset: number; limit: number }
export type GroupRecords = PageResult<WorkspaceRecord & { raw_display_truncated: boolean }> & { group: { id: string; label: string; record_count: number; check_status: string } | null; cluster_revision: number }

const messages: Record<string, string> = {
  UNAUTHORIZED: '로그인 정보가 올바르지 않거나 세션이 만료되었습니다.',
  FORBIDDEN: '이 블록의 담당자에게만 허용되는 작업입니다.',
  REVISION_CONFLICT: '다른 사용자가 PoR을 변경했습니다. 최신 내용을 불러왔으니 다시 확인해 주세요.',
  REQUEST_CONFLICT: '저장 요청 내용이 달라졌습니다. 화면을 새로고침한 뒤 확인해 주세요.',
  POR_UNCHANGED: '이미 현재 PoR로 지정된 버전입니다.',
  NOT_FOUND: '이 계정의 담당 범위에서 요청한 항목을 찾을 수 없습니다.',
  INVALID_RATIONALE: '이 그룹의 분류 근거와 대표 로그(1–20건), 참고 사례를 확인해 주세요.',
  RATIONALE_REQUIRED: '근거가 없는 그룹을 먼저 작성한 뒤 전체 분류를 확정해 주세요.',
  RATIONALE_STALE: '변경된 그룹의 근거를 재검토한 뒤 전체 분류를 확정해 주세요.',
  INVALID_REQUEST: '입력한 값과 변경 사유를 확인해 주세요.',
  INVALID_CSRF: '로그인 상태를 새로고침한 뒤 다시 확인해 주세요.',
  TOO_MANY_ATTEMPTS: '로그인 시도가 많습니다. 잠시 후 다시 시도해 주세요.',
  DATABASE_UNAVAILABLE: '저장 서버에 연결하지 못했습니다. 잠시 후 다시 확인해 주세요.',
  DATABASE_ERROR: '저장 결과를 확인하지 못했습니다. 같은 요청을 다시 확인할 수 있습니다.',
  INVALID_CRITERIA: '재사용할 기준과 원본에서 확인한 적용 조건을 함께 지정해 주세요.',
  INVALID_EXCEPTION: '규칙 예외는 PASS 또는 FAIL 판정에서 대상 규칙, 예외 사유와 성립 조건을 모두 작성해 주세요.',
  REVIEW_BLOCKED: '검사 근거가 미수신 또는 미완료입니다. 먼저 근거를 확인하거나 보류로 기록해 주세요.',
  SOURCE_CONTEXT_LIMIT: '보고서가 현재 검토 입력 한도를 초과했습니다. 전체 로그를 유지한 상태로 처리 범위 확장이 필요합니다.',
  SOURCE_FILE_ON_VWP: '원본 report 파일은 VWP에 보관됩니다. 로그는 검토 화면에서 확인할 수 있습니다.',
  PROPOSAL_STALE: '판정 또는 참조 기준이 변경되었습니다. 최신 기준으로 제안을 다시 받아 주세요.',
  AI_NOT_CONFIGURED: '사내 AI 검토가 아직 연결되지 않았습니다.',
  AI_USER_TOKEN_REQUIRED: '화면 상단의 개인 AI 토큰에서 본인의 토큰을 등록해 주세요.',
  AI_USER_TOKEN_EXPIRED: '개인 AI 토큰이 만료됐습니다. 새 토큰으로 교체해 주세요.',
  AI_AUTH_FAILED: 'AI 인증에 실패했습니다. 개인 토큰을 확인하고 계속 실패하면 운영 담당자에게 문의해 주세요.',
  AI_TOKEN_STORE_NOT_CONFIGURED: '개인 AI 토큰 저장 설정이 준비되지 않았습니다. 운영 담당자에게 문의해 주세요.',
  AI_TOKEN_UNAVAILABLE: '등록된 개인 AI 토큰을 사용할 수 없습니다. 운영 담당자에게 문의해 주세요.',
  AI_CREDENTIAL_CHANGED: 'AI 호출 중 개인 토큰이 변경되어 결과를 적용하지 않았습니다.',
  AI_CONFIG_CHANGED: 'AI 연결이나 모델 설정이 변경되었습니다. 최신 설정을 확인해 주세요.',
  AI_BATCH_LIMIT: 'AI 작업의 호출 한도에 도달해 이 배치를 재시도할 수 없습니다.',
  AI_CONTEXT_LIMIT: '전체 로그 또는 참고 사례가 AI 입력 한도를 넘었습니다. 일부만 처리하지 않았습니다. 운영 담당자가 처리 한도를 확인해야 합니다.',
  AI_INPUT_LIMIT: '전체 AI 입력이 처리 한도를 넘었습니다. 로그를 생략하지 않았습니다. 운영 담당자에게 확인해 주세요.',
  AI_INPUT_POLICY: '이 항목의 AI 입력 필드 설정을 확인해 주세요. 선택한 필드와 원문이 모두 없는 로그는 전송하지 않았습니다.',
  AI_OUTPUT_LIMIT: 'AI 응답이 출력 한도를 넘었습니다. 전체 결과가 검증되지 않아 적용하지 않았습니다.',
  AI_BATCH_COVERAGE: '분할 검토에서 전체 근거의 처리를 확인하지 못해 결과를 적용하지 않았습니다.',
  AI_BATCH_CONFLICT: 'AI 통합 판정이 하위 반례나 보류와 충돌하여 적용하지 않았습니다.',
  AI_BATCH_UNRESOLVED: '보존 본문의 미확인 조건이 남아 있어 AI의 PASS 제안을 적용하지 않았습니다.',
  AI_CALL_FAILED: '사내 AI 호출에 실패했습니다. 기존 검토 내용은 유지됩니다.',
  AI_CONNECT_FAILED: 'AI 주소에 연결하지 못해 이번 호출을 전송하지 못했습니다. 연결 상태를 확인해 주세요. 앞서 처리한 일부 결과는 확정되지 않았습니다.',
  AI_MODEL_NOT_ALLOWED: '선택한 AI 모델을 사용할 수 없습니다. 최신 모델 목록에서 다시 선택해 주세요.',
  AI_CONNECTION_UNAVAILABLE: '선택한 AI 연결을 사용할 수 없습니다. 최신 연결 목록을 확인해 주세요.',
  AI_CREDENTIAL_SCOPE_CHANGED: 'AI 연결 주소나 인증 방식이 바뀌었습니다. 이 연결의 개인 인증값을 다시 등록해 주세요.',
  AI_RESULT_UNKNOWN: 'AI 호출 결과가 불명확합니다. 자동 재호출하지 않습니다. 작업 상태를 확인해 주세요.',
  AI_OUTCOME_UNKNOWN: 'AI 호출 결과가 불명확합니다. 자동 재호출하지 않습니다. 작업 상태를 확인해 주세요.',
  AI_INVALID_RESPONSE: 'AI 응답의 형식 또는 근거를 검증하지 못해 적용하지 않았습니다.',
  INVALID_AI_RESPONSE: 'AI 응답의 형식 또는 근거를 검증하지 못해 적용하지 않았습니다.',
  INVALID_AI_INPUT: 'AI 판단에 필요한 전체 로그와 근거를 확인하지 못했습니다.',
  INVALID_AI_CRITERIA: '확인된 점검 기준의 형식과 적용 범위를 확인해 주세요.',
  INVALID_ADVICE: 'AI 제안의 직접 근거 또는 참고 사례를 검증하지 못해 적용하지 않았습니다.',
  AI_JOB_ACTIVE: 'AI 작업이 진행 중입니다. 현재 상태를 확인해 주세요.',
  AI_BUSY: '다른 AI 분류 요청이 진행 중입니다. 완료 후 다시 요청해 주세요.',
  AI_MOCK_LIMIT: '이 합성 report의 AI 테스트 호출 한도에 도달했습니다.',
  SYNTHETIC_ONLY: 'Codex 연결은 지정된 합성 mock report에서만 사용할 수 있습니다.',
  CASE_LIMIT: '참고 사례가 조회 한도를 넘었습니다. 적용 범위를 좁혀 주세요.',
  CLUSTER_NOT_CONFIRMED: 'Clustering 구성이 변경되었거나 아직 확정되지 않았습니다. 1단계를 확인해 주세요.',
  CLUSTER_NOT_READY: '먼저 clustering 초안을 만들고 그룹 구성을 확인해 주세요.',
  CLUSTER_BLOCKED: '확정할 검사 근거가 미수신 또는 미완료입니다.',
  INVALID_PARTITION: '모든 원본 로그를 빠짐없이 한 그룹에만 배정해 주세요.',
  CLUSTER_LIMIT: '보고서 전체가 처리 한도를 넘었습니다. 일부만 처리해 완료로 표시하지 않습니다.',
  CLUSTER_TASK_ACTIVE: '분류 작업이 진행 중이거나 확인이 필요합니다. 1단계에서 이어서 처리하거나 취소해 주세요.',
  REPORT_LIMIT: '보고서 전체가 서버의 바이트 예산을 넘었습니다. 일부만 처리하지 않았습니다. 처리 예산을 확인해 주세요.',
}

export class WorkspaceError extends Error {
  code: string
  status?: number
  constructor(code: string, status?: number) { super(messages[code] || '요청을 완료하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.'); this.code = code; this.status = status }
}

// Keep the server boundary fixed for this document: an in-app visit to the
// administrator must never redirect a retained workbench to the test API.
// Importing the fixture in Node does not select any browser environment.
const productionDocument = typeof location !== 'undefined' && location.pathname === '/'
  && (new URLSearchParams(location.search).get('page') === 'workbench' || new URLSearchParams(location.search).get('demo') !== '1')
export const productionWorkbench = () => productionDocument
export const workspaceApiBase = () => productionWorkbench() ? '/api/workbench' : '/workspace/api'
export const aiExecutionLabel = (value: { model: string; connection_label?: string }) => [value.connection_label, value.model].filter(Boolean).join(' / ')
export const aiJobActive = (job?: CodexJob | null) => !!job && ['PENDING', 'RUNNING', 'RESULT_READY'].includes(job.status)
const unknownRecoveryErrors: Record<string, string> = {
  AI_CREDENTIAL_CHANGED: '개인 인증값이 변경되어 이 배치를 현재 설정으로 재시도할 수 없습니다.',
  AI_CREDENTIAL_SCOPE_CHANGED: '개인 인증값의 연결 범위가 변경되었습니다. 인증값을 확인해 주세요.',
  AI_USER_TOKEN_REQUIRED: '개인 인증값이 필요합니다. 등록 상태를 확인해 주세요.',
  AI_USER_TOKEN_EXPIRED: '개인 인증값이 만료되었습니다. 등록 상태를 확인해 주세요.',
  AI_CONFIG_CHANGED: 'AI 연결이나 모델 설정이 변경되어 이 배치를 현재 설정으로 재시도할 수 없습니다.',
  AI_CONNECTION_UNAVAILABLE: 'AI 연결을 현재 사용할 수 없습니다. 연결 상태를 확인해 주세요.',
  AI_MODEL_NOT_ALLOWED: '이 작업의 AI 모델을 현재 사용할 수 없습니다. 연결 설정을 확인해 주세요.',
  AI_INPUT_STALE: '작업의 로그나 검토 입력이 변경되어 이 배치를 현재 입력으로 재시도할 수 없습니다.',
  AI_CASES_STALE: '작업의 참고 사례가 변경되어 이 배치를 현재 입력으로 재시도할 수 없습니다.',
  AI_CRITERIA_STALE: '작업의 판정 기준이 변경되어 이 배치를 현재 입력으로 재시도할 수 없습니다.',
  AI_BATCH_LIMIT: '이 작업의 호출 한도에 도달해 배치를 재시도할 수 없습니다.',
  AI_BATCH_DEADLINE: '이 작업의 처리 기한이 지나 배치를 재시도할 수 없습니다.',
  AI_VWP_PROFILE_UNAVAILABLE: 'VWP 실행 프로필을 현재 확인할 수 없습니다. 잠시 후 상태를 다시 확인해 주세요.',
  AI_TOKEN_STORE_NOT_CONFIGURED: '개인 인증값 저장 설정을 현재 확인할 수 없습니다. 운영 담당자에게 확인해 주세요.',
  AI_TOKEN_UNAVAILABLE: '저장된 개인 인증값을 현재 사용할 수 없습니다. 운영 담당자에게 확인해 주세요.',
  DATABASE_UNAVAILABLE: '저장소에 연결할 수 없습니다. 잠시 후 상태를 다시 확인해 주세요.',
  DATABASE_ERROR: '저장소 상태를 확인하지 못했습니다. 잠시 후 다시 확인해 주세요.',
  FORBIDDEN: '현재 사용자에게 이 작업의 재시도 권한이 없습니다.',
  UNAUTHORIZED: '로그인 상태를 확인해 주세요.',
}
export const aiJobMessage = (job: CodexJob) => {
  if (job.status === 'PENDING' && job.preparation_phase === 'PREPARING') return 'VWP 작업자가 전체 로그와 참고 사례를 준비하고 있습니다.'
  if (aiJobActive(job) && (job.retry_after || job.progress?.retry_after)) return job.progress ? 'AI 요청을 다시 처리하기 전 잠시 대기 중입니다. 완료된 배치는 유지합니다.' : 'AI 요청을 다시 처리하기 전 잠시 대기 중입니다.'
  if (job.status === 'PENDING') return 'AI 작업을 등록했습니다. 처리 시작을 기다리고 있습니다.'
  if (job.status === 'RUNNING' && job.progress) {
    const p = job.progress
    if (p.phase === 'TEXT' || p.phase === 'TEXT_MERGE') return `AI가 보존 본문을 나누어 읽고 조건·예외를 통합하고 있습니다. ${p.completed_calls.toLocaleString()}회 검토 완료. 로그 검토와 최종 검증이 남아 있습니다.`
    if (p.phase === 'REVIEW') return `AI가 확정 그룹의 전체 로그를 나누어 판정하고 있습니다. ${p.completed_records.toLocaleString()} / ${p.total_records.toLocaleString()}건 완료. 통합 판정은 아직 저장되지 않았습니다.`
    if (p.phase === 'REVIEW_MERGE') return `전체 ${p.total_records.toLocaleString()}건의 분할 판정을 비교하고 있습니다. 반례·보류와 근거를 검증한 뒤 판정 제안을 저장합니다.`
    return p.phase === 'CLASSIFY'
      ? `AI가 전체 로그를 나누어 검토하고 있습니다. ${p.completed_records.toLocaleString()} / ${p.total_records.toLocaleString()}건 완료. 전체 검증 후 분류 초안을 공개합니다.`
      : `전체 ${p.total_records.toLocaleString()}건의 개별 분류를 마치고 그룹의 의미·조건을 비교하고 있습니다. 통합과 전체 배정 검증이 남아 있습니다.`
  }
  if (job.status === 'RUNNING') return 'AI가 전체 로그와 관련 확정 사례를 검토하고 있습니다.'
  if (job.status === 'RESULT_READY') return 'AI 응답의 근거와 현재 개정을 검증하고 있습니다.'
  if (job.status === 'UNKNOWN') {
    const warning = '원격 AI가 원 호출을 계속 실행 중일 수 있습니다. 새 작업은 추가 계산과 비용을 발생시킬 수 있습니다.'
    if (job.can_retry_unknown && job.unknown_recovery && job.progress?.mode === 'BATCH') return `마지막 배치의 호출 결과가 불명확합니다. 완료된 배치는 유지되며 자동 재호출하지 않습니다. ${warning}`
    const reason = job.unknown_recovery_error ? unknownRecoveryErrors[job.unknown_recovery_error] || '배치 재시도 조건을 현재 확인할 수 없습니다. 작업 상태를 다시 확인해 주세요.' : '운영 담당자에게 작업 상태를 확인해 주세요.'
    return `AI 호출 결과가 불명확합니다. 자동 재호출하지 않습니다. ${reason} ${warning}`
  }
  if (job.status === 'STALE') return job.error === 'AI_CONFIG_CHANGED'
    ? 'AI 모델 목록이나 연결 설정이 변경되어 작업을 중단했습니다. 최신 설정을 확인한 뒤 다시 요청해 주세요.'
    : '로그·분류·판정 또는 참고 사례가 변경되어 결과를 적용하지 않았습니다. 최신 내용을 확인해 주세요.'
  if (job.status === 'CANCELLED') return 'AI 작업이 취소되었습니다. 이전 검토 내용은 유지됩니다.'
  if (job.status === 'COMPLETED') return 'AI 제안을 저장했습니다. 내용을 검토한 뒤 사람이 확정해 주세요.'
  return `${messages[job.error || ''] || 'AI 작업을 완료하지 못했습니다. 기존 검토 내용은 유지됩니다.'}${job.error ? ` (${job.error})` : ''}`
}

export async function workspaceRequest<T>(path: string, options: { body?: unknown; csrf?: string; signal?: AbortSignal } = {}): Promise<T> {
  // Explicit opt-in only. Server/authentication failures never select the demo.
  if (!productionWorkbench() && new URLSearchParams(location.search).get('demo') === '1') {
    const { workspaceDemoRequest } = await import('./workspaceDemo')
    options.signal?.throwIfAborted()
    return workspaceDemoRequest<T>(path, options)
  }
  let response: Response
  try {
    response = await fetch(`${workspaceApiBase()}${path}`, {
      method: options.body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: options.body === undefined ? undefined : { 'Content-Type': 'application/json', ...(productionWorkbench() ? { 'X-Sanity-Request': '1' } : { 'X-CSRF-Token': options.csrf || '' }) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new WorkspaceError('NETWORK_ERROR')
  }
  const value = await response.json().catch(() => ({ code: 'INVALID_RESPONSE' }))
  if (!response.ok) throw new WorkspaceError(value.code || 'REQUEST_FAILED', response.status)
  return value as T
}

export const workspaceLabels: Record<string, string> = { GCA: 'GCA', VCLP: 'VC LP', SPYGLASS: 'SpyGlass', PASS: 'PASS', FAIL: 'FAIL', DEFER: '보류', UNKNOWN: '판정 보류', NOT_PERFORMED: '판정 미실행', COMPLETE: '완료', PARTIAL: '일부 해석', MISSING: '미수신' }
export const workspaceTime = (value: string | null) => value ? new Date(value).toLocaleString('ko-KR', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

// UNKNOWN remains the wire verdict; collection-only metadata explains its cause.
export function toolResultStatus(value: { check_status: string; evaluation_mode?: string; evaluation_status?: string }): string {
  return value.check_status === 'UNKNOWN' && (value.evaluation_mode === 'none' || value.evaluation_status === 'NOT_PERFORMED')
    ? 'NOT_PERFORMED' : value.check_status
}
