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
export const detailSchema = z.object({ comparison: recordSchema, results: z.array(recordSchema), judgements: z.array(recordSchema), ai_runs: z.array(recordSchema) })
export type ComparisonDetail = z.infer<typeof detailSchema>
export const comparisonCreatedSchema = z.object({ comparison: recordSchema, job: recordSchema.nullable(), reused: z.boolean() })
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
  if (!result.success) throw new Error('OA API 응답 형식이 계약과 다릅니다. 서비스 버전과 연결 상태를 확인하세요.')
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
export const nameOf = (record: TrackingRecord) => text(record.payload.name || record.payload.text || record.id)
export const uploadName = (upload: Upload) => `${upload.execution_name || upload.original_filename || upload.filename || upload.id} · ${upload.process_label ?? '?'} / ${upload.voltage_v ?? '?'} V / ${upload.temp_c ?? '?'} °C / ${upload.pex ?? '?'} · ${upload.id.slice(0, 8)}`
export const date = (value?: string | null) => value ? new Date(value).toLocaleString('ko-KR', { hour12: false }) : '—'

export const accessSchema = z.object({
  actor: z.string(), is_admin: z.boolean(), local_mode: z.boolean(),
  projects: z.array(z.object({ id: z.string(), name: z.string(), blocks: z.array(z.object({ id: z.string(), name: z.string() })) })),
})
export type AreaAccess = z.infer<typeof accessSchema>
