import { z } from 'zod'

const count = z.union([z.string(), z.number()]).transform(Number).refine(v => Number.isSafeInteger(v) && v >= 0)
const optionalText = z.string().nullish()
const loose = z.record(z.string(), z.unknown())

export const cornerSpecSchema = z.object({
  label: z.string(),
  process_label: z.string(),
  voltage_v: optionalText,
  temp_c: optionalText,
  pex: optionalText,
}).passthrough()
export type CornerSpec = z.infer<typeof cornerSpecSchema>

export const mailDeliverySchema = z.object({
  state: z.string(),
  mail_id: optionalText,
  trigger: optionalText,
  frozen_at: optionalText,
  recipients: z.array(z.string()).optional(),
  job_id: optionalText,
  job_state: optionalText,
  phase: optionalText,
  accepted_recipients: z.array(z.string()).optional(),
  rejected_recipients: z.array(z.string()).optional(),
  error: z.unknown().nullish(),
}).passthrough()
export type MailDelivery = z.infer<typeof mailDeliverySchema>

export const trackingCornerStatusSchema = z.object({
  slot_id: optionalText,
  corner_id: z.string(),
  corner_label: z.string(),
  status: z.string(),
  status_reason: optionalText,
  upload_id: optionalText,
  baseline_upload_id: optionalText,
  comparison_id: optionalText,
  ai_run_id: optionalText,
  mapping_check: loose.nullish(),
  last_failed_upload: loose.nullish(),
  post_top_mail_revised: z.boolean().optional(),
  logic_area_delta_pct: optionalText,
  power_current_mw: optionalText,
  power_delta_pct: optionalText,
  counts: loose.optional(),
}).passthrough()

export const trackingBlockRowSchema = z.object({
  block_id: z.string(),
  block_name: z.string(),
  is_full_chip: z.boolean(),
  revision_label: z.string().optional(),
  required_corners: count,
  arrived_corners: count,
  completed_corners: count,
  missing_corners: z.array(z.string()),
  all_completed: z.boolean(),
  totals: z.record(z.string(), count).optional(),
  power_evaluation: loose.nullish(),
  corners: z.array(trackingCornerStatusSchema),
  post_top_mail_revised: z.boolean(),
  status_summary: z.string(),
  next_action: loose,
  block_mail: mailDeliverySchema.optional(),
  needs_attention: z.boolean(),
}).passthrough()
export type TrackingBlockRow = z.infer<typeof trackingBlockRowSchema>

export const trackingRoundSchema = z.object({
  id: z.string(),
  row_version: z.union([z.string(), z.number()]).transform(String),
  state: z.string(),
  net_ver: z.string(),
  netlist_arrived_at: optionalText,
  deadline_date: optionalText,
  deadline_time: optionalText,
  deadline_timezone: optionalText,
  deadline_at: optionalText,
  target_block_ids: z.array(z.string()),
  full_chip_counts: z.object({ total: count, arrived: count, completed: count }),
  sub_block_counts: z.object({ total: count, arrived: count, completed: count }),
  top_mail: mailDeliverySchema,
  has_post_top_revisions: z.boolean().optional(),
  blocks: z.array(trackingBlockRowSchema),
}).passthrough()
export type TrackingRound = z.infer<typeof trackingRoundSchema>

export const trackingOverviewSchema = z.object({
  project_id: z.string(),
  project_name: z.string(),
  project_config: loose,
  block_configs: z.array(loose),
  rounds: z.array(trackingRoundSchema),
  expectations: z.array(loose),
  expectation_proposals: z.array(loose),
  ai_model: z.object({
    ready: z.boolean(),
    error: optionalText,
    connection_label: optionalText,
    model: optionalText,
    site: optionalText,
  }).optional(),
}).passthrough()
export type TrackingOverviewData = z.infer<typeof trackingOverviewSchema>

export const trackingPreviewSchema = z.object({
  round: z.object({ id: z.string(), net_ver: z.string() }).nullable(),
  top_mail_preview: z.object({ subject: z.string(), body: z.string() }).nullable(),
  block_mail_previews: z.array(z.object({
    block_id: z.string(),
    block_name: z.string(),
    subject: z.string(),
    body: z.string(),
  })),
})
export type TrackingPreviewData = z.infer<typeof trackingPreviewSchema>

export const trackingMailSnapshotSchema = z.object({
  id: z.string(),
  kind: z.string(),
  project_id: z.string(),
  block_id: optionalText,
  round_id: z.string(),
  created_at: optionalText,
  delivery: mailDeliverySchema,
  subject: optionalText,
  body: optionalText,
  snapshot: loose,
  has_newer_results: z.boolean(),
}).passthrough()
export type TrackingMailSnapshot = z.infer<typeof trackingMailSnapshotSchema>

export const sentReportSchema = z.object({
  id: z.string(),
  kind: z.string(),
  round_id: optionalText,
  net_ver: z.string(),
  block_id: optionalText,
  block_name: z.string(),
  is_full_chip: z.boolean(),
  revision_label: z.string().optional(),
  trigger: z.string().optional(),
  created_at: optionalText,
  frozen_at: optionalText,
  delivery: mailDeliverySchema,
  has_newer_results: z.boolean(),
  subject: z.string(),
  body: z.string(),
  snapshot: loose,
}).passthrough()
export type SentReport = z.infer<typeof sentReportSchema>

export const liveRoundReportsSchema = z.object({
  round_id: z.string(),
  net_ver: z.string(),
  deadline_date: optionalText,
  deadline_time: optionalText,
  top_mail_sent: z.boolean(),
  top_report: z.object({ subject: z.string(), body: z.string(), snapshot: loose }),
  block_reports: z.array(z.object({
    block_id: z.string(),
    block_name: z.string(),
    is_full_chip: z.boolean(),
    revision_label: z.string().optional(),
    mail_sent: z.boolean(),
    subject: z.string(),
    body: z.string(),
    snapshot: loose,
  })),
}).passthrough()
export type LiveRoundReports = z.infer<typeof liveRoundReportsSchema>

export const trackingReportsSchema = z.object({
  project_id: z.string(),
  project_name: z.string(),
  sent_reports: z.array(sentReportSchema),
  live_reports: z.array(liveRoundReportsSchema),
}).passthrough()
export type TrackingReportsData = z.infer<typeof trackingReportsSchema>

export const MAIL_STATE_LABEL: Record<string, string> = {
  waiting: '발송 대기',
  requested: '발송 요청됨',
  accepted: '접수 완료',
  partially_accepted: '일부 거부',
  rejected: '수신자 거부',
  ambiguous: '결과 불명',
  failed: '발송 실패',
}

export const SLOT_STATE_LABEL: Record<string, string> = {
  waiting_upload: '미도착',
  waiting_baseline: '기준 실행 미고정',
  mapping_review: '매핑 확인 필요',
  queued: '분석 대기',
  running: '분석 중',
  completed: '완료',
  failed: '분석 실패',
  auth_error: '인증 오류',
}

export function cornerSpecId(corner: { process_label: string; voltage_v?: string | null; temp_c?: string | null; pex?: string | null }): string {
  const norm = (value?: string | null) => {
    if (!value || !value.trim()) return '*'
    const trimmed = value.trim()
    const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(trimmed)
    if (!match) return trimmed
    const intPart = match[2].replace(/^0+/, '') || '0'
    const fracPart = (match[3] || '').replace(/0+$/, '')
    return `${match[1] === '-' && (intPart !== '0' || fracPart) ? '-' : ''}${intPart}${fracPart ? '.' + fracPart : ''}`
  }
  return [
    corner.process_label.trim(),
    norm(corner.voltage_v),
    norm(corner.temp_c),
    corner.pex?.trim() || '*',
  ].join('|')
}
