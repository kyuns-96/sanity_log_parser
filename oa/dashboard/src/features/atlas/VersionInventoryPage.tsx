import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Download,
  FileSpreadsheet,
  FileText,
  Layers3,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { useAtlas } from './atlasContext'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import { useDashboardNavigation } from '@/lib/dashboardMode'
import WorkspaceRemoval from '@/features/workspace/WorkspaceRemoval'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type InventoryUpload = {
  id: string
  state: string
  execution_name?: string | null
  original_filename?: string | null
  uploaded_by?: string | null
  corner_label: string
  is_area_only: boolean
  is_power_layer: boolean
  storage_mode: string
  has_power: boolean
  eco_num?: string | null
  row_count?: number | null
  original_byte_length?: number | null
  compressed_byte_length?: number | null
  snapshot_id?: string | null
  created_at?: string | null
}

type InventoryCorner = {
  label: string
  is_area_only: boolean
  is_power_layer: boolean
  count: number
  latest_upload_id: string
  latest_created_at?: string | null
}

type InventoryItem = {
  block_id: string
  block_name: string
  block_area_id?: string | null
  version_id: string
  area_version_id?: string | null
  net_ver: string
  revision: string
  layout: string
  eco_num?: string | null
  is_por: boolean
  por_revision: number
  created_at?: string | null
  sanity: { total: number; pass: number; warn: number; fail: number; missing: number; latest_at?: string | null }
  area_power: {
    has_area: boolean
    has_power: boolean
    area_base_count: number
    power_layer_count: number
    failed_count: number
    total_uploads: number
    latest_at?: string | null
    corners: InventoryCorner[]
    uploads: InventoryUpload[]
  }
  latest_activity_at?: string | null
}

type InventoryResponse = {
  project: { id: string; name: string; description?: string; process?: string | null; can_write: boolean; can_manage: boolean; area_id?: string | null }
  area_enabled: boolean
  summary: { total_blocks: number; total_versions: number; por_count: number; with_reports: number; with_area: number; with_power: number; empty_versions: number }
  items: InventoryItem[]
}

type AtlasReportSummary = {
  id: string
  item_id?: string | null
  family?: string | null
  rule?: string | null
  check_status?: string | null
  parse_status?: string | null
  record_count?: number | null
  eco?: string | null
}

type UploadDeletePreview = {
  upload_id: string
  execution_name?: string | null
  original_filename?: string | null
  is_power_layer: boolean
  counts: { dependent_power_layers: number; snapshot_siblings: number; comparisons: number; tracking_records: number }
  is_standalone_unused: boolean
  delete_available: boolean
  can_delete: boolean
  can_force_delete: boolean
}

type StatusFilter = 'all' | 'por' | 'sanity' | 'area' | 'power' | 'empty'

const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString('ko-KR', { hour12: false }) : '—'
const formatBytes = (value?: number | null) => {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(2)} MB`
}

export default function VersionInventoryPage() {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const navigate = useDashboardNavigation()
  const catalog = atlas.catalog
  const projects = catalog?.projects || []

  const [selectedProjectId, setSelectedProjectId] = useState(() => atlas.scope.projectId || projects[0]?.id || '')
  const [selectedBlockId, setSelectedBlockId] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [data, setData] = useState<InventoryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [reportsByVer, setReportsByVer] = useState<Record<string, { loading: boolean; items: AtlasReportSummary[]; error?: string }>>({})
  const [deletingUpload, setDeletingUpload] = useState<InventoryUpload | null>(null)
  const [porTarget, setPorTarget] = useState<InventoryItem | null>(null)

  useEffect(() => {
    if (atlas.scope.projectId && projects.some(p => p.id === atlas.scope.projectId)) {
      setSelectedProjectId(atlas.scope.projectId)
    } else if ((!selectedProjectId || !projects.some(p => p.id === selectedProjectId)) && projects.length) {
      setSelectedProjectId(projects[0].id)
    }
  }, [atlas.scope.projectId, projects, selectedProjectId])

  const loadInventory = useCallback(async () => {
    if (!selectedProjectId) { setData(null); return }
    setLoading(true); setError('')
    try {
      const qs = selectedBlockId ? `?block_id=${encodeURIComponent(selectedBlockId)}` : ''
      const res = await auth.request<InventoryResponse>(`/api/atlas/projects/${encodeURIComponent(selectedProjectId)}/version-inventory${qs}`)
      setData(res)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [auth.request, selectedProjectId, selectedBlockId])

  useEffect(() => {
    setExpanded(new Set())
    setReportsByVer({})
    void loadInventory()
  }, [loadInventory])

  const loadReportsForVersion = useCallback(async (item: InventoryItem) => {
    const key = `${item.block_id}:${item.version_id}`
    setReportsByVer(prev => ({ ...prev, [key]: { loading: true, items: prev[key]?.items || [] } }))
    try {
      const res = await auth.request<{ items: AtlasReportSummary[] }>(
        `/api/atlas/reports?project_id=${encodeURIComponent(selectedProjectId)}&block_id=${encodeURIComponent(item.block_id)}&version_id=${encodeURIComponent(item.version_id)}&limit=100`,
      )
      setReportsByVer(prev => ({ ...prev, [key]: { loading: false, items: res.items || [] } }))
    } catch (cause) {
      setReportsByVer(prev => ({ ...prev, [key]: { loading: false, items: [], error: cause instanceof Error ? cause.message : String(cause) } }))
    }
  }, [auth.request, selectedProjectId])

  function toggleRow(item: InventoryItem) {
    const key = `${item.block_id}:${item.version_id}`
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
        if (!reportsByVer[key]) void loadReportsForVersion(item)
      }
      return next
    })
  }

  async function downloadUpload(upload: InventoryUpload) {
    try {
      const blob = await auth.request<Blob>(`/api/area-power/uploads/${encodeURIComponent(upload.id)}/download`, undefined, 'blob')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${upload.id}.csv.gz`
      a.click()
      URL.revokeObjectURL(url)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  async function openScoped(page: string, item: InventoryItem) {
    const url = new URL(location.origin + '/')
    url.searchParams.set('page', page)
    url.searchParams.set('project', selectedProjectId)
    url.searchParams.set('block', item.block_id)
    url.searchParams.set('version', item.version_id)
    if (atlas.demo) url.searchParams.set('demo', '1')
    await navigate(url.pathname + url.search, auth.session?.user_id).catch(cause => setError(String(cause)))
  }

  const currentProject = projects.find(p => p.id === selectedProjectId)
  const blocks = currentProject?.blocks || []
  const isSSO = auth.session?.auth_method === 'sso' && !atlas.demo
  const canWrite = Boolean(data?.project.can_write || data?.project.can_manage)
  const logsPage = catalog?.local_mode ? 'review' : 'workbench'

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (data?.items || []).filter(item => {
      if (statusFilter === 'por' && !item.is_por) return false
      if (statusFilter === 'sanity' && item.sanity.total === 0) return false
      if (statusFilter === 'area' && !item.area_power.has_area) return false
      if (statusFilter === 'power' && !item.area_power.has_power) return false
      if (statusFilter === 'empty' && (item.sanity.total > 0 || item.area_power.total_uploads > 0)) return false
      if (needle) {
        const hay = `${item.block_name} ${item.net_ver} ${item.revision} ${item.eco_num || ''} ${item.layout}`.toLowerCase()
        if (!hay.includes(needle)) return false
      }
      return true
    })
  }, [data, search, statusFilter])

  return (
    <main className="atlas-home">
      <section className="atlas-home-heading">
        <div>
          <h1>버전 · 자료 현황</h1>
          <p className="text-sm text-muted-foreground">프로젝트별 모든 버전의 Sanity 보고서·Area 기준·Power 레이어 적재 현황을 한눈에 보고 관리합니다.</p>
        </div>
        <div className="atlas-actions">
          <button type="button" className="atlas-button secondary" disabled={loading} onClick={() => void loadInventory()}>
            <RefreshCw size={15} />새로고침
          </button>
        </div>
      </section>

      {error && <p className="atlas-message error" role="alert">{error}</p>}
      {notice && <p className="atlas-message" role="status">{notice}</p>}

      {data && (
        <section className="grid grid-cols-2 gap-3 md:grid-cols-6" aria-label="버전 자료 요약">
          <div className="rounded-lg border border-border bg-card p-3"><div className="text-xs text-muted-foreground">전체 버전</div><div className="mt-1 text-xl font-semibold">{data.summary.total_versions}</div></div>
          <div className="rounded-lg border border-border bg-card p-3"><div className="text-xs text-muted-foreground">PoR 지정 블록</div><div className="mt-1 text-xl font-semibold">{data.summary.por_count} / {data.summary.total_blocks}</div></div>
          <div className="rounded-lg border border-border bg-card p-3"><div className="text-xs text-muted-foreground">Sanity 수신 버전</div><div className="mt-1 text-xl font-semibold">{data.summary.with_reports}</div></div>
          <div className="rounded-lg border border-border bg-card p-3"><div className="text-xs text-muted-foreground">Area 확보 버전</div><div className="mt-1 text-xl font-semibold">{data.summary.with_area}</div></div>
          <div className="rounded-lg border border-border bg-card p-3"><div className="text-xs text-muted-foreground">Power 확보 버전</div><div className="mt-1 text-xl font-semibold">{data.summary.with_power}</div></div>
          <div className="rounded-lg border border-border bg-card p-3"><div className="text-xs text-muted-foreground">미수신(빈) 버전</div><div className="mt-1 text-xl font-semibold">{data.summary.empty_versions}</div></div>
        </section>
      )}

      <section className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card p-3" aria-label="필터">
        <label className="flex items-center gap-2 text-xs font-medium">
          프로젝트
          <select
            className="studio-native-select"
            value={selectedProjectId}
            onChange={e => { setSelectedProjectId(e.target.value); setSelectedBlockId('') }}
          >
            {!projects.length && <option value="">등록된 프로젝트 없음</option>}
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs font-medium">
          블록
          <select className="studio-native-select" value={selectedBlockId} onChange={e => setSelectedBlockId(e.target.value)}>
            <option value="">전체 블록 ({blocks.length})</option>
            {blocks.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </label>
        <div className="relative min-w-[200px] flex-1">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-2.5 text-muted-foreground" />
          <Input className="h-8 pl-8 text-xs" placeholder="NET_VER / REVISION / ECO / 블록 검색" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <div className="flex flex-wrap gap-1">
          {([
            ['all', '전체'],
            ['por', 'PoR만'],
            ['sanity', 'Sanity'],
            ['area', 'Area'],
            ['power', 'Power'],
            ['empty', '자료 없음'],
          ] as [StatusFilter, string][]).map(([key, label]) => (
            <Button key={key} size="sm" variant={statusFilter === key ? 'default' : 'outline'} onClick={() => setStatusFilter(key)}>
              {label}
            </Button>
          ))}
        </div>
      </section>

      <section className="overflow-x-auto rounded-lg border border-border bg-card">
        {loading && !data ? (
          <p className="p-6 text-sm text-muted-foreground" role="status">버전 현황을 불러오고 있습니다…</p>
        ) : !filtered.length ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            <Layers3 className="mx-auto mb-2 opacity-50" size={24} />
            조건에 맞는 버전이 없습니다.
          </div>
        ) : (
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-border bg-muted/40 text-muted-foreground">
                <th className="w-8 px-3 py-2.5"></th>
                <th className="px-3 py-2.5">블록</th>
                <th className="px-3 py-2.5">NET_VER · REVISION · ECO</th>
                <th className="px-3 py-2.5">Sanity 보고서</th>
                <th className="px-3 py-2.5">Area / Power 현황</th>
                <th className="px-3 py-2.5">최근 갱신</th>
                <th className="px-3 py-2.5 text-right">빠른 이동</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(item => {
                const key = `${item.block_id}:${item.version_id}`
                const isOpen = expanded.has(key)
                const reportState = reportsByVer[key]
                return (
                  <Fragment key={key}>
                    <tr
                      className={`cursor-pointer border-b border-border transition-colors hover:bg-muted/30 ${isOpen ? 'bg-muted/20' : ''}`}
                      onClick={() => toggleRow(item)}
                    >
                      <td className="px-3 py-2.5 text-muted-foreground">{isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</td>
                      <td className="px-3 py-2.5 font-medium">{item.block_name}</td>
                      <td className="px-3 py-2.5">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <strong>{item.net_ver}</strong>
                          <span className="text-muted-foreground">{item.revision}</span>
                          {item.eco_num && <Badge variant="outline">{item.eco_num}</Badge>}
                          <Badge variant="secondary">{item.layout}</Badge>
                          {item.is_por && <Badge className="gap-1"><ShieldCheck size={11} />PoR</Badge>}
                        </div>
                      </td>
                      <td className="px-3 py-2.5">
                        {item.sanity.total > 0 ? (
                          <div className="flex flex-wrap items-center gap-1">
                            <strong>{item.sanity.total}건</strong>
                            {item.sanity.pass > 0 && <Badge variant="outline">PASS {item.sanity.pass}</Badge>}
                            {item.sanity.warn > 0 && <Badge variant="secondary">WARN {item.sanity.warn}</Badge>}
                            {item.sanity.fail > 0 && <Badge variant="destructive">FAIL {item.sanity.fail}</Badge>}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">미수신</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        {item.area_power.total_uploads > 0 ? (
                          <div className="flex flex-wrap items-center gap-1">
                            {item.area_power.has_area && <Badge variant="outline">Area 기준 {item.area_power.area_base_count}</Badge>}
                            {item.area_power.power_layer_count > 0 && <Badge variant="secondary">Power 레이어 {item.area_power.power_layer_count}</Badge>}
                            {item.area_power.corners.map(c => (
                              <span key={c.label} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                                {c.label}{c.count > 1 ? ` ×${c.count}` : ''}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">미업로드</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-muted-foreground">{formatTime(item.latest_activity_at)}</td>
                      <td className="px-3 py-2.5 text-right" onClick={e => e.stopPropagation()}>
                        <div className="inline-flex items-center gap-1">
                          <Button size="sm" variant="ghost" onClick={() => void openScoped(logsPage, item)}>로그<ArrowRight size={12} /></Button>
                          {data?.area_enabled && <Button size="sm" variant="ghost" onClick={() => void openScoped('area-power', item)}>비교<ArrowRight size={12} /></Button>}
                        </div>
                      </td>
                    </tr>

                    {isOpen && (
                      <tr className="border-b border-border bg-muted/10">
                        <td colSpan={7} className="p-4">
                          <div className="space-y-4">
                            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3">
                              <div className="text-xs text-muted-foreground">
                                버전 상세 관리 · <strong>{item.block_name}</strong> / {item.net_ver} · {item.revision} {item.eco_num ? `(${item.eco_num})` : ''}
                              </div>
                              <div className="flex flex-wrap items-center gap-2">
                                {canWrite && !item.is_por && (
                                  <Button size="sm" variant="outline" onClick={() => setPorTarget(item)}>
                                    <ShieldCheck size={14} />이 버전을 PoR로 지정
                                  </Button>
                                )}
                                {isSSO && canWrite && (
                                  <WorkspaceRemoval
                                    base={`/blocks/${item.block_id}/versions/${item.version_id}`}
                                    title={`${item.block_name} · ${item.net_ver} · ${item.revision}`}
                                    kind="버전"
                                    onRemoved={() => {
                                      setNotice(`버전 ${item.net_ver} · ${item.revision}을 삭제했습니다.`)
                                      void atlas.reload()
                                      void loadInventory()
                                    }}
                                    onAccessDenied={() => void loadInventory()}
                                  />
                                )}
                              </div>
                            </div>

                            <div className="grid gap-4 lg:grid-cols-2">
                              <div className="rounded-md border border-border bg-background p-3">
                                <div className="mb-2 flex items-center gap-1.5 font-medium">
                                  <FileSpreadsheet size={14} />Area / Power CSV ({item.area_power.uploads.length}건)
                                </div>
                                {!item.area_power.uploads.length ? (
                                  <p className="text-xs text-muted-foreground">이 버전에 업로드된 Area/Power CSV가 없습니다.</p>
                                ) : (
                                  <ul className="divide-y divide-border">
                                    {item.area_power.uploads.map(u => (
                                      <li key={u.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                                        <div>
                                          <div className="flex flex-wrap items-center gap-1.5">
                                            <strong>{u.execution_name || u.original_filename || u.id.slice(0, 8)}</strong>
                                            <Badge variant="outline">{u.corner_label}</Badge>
                                            {u.is_power_layer && <Badge variant="secondary">Power 레이어</Badge>}
                                            {u.state !== 'ready' && <Badge variant="destructive">{u.state}</Badge>}
                                          </div>
                                          <div className="mt-0.5 text-[11px] text-muted-foreground">
                                            {u.row_count != null ? `${u.row_count.toLocaleString()}행 · ` : ''}
                                            저장 {formatBytes(u.compressed_byte_length)} · {formatTime(u.created_at)}
                                          </div>
                                        </div>
                                        <div className="flex items-center gap-1">
                                          <Button size="sm" variant="ghost" aria-label="원본 gzip 다운로드" onClick={() => void downloadUpload(u)}>
                                            <Download size={14} />
                                          </Button>
                                          {canWrite && !atlas.demo && (
                                            <Button size="sm" variant="ghost" aria-label="CSV 삭제" onClick={() => setDeletingUpload(u)}>
                                              <Trash2 size={14} />
                                            </Button>
                                          )}
                                        </div>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </div>

                              <div className="rounded-md border border-border bg-background p-3">
                                <div className="mb-2 flex items-center gap-1.5 font-medium">
                                  <FileText size={14} />Sanity 보고서 ({item.sanity.total}건)
                                </div>
                                {reportState?.loading ? (
                                  <p className="text-xs text-muted-foreground">보고서 목록을 불러오고 있습니다…</p>
                                ) : reportState?.error ? (
                                  <p className="text-xs text-destructive">{reportState.error}</p>
                                ) : !(reportState?.items.length) ? (
                                  <p className="text-xs text-muted-foreground">이 버전에 수신된 Sanity 보고서가 없습니다.</p>
                                ) : (
                                  <ul className="divide-y divide-border">
                                    {reportState.items.map(r => (
                                      <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                                        <div>
                                          <div className="flex flex-wrap items-center gap-1.5">
                                            <strong>{r.item_id || r.id}</strong>
                                            {r.family && <span className="text-muted-foreground">{r.family}</span>}
                                            {r.check_status && <Badge variant={r.check_status === 'FAIL' ? 'destructive' : 'outline'}>{r.check_status}</Badge>}
                                          </div>
                                          <div className="mt-0.5 text-[11px] text-muted-foreground">
                                            {r.record_count != null ? `로그 ${r.record_count.toLocaleString()}건` : ''}{r.eco ? ` · ECO ${r.eco}` : ''}
                                          </div>
                                        </div>
                                        {isSSO && canWrite && (
                                          <WorkspaceRemoval
                                            base={`/blocks/${item.block_id}/versions/${item.version_id}/reports/${r.id}`}
                                            title={r.item_id || r.id}
                                            kind="보고서"
                                            onRemoved={() => {
                                              setNotice(`보고서 ${r.item_id || r.id}을 삭제했습니다.`)
                                              void loadReportsForVersion(item)
                                              void loadInventory()
                                            }}
                                            onAccessDenied={() => void loadInventory()}
                                          />
                                        )}
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      {deletingUpload && (
        <CsvDeleteDialog
          upload={deletingUpload}
          api={auth.request}
          onClose={() => setDeletingUpload(null)}
          onDeleted={async () => {
            setDeletingUpload(null)
            setNotice(`CSV ‘${deletingUpload.execution_name || deletingUpload.original_filename || deletingUpload.id}’을 삭제했습니다.`)
            await loadInventory()
          }}
        />
      )}

      {porTarget && (
        <QuickPorDialog
          projectId={selectedProjectId}
          item={porTarget}
          api={auth.request}
          onClose={() => setPorTarget(null)}
          onAssigned={async () => {
            setPorTarget(null)
            setNotice(`${porTarget.block_name}의 PoR을 ${porTarget.net_ver} · ${porTarget.revision}로 지정했습니다.`)
            await atlas.reload()
            await loadInventory()
          }}
        />
      )}
    </main>
  )
}

function CsvDeleteDialog({ upload, api, onClose, onDeleted }: {
  upload: InventoryUpload
  api: <T>(path: string, body?: Record<string, unknown>) => Promise<T>
  onClose: () => void
  onDeleted: () => Promise<void>
}) {
  const [preview, setPreview] = useState<UploadDeletePreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api<UploadDeletePreview>(`/api/area-power/uploads/${encodeURIComponent(upload.id)}/delete-preview`)
      .then(d => { if (active) setPreview(d) })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api, upload.id])

  const canSubmit = Boolean(preview?.delete_available && (preview.is_standalone_unused || force))
  async function confirmDelete() {
    setBusy(true); setError('')
    try {
      await api(`/api/area-power/uploads/${encodeURIComponent(upload.id)}/delete`, {
        force: Boolean(!preview?.is_standalone_unused && force),
        reason: '',
      })
      await onDeleted()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>CSV 삭제 · {upload.execution_name || upload.original_filename || upload.id.slice(0, 8)}</DialogTitle>
          <DialogDescription>Corner: {upload.corner_label}{upload.is_power_layer ? ' · Power 레이어' : ''}</DialogDescription>
        </DialogHeader>
        {error && <p className="am-error" role="alert">{error}</p>}
        {loading || !preview ? (
          <p className="text-xs text-muted-foreground">연관 데이터를 확인하고 있습니다…</p>
        ) : (
          <div className="space-y-3 text-xs">
            <div className="rounded-md border border-border bg-muted/30 p-3">
              <div>연관 Power 레이어: <strong>{preview.counts.dependent_power_layers}건</strong> · 동일 snapshot 형제: <strong>{preview.counts.snapshot_siblings}건</strong></div>
              <div>연관 비교 결과: <strong>{preview.counts.comparisons}건</strong> · 추적 레코드: <strong>{preview.counts.tracking_records}건</strong></div>
            </div>
            {!preview.delete_available && <p className="text-destructive">DB 계정에 DELETE 권한이 없어 삭제할 수 없습니다.</p>}
            {preview.delete_available && !preview.is_standalone_unused && (
              <label className="flex cursor-pointer items-center gap-2 text-destructive">
                <input type="checkbox" checked={force} disabled={busy} onChange={e => setForce(e.target.checked)} />
                <strong>연관 Power 레이어·비교 결과까지 함께 영구 삭제</strong>
              </label>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>취소</Button>
          <Button variant="destructive" disabled={busy || !canSubmit} onClick={() => void confirmDelete()}>
            {busy ? '삭제 중…' : preview && !preview.is_standalone_unused ? '연관 데이터 포함 삭제' : 'CSV 삭제'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function QuickPorDialog({ projectId, item, api, onClose, onAssigned }: {
  projectId: string
  item: InventoryItem
  api: <T>(path: string, body?: Record<string, unknown>) => Promise<T>
  onClose: () => void
  onAssigned: () => Promise<void>
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [requestId] = useState(() => crypto.randomUUID())

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!reason.trim()) return
    setBusy(true); setError('')
    try {
      await api(`/api/atlas/blocks/${encodeURIComponent(item.block_id)}/por`, {
        project_id: projectId,
        version_id: item.version_id,
        expected_revision: item.por_revision,
        request_id: requestId,
        reason: reason.trim(),
      })
      await onAssigned()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>PoR 지정 · {item.block_name}</DialogTitle>
          <DialogDescription>{item.net_ver} · {item.revision} ({item.layout})을 이 블록의 PoR로 지정합니다.</DialogDescription>
        </DialogHeader>
        <form className="am-form am-dialog-form" onSubmit={submit}>
          {error && <p className="am-error" role="alert">{error}</p>}
          <label>
            지정 사유
            <Textarea value={reason} required rows={2} maxLength={500} disabled={busy} onChange={e => setReason(e.target.value)} />
          </label>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>취소</Button>
            <Button type="submit" disabled={busy || !reason.trim()}>{busy ? '저장 중…' : 'PoR 지정 확정'}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
