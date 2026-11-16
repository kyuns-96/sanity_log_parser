import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronRight, GitCompareArrows, LoaderCircle, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { z } from 'zod'
import { comparisonCreatedSchema, cornerKey, date, detailSchema, format, formatPercent, object, recordSchema, request, revision, text, uploadName, type AreaAccess, type Auth, type ComparisonDetail, type TrackingRecord, type Upload } from './api'
import { Badge, Button, Empty, Field, useAction } from './components/workspace'
import { Uploads } from './UploadViews'
import { useAtlas, type AtlasProject } from '../atlasContext'

const baselineSchema = z.object({ project_id: z.string(), row_version: z.string(), baseline_project_id: z.string().nullable(), baseline_project: recordSchema.nullable(), status: z.enum(['unset', 'available', 'unavailable']) })
const hierarchySchema = z.object({ upload_id: z.string(), snapshot_id: z.string().nullable(), hierarchy_separator: z.string(), items: z.array(z.object({ path: z.string(), parent_path: z.string().nullable(), depth: z.string().transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0), area: z.unknown().nullable(), power_mw: z.unknown().nullable() })) })
type Hierarchy = z.infer<typeof hierarchySchema>
type Selection = { block: string; net: string; version: string; corner: string; upload: string }
type MappingDraft = { baselinePath: string; currentPath: string; scope: 'node' | 'subtree' }
type ResultGroup = { id: string; baselinePaths: string[]; currentPaths: string[]; metrics: Record<string, TrackingRecord['payload']> }
type TreeRow = { key: string; parent: string; baselinePaths: string[]; currentPaths: string[]; baseline?: Hierarchy['items'][number]; current?: Hierarchy['items'][number]; result?: ResultGroup }
const emptySelection: Selection = { block: '', net: '', version: '', corner: '', upload: '' }
interface ComparisonProps { auth: Auth; access: AreaAccess; records: TrackingRecord[]; uploads: Upload[]; comparisons: TrackingRecord[]; refresh: () => Promise<void>; refreshVersion: number; loading: boolean }

export function Comparisons(props: ComparisonProps) {
  const atlas = useAtlas()!
  const project = atlas.project!
  const action = useAction()
  const alive = useRef(true)
  const mutation = useRef<AbortController | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false; mutation.current?.abort() } }, [])
  const [baseline, setBaseline] = useState<z.infer<typeof baselineSchema> | null>(null)
  const [baselineError, setBaselineError] = useState('')
  const [baselineLoading, setBaselineLoading] = useState(false)
  const [baselineEditing, setBaselineEditing] = useState(false)
  const [baselineChoice, setBaselineChoice] = useState('')
  const lastDesignation = useRef<string | null>(null)
  const [currentRun, setCurrentRun] = useState({ versionId: '', corner: '', upload: '' })
  const [before, setBefore] = useState<Selection>(emptySelection)
  const [mappingId, setMappingId] = useState('')
  const [mappingDraft, setMappingDraft] = useState<MappingDraft[]>([])
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
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const projectRecords = useMemo(() => new Map(props.records.filter(row => row.record_type === 'project').map(row => [text(row.payload.sanity_project_id), row])), [props.records])
  const currentAreaId = project.area_id || projectRecords.get(project.id)?.id || ''
  const candidates = atlas.catalog?.projects || []
  const baselineCandidates = candidates.filter(row => row.id !== project.id)
  const savedProject = candidates.find(row => (row.area_id || projectRecords.get(row.id)?.id) === baseline?.baseline_project_id)
  const choiceProject = baselineCandidates.find(row => row.id === baselineChoice)
  const choiceAreaId = choiceProject?.area_id || (choiceProject ? projectRecords.get(choiceProject.id)?.id : '') || ''
  const currentBlock = atlas.block
  const beforeBlock = savedProject?.blocks.find(row => row.id === before.block)
  const currentVersion = atlas.version
  const current: Selection = { block: currentBlock?.id || '', net: atlas.netVer, version: currentVersion?.id || '', corner: currentRun.versionId === currentVersion?.id ? currentRun.corner : '', upload: currentRun.versionId === currentVersion?.id ? currentRun.upload : '' }
  const beforeVersion = beforeBlock?.versions.find(row => row.id === before.version && row.net_ver === before.net)
  const currentUploads = props.uploads.filter(row => row.state === 'ready' && row.project_id === currentAreaId && !!currentBlock?.area_id && row.block_id === currentBlock.area_id && !!currentVersion?.area_id && row.version_id === currentVersion.area_id)
  const beforeUploads = props.uploads.filter(row => row.state === 'ready' && row.project_id === baseline?.baseline_project_id && !!beforeBlock?.area_id && row.block_id === beforeBlock.area_id && !!beforeVersion?.area_id && row.version_id === beforeVersion.area_id)
  const selectedCurrent = currentUploads.find(row => row.id === current.upload && cornerKey(row) === current.corner)
  const selectedBefore = beforeUploads.find(row => row.id === before.upload && cornerKey(row) === before.corner)
  const mappings = props.records.filter(row => row.record_type === 'mapping' && selectedCurrent && selectedBefore && ['current', 'baseline'].every(side => {
    const upload = side === 'current' ? selectedCurrent : selectedBefore
    return ['project_id', 'block_id', 'version_id'].every(key => text(row.payload[`${side}_${key}`]) === upload[key])
  }))
  const savedInput = object(detail?.comparison.payload.request)
  const referenceComparison = detail?.comparison.payload.result_format === 'hierarchy-reference-v1'
  const displayedCurrentId = comparisonId ? text(savedInput.current_upload_id) : selectedCurrent?.id || ''
  const displayedBaselineId = comparisonId ? text(savedInput.baseline_upload_id) : selectedBefore?.id || ''
  const readHierarchy = !comparisonId || !!detail && !referenceComparison
  const currentHierarchy = useHierarchy(props.auth, readHierarchy ? displayedCurrentId : '', detailRefresh)
  const beforeHierarchy = useHierarchy(props.auth, readHierarchy ? displayedBaselineId : '', detailRefresh)
  const validBaselinePath = !beforeHierarchy.loading && !beforeHierarchy.error && !!beforeHierarchy.data?.items.some(row => row.path === baselinePath)
  const validCurrentPath = !currentHierarchy.loading && !currentHierarchy.error && !!currentHierarchy.data?.items.some(row => row.path === currentPath)
  const results = useMemo(() => groupedResults(detail), [detail])
  const treeRows = useMemo<TreeRow[]>(() => referenceComparison
    ? results.map(result => ({ key: `g:${result.id}`, parent: '', baselinePaths: result.baselinePaths, currentPaths: result.currentPaths, result }))
    : mergeHierarchy(currentHierarchy.data?.items || [], beforeHierarchy.data?.items || [], results), [referenceComparison, currentHierarchy.data, beforeHierarchy.data, results])
  const displayedCurrent = props.uploads.find(row => row.id === displayedCurrentId)
  const displayedBefore = props.uploads.find(row => row.id === displayedBaselineId)
  const canCompare = project.can_write && !!selectedCurrent && !!baseline?.baseline_project_id && (!before.upload || !!selectedBefore) && (!mappingId || mappings.some(row => row.id === mappingId))

  useEffect(() => {
    setCurrentRun({ versionId: '', corner: '', upload: '' })
  }, [atlas.scope.blockId, atlas.netVer, atlas.scope.versionId])
  useEffect(() => {
    if (!currentAreaId) { setBaseline(null); return }
    const controller = new AbortController()
    setBaselineLoading(true); setBaselineError('')
    request(props.auth, `/projects/${encodeURIComponent(currentAreaId)}/baseline`, baselineSchema, undefined, controller.signal).then(value => {
      if (controller.signal.aborted) return
      setBaseline(value)
      const designation = `${value.status}:${value.baseline_project_id || ''}`
      if (lastDesignation.current !== designation) {
        lastDesignation.current = designation
        setBaselineChoice(candidates.find(row => (row.area_id || projectRecords.get(row.id)?.id) === value.baseline_project_id)?.id || '')
        setBefore(emptySelection); setMappingId(''); setMappingDraft([])
        setBaselineEditing(value.status !== 'available')
      }
    }).catch(cause => { if (!controller.signal.aborted) { setBaseline(null); setBaselineChoice(''); setBaselineError(cause instanceof Error ? cause.message : '기준을 조회하지 못했습니다.') } }).finally(() => { if (!controller.signal.aborted) setBaselineLoading(false) })
    return () => controller.abort()
  }, [props.auth, currentAreaId, props.refreshVersion])
  useEffect(() => { setMappingId(''); setMappingDraft([]); setBaselinePath(''); setCurrentPath('') }, [current.block, current.net, current.version, current.upload, before.upload])
  useEffect(() => { setExpanded(new Set()) }, [displayedCurrentId, displayedBaselineId, comparisonId])
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
          const params = new URLSearchParams({ view: 'hierarchy', offset: String(resultQuery.offset), limit: '200', search: resultQuery.search })
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
  }, [props.auth, comparisonId, detailRefresh, props.refreshVersion, resultQuery])

  function beginMutation() { mutation.current?.abort(); const controller = new AbortController(); mutation.current = controller; return controller.signal }
  function chooseCurrent(selection: Selection) {
    if (selection.net !== atlas.netVer) {
      void action.run(() => atlas.selectNetVer(selection.net), '')
    } else if (selection.version !== current.version) {
      void action.run(() => atlas.selectScope({ ...atlas.scope, versionId: selection.version }), '')
    } else {
      setCurrentRun({ versionId: current.version, corner: selection.corner, upload: selection.upload })
    }
  }
  async function prepare(nativeProject: AtlasProject, selection?: Selection, previous?: string | null) {
    const signal = beginMutation()
    await props.auth.request('/api/atlas/area-scope', { project_id: nativeProject.id, ...(selection?.block ? { block_id: selection.block } : {}), ...(selection?.version ? { version_id: selection.version } : {}), ...(previous !== undefined ? { previous_version_id: previous } : {}) }, false, signal)
    if (!alive.current) return
    await atlas.reload(); if (alive.current) await props.refresh()
  }
  async function saveBaseline(target: string | null) {
    if (!currentAreaId || !baseline) throw new Error('현재 프로젝트 연결과 기준 조회가 필요합니다.')
    const value = await request(props.auth, `/projects/${encodeURIComponent(currentAreaId)}/baseline`, baselineSchema, { baseline_project_id: target, expected_row_version: revision(baseline.row_version) }, beginMutation())
    if (!alive.current) return
    setBaseline(value); setBaselineEditing(false)
    await props.refresh()
  }
  async function saveMapping() {
    if (!selectedCurrent || !selectedBefore || !mappingDraft.length) throw new Error('양쪽 실행과 대응 경로를 선택하세요.')
    const payload: Record<string, unknown> = { rules: mappingDraft.map(row => ({ kind: 'one_to_one', scope: row.scope, baseline_paths: [row.baselinePath], current_paths: [row.currentPath] })) }
    for (const [side, upload] of [['current', selectedCurrent], ['baseline', selectedBefore]] as const) for (const key of ['project_id', 'block_id', 'version_id']) payload[`${side}_${key}`] = upload[key]
    const value = await request(props.auth, '/config', recordSchema, { record_type: 'mapping', project_id: selectedCurrent.project_id, block_id: selectedCurrent.block_id, version_id: selectedCurrent.version_id, payload }, beginMutation())
    if (!alive.current) return
    setMappingId(value.id); setMappingDraft([])
    await props.refresh()
  }
  async function compare() {
    if (!canCompare || !selectedCurrent) throw new Error('현재 화면 범위의 정확한 실행을 다시 선택하세요.')
    const value = await request(props.auth, '/comparisons', comparisonCreatedSchema, { current_upload_id: selectedCurrent.id, baseline_upload_id: selectedBefore?.id || null, comparison_kind: 'previous_project', scope: { kind: 'all' }, mapping_id: mappingId || null }, beginMutation())
    if (!alive.current) return
    setDetail(null); setDetailError(''); setResultSearch(''); setResultQuery({ offset: 0, search: '' }); setComparisonId(value.comparison.id)
    await props.refresh()
  }
  const filters = { project_id: currentAreaId, block_id: currentBlock?.area_id || '', version_id: currentVersion?.area_id || '', snapshot_id: '' }
  const history = props.comparisons.filter(row => row.project_id === currentAreaId)
  const pending = detail && ['queued', 'pending', 'running'].includes(detail.comparison.state)
  const resultPage = detail?.result_page

  return <>
    {action.feedback}
    {!project.can_write && <p className="notice">조회 전용 · 기준 설정, 비교 실행과 구조 대응 변경에는 프로젝트 멤버 권한이 필요합니다.</p>}
    <section className="panel project-baseline" aria-label="프로젝트 기준 설정">
      <div className="baseline-summary"><div><h2>{project.name} <span aria-hidden="true">→</span> {baseline?.status === 'available' ? text(baseline.baseline_project?.payload.name) || savedProject?.name : baseline?.status === 'unavailable' ? '기준 조회 불가' : '기준 미설정'}</h2></div><Button variant="outline" disabled={baselineLoading || !project.can_write} onClick={() => setBaselineEditing(value => !value)}>{baselineEditing ? '설정 접기' : '기준 변경'}</Button></div>
      {baselineError && <div className="notice error" role="alert">{baselineError}</div>}
      {!currentAreaId ? <div className="notice"><p>프로젝트만 연결하면 CSV·블록·버전 없이 기준을 설정할 수 있습니다.</p><Button disabled={action.busy || !project.can_write} onClick={() => action.run(() => prepare(project))}>현재 프로젝트 연결</Button></div> : baselineEditing && <div className="baseline-editor">
        {baseline?.status === 'unavailable' && <p className="notice warning">저장된 기준은 현재 권한으로 조회할 수 없습니다. 다른 기준으로 교체하거나 해제할 수 있습니다.</p>}
        <Field label="기준 프로젝트"><select value={baselineChoice} onChange={event => setBaselineChoice(event.target.value)}><option value="">기준 프로젝트 선택</option>{baselineCandidates.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
        {choiceProject && !choiceAreaId && <Button disabled={action.busy || !choiceProject.can_write} onClick={() => action.run(() => prepare(choiceProject))}>선택한 기준 프로젝트 연결</Button>}
        <Button disabled={action.busy || baselineLoading || !baseline || !choiceAreaId || !project.can_write} onClick={() => action.run(() => saveBaseline(choiceAreaId))}>기준 저장</Button>
        <Button variant="outline" disabled={action.busy || !baseline || baseline.status === 'unset' || !project.can_write} onClick={() => action.run(() => saveBaseline(null))}>기준 해제</Button>
        <small>A→B는 현재 프로젝트에만 저장됩니다. 다른 프로젝트의 기준을 바꾸지 않습니다.</small>
      </div>}
    </section>
    <section className="panel comparison-workspace">
      <div className="compare-sides">
        <RunSelector label="현재" project={project} selection={current} onChange={chooseCurrent} blockReadOnly uploads={currentUploads} busy={action.busy} onPrepare={(selection, previous) => action.run(() => prepare(project, selection, previous))} />
        {savedProject && baseline?.status === 'available' ? <RunSelector label="기준" project={savedProject} selection={before} onChange={setBefore} uploads={beforeUploads} busy={action.busy} onPrepare={(selection, previous) => action.run(() => prepare(savedProject, selection, previous))} /> : <Empty title={baseline?.status === 'unavailable' ? '기준 프로젝트 조회 불가' : '비교 기준을 저장하세요'}>현재 실행의 관측값은 기준이 없어도 볼 수 있습니다. 기준·대응이 없는 Δ/%는 unavailable입니다.</Empty>}
      </div>
      {selectedCurrent && selectedBefore && cornerKey(selectedCurrent) !== cornerKey(selectedBefore) && <div className="notice warning">양쪽 corner가 다릅니다. Power의 비교 가능성과 조건 차이 사유를 확인하세요. 같은 이름으로 조건을 추정하지 않습니다.</div>}
      <div className="comparison-actions"><div><h2>Instance Area · Power</h2><small>부모 값에는 자식이 포함됩니다. 행을 다시 합산하지 않습니다.</small></div>{comparisonId ? <><Badge value={referenceComparison && detail?.comparison.state === 'completed' ? '조회 준비 완료' : detail?.comparison.state || 'loading'} /><Button variant="outline" onClick={() => { setComparisonId(''); setDetail(null) }}>현재 선택으로 돌아가기</Button><Button variant="outline" disabled={detailLoading} onClick={() => setDetailRefresh(value => value + 1)}><RefreshCw size={15} />결과 갱신</Button></> : <Button disabled={action.busy || !canCompare} onClick={() => action.run(compare)}><GitCompareArrows size={16} />비교 실행</Button>}</div>
      {!comparisonId && selectedCurrent && !selectedBefore && baseline?.status === 'available' && <p className="notice">기준 실행을 선택하지 않으면 현재 관측만 보존하고 비교 값은 unavailable로 남깁니다.</p>}
      {comparisonId && <p className="frozen-inputs">고정 입력 · 현재 {displayedCurrent ? uploadName(displayedCurrent) : displayedCurrentId ? '선택 당시 실행' : '조회 중'} ↔ 기준 {displayedBefore ? uploadName(displayedBefore) : displayedBaselineId ? '선택 당시 실행' : '기준 실행 없음'} · 경로 대응 {savedInput.mapping_id ? '고정 revision' : '미선택'}</p>}
      {referenceComparison && detail?.comparison.state === 'completed' && <p className="notice">비교 조회 준비 완료 · 수치는 조회할 때 계산합니다. 전체 지표의 분석이나 AI 판정이 완료된 상태가 아닙니다.</p>}
      {pending && <p className="notice"><LoaderCircle size={15} className="spin" />수치 작업 {detail.comparison.state} · 완료까지 자동 갱신합니다.</p>}
      {pending && object(detail.comparison.payload.coverage).expected_metrics != null && <p role="status">지표 처리 {format(object(detail.comparison.payload.coverage).processed_metrics)} / {format(object(detail.comparison.payload.coverage).expected_metrics)}</p>}
      {detail?.comparison.state === 'failed' && <p className="notice error">수치 작업이 실패했습니다. 작업 상태와 입력을 확인하세요. 없는 결과를 0으로 대체하지 않습니다.</p>}
      {detailError && <div className="notice error" role="alert">{detailError}</div>}
      {[['현재', currentHierarchy.error], ['기준', beforeHierarchy.error]].filter(([, error]) => error).map(([side, error]) => <div className="notice error" role="alert" key={side}>{side} 경로 조회 실패 · {error}<Button variant="outline" onClick={() => setDetailRefresh(value => value + 1)}>경로 다시 조회</Button></div>)}
      {(currentHierarchy.loading || beforeHierarchy.loading) && <p role="status"><LoaderCircle size={16} className="spin" />선택한 실행의 hierarchy 조회 중…</p>}
      {referenceComparison && <form className="comparison-result-search" onSubmit={event => { event.preventDefault(); setResultQuery({ offset: 0, search: resultSearch.trim() }) }}><Field label="비교 경로 검색"><input type="search" value={resultSearch} onChange={event => setResultSearch(event.target.value)} placeholder="기준 또는 현재 instance 경로 검색" autoComplete="off" spellCheck={false} /></Field><Button type="submit" disabled={detailLoading}>검색</Button></form>}
      {detailLoading && <p role="status"><LoaderCircle size={16} className="spin" />비교 수치 조회 중…</p>}
      {resultPage && <div className="comparison-result-pages"><p role="status">{resultPage.total_groups ? `${(resultPage.offset + 1).toLocaleString()}–${Math.min(resultPage.offset + resultPage.limit, resultPage.total_groups).toLocaleString()} / ${resultPage.total_groups.toLocaleString()}그룹` : '검색 조건에 맞는 경로가 없습니다.'}</p><Button variant="outline" disabled={detailLoading || resultPage.offset === 0} onClick={() => setResultQuery({ offset: Math.max(0, resultPage.offset - resultPage.limit), search: resultPage.search })}>이전</Button><Button variant="outline" disabled={detailLoading || resultPage.offset + resultPage.limit >= resultPage.total_groups} onClick={() => setResultQuery({ offset: resultPage.offset + resultPage.limit, search: resultPage.search })}>다음</Button></div>}
      {treeRows.length ? <HierarchyTable rows={treeRows} expanded={expanded} onExpanded={setExpanded} hasResult={!!detail} paged={referenceComparison} /> : !currentHierarchy.loading && !beforeHierarchy.loading && !currentHierarchy.error && !beforeHierarchy.error && !pending && !detailLoading && !referenceComparison && <Empty title={comparisonId || selectedCurrent ? '선택한 실행에 표시할 hierarchy가 없습니다' : '현재 실행을 선택하세요'}>Block → NET_VER → REVISION → corner → 실행 파일을 명시적으로 선택하세요. 최신 실행이나 같은 경로를 자동 연결하지 않습니다.</Empty>}
      {!comparisonId && selectedCurrent && selectedBefore && <details className="correspondence-settings"><summary>구조 대응 설정 · 다른 Block / hierarchy 연결</summary><p className="muted">경로 일부를 검색하거나 전체 경로를 붙여넣으세요. 양쪽 상위 경로를 ‘하위 트리 전체’로 연결하면 상대 경로가 같은 자식도 함께 대응합니다. 이름이 달라진 자식은 별도 규칙을 추가하세요.</p>
        <div className="mapping-controls"><PathPicker key={`baseline:${displayedBaselineId}`} label="기준 경로" hierarchy={beforeHierarchy} value={baselinePath} onChange={setBaselinePath} onReload={() => setDetailRefresh(value => value + 1)} disabled={action.busy} /><PathPicker key={`current:${displayedCurrentId}`} label="현재 경로" hierarchy={currentHierarchy} value={currentPath} onChange={setCurrentPath} onReload={() => setDetailRefresh(value => value + 1)} disabled={action.busy} /></div>
        <div className="mapping-actions"><Field label="대응 범위"><select value={mappingScope} onChange={event => setMappingScope(event.target.value as 'node' | 'subtree')}><option value="subtree">하위 트리 전체</option><option value="node">선택한 instance만</option></select></Field><Button variant="outline" disabled={action.busy || !project.can_write || !validBaselinePath || !validCurrentPath} onClick={() => { setMappingDraft(rows => [...rows, { baselinePath, currentPath, scope: mappingScope }]); setBaselinePath(''); setCurrentPath('') }}><Plus size={15} />대응 추가</Button></div>
        {mappingDraft.length > 0 && <div className="draft-mappings">{mappingDraft.map((row, index) => <div key={index}><span>{row.baselinePath} → {row.currentPath} · {row.scope}</span><Button variant="ghost" disabled={!project.can_write} aria-label={`${index + 1}번 대응 삭제`} onClick={() => setMappingDraft(rows => rows.filter((_, position) => position !== index))}><Trash2 size={15} /></Button></div>)}<Button disabled={action.busy || !project.can_write} onClick={() => action.run(saveMapping)}>대응 {mappingDraft.length}개 저장</Button></div>}
        <Field label="저장된 구조 대응"><select value={mappingId} onChange={event => setMappingId(event.target.value)}><option value="">대응 미선택 · 비교 불가</option>{mappings.map(row => <option key={row.id} value={row.id}>{date(row.created_at)} · 규칙 {Array.isArray(row.payload.rules) ? row.payload.rules.length : 0}개 · r{row.revision}</option>)}</select></Field>
      </details>}
    </section>
    <details className="panel upload-history"><summary>업로드 이력 · PC CLI 메타데이터</summary><Uploads auth={props.auth} access={props.access} uploads={props.uploads} records={props.records} filters={filters} /></details>
    <details className="panel comparison-history"><summary>저장된 비교 이력 · {history.length}건</summary><div className="compact-list">{history.map(row => <button key={row.id} onClick={() => { setDetail(null); setDetailError(''); setResultSearch(''); setResultQuery({ offset: 0, search: '' }); setComparisonId(row.id); setDetailRefresh(value => value + 1) }}><span>{date(row.created_at)} · {text(object(row.payload.request).comparison_kind) === 'previous_version' ? '이전 버전' : '프로젝트 비교'}</span><Badge value={row.payload.result_format === 'hierarchy-reference-v1' && row.state === 'completed' ? '조회 준비 완료' : row.state} /></button>)}</div>{!history.length && <p className="muted">저장된 비교가 없습니다.</p>}</details>
  </>
}

function PathPicker({ label, hierarchy, value, onChange, onReload, disabled }: { label: string; hierarchy: ReturnType<typeof useHierarchy>; value: string; onChange: (value: string) => void; onReload: () => void; disabled: boolean }) {
  const id = useId()
  const [page, setPage] = useState(0)
  const matches = useMemo(() => {
    const terms = value.toLowerCase().split(/\s+/).filter(Boolean)
    return (hierarchy.data?.items || []).filter(row => terms.every(term => row.path.toLowerCase().includes(term)))
  }, [hierarchy.data, value])
  const selected = !!hierarchy.data?.items.some(row => row.path === value)
  const pageSize = 50
  const lastPage = Math.max(0, Math.ceil(matches.length / pageSize) - 1)
  const currentPage = Math.min(page, lastPage)
  const start = currentPage * pageSize
  const visible = matches.slice(start, start + pageSize)
  function choose(path: string) { setPage(0); onChange(path) }
  return <section className="path-picker" aria-labelledby={`${id}-label`}>
    <label className="field" htmlFor={`${id}-search`}><span id={`${id}-label`}>{label}</span><input id={`${id}-search`} type="search" value={value} placeholder="경로 일부 검색 또는 전체 경로 붙여넣기" autoComplete="off" spellCheck={false} disabled={disabled || hierarchy.loading || !!hierarchy.error || !hierarchy.data?.items.length} aria-describedby={`${id}-status`} onChange={event => choose(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && matches.length === 1) { event.preventDefault(); choose(matches[0].path) } }} /></label>
    {hierarchy.loading ? <p id={`${id}-status`} role="status"><LoaderCircle size={15} className="spin" />{label} 조회 중…</p> : hierarchy.error ? <div className="notice error" id={`${id}-status`}><p>{label}를 불러오지 못했습니다. 위 오류 안내를 확인하세요.</p><Button variant="outline" onClick={onReload}>경로 다시 조회</Button></div> : !hierarchy.data?.items.length ? <p id={`${id}-status`} role="status">선택한 업로드에서 조회된 경로가 없습니다.</p> : <>
      <p id={`${id}-status`} role="status">검색 {matches.length.toLocaleString()}개 / 전체 {hierarchy.data.items.length.toLocaleString()}개{!selected && value && ' · 목록에서 경로를 선택하세요.'}</p>
      {selected && <p className="selected-path"><Check size={15} aria-hidden="true" /><span>선택됨 · {value}</span></p>}
      {!matches.length ? <p className="path-no-match">검색 결과가 없습니다. 경로 일부 또는 공백으로 구분한 단어를 입력하세요.</p> : <>
        <ul className="path-results" aria-label={`${label} 검색 결과`}>{visible.map(row => <li key={row.path}><button type="button" disabled={disabled} aria-pressed={row.path === value} onClick={() => choose(row.path)}>{row.path}</button></li>)}</ul>
        {matches.length > pageSize && <div className="path-pages"><Button variant="outline" disabled={disabled || !currentPage} aria-label={`${label} 이전 검색 결과`} onClick={() => setPage(currentPage - 1)}>이전</Button><small>{start + 1}–{Math.min(start + pageSize, matches.length)} / {matches.length.toLocaleString()}</small><Button variant="outline" disabled={disabled || currentPage === lastPage} aria-label={`${label} 다음 검색 결과`} onClick={() => setPage(currentPage + 1)}>다음</Button></div>}
      </>}
    </>}
  </section>
}

function RunSelector({ label, project, selection, onChange, uploads, busy, onPrepare, blockReadOnly = false }: { label: string; project: AtlasProject; selection: Selection; onChange: (value: Selection) => void; uploads: Upload[]; busy: boolean; onPrepare: (selection: Selection, previous?: string | null) => void; blockReadOnly?: boolean }) {
  const block = project.blocks.find(row => row.id === selection.block)
  const version = block?.versions.find(row => row.id === selection.version && row.net_ver === selection.net)
  const [previous, setPrevious] = useState('')
  useEffect(() => setPrevious(''), [project.id, selection.block, selection.version])
  const needsLineage = version && !Object.hasOwn(version, 'previous_version_id')
  const corners = new Map<string, string>()
  for (const upload of uploads) if (!corners.has(cornerKey(upload))) corners.set(cornerKey(upload), `${upload.process_label ?? '?'} · ${upload.voltage_v ?? '?'} V · ${upload.temp_c ?? '?'} °C · ${upload.pex ?? '?'}`)
  const runs = selection.corner ? uploads.filter(row => cornerKey(row) === selection.corner) : []
  return <fieldset className="run-selector"><legend>{label} · {project.name}</legend><div className="run-fields">
    <Field label={`${label} Block`}>{blockReadOnly ? <input readOnly value={block?.name || '상단 공통 Block 선택'} /> : <select value={selection.block} onChange={event => { onChange({ ...emptySelection, block: event.target.value }); setPrevious('') }}><option value="">Block 선택</option>{project.blocks.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select>}</Field>
    <Field label={`${label} NET_VER`}><select value={selection.net} disabled={!block} onChange={event => { onChange({ ...selection, net: event.target.value, version: '', corner: '', upload: '' }); setPrevious('') }}><option value="">NET_VER 선택</option>{[...new Set(block?.versions.map(row => row.net_ver) || [])].map(value => <option key={value}>{value}</option>)}</select></Field>
    <Field label={`${label} REVISION`}><select value={selection.version} disabled={!block || !selection.net} onChange={event => { onChange({ ...selection, version: event.target.value, corner: '', upload: '' }); setPrevious('') }}><option value="">REVISION 선택</option>{block?.versions.filter(row => row.net_ver === selection.net).map(row => <option key={row.id} value={row.id}>{row.revision} · {row.layout}</option>)}</select></Field>
    <Field label={`${label} corner`}><select value={selection.corner} disabled={!version?.area_id} onChange={event => onChange({ ...selection, corner: event.target.value, upload: '' })}><option value="">corner 선택</option>{[...corners].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label={`${label} 실행 파일`} className="run-file"><select value={selection.upload} disabled={!selection.corner} onChange={event => onChange({ ...selection, upload: event.target.value })}><option value="">실행 파일 선택</option>{runs.map(row => <option key={row.id} value={row.id}>{uploadName(row)}</option>)}</select></Field>
  </div>
    {blockReadOnly && <small>현재 Block은 상단 공통 범위를 사용하며, NET_VER·REVISION은 양쪽에서 함께 변경됩니다.</small>}
    {block && (!block.area_id || version && !version.area_id) && <div className="scope-preparation"><small>선택한 정확한 범위의 수치 연결이 필요합니다. 다른 범위 파일로 대체하지 않습니다.</small>{needsLineage && <Field label={`${label} 명시적 이전 버전`}><select value={previous} onChange={event => setPrevious(event.target.value)}><option value="">계보를 선택하세요</option><option value="none">이 버전에서 계보 시작</option>{block.versions.filter(row => row.id !== version.id).map(row => <option key={row.id} value={row.id}>{row.net_ver} · {row.revision} · {row.layout}</option>)}</select></Field>}<Button variant="outline" disabled={busy || !project.can_write || !!needsLineage && !previous} onClick={() => onPrepare(selection, needsLineage ? previous === 'none' ? null : previous : undefined)}>선택 범위 연결</Button></div>}
    {version?.area_id && !uploads.length && <small>선택한 버전에 준비된 CSV가 없습니다. 아래 업로드 이력의 메타데이터로 PC CLI 업로드를 준비하세요.</small>}
  </fieldset>
}

function useHierarchy(auth: Auth, id: string, refreshVersion: number) {
  const [state, setState] = useState<{ id: string; data: Hierarchy | null; error: string; loading: boolean }>({ id: '', data: null, error: '', loading: false })
  useEffect(() => {
    if (!id) { setState({ id, data: null, error: '', loading: false }); return }
    const controller = new AbortController()
    setState({ id, data: null, error: '', loading: true })
    request(auth, `/uploads/${encodeURIComponent(id)}/hierarchy`, hierarchySchema, undefined, controller.signal).then(data => { if (!controller.signal.aborted) setState({ id, data, error: '', loading: false }) }).catch(cause => { if (!controller.signal.aborted) setState({ id, data: null, error: cause instanceof Error ? cause.message : 'hierarchy를 조회하지 못했습니다.', loading: false }) })
    return () => controller.abort()
  }, [auth, id, refreshVersion])
  return state.id === id ? state : { id, data: null, error: '', loading: !!id }
}

function groupedResults(detail: ComparisonDetail | null) {
  const groups = new Map<string, ResultGroup>()
  const input = object(detail?.comparison.payload.request)
  function include(value: TrackingRecord['payload'], uploadId?: string | null, baselineUploadId?: string | null) {
    const pair = object(value.power_pair)
    if (text(value.metric).startsWith('pwr_') && (text(pair.current_upload_id || uploadId) !== text(input.current_upload_id) || text(pair.baseline_upload_id || baselineUploadId) !== text(input.baseline_upload_id))) return
    const baselinePaths = Array.isArray(value.baseline_paths) ? value.baseline_paths.map(text) : []
    const currentPaths = Array.isArray(value.current_paths) ? value.current_paths.map(text) : []
    const id = text(value.group_id) || JSON.stringify([baselinePaths, currentPaths])
    const group: ResultGroup = groups.get(id) || { id, baselinePaths, currentPaths, metrics: {} }
    group.metrics[text(value.metric)] = value
    groups.set(id, group)
  }
  for (const row of detail?.results || []) include(row.payload, row.upload_id, row.baseline_upload_id)
  for (const observation of detail?.observations || []) include(observation)
  return [...groups.values()]
}

function mergeHierarchy(current: Hierarchy['items'], baseline: Hierarchy['items'], results: ResultGroup[]) {
  const byCurrent = new Map(current.map(row => [row.path, row])), byBaseline = new Map(baseline.map(row => [row.path, row]))
  const currentKeys = new Map<string, string>(), baselineKeys = new Map<string, string>()
  const rows: TreeRow[] = results.map(result => {
    const row: TreeRow = { key: `g:${result.id}`, parent: '', baselinePaths: result.baselinePaths, currentPaths: result.currentPaths, result, baseline: result.baselinePaths.length === 1 ? byBaseline.get(result.baselinePaths[0]) : undefined, current: result.currentPaths.length === 1 ? byCurrent.get(result.currentPaths[0]) : undefined }
    if (result.currentPaths.length === 1 && result.baselinePaths.length <= 1) currentKeys.set(result.currentPaths[0], row.key)
    if (result.baselinePaths.length === 1 && result.currentPaths.length <= 1) baselineKeys.set(result.baselinePaths[0], row.key)
    return row
  })
  for (const item of current) if (!currentKeys.has(item.path)) { const key = `c:${item.path}`; currentKeys.set(item.path, key); rows.push({ key, parent: '', currentPaths: [item.path], baselinePaths: [], current: item }) }
  for (const item of baseline) if (!baselineKeys.has(item.path)) { const key = `b:${item.path}`; baselineKeys.set(item.path, key); rows.push({ key, parent: '', currentPaths: [], baselinePaths: [item.path], baseline: item }) }
  const byKey = new Map(rows.map(row => [row.key, row]))
  for (const row of rows) {
    const parent = row.current?.parent_path ? currentKeys.get(row.current.parent_path) : row.baseline?.parent_path ? baselineKeys.get(row.baseline.parent_path) : undefined
    if (parent && parent !== row.key) row.parent = parent
  }
  // An explicit mapping may invert nesting across sides. Break only display
  // cycles; never alter the engine's correspondence or calculated values.
  const settled = new Set<string>()
  for (const row of rows) {
    const trail = new Set<string>()
    let node: TreeRow | undefined = row
    while (node && !settled.has(node.key)) {
      if (trail.has(node.key)) { node.parent = ''; break }
      trail.add(node.key); node = byKey.get(node.parent)
    }
    for (const key of trail) settled.add(key)
  }
  return rows
}

function HierarchyTable({ rows, expanded, onExpanded, hasResult, paged }: { rows: TreeRow[]; expanded: Set<string>; onExpanded: (value: Set<string>) => void; hasResult: boolean; paged: boolean }) {
  const children = new Map<string, TreeRow[]>()
  for (const row of rows) { const list = children.get(row.parent) || []; list.push(row); children.set(row.parent, list) }
  const visible: { row: TreeRow; depth: number }[] = []
  const stack = [...(children.get('') || [])].reverse().map(row => ({ row, depth: 0 }))
  while (stack.length) {
    const entry = stack.pop()!
    visible.push(entry)
    if (expanded.has(entry.row.key)) for (const row of [...(children.get(entry.row.key) || [])].reverse()) stack.push({ row, depth: entry.depth + 1 })
  }
  return <div className="hierarchy-table"><small>{paged ? `현재 페이지 ${rows.length}행 · instance 전체 경로 표시` : `${visible.length} / ${rows.length}행 표시 · 가지를 펼쳐 하위 instance를 확인하세요.`} · 수치 비교이며 정상/이상 판정이 아님</small><div className="table-scroll"><table><thead><tr><th>기준 hierarchy</th><th>현재 hierarchy</th><th>기준 Area</th><th>현재 Area</th><th>Δ Area</th><th>Δ Area %</th><th>기준 Power (mW)</th><th>현재 Power (mW)</th><th>Δ Power</th><th>Δ Power %</th><th>비교 가능성 / 사유</th></tr></thead><tbody>{visible.map(({ row, depth }) => {
    const area = row.result?.metrics.total_area, power = row.result?.metrics.pwr_all_total_mw
    let areaReason = text(area?.reason), powerReason = text(power?.reason)
    if (areaReason === 'policy_unconfigured' || areaReason === 'policy_unconfigured · 정상 판정 아님') areaReason = ''
    if (powerReason === 'policy_unconfigured' || powerReason === 'policy_unconfigured · 정상 판정 아님') powerReason = ''
    const missingReason = !row.result ? hasResult ? '결과 없음' : '대응 미지정' : ''
    const hasChildren = children.has(row.key)
    const toggle = () => { const next = new Set(expanded); if (next.has(row.key)) next.delete(row.key); else next.add(row.key); onExpanded(next) }
    return <tr key={row.key}><td><span className="hierarchy-path" style={{ paddingInlineStart: `${Math.min(depth, 16) * 12}px` }}>{hasChildren && <button type="button" className="tree-row-toggle" aria-label={`${row.baselinePaths.join(' + ') || row.currentPaths.join(' + ')} ${expanded.has(row.key) ? '접기' : '펼치기'}`} aria-expanded={expanded.has(row.key)} onClick={toggle}>{expanded.has(row.key) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>}{row.baselinePaths.join(' + ') || '— · 기준 대응 없음'}</span></td><td>{row.currentPaths.join(' + ') || '— · 현재 대응 미지정'}</td><td className="numeric">{format(area ? area.baseline : row.baseline?.area)}</td><td className="numeric">{format(area ? area.current : row.current?.area)}</td><td className="numeric">{format(area?.delta)}</td><td className="numeric" title={text(area?.relative_change_pct)} aria-label={`Area 상대 변화율 ${area?.relative_change_pct == null ? '계산 불가' : `${text(area.relative_change_pct)}%`}`}>{formatPercent(area?.relative_change_pct)}</td><td className="numeric">{format(power ? power.baseline : row.baseline?.power_mw)}</td><td className="numeric">{format(power ? power.current : row.current?.power_mw)}</td><td className="numeric">{format(power?.delta)}</td><td className="numeric" title={text(power?.relative_change_pct)} aria-label={`Power 상대 변화율 ${power?.relative_change_pct == null ? '계산 불가' : `${text(power.relative_change_pct)}%`}`}>{formatPercent(power?.relative_change_pct)}</td><td className="metric-status"><div>Area <Badge value={area?.comparability || 'unavailable'} />{(areaReason || missingReason) && <small>{areaReason || missingReason}</small>}</div><div>Power <Badge value={power?.comparability || 'unavailable'} />{(powerReason || missingReason) && <small>{powerReason || missingReason}</small>}</div></td></tr>
  })}</tbody></table></div></div>
}
