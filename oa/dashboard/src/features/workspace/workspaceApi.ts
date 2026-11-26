import { appUrl } from '@/lib/appUrl'
import { formatUnknownRecoveryError, formatWorkspaceErrorMessage, WORKSPACE_ERROR_MESSAGES } from '@/lib/apiErrors'
import { productionWorkbench } from '@/lib/entryMode'
import { fetchJson } from '@/lib/fetchJson'
import type { PreservedSource, PreservedCitation, PreservedCitationSource } from './PreservedSources'
export type WorkspaceUser = { id: string; name: string; username: string; role: string }
export type WorkspaceSession = { user: WorkspaceUser; csrf: string; expires_at: string; environment: string; is_admin?: boolean }
export type WorkspaceVersion = { id: string; project_id: string; block_id: string; net_ver: string; revision: string; layout: 'PRE' | 'POST'; eco_num?: string | null; source_pre_id: string | null; ecos: (string | null)[] }
export type WorkspacePor = { revision: number; version: WorkspaceVersion | null; assigned_by: WorkspaceUser | null; assigned_at: string | null }
export type WorkspaceBlock = { id: string; project_id: string; name: string; owners: WorkspaceUser[]; can_assign_por: boolean; por: WorkspacePor; report_version: WorkspaceVersion | null; counts: Record<string, number>; total: number }
export type WorkspaceProject = { id: string; name: string; description: string; tops: WorkspaceUser[]; blocks: WorkspaceBlock[] }
export type WorkspaceOverview = { user: WorkspaceUser; projects: WorkspaceProject[]; data_origin: string }
export type WorkspaceVersions = { items: WorkspaceVersion[]; por: WorkspacePor; can_assign_por: boolean }
export type WorkspaceRecord = { evaluation_status?: 'NOT_PERFORMED'; id: string; fields: Record<string, unknown>; check_status: string; raw: string; raw_display_truncated?: boolean; line_start: number; line_end: number; reasons?: string[] }
export type WorkspaceReport = { submission_id?: string; submitted_at?: string; analysis_created_at?: string; is_current?: boolean; preserved_sources?: PreservedSource[]; evaluation_mode?: 'none'; not_evaluated_count?: number; id: string; item_id?: string; project_id: string; block_id: string; version_id: string; eco: string | null; family: string; rule: string; check_status: string; parse_status: string; parser: string; raw_report?: string | null; record_count: number; records?: WorkspaceRecord[]; blockers: string[]; analysis_ready: boolean; source_storage?: 'PARTS_V2'; delivery_ready?: boolean; delivery_blockers?: string[]; human_review?: { conclusion: string; actor: WorkspaceUser; at: string; revision: number; reusable: boolean } | null; group_review_summary?: PipelineSummary | null }
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
export type RationaleContent = { reason: string; summary?: string; evidence_ids: string[]; reference_groups: { case_id: string; group_id: string; reason: string }[] }
export type GroupRationale = { content: RationaleContent; proposal?: { content: RationaleContent; source: string; event_id: string }; source: string; status: RationaleStatus; binding: { source_sha256: string; cluster_event_id: string; cluster_revision: number; group_id: string; label: string; membership_sha256: string }; reviewed_by: WorkspaceUser | null; reviewed_at: string | null; change_reason: string; previous_event_id: string | null; edited_by?: WorkspaceUser; edited_at?: string }
export type RationaleDetail = { group_id: string; cluster_revision: number; event_id: string; status: RationaleStatus; rationale: GroupRationale | null; samples: WorkspaceRecord[]; references: (CaseSourceMetadata & { id: string; report_id: string; revision: number; group: { id: string; label: string }; rationale: GroupRationale | null })[]; history: PageResult<{ id: string; revision: number; at: string; actor: WorkspaceUser; rationale: GroupRationale }> }
export type PipelineSummary = { cluster_revision: number; confirmed: boolean; total: number; reviewed: number; stale: number; groups: Record<string, { conclusion: Judgment | null; stale: boolean; revision: number }>; removed: { id: string; label: string; conclusion: Judgment }[] }
export type ClusterModelMetadata = { connection_id?: string; connection_label?: string; connection_revision?: number; preserved_citations?: PreservedCitation[]; preserved_citation_sources?: PreservedCitationSource[]; model: string; prompt_version: string; input_sha256: string; usage: Record<string, number>; seconds: number; input_case_count: number; synthetic_only: boolean }
export type ClusterEvent = { id: string; report_id: string; revision: number; action: 'PROPOSE' | 'EDIT' | 'CONFIRM' | 'WITHDRAW'; confirmed: boolean; reusable: boolean; compatible_versions: boolean; criteria: string; reason: string; identity_fields: string[]; groups: PipelineGroup[]; at: string; actor: WorkspaceUser; references: (CaseSourceMetadata & { id: string; report_id: string; revision: number; criteria: string; source_sha256: string })[]; advisor: string; model_called: boolean; model_metadata?: ClusterModelMetadata; initial_ai_event_id?: string }
export type ClusterBrief = Omit<ClusterEvent, 'groups'> & { group_count: number; record_count: number; rationale_counts: Record<RationaleStatus, number> }
export type PipelineTask = { id: string; status: 'PENDING' | 'RUNNING' | 'FAILED' | 'STALE' | 'CANCELLED' | 'COMPLETED'; record_count: number; processed: number; next_chunk: number; error: string | null; chunks: { index: number; start: number; stop: number; record_count: number; input_sha256: string; status: string }[] }
export type UnknownRecovery = { event_sha256: string; step_sha256: string; call_token: string; phase: string; round: number; index: number; total: number; input_bytes?: number }
export type AIValidationFailure = { schema_version: 1; code: string; field: string; group_index?: number; actual_count?: number; actual_length?: number }
export type CodexJob = { completion_reason?: 'MAX_ITERATIONS'; can_delete?: boolean; delete_error?: string; merge_iteration?: number | null; current_group_count?: number | null; validation_failure?: AIValidationFailure | null; can_retry_unknown?: boolean; unknown_recovery?: UnknownRecovery; unknown_recoveries?: UnknownRecovery[]; unknown_recovery_error?: string; unknown_reason?: string; worker_site?: 'oa' | 'vwp'; preparation_phase?: string; total_records?: number; call_seconds?: number; retry_count?: number; retry_limit?: number; retry_after?: string | null; resume_count?: number; resumable?: boolean; started_at?: string; connection_id?: string; connection_label?: string; id: string; status: 'PENDING' | 'RUNNING' | 'RESULT_READY' | 'COMPLETED' | 'FAILED' | 'STALE' | 'UNKNOWN' | 'CANCELLED' | 'SAVE_FAILED'; error: string | null; model: string; created_at: string; finished_at?: string; expected_revision: number; input_case_count: number; event_id?: string; progress?: { mode: 'BATCH'; execution?: 'parallel-v1' | 'parallel-v2'; cancel_requested?: boolean; concurrency?: number; inflight_calls?: number; allocated_concurrency?: number; active_descriptors?: { index: number; phase?: string }[]; wall_seconds_per_record?: number | null; wall_stage_eta_seconds?: number | null; wall_measured_at?: string | null; phase: 'TEXT' | 'TEXT_MERGE' | 'CLASSIFY' | 'MERGE' | 'REVIEW' | 'REVIEW_MERGE'; completed_records: number; total_records: number; completed_calls: number; round: number; waiting: boolean; round_completed?: number; round_total?: number; average_call_seconds?: number | null; seconds_per_record?: number | null; stage_eta_seconds?: number | null; retry_count?: number; child_retries?: number; retry_limit?: number; retry_error?: string | null; retry_http_status?: number | null; retry_after?: string | null } }
export type AIConnectionChoice = { id: string; label: string; execution_site?: 'vwp'; model: string; models: string[]; available: boolean; enabled: boolean; credential_required?: boolean; credential_ready?: boolean; credential_error?: string | null }
export type CodexCapability = { connection_id?: string; connection_label?: string; execution_site?: 'vwp'; connections?: AIConnectionChoice[]; task_default?: { connection_id: string; model_id: string; applied: boolean; reason?: 'AI_CONNECTION_UNAVAILABLE' | 'AI_MODEL_NOT_ALLOWED' }; available: boolean; supported: boolean; enabled: boolean; model: string; models?: string[]; remaining: number | null; job: CodexJob | null; provider?: string; synthetic_only?: boolean; credential_required?: boolean; credential_ready?: boolean; credential_error?: string | null }
export type WorkbenchView = { report: WorkspaceReport; version: WorkspaceVersion; current: ClusterBrief | null; revision: number; ready: boolean; can_edit: boolean; stale: boolean; task: PipelineTask | null; ai?: CodexCapability; record_count: number; raw_line_count: number; source_sha256: string; case_count: number; case_count_scope?: 'REFERENCED' | 'CANDIDATES'; reusable_count: number; removed_count: number; corporate_ai_connected: boolean; summary: { confirmed: boolean; total: number; reviewed: number; pending: number; complete: boolean; rationale_counts: Record<RationaleStatus, number>; counts: Record<string, number> } }
export type GroupBrief = { id: string; ordinal?: number; label: string; record_count: number; status: string; revision: number; example: Record<string, unknown>; rationale: { status: RationaleStatus; reason: string; summary?: string; reason_truncated: boolean; reason_omitted_chars: number; source: string | null; evidence_count: number } }
export type PageResult<T> = { items: T[]; total: number; offset: number; limit: number }
export type GroupRecords = PageResult<WorkspaceRecord & { raw_display_truncated: boolean }> & { group: { id: string; label: string; record_count: number; check_status: string } | null; cluster_revision: number }

export class WorkspaceError extends Error {
  code: string
  status?: number
  constructor(code: string, status?: number) { super(formatWorkspaceErrorMessage(code)); this.code = code; this.status = status }
}

export { productionWorkbench }
export const workspaceApiBase = () => productionWorkbench() ? '/api/workbench' : '/workspace/api'
export const aiExecutionLabel = (value: { model: string; connection_label?: string }) => [value.connection_label, value.model].filter(Boolean).join(' / ')
export const aiJobActive = (job?: CodexJob | null) => !!job && ['PENDING', 'RUNNING', 'RESULT_READY'].includes(job.status)
// Only fixed diagnostics and bounded integers are shown; server/model strings are never echoed.
const rationaleDiagnostics: Record<string, { field: string; message: string }> = {
  RATIONALE_MISSING: { field: 'group_rationales', message: '그룹별 분류 근거가 누락되었습니다.' },
  RATIONALE_COUNT: { field: 'group_rationales', message: '그룹 수와 분류 근거 수가 일치하지 않습니다.' },
  RATIONALE_ENTRY_SHAPE: { field: 'group_rationales', message: '그룹별 분류 근거의 형식이 올바르지 않습니다.' },
  RATIONALE_GROUP_ID: { field: 'group_id', message: '분류 근거의 그룹 지정이 올바르지 않습니다.' },
  RATIONALE_REASON: { field: 'reason', message: '분류 근거 설명이 없거나 허용 범위를 벗어났습니다.' },
  RATIONALE_SUMMARY: { field: 'summary', message: '분류 근거 요약은 1~120자로 작성해야 합니다.' },
  RATIONALE_EVIDENCE_TYPE: { field: 'evidence_ids', message: '대표 로그 목록의 형식이 올바르지 않습니다.' },
  RATIONALE_EVIDENCE_COUNT: { field: 'evidence_ids', message: '대표 로그 수가 허용 범위에 맞지 않습니다.' },
  RATIONALE_EVIDENCE_ITEM_TYPE: { field: 'evidence_ids', message: '대표 로그 항목의 형식이 올바르지 않습니다.' },
  RATIONALE_EVIDENCE_DUPLICATE: { field: 'evidence_ids', message: '대표 로그가 중복되었습니다.' },
  RATIONALE_EVIDENCE_OUTSIDE_GROUP: { field: 'evidence_ids', message: '대표 로그에 해당 그룹 밖의 로그가 포함되었습니다.' },
  RATIONALE_REFERENCE_TYPE: { field: 'reference_groups', message: '참고 사례 목록의 형식이 올바르지 않습니다.' },
  RATIONALE_REFERENCE_COUNT: { field: 'reference_groups', message: '참고 사례 수가 허용 범위에 맞지 않습니다.' },
  RATIONALE_REFERENCE_ENTRY_SHAPE: { field: 'reference_groups', message: '참고 사례 항목의 형식이 올바르지 않습니다.' },
  RATIONALE_REFERENCE_ENTRY_VALUE: { field: 'reference_groups', message: '참고 사례 항목의 값이 올바르지 않습니다.' },
  RATIONALE_REFERENCE_CASE_UNKNOWN: { field: 'reference_groups', message: '제공되지 않은 참고 사례가 지정되었습니다.' },
  RATIONALE_REFERENCE_GROUP_UNKNOWN: { field: 'reference_groups', message: '제공되지 않은 참고 그룹이 지정되었습니다.' },
  RATIONALE_REFERENCE_DUPLICATE: { field: 'reference_groups', message: '참고 사례가 중복되었습니다.' },
  RATIONALE_REFERENCES_MISMATCH: { field: 'reference_ids', message: '참고 사례 목록이 일치하지 않습니다.' },
}
export const aiValidationMessage = (job: CodexJob) => {
  const detail = job.validation_failure
  if (job.status !== 'FAILED' && !(aiJobActive(job) && (job.retry_after || job.progress?.retry_after))) return null
  if (!detail || detail.schema_version !== 1 || typeof detail.code !== 'string' || !Object.hasOwn(rationaleDiagnostics, detail.code)) return null
  const known = rationaleDiagnostics[detail.code]
  if (detail.field !== known.field) return null
  const safeNumber = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 1_000_000
  const parts = [known.message]
  if (safeNumber(detail.group_index)) parts.push(`응답 내 그룹 순번 ${detail.group_index + 1}`)
  if (safeNumber(detail.actual_count)) parts.push(`항목 수 ${detail.actual_count.toLocaleString()}개`)
  if (safeNumber(detail.actual_length)) parts.push(`설명 길이 ${detail.actual_length.toLocaleString()}자`)
  return parts.join(' · ')
}
export const aiJobParallel = (job?: CodexJob | null) => job?.progress?.execution === 'parallel-v1' || job?.progress?.execution === 'parallel-v2'
export const aiJobMessage = (job: CodexJob) => {
  if (job.status === 'COMPLETED' && job.completion_reason === 'MAX_ITERATIONS') return '통합 반복 상한에 도달해 현재 그룹 구성으로 완료했습니다. 그룹을 검토해 확정해 주세요.'
  if (job.status === 'PENDING' && job.preparation_phase === 'PREPARING') return 'VWP 작업자가 전체 로그와 참고 사례를 준비하고 있습니다.'
  if (aiJobActive(job) && (job.retry_after || job.progress?.retry_after)) return aiJobParallel(job) ? '일부 배치의 재시도가 예정되어 있습니다. 완료된 배치는 유지합니다.' : job.progress ? 'AI 요청을 다시 처리하기 전 잠시 대기 중입니다. 완료된 배치는 유지합니다.' : 'AI 요청을 다시 처리하기 전 잠시 대기 중입니다.'
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
    if (job.can_retry_unknown && job.unknown_recovery && job.progress?.mode === 'BATCH') return `${aiJobParallel(job) ? '일부 배치' : '마지막 배치'}의 호출 결과가 불명확합니다. 완료된 배치는 유지되며 자동 재호출하지 않습니다. ${warning}`
    const reason = job.unknown_recovery_error ? formatUnknownRecoveryError(job.unknown_recovery_error) || '배치 재시도 조건을 현재 확인할 수 없습니다. 작업 상태를 다시 확인해 주세요.' : '운영 담당자에게 작업 상태를 확인해 주세요.'
    return `AI 호출 결과가 불명확합니다. 자동 재호출하지 않습니다. ${reason} ${warning}`
  }
  if (job.status === 'STALE') return job.error === 'AI_CONFIG_CHANGED'
    ? 'AI 모델 목록이나 연결 설정이 변경되어 작업을 중단했습니다. 최신 설정을 확인한 뒤 다시 요청해 주세요.'
    : '로그·분류·판정 또는 참고 사례가 변경되어 결과를 적용하지 않았습니다. 최신 내용을 확인해 주세요.'
  if (job.status === 'CANCELLED') return 'AI 작업이 취소되었습니다. 이전 검토 내용은 유지됩니다.'
  if (job.status === 'COMPLETED') return 'AI 제안을 저장했습니다. 내용을 검토한 뒤 사람이 확정해 주세요.'
  return `${WORKSPACE_ERROR_MESSAGES[job.error || ''] || 'AI 작업을 완료하지 못했습니다. 기존 검토 내용은 유지됩니다.'}${job.error ? ` (${job.error})` : ''}`
}

export async function workspaceRequest<T>(path: string, options: { body?: unknown; csrf?: string; signal?: AbortSignal } = {}): Promise<T> {
  // Explicit opt-in only. Server/authentication failures never select the demo.
  if (!productionWorkbench() && new URLSearchParams(location.search).get('demo') === '1') {
    const { workspaceDemoRequest } = await import('@/features/demo/workspaceDemo')
    options.signal?.throwIfAborted()
    return workspaceDemoRequest<T>(path, options)
  }
  return fetchJson<T, WorkspaceError>(appUrl(`${workspaceApiBase()}${path}`), {
    body: options.body,
    signal: options.signal,
    headers: options.body === undefined ? undefined : (productionWorkbench() ? { 'X-Sanity-Request': '1' } : { 'X-CSRF-Token': options.csrf || '' }),
    createError: (code, status) => new WorkspaceError(code, status),
    networkErrorCode: 'NETWORK_ERROR',
    missingErrorCode: () => 'REQUEST_FAILED',
    nonJsonErrorCode: () => 'INVALID_RESPONSE',
  })
}

export const workspaceLabels: Record<string, string> = { GCA: 'GCA', VCLP: 'VC LP', SPYGLASS: 'SpyGlass', PASS: 'PASS', FAIL: 'FAIL', DEFER: '보류', UNKNOWN: '판정 보류', NOT_PERFORMED: '판정 미실행', COMPLETE: '완료', PARTIAL: '일부 해석', MISSING: '미수신' }
export const workspaceTime = (value: string | null) => value ? new Date(value).toLocaleString('ko-KR', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

// UNKNOWN remains the wire verdict; collection-only metadata explains its cause.
export function toolResultStatus(value: { check_status: string; evaluation_mode?: string; evaluation_status?: string }): string {
  return value.check_status === 'UNKNOWN' && (value.evaluation_mode === 'none' || value.evaluation_status === 'NOT_PERFORMED')
    ? 'NOT_PERFORMED' : value.check_status
}

export function reportDisplayName(report: WorkspaceReport) {
  if (report.source_storage !== 'PARTS_V2') return report.id
  const raw = report.submitted_at || report.analysis_created_at
  const at = raw ? new Date(raw) : null
  const label = report.item_id || report.rule || report.family
  return at && Number.isFinite(at.getTime()) ? `${label} · ${at.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })} 제출` : label
}
