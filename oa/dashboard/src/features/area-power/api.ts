import { isLosslessNumber, parse, stringify } from 'lossless-json'
import { z } from 'zod'

export type Payload = Record<string, unknown>
export interface Auth {
  actor: string
  demo?: boolean
  request: <T>(path: string, body?: Payload | string, plain?: boolean | 'blob', signal?: AbortSignal) => Promise<T>
}
export interface Filters { project_id: string; version_id: string; block_id: string; snapshot_id: string }
export const object = (value: unknown): Payload => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Payload : {}
export const text = (value: unknown): string => value == null ? '' : typeof value === 'object' ? pretty(value) : String(value)
export const array = (value: unknown): unknown[] => Array.isArray(value) ? value : []
const optionalText = z.string().nullish()
export const recordSchema = z.object({
  id: z.string(), entity_id: z.string(), record_type: z.string(), revision: z.string(), row_version: z.string(),
  state: z.string(), payload: z.record(z.string(), z.unknown()), created_at: optionalText, updated_at: optionalText,
  project_id: optionalText, version_id: optionalText, block_id: optionalText, snapshot_id: optionalText,
  parent_id: optionalText, upload_id: optionalText, baseline_upload_id: optionalText,
  consumer_server: optionalText, job_kind: optionalText, claim_owner: optionalText,
  read_at: optionalText, acknowledged_at: optionalText, resolved_at: optionalText,
}).passthrough()
export const uploadSchema = z.object({
  id: z.string(), state: z.string(), project_id: z.string(), version_id: z.string(), block_id: z.string(), snapshot_id: optionalText,
  execution_name: optionalText, original_filename: optionalText, filename: optionalText,
  process_label: optionalText, voltage_v: optionalText, temp_c: optionalText, pex: optionalText,
  condition_key: optionalText, created_at: optionalText, root_summary: z.unknown().optional(),
}).passthrough()
export type TrackingRecord = z.infer<typeof recordSchema>
export type Upload = z.infer<typeof uploadSchema>
export const dashboardSchema = z.object({
  uploads: z.array(uploadSchema), records: z.array(recordSchema), jobs: z.array(recordSchema),
  alerts: z.array(recordSchema), comparisons: z.array(recordSchema), counts: z.record(z.string(), z.unknown()),
  root_metrics: z.record(z.string(), z.unknown()).nullable(),
})
export type Dashboard = z.infer<typeof dashboardSchema>
export const detailSchema = z.object({
  comparison: recordSchema, results: z.array(recordSchema), judgements: z.array(recordSchema), ai_runs: z.array(recordSchema),
  observations: z.array(z.record(z.string(), z.unknown())).optional(),
  hierarchy_nodes: z.array(z.object({ group_id: z.string(), parent_id: z.string().nullable(), child_count: z.union([z.string(), z.number()]).transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0), hidden_child_count: z.union([z.string(), z.number()]).transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0).optional() })).nullable().optional(),
  result_page: z.object({
    offset: z.string().transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0),
    limit: z.string().transform(Number).refine(value => Number.isSafeInteger(value) && value > 0),
    total_groups: z.string().transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0),
    hidden_groups: z.union([z.string(), z.number()]).transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0).optional(),
    search: z.string(),
  }).optional(),
})
export type ComparisonDetail = z.infer<typeof detailSchema>
export const comparisonCreatedSchema = z.object({ comparison: recordSchema, job: recordSchema.nullable(), reused: z.boolean() })
// AI instance triage. Numbers arrive as exact strings; counts are small integers.
const count = z.union([z.string(), z.number()]).transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0)
const loose = z.record(z.string(), z.unknown())
export const triageNoteSchema = z.object({ id: z.string(), kind: z.string(), net_ver: z.string().nullish(), text: z.string().optional() }).passthrough()
export const triagePreviewSchema = z.object({
  counts: z.record(z.string(), count), estimated_calls: count, max_items: count, over_limit: z.boolean(),
  attribution: loose.nullish(),
  missing_watched: z.array(z.string()), watched_paths: z.array(z.string()), context: loose,
  notes: z.array(triageNoteSchema), note_warnings: z.array(loose), same_rtl: z.boolean(),
  applicable_net_vers: z.array(z.string()), process: loose,
  ai_model: z.object({ ready: z.boolean(), error: z.string().nullish(), connection_label: z.string().nullish(), model: z.string().nullish(), site: z.string().nullish() }).optional(),
})
export type TriagePreview = z.infer<typeof triagePreviewSchema>
export const triageRunSchema = z.object({ can_delete: z.boolean().optional(), id: z.string(), state: z.string(), created_at: optionalText, error_detail: z.unknown().nullish(), payload: loose, note_count: count.optional(), notes: z.array(triageNoteSchema).optional() }).passthrough()
export type TriageRun = z.infer<typeof triageRunSchema>
export const triageRunsSchema = z.object({ runs: z.array(triageRunSchema) })
export const triageItemSchema = z.object({ group_id: z.string(), verdict: z.string(), cause: z.string(), explanation: z.string(), failed: z.boolean(), failure: z.string().nullish(), quotes: z.array(z.string()).optional(), card: loose }).passthrough()
export type TriageItem = z.infer<typeof triageItemSchema>
export const triageJobSchema = z.object({
  id: z.string(),
  state: z.string(),
  job_kind: optionalText,
  created_at: optionalText,
  claimed_at: optionalText,
  started_at: optionalText,
  finished_at: optionalText,
  error_detail: z.unknown().nullish(),
  claimed: z.boolean().optional(),
}).passthrough()
export type TriageJob = z.infer<typeof triageJobSchema>
export const triageQueueSchema = z.object({
  pending_ahead: count.optional(),
  total_pending: count.optional(),
  other_running_count: count.optional(),
  other_running_job_kind: optionalText,
  other_running_since: optionalText,
}).passthrough()
export type TriageQueue = z.infer<typeof triageQueueSchema>
export const triageWorkerSchema = z.object({
  online: z.boolean().nullable().optional(),
  state: optionalText,
  current_job_kind: optionalText,
  heartbeat_at: optionalText,
  last_job_activity_at: optionalText,
}).passthrough()
export type TriageWorker = z.infer<typeof triageWorkerSchema>
export const triageResultSchema = z.object({
  ai_run: triageRunSchema,
  items: z.array(triageItemSchema),
  mails: z.array(loose),
  job: triageJobSchema.nullish(),
  queue: triageQueueSchema.nullish(),
  worker: triageWorkerSchema.nullish(),
  server_time: optionalText,
})
export type TriageResult = z.infer<typeof triageResultSchema>
export const triageStartedSchema = z.object({ ai_run: recordSchema, job: recordSchema.nullable() })
export const unknownSchema = z.unknown()

// No server numeric value passes through IEEE-754 before display.
function exact(value: unknown): unknown {
  if (isLosslessNumber(value)) return value.toString()
  if (Array.isArray(value)) return value.map(exact)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, exact(entry)]))
  return value
}
export const pretty = (value: unknown) => stringify(value, null, 2) || 'null'
export function jsonObject(source: string): Payload {
  const result: unknown = parse(source)
  if (!result || typeof result !== 'object' || Array.isArray(result) || isLosslessNumber(result)) throw new Error('JSON 객체를 입력하세요. 배열이나 단일 값은 허용되지 않습니다.')
  return object(result)
}
export const revision = (value: unknown): number => {
  const result = Number(String(value))
  if (!Number.isSafeInteger(result) || result < 1) throw new Error('유효한 row_version이 없습니다. 새로고침하세요.')
  return result
}
export function query(values: Record<string, string>): string {
  const params = new URLSearchParams(Object.entries(values).filter(([, value]) => value !== ''))
  return params.size ? `?${params}` : ''
}
export async function request<T>(auth: Auth, path: string, schema: z.ZodType<T>, body?: unknown, signal?: AbortSignal): Promise<T> {
  const source = await auth.request<string>(`/api/area-power${path}`, body === undefined ? undefined : stringify(body), true, signal)
  let value: unknown
  try { value = source ? exact(parse(source)) : null } catch { throw new Error('Area/Power API 응답을 읽을 수 없습니다.') }
  const result = schema.safeParse(value)
  if (!result.success) {
    // Only the API route and top-level schema field: never response values,
    // search text, instance paths, credentials or provider responses.
    const route = path.split('?')[0].replace(/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/gi, ':id')
    const issue = result.error.issues[0]
    const field = typeof issue.path[0] === 'string' ? issue.path[0] : 'response'
    throw new Error(`프로젝트 비교 API 응답 형식이 맞지 않습니다. (${body === undefined ? 'GET' : 'POST'} /api/area-power${route} · ${field} · ${issue.code})`)
  }
  return result.data
}
export async function allRecords(auth: Auth, type: string, filters: Record<string, string> = {}, signal?: AbortSignal): Promise<TrackingRecord[]> {
  const items: TrackingRecord[] = []
  let offset = 0
  while (true) {
    const page = await request(auth, `/records${query({ ...filters, record_type: type, limit: '200', offset: String(offset) })}`, z.object({ items: z.array(recordSchema) }), undefined, signal)
    items.push(...page.items)
    if (page.items.length < 200) return items
    offset += page.items.length
  }
}
export async function allUploads(auth: Auth, signal?: AbortSignal): Promise<Upload[]> {
  const items: Upload[] = []
  let offset = 0
  while (true) {
    const page = await request(auth, `/uploads${query({ limit: '200', offset: String(offset) })}`, z.object({ items: z.array(uploadSchema) }), undefined, signal)
    items.push(...page.items)
    if (page.items.length < 200) return items
    offset += page.items.length
  }
}
export function format(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  const source = String(value)
  const match = /^([+-]?)(\d+)(\.\d+)?$/.exec(source)
  return match ? `${match[1]}${match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${match[3] || ''}` : source
}
export function formatPercent(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  const source = String(value)
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(source)
  if (!match) return format(value)
  const fractional = match[3] || ''
  const coefficient = BigInt(match[2] + fractional)
  const exponent = BigInt(match[4] || '0') - BigInt(fractional.length) + 2n
  const divisor = exponent < 0n ? 10n ** -exponent : 1n
  const scaled = exponent < 0n ? (coefficient * 2n + divisor) / (divisor * 2n) : coefficient * 10n ** exponent
  return format(`${match[1] === '-' && scaled ? '-' : ''}${scaled / 100n}.${(scaled % 100n).toString().padStart(2, '0')}`)
}
export const nameOf = (record: TrackingRecord) => text(record.payload.name || record.payload.text || record.id)
function formatCornerNumber(value: string | null | undefined, maxDecimals: number): string {
  const source = value?.trim() || ''
  if (!source) return ''
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(source)
  if (!match) return source
  const fractional = match[3] || ''
  const coefficient = BigInt(match[2] + fractional)
  if (coefficient === 0n) return '0'
  const shift = BigInt(match[4] || '0') - BigInt(fractional.length) + BigInt(maxDecimals)
  if (shift > 50n || shift < -100n) return source
  let scaled: bigint
  if (shift < 0n) {
    const divisor = 10n ** -shift
    scaled = (coefficient * 2n + divisor) / (divisor * 2n)
  } else {
    scaled = coefficient * (10n ** shift)
  }
  if (scaled === 0n) return '0'
  const sign = match[1] === '-' ? '-' : ''
  const unit = 10n ** BigInt(maxDecimals)
  const whole = scaled / unit
  const rem = scaled % unit
  if (rem === 0n) return `${sign}${whole}`
  const trimmedFrac = rem.toString().padStart(maxDecimals, '0').replace(/0+$/, '')
  return `${sign}${whole}.${trimmedFrac}`
}
export function formatCornerLabel(upload: Pick<Upload, 'process_label' | 'voltage_v' | 'temp_c' | 'pex'>): string {
  const voltage = formatCornerNumber(upload.voltage_v, 3)
  const temp = formatCornerNumber(upload.temp_c, 1)
  const parts = [
    upload.process_label?.trim() || '',
    voltage ? `${voltage}V` : '',
    temp ? `${temp}°C` : '',
    upload.pex?.trim() || '',
  ].filter(Boolean)
  return parts.length ? parts.join(' / ') : '코너 미기재'
}
export const uploadName = (upload: Upload) => `${upload.execution_name || upload.original_filename || upload.filename || upload.id} · ${formatCornerLabel(upload)} · ${upload.id.slice(0, 8)}`
// Match the backend's Decimal identity without converting PVT values to Number.
function cornerDecimal(value: string | null | undefined) {
  if (value == null) return null
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(value)
  if (!match) return value
  const fractional = match[3] || ''
  const digits = (match[2] + fractional).replace(/^0+/, '')
  if (!digits) return '0'
  const coefficient = digits.replace(/0+$/, '')
  const exponent = BigInt(match[4] || '0') - BigInt(fractional.length) + BigInt(digits.length - coefficient.length)
  return `${match[1] === '-' ? '-' : ''}${coefficient}e${exponent}`
}
export function cornerKey(upload: Pick<Upload, 'process_label' | 'voltage_v' | 'temp_c' | 'pex'>) {
  return JSON.stringify([upload.process_label ?? null, cornerDecimal(upload.voltage_v), cornerDecimal(upload.temp_c), upload.pex ?? null])
}
export const date = (value?: string | null) => value ? new Date(value).toLocaleString('ko-KR', { hour12: false }) : '—'

export const accessSchema = z.object({
  actor: z.string(), is_admin: z.boolean(), local_mode: z.boolean(),
  projects: z.array(z.object({ id: z.string(), name: z.string(), blocks: z.array(z.object({ id: z.string(), name: z.string() })) })),
})
export type AreaAccess = z.infer<typeof accessSchema>
