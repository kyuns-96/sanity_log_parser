import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Download, GitCompareArrows, LoaderCircle, Plus, RefreshCw, Trash2, Upload as UploadIcon } from 'lucide-react'
import type { z } from 'zod'
import { comparisonCreatedSchema, cornerKey, date, detailSchema, format, object, recordSchema, request, text, uploadName, type AreaAccess, type Auth, type ComparisonDetail, type TrackingRecord, type TriageItem, type Upload } from './api'
import { Badge, Button, Empty, Field, useAction } from './components/workspace'
import { Uploads } from './UploadViews'
import { useAtlas, type AtlasProject } from '@/features/atlas/atlasContext'
import { gateBasis } from './gateCount'
import { mappingCsv, readMappingCsv, validateMappingDraft, type MappingDraft } from './mappingCsv'
import { HierarchyBrowser, type BrowserRow, type ChildState } from './HierarchyBrowser'
import { AREA_THRESHOLD, atLeast, loadColumns, loadMinArea, requestedMetrics, saveColumns, saveMinArea } from './metricCatalog'
import { MappingSuggestions } from './MappingSuggestions'
import { NotesPanel } from './NotesPanel'
import { ComparisonHistoryView } from './ComparisonHistoryView'
import { ReportsView } from './TrackingReportsView'
import { TrackingOverview } from './TrackingOverview'
import { trackingOverviewSchema, type TrackingOverviewData } from './trackingApi'
import { TriagePanel } from './TriagePanel'
import { baselineSchema, emptySelection, groupedResults, mergeHierarchy, referenceRows, useHierarchy, type Selection, type TreeRow } from './comparison/hierarchyUtils'
import { PathPicker } from './comparison/PathPicker'
import { RunSelector } from './comparison/RunSelector'

interface ComparisonProps { auth: Auth; access: AreaAccess; records: TrackingRecord[]; uploads: Upload[]; comparisons: TrackingRecord[]; refresh: () => Promise<void>; refreshVersion: number; loading: boolean }

export function Comparisons(props: ComparisonProps) {
  const atlas = useAtlas()!
  const [projectId, setProjectId] = useState(() => atlas.scope.projectId || '')
  useEffect(() => {
    if (atlas.scope.projectId) setProjectId(atlas.scope.projectId)
  }, [atlas.scope.projectId])
  const [gateCount, setGateCount] = useState(false)
  const unitSwitch = <button type="button" className="gate-count-switch" role="switch" aria-label="Gate Count 표시" aria-checked={gateCount} onClick={() => setGateCount(value => !value)}><span>Area</span><i aria-hidden="true" /><span>Gate Count</span></button>
  const projects = atlas.catalog?.projects || []
  const project = projects.find(row => row.id === projectId)
  const picker = <label className="setup-project" title="상단 공통 범위에서 선택한 프로젝트를 이어받으며, 이 화면에서도 바꿀 수 있습니다."><span>프로젝트</span><select value={projectId} onChange={event => setProjectId(event.target.value)}><option value="">프로젝트 선택</option>{projects.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
  return project ? <ProjectComparison key={project.id} {...props} project={project} picker={picker} gateCount={gateCount} unitSwitch={unitSwitch} /> : <>
    <section className="panel comparison-setup-bar"><div className="baseline-summary">{picker}<small>상단 공통 범위의 프로젝트를 이어받거나 여기서 직접 선택하세요.</small></div></section>
    <Empty title="프로젝트를 선택하세요">프로젝트를 선택하면 비교 기준, 현재 버전 현황, 블록별 결과와 직접 비교를 바로 확인할 수 있습니다.</Empty>
  </>
}

function ProjectComparison(props: ComparisonProps & { project: AtlasProject; picker: ReactNode; gateCount: boolean; unitSwitch: ReactNode }) {
  const atlas = useAtlas()!
  const project = props.project
  const action = useAction()
  const alive = useRef(true)
  const mutation = useRef<AbortController | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false; mutation.current?.abort() } }, [])
  const [baseline, setBaseline] = useState<z.infer<typeof baselineSchema> | null>(null)
  const [baselineError, setBaselineError] = useState('')
  const lastDesignation = useRef<string | null>(null)
  const [section, setSection] = useState<'status' | 'compare' | 'reports' | 'history'>('status')
  const [trackingData, setTrackingData] = useState<TrackingOverviewData | null>(null)
  const [trackingLoading, setTrackingLoading] = useState(false)
  const [trackingError, setTrackingError] = useState('')
  const initialMailId = useMemo(() => new URLSearchParams(atlas.routeSearch).get('tracking_mail') || '', [atlas.routeSearch])
  const [current, setCurrent] = useState<Selection>(emptySelection)
  const [before, setBefore] = useState<Selection>(emptySelection)
  // What the current run is compared with: the designated baseline project,
  // or an earlier version of the same project and block (an RTL update).
  const [mode, setMode] = useState<'project' | 'version'>('project')
  const [triageItems, setTriageItems] = useState<TriageItem[]>([])
  const [locate, setLocate] = useState<{ ancestors: string[]; id: string; nonce: number } | null>(null)
  const [mappingId, setMappingId] = useState('')
  const [mappingDraft, setMappingDraft] = useState<MappingDraft[]>([])
  const [mappingDraftPage, setMappingDraftPage] = useState(0)
  const mappingFile = useRef<HTMLInputElement>(null)
  const mappingInputs = useRef('')
  const [baselinePath, setBaselinePath] = useState('')
  const [currentPath, setCurrentPath] = useState('')
  const [mappingScope, setMappingScope] = useState<'node' | 'subtree'>('subtree')
  const [comparisonId, setComparisonId] = useState('')
  const [detail, setDetail] = useState<ComparisonDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailRefresh, setDetailRefresh] = useState(0)
  const [detailLoading, setDetailLoading] = useState(false)
  const [resultSearch, setResultSearch] = useState('')
  const [resultQuery, setResultQuery] = useState({ offset: 0, search: '' })
  const [children, setChildren] = useState<Map<string, ChildState>>(() => new Map())
  // Run selection folds into one line while a result is open, so the result
  // table keeps most of the viewport.
  const [setupOpen, setSetupOpen] = useState(true)
  const [columns, setColumns] = useState<string[]>(loadColumns)
  const [minArea, setMinArea] = useState<string>(loadMinArea)
  const chooseColumns = (next: string[]) => { setColumns(next); saveColumns(next) }
  const chooseMinArea = (next: string) => { setMinArea(next); saveMinArea(next) }
  // Server-side metrics and threshold for reference comparisons.
  const observed = useMemo(() => {
    const params = new URLSearchParams({ metrics: requestedMetrics(columns).join(',') })
    if (minArea && AREA_THRESHOLD.test(minArea)) params.set('min_area', minArea)
    return params.toString()
  }, [columns, minArea])
  const resultTop = useRef<HTMLDivElement>(null)
  const treeEpoch = useRef(0)
  const projectRecords = useMemo(() => new Map(props.records.filter(row => row.record_type === 'project').map(row => [text(row.payload.sanity_project_id), row])), [props.records])
  const currentAreaId = project.area_id || projectRecords.get(project.id)?.id || ''
  const candidates = atlas.catalog?.projects || []
  const savedProject = candidates.find(row => (row.area_id || projectRecords.get(row.id)?.id) === baseline?.baseline_project_id)
  const currentBlock = project.blocks.find(row => row.id === current.block)
  const baselineSide = mode === 'version' ? project : savedProject
  const baselineAreaId = mode === 'version' ? currentAreaId : baseline?.baseline_project_id
  const beforeBlock = baselineSide?.blocks.find(row => row.id === before.block)
  const currentVersion = currentBlock?.versions.find(row => row.id === current.version && row.net_ver === current.net)
  const beforeVersion = beforeBlock?.versions.find(row => row.id === before.version && row.net_ver === before.net)
  const currentUploads = props.uploads.filter(row => row.state === 'ready' && row.project_id === currentAreaId && !!currentBlock?.area_id && row.block_id === currentBlock.area_id && !!currentVersion?.area_id && row.version_id === currentVersion.area_id)
  const beforeUploads = props.uploads.filter(row => row.state === 'ready' && !!baselineAreaId && row.project_id === baselineAreaId && !!beforeBlock?.area_id && row.block_id === beforeBlock.area_id && !!beforeVersion?.area_id && row.version_id === beforeVersion.area_id)
  const selectedCurrent = currentUploads.find(row => row.id === current.upload && cornerKey(row) === current.corner)
  const selectedBefore = beforeUploads.find(row => row.id === before.upload && cornerKey(row) === before.corner)
  const mappings = props.records.filter(row => row.record_type === 'mapping' && selectedCurrent && selectedBefore && ['current', 'baseline'].every(side => {
    const upload = side === 'current' ? selectedCurrent : selectedBefore
    return ['project_id', 'block_id', 'version_id'].every(key => text(row.payload[`${side}_${key}`]) === upload[key])
  }))
  const savedInput = object(detail?.comparison.payload.request)
  const referenceComparison = detail?.comparison.payload.result_format === 'hierarchy-reference-v1'
  const displayedCurrentId = comparisonId ? text(savedInput.current_upload_id || detail?.comparison.upload_id) : selectedCurrent?.id || ''
  const displayedBaselineId = comparisonId ? text(savedInput.baseline_upload_id || detail?.comparison.baseline_upload_id) : selectedBefore?.id || ''
  const readHierarchy = !comparisonId || !!detail && !referenceComparison
  const currentHierarchy = useHierarchy(props.auth, readHierarchy ? displayedCurrentId : '', detailRefresh)
  const beforeHierarchy = useHierarchy(props.auth, readHierarchy ? displayedBaselineId : '', detailRefresh)
  const maxHierarchyDepth = useMemo(() => {
    let max = 0
    for (const item of beforeHierarchy.data?.items || []) if (item.depth > max) max = item.depth
    for (const item of currentHierarchy.data?.items || []) if (item.depth > max) max = item.depth
    return max
  }, [beforeHierarchy.data, currentHierarchy.data])
  const validBaselinePath = !beforeHierarchy.loading && !beforeHierarchy.error && !!beforeHierarchy.data?.items.some(row => row.path === baselinePath)
  const validCurrentPath = !currentHierarchy.loading && !currentHierarchy.error && !!currentHierarchy.data?.items.some(row => row.path === currentPath)
  mappingInputs.current = `${selectedBefore?.id || ''}:${selectedCurrent?.id || ''}`
  const results = useMemo(() => groupedResults(detail), [detail])
  const treeRows = useMemo<TreeRow[]>(() => referenceComparison ? [] : mergeHierarchy(currentHierarchy.data?.items || [], beforeHierarchy.data?.items || [], results), [referenceComparison, currentHierarchy.data, beforeHierarchy.data, results])
  // Reference comparisons load one hierarchy level per request; earlier results
  // and previews already hold every row and nest them locally.
  const browser = useMemo(() => {
    if (referenceComparison) return { roots: detail ? referenceRows(detail) : [], local: null, hidden: null }
    // Same rule as the server: below the threshold on both sides is hidden,
    // design roots always stay.
    const visible = (row: TreeRow) => {
      if (!row.parent || !minArea || !AREA_THRESHOLD.test(minArea)) return true
      const sides = [row.current?.area, row.baseline?.area].filter(value => value !== undefined && value !== null && value !== '')
      return !sides.length || sides.some(value => atLeast(value, minArea) !== false)
    }
    const counts = new Map<string, number>(), hidden = new Map<string, number>()
    for (const row of treeRows) { const target = visible(row) ? counts : hidden; target.set(row.parent, (target.get(row.parent) || 0) + 1) }
    const local = new Map<string, BrowserRow[]>()
    for (const row of treeRows) {
      if (!visible(row)) continue
      const item = { ...row, childCount: counts.get(row.key) || 0, hiddenChildren: hidden.get(row.key) || 0 }, list = local.get(row.parent)
      if (list) list.push(item); else local.set(row.parent, [item])
    }
    return { roots: local.get('') || [], local, hidden }
  }, [referenceComparison, detail, treeRows, minArea])
  const childrenOf = (row: BrowserRow): ChildState | undefined => {
    if (browser.local) { const rows = browser.local.get(row.key) || []; return { rows, total: rows.length, loading: false, error: '', hidden: browser.hidden?.get(row.key) || 0 } }
    return children.get(row.key)
  }
  const flatResults = referenceComparison && !!resultQuery.search
  const displayedCurrent = props.uploads.find(row => row.id === displayedCurrentId)
  const displayedBefore = props.uploads.find(row => row.id === displayedBaselineId)
  // Resolve each side from the displayed upload, including a history entry with a different baseline.
  const ownerOf = (upload?: Upload) => upload && candidates.find(row => (row.area_id || projectRecords.get(row.id)?.id) === upload.project_id)
  const currentOwner = ownerOf(displayedCurrent), beforeOwner = ownerOf(displayedBefore)
  const gateBases = useMemo(() => {
    if (!props.gateCount) return null
    const definitions = atlas.catalog?.processes || []
    return { baseline: gateBasis(displayedBefore, beforeOwner?.process, definitions),
      current: gateBasis(displayedCurrent, currentOwner?.process, definitions) }
  }, [props.gateCount, displayedBefore, displayedCurrent, beforeOwner?.process, currentOwner?.process, atlas.catalog?.processes])
  const sideLabel = (owner: AtlasProject | undefined, upload: Upload | undefined, fallback: string) =>
    [owner?.name, owner?.blocks.find(row => row.area_id === upload?.block_id)?.name].filter(Boolean).join(' · ') || fallback
  const sideLabels = { current: sideLabel(currentOwner, displayedCurrent, project.name), baseline: sideLabel(beforeOwner, displayedBefore, savedProject?.name || '') }
  // Project · Block · NET_VER · REVISION for mail subjects and analysis records.
  const runLabel = (owner: AtlasProject | undefined, upload: Upload | undefined, label: string) => {
    const version = owner?.blocks.find(row => row.area_id === upload?.block_id)?.versions.find(row => row.area_id === upload?.version_id)
    return [label, version && `${version.net_ver} ${version.revision} ${version.layout}`].filter(Boolean).join(' · ').slice(0, 280)
  }
  const triageLabels = { current: runLabel(currentOwner, displayedCurrent, sideLabels.current), baseline: runLabel(beforeOwner, displayedBefore, sideLabels.baseline) }
  const marks = useMemo(() => new Map(triageItems.map(item => [item.group_id, item.verdict])), [triageItems])
  const ready = mode === 'version' ? !!selectedBefore && selectedBefore.id !== selectedCurrent?.id : !!baseline?.baseline_project_id && (!before.upload || !!selectedBefore)
  const canCompare = project.can_write && !!selectedCurrent && ready && !mappingDraft.length && (!mappingId || mappings.some(row => row.id === mappingId))

  useEffect(() => {
    if (!currentAreaId) { setBaseline(null); return }
    const controller = new AbortController()
    setBaselineError('')
    request(props.auth, `/projects/${encodeURIComponent(currentAreaId)}/baseline`, baselineSchema, undefined, controller.signal).then(value => {
      if (controller.signal.aborted) return
      setBaseline(value)
      const designation = `${value.status}:${value.baseline_project_id || ''}`
      if (lastDesignation.current !== designation) {
        lastDesignation.current = designation
        setBefore(emptySelection); resetCorrespondence()
      }
    }).catch(cause => { if (!controller.signal.aborted) { setBaseline(null); setBaselineError(cause instanceof Error ? cause.message : '기준을 조회하지 못했습니다.') } })
    return () => controller.abort()
  }, [props.auth, currentAreaId, props.refreshVersion])
  useEffect(() => {
    if (!currentAreaId) { setTrackingData(null); setTrackingError(''); return }
    const controller = new AbortController()
    setTrackingLoading(true)
    request(props.auth, `/tracking/projects/${encodeURIComponent(currentAreaId)}/overview`, trackingOverviewSchema, undefined, controller.signal)
      .then(value => {
        if (!controller.signal.aborted) { setTrackingData(value); setTrackingError('') }
      })
      .catch(cause => {
        if (!controller.signal.aborted) { setTrackingError(cause instanceof Error ? cause.message : 'NET_VER 추적 현황을 조회하지 못했습니다.') }
      })
      .finally(() => { if (!controller.signal.aborted) setTrackingLoading(false) })
    return () => controller.abort()
  }, [props.auth, currentAreaId, props.refreshVersion])

  function openTrackingResult(target: { comparisonId?: string; uploadId?: string; baselineUploadId?: string; openMapping?: boolean; blockId?: string }) {
    setSection('compare')
    if (target.comparisonId) {
      setDetail(null); setDetailError(''); setResultSearch(''); setResultQuery({ offset: 0, search: '' })
      setComparisonId(target.comparisonId)
      setDetailRefresh(value => value + 1)
      setSetupOpen(false)
      return
    }
    setComparisonId(''); setDetail(null); setSetupOpen(true)
    if (target.uploadId) {
      const cu = props.uploads.find(u => u.id === target.uploadId)
      const blk = cu ? project.blocks.find(b => b.area_id === cu.block_id) : undefined
      const ver = cu && blk ? blk.versions.find(v => v.area_id === cu.version_id) : undefined
      if (cu && blk && ver) {
        setCurrent({ block: blk.id, net: ver.net_ver, version: ver.id, corner: cornerKey(cu), upload: cu.id })
      }
    }
    if (target.baselineUploadId) {
      const bu = props.uploads.find(u => u.id === target.baselineUploadId)
      const owner = bu ? candidates.find(c => (c.area_id || projectRecords.get(c.id)?.id) === bu.project_id) : undefined
      const blk = bu && owner ? owner.blocks.find(b => b.area_id === bu.block_id) : undefined
      const ver = bu && blk ? blk.versions.find(v => v.area_id === bu.version_id) : undefined
      if (bu && blk && ver) {
        setBefore({ block: blk.id, net: ver.net_ver, version: ver.id, corner: cornerKey(bu), upload: bu.id })
      }
    }
  }

  useEffect(() => { treeEpoch.current += 1; setChildren(new Map()) }, [comparisonId, detailRefresh, props.refreshVersion, resultQuery, observed])
  useEffect(() => {
    if (!comparisonId) { setDetail(null); setDetailError(''); setDetailLoading(false); return }
    const controller = new AbortController()
    let timer: number | undefined
    const load = async () => {
      setDetailLoading(true)
      try {
        const path = `/comparisons/${encodeURIComponent(comparisonId)}`
        let value = await request(props.auth, `${path}?view=summary`, detailSchema, undefined, controller.signal)
        const running = ['queued', 'pending', 'running'].includes(value.comparison.state)
        if (!running) {
          const params = new URLSearchParams(observed)
          for (const [key, value] of Object.entries({ view: 'hierarchy', offset: String(resultQuery.offset), limit: '200', search: resultQuery.search })) params.set(key, value)
          if (!resultQuery.search) params.set('parent', '')
          value = await request(props.auth, `${path}?${params}`, detailSchema, undefined, controller.signal)
        }
        if (controller.signal.aborted) return
        setDetail(value); setDetailError('')
        if (['queued', 'pending', 'running'].includes(value.comparison.state)) timer = window.setTimeout(() => void load(), 2500)
      } catch (cause) {
        if (!controller.signal.aborted) { setDetail(null); setDetailError(cause instanceof Error ? cause.message : '비교 결과를 조회하지 못했습니다.') }
      } finally { if (!controller.signal.aborted) setDetailLoading(false) }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [props.auth, comparisonId, detailRefresh, props.refreshVersion, resultQuery, observed])

  async function loadChildren(key: string, offset: number) {
    const epoch = treeEpoch.current, id = comparisonId
    const update = (change: (state: ChildState) => ChildState) => setChildren(map => {
      if (epoch !== treeEpoch.current) return map
      const state = map.get(key) || { rows: [], total: 0, loading: false, error: '' }
      return new Map(map).set(key, change(state))
    })
    update(state => ({ ...state, loading: true, error: '' }))
    try {
      const params = new URLSearchParams(observed)
      for (const [name, value] of Object.entries({ view: 'hierarchy', parent: key, offset: String(offset), limit: '500' })) params.set(name, value)
      const value = await request(props.auth, `/comparisons/${encodeURIComponent(id)}?${params}`, detailSchema)
      if (!alive.current) return
      update(state => ({ rows: [...state.rows.slice(0, offset), ...referenceRows(value)], total: value.result_page?.total_groups ?? 0, hidden: value.result_page?.hidden_groups ?? 0, loading: false, error: '' }))
    } catch (cause) {
      if (alive.current) update(state => ({ ...state, loading: false, error: cause instanceof Error ? cause.message : '하위 instance를 조회하지 못했습니다.' }))
    }
  }
  function showResult() {
    setSetupOpen(false)
    window.requestAnimationFrame(() => resultTop.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
  }
  function beginMutation() { mutation.current?.abort(); const controller = new AbortController(); mutation.current = controller; return controller.signal }
  function resetCorrespondence() {
    setMappingId(''); setMappingDraft([]); setMappingDraftPage(0); setBaselinePath(''); setCurrentPath('')
  }
  function configuredBaselineSelection(blockNativeId: string, activeMode: 'project' | 'version', preferCorner = ''): Selection | null {
    const blk = project.blocks.find(b => b.id === blockNativeId)
    if (!blk?.area_id) return null
    const cfg = (trackingData?.block_configs || []).find(row => text(row.block_id) === blk.area_id)
    if (!cfg) return null
    const cfgKind = text(cfg.comparison_kind) === 'previous_version' ? 'version' : 'project'
    if (cfgKind !== activeMode) return null
    const sideProj = activeMode === 'version' ? project : savedProject
    const sideAreaProjId = activeMode === 'version' ? currentAreaId : baseline?.baseline_project_id
    if (!sideProj || !sideAreaProjId) return null
    const targetBlkAreaId = activeMode === 'version' ? blk.area_id : text(cfg.baseline_block_id)
    const baseBlk = sideProj.blocks.find(b => b.area_id === targetBlkAreaId)
    if (!baseBlk) return null
    const targetVerAreaId = text(cfg.baseline_version_id)
    const baseVer = targetVerAreaId ? baseBlk.versions.find(v => v.area_id === targetVerAreaId) : undefined
    if (!baseVer) return { ...emptySelection, block: baseBlk.id }
    const readyUploads = props.uploads.filter(
      u => u.state === 'ready' && u.project_id === sideAreaProjId && u.block_id === baseBlk.area_id && u.version_id === baseVer.area_id,
    )
    const matched = (preferCorner && readyUploads.find(u => cornerKey(u) === preferCorner))
      || (readyUploads.length === 1 ? readyUploads[0] : undefined)
    return {
      block: baseBlk.id,
      net: baseVer.net_ver,
      version: baseVer.id,
      corner: matched ? cornerKey(matched) : '',
      upload: matched?.id || '',
    }
  }
  function chooseCurrent(selection: Selection) {
    if (selection.block !== current.block || selection.net !== current.net || selection.version !== current.version || selection.corner !== current.corner || selection.upload !== current.upload) resetCorrespondence()
    if (selection.block !== current.block) {
      const preset = configuredBaselineSelection(selection.block, mode, selection.corner)
      if (preset) setBefore(preset)
      else if (mode === 'version') setBefore({ ...emptySelection, block: selection.block })
    }
    setCurrent(selection)
  }
  function chooseMode(next: 'project' | 'version') {
    if (next === mode) return
    setMode(next); resetCorrespondence()
    const preset = current.block ? configuredBaselineSelection(current.block, next, current.corner) : null
    setBefore(preset || (next === 'version' ? { ...emptySelection, block: current.block } : emptySelection))
  }
  // Open the tree down to one analysed instance. Reference results are keyed
  // by group id; earlier results and previews nest rows locally.
  function locateItem(item: TriageItem) {
    const nonce = Date.now()
    if (referenceComparison) {
      const ancestors = Array.isArray(item.card.ancestor_ids) ? item.card.ancestor_ids.map(text) : []
      if (resultQuery.search) { setResultSearch(''); setResultQuery({ offset: 0, search: '' }) }
      setLocate({ ancestors, id: item.group_id, nonce })
      return
    }
    const byKey = new Map(treeRows.map(row => [row.key, row]))
    const target = treeRows.find(row => row.result?.id === item.group_id)
    if (!target) return
    const ancestors: string[] = []
    for (let node = byKey.get(target.parent); node; node = byKey.get(node.parent)) ancestors.unshift(node.key)
    setLocate({ ancestors, id: target.key, nonce })
  }
  function chooseBefore(selection: Selection) {
    if (selection.block !== before.block || selection.net !== before.net || selection.version !== before.version || selection.corner !== before.corner || selection.upload !== before.upload) resetCorrespondence()
    setBefore(selection)
  }
  async function prepare(nativeProject: AtlasProject, selection?: Selection, previous?: string | null) {
    const signal = beginMutation()
    await props.auth.request('/api/atlas/area-scope', { project_id: nativeProject.id, ...(selection?.block ? { block_id: selection.block } : {}), ...(selection?.version ? { version_id: selection.version } : {}), ...(previous !== undefined ? { previous_version_id: previous } : {}) }, false, signal)
    if (!alive.current) return
    await atlas.reload(); if (alive.current) await props.refresh()
  }
  async function saveMapping() {
    if (!selectedCurrent || !selectedBefore || !mappingDraft.length) throw new Error('양쪽 실행과 대응 경로를 선택하세요.')
    if (!beforeHierarchy.data || !currentHierarchy.data || beforeHierarchy.loading || currentHierarchy.loading || beforeHierarchy.error || currentHierarchy.error) throw new Error('양쪽 경로 조회를 완료한 뒤 저장하세요.')
    validateMappingDraft(mappingDraft, new Set(beforeHierarchy.data.items.map(row => row.path)), new Set(currentHierarchy.data.items.map(row => row.path)))
    const payload: Record<string, unknown> = { rules: mappingDraft.map(row => ({ kind: 'one_to_one', scope: row.scope, baseline_paths: [row.baselinePath], current_paths: [row.currentPath] })) }
    for (const [side, upload] of [['current', selectedCurrent], ['baseline', selectedBefore]] as const) for (const key of ['project_id', 'block_id', 'version_id']) payload[`${side}_${key}`] = upload[key]
    const input = mappingInputs.current
    const value = await request(props.auth, '/config', recordSchema, { record_type: 'mapping', project_id: selectedCurrent.project_id, block_id: selectedCurrent.block_id, version_id: selectedCurrent.version_id, payload }, beginMutation())
    if (!alive.current || input !== mappingInputs.current) return
    setMappingId(value.id); setMappingDraft([])
    await props.refresh()
  }
  function exportMappingTemplate() {
    const rows = mappingDraft.length ? mappingDraft : baselinePath || currentPath ? [{ baselinePath, currentPath, scope: mappingScope }] : []
    const url = URL.createObjectURL(new Blob([mappingCsv(rows)], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = 'instance_mapping_template.csv'; anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  async function importMapping(file: File) {
    if (!selectedCurrent || !selectedBefore || !beforeHierarchy.data || !currentHierarchy.data || beforeHierarchy.loading || currentHierarchy.loading || beforeHierarchy.error || currentHierarchy.error) throw new Error('양쪽 실행을 선택하고 경로 조회를 완료하세요.')
    const input = mappingInputs.current
    let source: string
    try { source = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()) }
    catch { throw new Error('UTF-8 CSV 파일을 읽지 못했습니다. UTF-8 CSV로 저장한 뒤 다시 선택하세요.') }
    if (!alive.current) return
    if (input !== mappingInputs.current) throw new Error('실행 선택이 바뀌었습니다. 현재 양쪽 실행에서 CSV를 다시 가져오세요.')
    const rows = readMappingCsv(source)
    validateMappingDraft(rows, new Set(beforeHierarchy.data.items.map(row => row.path)), new Set(currentHierarchy.data.items.map(row => row.path)))
    setMappingDraft(rows); setMappingDraftPage(0); setMappingId('')
  }
  async function compare() {
    if (!canCompare || !selectedCurrent) throw new Error('현재·기준의 정확한 실행을 다시 선택하세요.')
    const value = await request(props.auth, '/comparisons', comparisonCreatedSchema, { current_upload_id: selectedCurrent.id, baseline_upload_id: selectedBefore?.id || null, comparison_kind: mode === 'version' ? 'previous_version' : 'previous_project', scope: { kind: 'all' }, mapping_id: mappingId || null }, beginMutation())
    if (!alive.current) return
    setDetail(null); setDetailError(''); setResultSearch(''); setResultQuery({ offset: 0, search: '' }); setComparisonId(value.comparison.id)
    showResult()
    await props.refresh()
  }
  const filters = { project_id: currentAreaId, block_id: currentBlock?.area_id || '', version_id: currentVersion?.area_id || '', snapshot_id: '' }
  const history = props.comparisons.filter(row => row.project_id === currentAreaId)
  const pending = detail && ['queued', 'pending', 'running'].includes(detail.comparison.state)
  const resultPage = detail?.result_page
  const draftPageSize = 50
  const draftLastPage = Math.max(0, Math.ceil(mappingDraft.length / draftPageSize) - 1)
  const draftPage = Math.min(mappingDraftPage, draftLastPage)
  const draftStart = draftPage * draftPageSize

  const currentProcess = (baseline ? baseline.process : project.process) || '미등록'
  const baselineName = baseline?.status === 'available'
    ? text(baseline.baseline_project?.payload.name) || savedProject?.name || '기준 프로젝트'
    : baseline?.status === 'unavailable' ? '기준 조회 불가' : '미설정'
  const latestNetVer = useMemo(() => {
    if (trackingData?.rounds[0]?.net_ver) return trackingData.rounds[0].net_ver
    const versions = project.blocks.flatMap(b => b.versions.map(v => v.net_ver).filter(Boolean))
    return versions.length ? versions[versions.length - 1] : ''
  }, [trackingData?.rounds, project.blocks])

  return <>
    {action.feedback}
    {!project.can_write && <p className="notice">조회 전용 · 기준 설정, 비교 실행과 구조 대응 변경에는 프로젝트 멤버 권한이 필요합니다.</p>}
    <section className="panel comparison-setup-bar project-baseline project-context-bar" aria-label="프로젝트 요약 및 비교 기준">
      <div className="baseline-summary">
        {props.picker}
        <span className="context-pill">{currentProcess}</span>
        <span className="setup-arrow" aria-hidden="true">↔</span>
        <span className="setup-baseline">
          <small>비교 기준</small>
          <strong className={baseline?.status !== 'available' ? 'is-unset' : ''}>{baselineName}</strong>
          {baseline?.status === 'available' && baseline.baseline_process && <span className="context-pill subtle">{baseline.baseline_process}</span>}
        </span>
        {latestNetVer && (
          <span className="setup-version">
            <small>최신 버전</small>
            <strong>{latestNetVer}</strong>
          </span>
        )}
      </div>
      {baselineError && <div className="notice error" role="alert">{baselineError}</div>}
      {!currentAreaId && <div className="notice"><p>프로젝트가 아직 수치 분석 서비스에 연결되지 않았습니다.</p><Button disabled={action.busy || !project.can_write} onClick={() => action.run(() => prepare(project))}>현재 프로젝트 연결</Button></div>}
    </section>
    {currentAreaId && <nav className="tracking-view-tabs" role="tablist" aria-label="프로젝트 비교 보기">
      <div className="tracking-tabs-primary">
        {([
          ['status', '프로젝트 현황', null],
          ['compare', '직접 비교', null],
          ['reports', '발송 리포트', null],
          ['history', '비교 이력', history.length],
        ] as const).map(([key, label, badgeCount]) => (
          <button key={key} type="button" role="tab" aria-selected={section === key} onClick={() => setSection(key)}>
            <span>{label}</span>
            {badgeCount !== null && <span className="tab-count">{badgeCount}</span>}
          </button>
        ))}
      </div>
    </nav>}
    {currentAreaId && section === 'status' && (
      <TrackingOverview
        auth={props.auth}
        project={project}
        baselineProject={savedProject}
        baselineName={baselineName}
        baselineStatus={baseline?.status || 'unset'}
        currentProcess={currentProcess}
        baselineProcess={baseline?.baseline_process || ''}
        areaProjectId={currentAreaId}
        uploads={props.uploads}
        data={trackingData}
        loading={trackingLoading}
        error={trackingError}
        initialMailId={initialMailId}
        onClearMailId={() => {
          const next = new URLSearchParams(atlas.routeSearch)
          next.delete('tracking_mail')
          atlas.replaceRoute(`${location.pathname}${next.toString() ? `?${next}` : ''}`)
        }}
        onOpenResult={openTrackingResult}
      />
    )}
    {currentAreaId && section === 'reports' && (
      <ReportsView
        auth={props.auth}
        areaProjectId={currentAreaId}
        refreshVersion={props.refreshVersion}
        onOpenComparison={id => openTrackingResult({ comparisonId: id })}
      />
    )}
    {currentAreaId && section === 'history' && (
      <ComparisonHistoryView
        project={project}
        candidates={candidates}
        projectRecords={projectRecords}
        uploads={props.uploads}
        comparisons={history}
        trackingData={trackingData}
        onOpenComparison={id => openTrackingResult({ comparisonId: id })}
      />
    )}
    {(!currentAreaId || section === 'compare') && <>
    <section className="panel comparison-workspace">
      <div className="compare-mode-bar">
        <div className="view-switch" role="group" aria-label="비교 대상">{([['project', '기준 프로젝트와 비교'], ['version', '같은 프로젝트의 이전 버전과 비교']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={mode === value} disabled={!!comparisonId} onClick={() => chooseMode(value)}>{label}</button>)}</div>
        <small>{mode === 'version' ? '같은 블록의 이전 NET_VER·버전과 RTL 변경 사항을 비교합니다.' : `기준 프로젝트(${baselineName})의 실행과 비교합니다.`}</small>
      </div>
      {setupOpen || (!comparisonId && !selectedCurrent) ? <div className="compare-sides">
        <RunSelector label="현재" project={project} selection={current} onChange={chooseCurrent} uploads={currentUploads} busy={action.busy} onPrepare={(selection, previous) => action.run(() => prepare(project, selection, previous))} />
        {mode === 'version' ? <RunSelector label="기준" project={project} selection={before} onChange={chooseBefore} uploads={beforeUploads} busy={action.busy} lockedBlock hint="기준 버전은 현재 버전의 명시적 이전 버전(계보)이어야 합니다. NET_VER가 다르면 RTL 변경 비교입니다." onPrepare={(selection, previous) => action.run(() => prepare(project, selection, previous))} />
          : savedProject && baseline?.status === 'available' ? <RunSelector label="기준" project={savedProject} selection={before} onChange={chooseBefore} uploads={beforeUploads} busy={action.busy} onPrepare={(selection, previous) => action.run(() => prepare(savedProject, selection, previous))} /> : <Empty title={baseline?.status === 'unavailable' ? '기준 프로젝트 조회 불가' : '비교 기준을 저장하세요'}>현재 실행의 관측값은 기준이 없어도 볼 수 있습니다. 기준·대응이 없는 Δ/%는 unavailable입니다.</Empty>}
      </div> : !comparisonId && <p className="setup-summary">현재 {selectedCurrent ? uploadName(selectedCurrent) : '실행 미선택'} <span aria-hidden="true">↔</span> 기준 {selectedBefore ? uploadName(selectedBefore) : '기준 실행 없음'}</p>}
      {selectedCurrent && selectedBefore && cornerKey(selectedCurrent) !== cornerKey(selectedBefore) && <div className="notice warning">양쪽 corner가 다릅니다. Power의 비교 가능성과 조건 차이 사유를 확인하세요. 같은 이름으로 조건을 추정하지 않습니다.</div>}
      <div className="comparison-actions" ref={resultTop}><div><h2 title="부모 값에는 자식이 포함됩니다. 행을 다시 합산하지 않습니다.">Instance {props.gateCount ? 'Gate Count' : 'Area'} · Power</h2></div>{props.unitSwitch}{(comparisonId || selectedCurrent) && <Button variant="ghost" size="sm" aria-expanded={setupOpen} onClick={() => setSetupOpen(value => !value)}>{setupOpen ? '실행 선택 접기' : '실행 선택 펼치기'}</Button>}{comparisonId ? <><Badge value={referenceComparison && detail?.comparison.state === 'completed' ? '조회 준비 완료' : detail?.comparison.state || 'loading'} /><Button variant="outline" onClick={() => { setComparisonId(''); setDetail(null); setSetupOpen(true); setTriageItems([]) }}>현재 선택으로 돌아가기</Button><Button variant="outline" disabled={detailLoading} onClick={() => setDetailRefresh(value => value + 1)}><RefreshCw size={15} />결과 갱신</Button></> : <Button disabled={action.busy || !canCompare} onClick={() => action.run(compare)}><GitCompareArrows size={16} />비교 실행</Button>}</div>
      {!comparisonId && selectedCurrent && !selectedBefore && baseline?.status === 'available' && <p className="notice">기준 실행을 선택하지 않으면 현재 관측만 보존하고 비교 값은 unavailable로 남깁니다.</p>}
      {!comparisonId && mappingDraft.length > 0 && <p className="notice">작성 중인 대응 {mappingDraft.length.toLocaleString()}개를 저장한 뒤 비교를 실행하세요.</p>}
      {comparisonId && <p className="frozen-inputs">고정 입력 · 현재 {displayedCurrent ? uploadName(displayedCurrent) : displayedCurrentId ? '선택 당시 실행' : '조회 중'} ↔ 기준 {displayedBefore ? uploadName(displayedBefore) : displayedBaselineId ? '선택 당시 실행' : '기준 실행 없음'} · 경로 대응 {savedInput.mapping_id ? '고정 revision' : '미선택'}</p>}
      {referenceComparison && detail?.comparison.state === 'completed' && <p className="notice">비교 조회 준비 완료 · 수치는 조회할 때 계산합니다. 전체 지표의 분석이나 AI 판정이 완료된 상태가 아닙니다.</p>}
      {pending && <p className="notice"><LoaderCircle size={15} className="spin" />수치 작업 {detail.comparison.state} · 완료까지 자동 갱신합니다.</p>}
      {pending && object(detail.comparison.payload.coverage).expected_metrics != null && <p role="status">지표 처리 {format(object(detail.comparison.payload.coverage).processed_metrics)} / {format(object(detail.comparison.payload.coverage).expected_metrics)}</p>}
      {detail?.comparison.state === 'failed' && <p className="notice error">수치 작업이 실패했습니다. 작업 상태와 입력을 확인하세요. 없는 결과를 0으로 대체하지 않습니다.</p>}
      {detailError && <div className="notice error" role="alert">{detailError}</div>}
      {[['현재', currentHierarchy.error], ['기준', beforeHierarchy.error]].filter(([, error]) => error).map(([side, error]) => <div className="notice error" role="alert" key={side}>{side} 경로 조회 실패 · {error}<Button variant="outline" onClick={() => setDetailRefresh(value => value + 1)}>경로 다시 조회</Button></div>)}
      {(currentHierarchy.loading || beforeHierarchy.loading) && <p role="status"><LoaderCircle size={16} className="spin" />선택한 실행의 hierarchy 조회 중…</p>}
      {comparisonId && detail?.comparison.state === 'completed' && !!displayedBaselineId && (referenceComparison || props.auth.demo) && <TriagePanel auth={props.auth} comparisonId={comparisonId} canWrite={project.can_write} labels={triageLabels} onItems={setTriageItems} onLocate={locateItem} />}
      {referenceComparison && <form className="comparison-result-search" onSubmit={event => { event.preventDefault(); setResultQuery({ offset: 0, search: resultSearch.trim() }) }}><Field label="비교 경로 검색"><input type="search" value={resultSearch} onChange={event => setResultSearch(event.target.value)} placeholder="기준 또는 현재 instance 경로 검색" autoComplete="off" spellCheck={false} /></Field><Button type="submit" disabled={detailLoading}>검색</Button></form>}
      {detailLoading && <p role="status"><LoaderCircle size={16} className="spin" />비교 수치 조회 중…</p>}
      {resultPage && (flatResults || resultPage.total_groups > resultPage.limit) && <div className="comparison-result-pages"><p role="status">{resultPage.total_groups ? `${(resultPage.offset + 1).toLocaleString()}–${Math.min(resultPage.offset + resultPage.limit, resultPage.total_groups).toLocaleString()} / ${resultPage.total_groups.toLocaleString()}그룹` : '검색 조건에 맞는 경로가 없습니다.'}</p><Button variant="outline" disabled={detailLoading || resultPage.offset === 0} onClick={() => setResultQuery({ offset: Math.max(0, resultPage.offset - resultPage.limit), search: resultPage.search })}>이전</Button><Button variant="outline" disabled={detailLoading || resultPage.offset + resultPage.limit >= resultPage.total_groups} onClick={() => setResultQuery({ offset: resultPage.offset + resultPage.limit, search: resultPage.search })}>다음</Button></div>}
      {!comparisonId && selectedCurrent && selectedBefore && <details className="correspondence-settings"><summary>구조 대응 · AI 자동 매핑 설정 (다른 Block / hierarchy 연결)</summary><p className="muted">규칙이 없는 instance는 양쪽 design 루트 아래 상대 경로가 같으면 자동으로 대응합니다. 이름이나 위치가 달라진 경로만 규칙을 추가하세요. 경로 일부를 검색하거나 전체 경로를 붙여넣고, ‘하위 트리 전체’는 상대 경로가 같은 자식까지 함께 대응합니다.</p>
        <MappingSuggestions key={JSON.stringify([selectedCurrent.id, selectedBefore.id, mappingId, mappingDraft])} auth={props.auth}
          current={selectedCurrent.id} baseline={selectedBefore.id} mapping={mappingId} draft={mappingDraft}
          maxAvailableDepth={maxHierarchyDepth}
          disabled={!project.can_write || action.busy}
          onInspect={(before, after) => { setBaselinePath(before); setCurrentPath(after); setMappingScope('node') }}
          onSaved={async id => { setMappingId(id); setMappingDraft([]); setMappingDraftPage(0); await props.refresh() }} />
        <div className="mapping-actions"><Button variant="outline" onClick={exportMappingTemplate}><Download size={15} />Export template</Button><Button variant="outline" disabled={action.busy || !project.can_write || beforeHierarchy.loading || currentHierarchy.loading || !!beforeHierarchy.error || !!currentHierarchy.error || !beforeHierarchy.data || !currentHierarchy.data} onClick={() => mappingFile.current?.click()}><UploadIcon size={15} />Import mapping CSV</Button><input ref={mappingFile} type="file" accept=".csv,text/csv" hidden aria-label="Mapping CSV 파일" onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void action.run(() => importMapping(file), 'CSV 대응을 불러왔습니다. 내용을 확인한 뒤 대응 저장을 누르세요.') }} /></div>
        <p className="muted">UTF-8 CSV · baseline_path,current_path,scope · 한 행에 1:1 대응 하나, scope는 node 또는 subtree입니다. 템플릿에는 작성 중인 대응 또는 선택한 경로가 포함됩니다. 가져오기는 작성 중인 목록 전체를 교체하며, 모든 행을 검증한 뒤 적용합니다. 대응 저장을 눌러야 서버에 저장됩니다.</p>
        <div className="mapping-controls"><PathPicker key={`baseline:${displayedBaselineId}`} label="기준 경로" hierarchy={beforeHierarchy} value={baselinePath} onChange={setBaselinePath} onReload={() => setDetailRefresh(value => value + 1)} disabled={action.busy} /><PathPicker key={`current:${displayedCurrentId}`} label="현재 경로" hierarchy={currentHierarchy} value={currentPath} onChange={setCurrentPath} onReload={() => setDetailRefresh(value => value + 1)} disabled={action.busy} /></div>
        <div className="mapping-actions"><Field label="대응 범위"><select value={mappingScope} onChange={event => setMappingScope(event.target.value as 'node' | 'subtree')}><option value="subtree">하위 트리 전체</option><option value="node">선택한 instance만</option></select></Field><Button variant="outline" disabled={action.busy || !project.can_write || !validBaselinePath || !validCurrentPath} onClick={() => { setMappingDraft(rows => [...rows, { baselinePath, currentPath, scope: mappingScope }]); setBaselinePath(''); setCurrentPath('') }}><Plus size={15} />대응 추가</Button></div>
        {mappingDraft.length > 0 && <div className="draft-mappings">{mappingDraft.slice(draftStart, draftStart + draftPageSize).map((row, position) => { const index = draftStart + position; return <div key={index}><span>{index + 1}. {row.baselinePath} → {row.currentPath} · {row.scope}</span><Button variant="ghost" disabled={action.busy || !project.can_write} aria-label={`${index + 1}번 대응 삭제`} onClick={() => setMappingDraft(rows => rows.filter((_, position) => position !== index))}><Trash2 size={15} /></Button></div> })}<div className="mapping-actions">{mappingDraft.length > draftPageSize && <><Button variant="outline" disabled={action.busy || !draftPage} onClick={() => setMappingDraftPage(draftPage - 1)}>이전 대응</Button><small>{draftStart + 1}–{Math.min(draftStart + draftPageSize, mappingDraft.length)} / {mappingDraft.length.toLocaleString()}</small><Button variant="outline" disabled={action.busy || draftPage === draftLastPage} onClick={() => setMappingDraftPage(draftPage + 1)}>다음 대응</Button></>}<Button disabled={action.busy || !project.can_write} onClick={() => action.run(saveMapping)}>대응 {mappingDraft.length}개 저장</Button></div></div>}
        <Field label="저장된 구조 대응"><select value={mappingId} onChange={event => setMappingId(event.target.value)}><option value="">대응 미선택 · 비교 불가</option>{mappings.map(row => <option key={row.id} value={row.id}>{date(row.created_at)} · 규칙 {Array.isArray(row.payload.rules) ? row.payload.rules.length : 0}개 · r{row.revision}</option>)}</select></Field>
      </details>}
      {browser.roots.length ? <HierarchyBrowser roots={browser.roots} childrenOf={childrenOf} onRequestChildren={referenceComparison ? (key, offset) => void loadChildren(key, offset) : undefined} flat={flatResults} hasResult={!!detail} separator={currentHierarchy.data?.hierarchy_separator} columns={columns} onColumns={chooseColumns} minArea={minArea} onMinArea={chooseMinArea} labels={sideLabels} gateBases={gateBases} unitSwitch={props.unitSwitch} marks={marks} locate={locate} /> : !currentHierarchy.loading && !beforeHierarchy.loading && !currentHierarchy.error && !beforeHierarchy.error && !pending && !detailLoading && !referenceComparison && <Empty title={comparisonId || selectedCurrent ? '선택한 실행에 표시할 hierarchy가 없습니다' : '현재 실행을 선택하세요'}>Block → NET_VER → REVISION → corner → 실행 파일을 명시적으로 선택하세요. 최신 실행을 자동 선택하지 않습니다.</Empty>}
    </section>
    {currentAreaId && <NotesPanel auth={props.auth} canWrite={project.can_write} projectId={currentAreaId}
      block={currentBlock?.area_id ? { areaId: currentBlock.area_id, name: currentBlock.name } : undefined}
      netVers={[...new Set(currentBlock?.versions.map(row => row.net_ver) || [])]} currentNet={current.net}
      baselineProject={mode === 'project' && baseline?.status === 'available' && baseline.baseline_project_id ? { areaId: baseline.baseline_project_id, name: savedProject?.name || text(baseline.baseline_project?.payload.name) } : null}
      records={props.records} onSaved={props.refresh} />}
    <details className="panel upload-history"><summary>업로드 이력 · PC CLI 메타데이터</summary><Uploads auth={props.auth} access={props.access} uploads={props.uploads} records={props.records} filters={filters} /></details>
    {currentAreaId && (
      <section className="panel history-shortcut-bar">
        <span>이 프로젝트에 저장된 비교 이력 <strong>{history.length}건</strong>을 블록·버전·코너별로 검색해 다시 열 수 있습니다.</span>
        <Button variant="outline" size="sm" onClick={() => setSection('history')}>비교 이력 열기 →</Button>
      </section>
    )}
    </>}
  </>
}
