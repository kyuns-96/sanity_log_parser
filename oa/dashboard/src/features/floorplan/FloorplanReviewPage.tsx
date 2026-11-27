import { useCallback, useEffect, useMemo, useState } from 'react'
import { Share2, Terminal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useAtlas } from '@/features/atlas/atlasContext'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import { FloorplanCanvas, type CanvasTool } from './FloorplanCanvas'
import { FloorplanInspector } from './FloorplanInspector'
import { ConfluencePublishDialog } from './ConfluencePublishDialog'
import { MODULE_PALETTE, type ModuleLayer } from './moduleContours'
import {
  floorplanRequest,
  type ChecklistTemplateItem,
  type ChecklistVerdict,
  type DatapathFlow,
  type FloorplanVersionDetail,
  type FloorplanWorkspaceData,
  type MacroOverride,
  type ReviewPin,
} from './floorplanApi'
import './floorplan.css'

export default function FloorplanReviewPage() {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const projects = atlas.catalog?.projects || []

  const [projectId, setProjectId] = useState(() => atlas.scope.projectId || projects[0]?.id || '')
  useEffect(() => {
    if (atlas.scope.projectId) setProjectId(atlas.scope.projectId)
    else if (!projectId && projects[0]) setProjectId(projects[0].id)
  }, [atlas.scope.projectId, projects, projectId])

  const project = projects.find(p => p.id === projectId)
  const areaProjectId = project?.area_id || ''
  const blocks = project?.blocks || []

  const [selectedBlockId, setSelectedBlockId] = useState('')
  useEffect(() => {
    if (!blocks.length) { setSelectedBlockId(''); return }
    if (!blocks.some(b => (b.area_id || b.id) === selectedBlockId)) {
      const preferred = blocks.find(b => b.area_id) || blocks[0]
      setSelectedBlockId(preferred.area_id || preferred.id)
    }
  }, [blocks, selectedBlockId])

  const selectedBlock = blocks.find(b => (b.area_id || b.id) === selectedBlockId)
  const isTopBlock = Boolean(project && selectedBlock && selectedBlock.name.trim() === project.name.trim())

  const [workspace, setWorkspace] = useState<FloorplanWorkspaceData | null>(null)
  const [selectedFpId, setSelectedFpId] = useState('')
  const [compareFpId, setCompareFpId] = useState('')
  const [selectedUploadId, setSelectedUploadId] = useState('')
  const [detail, setDetail] = useState<FloorplanVersionDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // Canvas & Review working state
  const [tool, setTool] = useState<CanvasTool>('inspect')
  const [selectedInst, setSelectedInst] = useState('')
  const [selectedModule, setSelectedModule] = useState('')
  const [moduleLayers, setModuleLayers] = useState<ModuleLayer[]>([])
  const [selectedBistGroup, setSelectedBistGroup] = useState('')
  const [overrides, setOverrides] = useState<Map<string, MacroOverride>>(new Map())
  const [overallVerdict, setOverallVerdict] = useState<'PENDING' | 'PASS' | 'WARN' | 'ACTION'>('PENDING')
  const [summaryNote, setSummaryNote] = useState('')
  const [checklistVerdicts, setChecklistVerdicts] = useState<ChecklistVerdict[]>([])
  const [reviewPins, setReviewPins] = useState<ReviewPin[]>([])
  const [datapaths, setDatapaths] = useState<DatapathFlow[]>([])
  const [onlyChangedTcl, setOnlyChangedTcl] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)

  const loadWorkspace = useCallback(async (signal?: AbortSignal) => {
    if (!areaProjectId && !atlas.demo) {
      setWorkspace(null); setDetail(null)
      return
    }
    setLoading(true); setError('')
    try {
      const targetProj = areaProjectId || project?.id || 'demo'
      const qs = selectedBlockId ? `?block_id=${encodeURIComponent(selectedBlockId)}` : ''
      const ws = await floorplanRequest<FloorplanWorkspaceData>(auth.request, atlas.demo, atlas.catalog, `/projects/${encodeURIComponent(targetProj)}/workspace${qs}`, undefined, signal)
      if (signal?.aborted) return
      setWorkspace(ws)
      if (ws.versions.length > 0) {
        setSelectedFpId(prev => ws.versions.some(v => v.id === prev) ? prev : ws.versions[0].id)
      } else {
        setSelectedFpId('')
        setDetail(null)
      }
    } catch (err) {
      if (!signal?.aborted) setError(err instanceof Error ? err.message : 'Floorplan 현황을 불러오지 못했습니다.')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [areaProjectId, atlas.demo, atlas.catalog, auth.request, project?.id, selectedBlockId])

  useEffect(() => {
    const ctrl = new AbortController()
    void loadWorkspace(ctrl.signal)
    return () => ctrl.abort()
  }, [loadWorkspace])

  const loadDetail = useCallback(async (fpId: string, signal?: AbortSignal) => {
    if (!fpId) { setDetail(null); return }
    const params = new URLSearchParams()
    if (compareFpId && compareFpId !== fpId) params.set('compare_fp_version_id', compareFpId)
    if (selectedUploadId) params.set('area_upload_id', selectedUploadId)
    if (project?.process) params.set('process', project.process)
    const qs = params.toString() ? `?${params.toString()}` : ''
    const d = await floorplanRequest<FloorplanVersionDetail>(auth.request, atlas.demo, atlas.catalog, `/versions/${encodeURIComponent(fpId)}${qs}`, undefined, signal)
    if (signal?.aborted) return
    setDetail(d)
    setOverrides(new Map())
    setSelectedInst('')
    setSelectedModule('')
    setModuleLayers([])
    setSelectedBistGroup('')
    setOverallVerdict(d.review.overall_verdict || 'PENDING')
    setSummaryNote(d.review.summary_note || '')
    setChecklistVerdicts(d.review.checklist || [])
    setReviewPins(d.review.review_pins || [])
    setDatapaths(d.review.datapaths || [])
    if (d.metrics?.upload_id && !selectedUploadId) setSelectedUploadId(d.metrics.upload_id)
  }, [auth.request, atlas.demo, atlas.catalog, compareFpId, selectedUploadId, project?.process])

  useEffect(() => {
    if (!selectedFpId) return
    const ctrl = new AbortController()
    void loadDetail(selectedFpId, ctrl.signal).catch(err => {
      if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : '버전 상세 조회 실패')
    })
    return () => ctrl.abort()
  }, [selectedFpId, loadDetail])

  const blockUploads = useMemo(
    () => (workspace?.available_area_uploads || []).filter(u => !selectedBlockId || u.block_id === selectedBlockId),
    [workspace?.available_area_uploads, selectedBlockId],
  )

  const activeIsTop = detail ? detail.fp_version.scope_kind === 'top' : isTopBlock
  const activeChecklistTpl = activeIsTop
    ? (workspace?.checklist_template.top_items || [])
    : (workspace?.checklist_template.subblock_items || [])

  async function handleSaveReview() {
    if (!selectedFpId) return
    setError(''); setNotice('')
    await floorplanRequest(auth.request, atlas.demo, atlas.catalog, `/versions/${encodeURIComponent(selectedFpId)}/review`, {
      overall_verdict: overallVerdict,
      summary_note: summaryNote,
      checklist: checklistVerdicts,
      review_pins: reviewPins,
      datapaths,
    })
    setNotice('Floorplan 리뷰 내용을 저장했습니다.')
    await loadWorkspace()
  }

  async function handleSaveChecklistTemplate(items: ChecklistTemplateItem[]) {
    if (!workspace) return
    const body = activeIsTop
      ? { top_items: items, subblock_items: workspace.checklist_template.subblock_items }
      : { top_items: workspace.checklist_template.top_items, subblock_items: items }
    const saved = await floorplanRequest<FloorplanWorkspaceData['checklist_template']>(
      auth.request, atlas.demo, atlas.catalog, `/projects/${encodeURIComponent(workspace.project_id)}/checklist-template`, body,
    )
    setWorkspace(prev => prev ? { ...prev, checklist_template: saved } : prev)
  }

  async function handleSaveProposal(title: string, note: string) {
    if (!selectedFpId || !overrides.size) return
    await floorplanRequest(auth.request, atlas.demo, atlas.catalog, `/versions/${encodeURIComponent(selectedFpId)}/proposals`, {
      title,
      note,
      overrides: [...overrides.values()],
    })
    setNotice(`Designer 배치 제안안 '${title}'을 저장했습니다.`)
    await loadDetail(selectedFpId)
  }

  function handleAddPinAt(xUm: number, yUm: number) {
    const nextNum = reviewPins.length + 1
    setReviewPins(prev => [
      ...prev,
      { id: `pin-${Date.now()}`, number: nextNum, x: xUm, y: yUm, severity: 'ACTION', title: `리뷰 코멘트 #${nextNum}`, comment: '', resolved: false },
    ])
    setTool('inspect')
  }

  function addModuleLayer(path: string) {
    setModuleLayers(prev => {
      if (prev.some(l => l.path === path)) return prev
      const used = new Set(prev.map(l => l.color))
      const color = MODULE_PALETTE.find(c => !used.has(c)) || MODULE_PALETTE[prev.length % MODULE_PALETTE.length]
      return [...prev, { path, color }]
    })
  }

  function toggleModuleLayer(path: string) {
    if (moduleLayers.some(l => l.path === path)) {
      const rest = moduleLayers.filter(l => l.path !== path)
      setModuleLayers(rest)
      if (selectedModule === path) setSelectedModule(rest[rest.length - 1]?.path || '')
    } else {
      addModuleLayer(path)
      setSelectedModule(path)
    }
  }

  function handleSelectModule(path: string) {
    setSelectedModule(path)
    const region = detail?.scene?.module_regions.find(mr => mr.hier_path === path || path.startsWith(mr.hier_path + '/'))
    if (region) addModuleLayer(region.hier_path)
  }

  const canWrite = Boolean(project?.can_write ?? true)

  return (
    <main className="fp-page" aria-label="Floorplan 리뷰">
      <header className="fp-topbar">
        <div className="fp-topbar-selectors">
          <label className="fp-select-field">
            <span>프로젝트</span>
            <select value={projectId} onChange={e => { setProjectId(e.target.value); setSelectedFpId(''); setCompareFpId(''); setSelectedUploadId('') }}>
              <option value="">프로젝트 선택</option>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>

          <label className="fp-select-field">
            <span>대상 (TOP / Sub-block)</span>
            <select value={selectedBlockId} onChange={e => { setSelectedBlockId(e.target.value); setSelectedFpId(''); setCompareFpId(''); setSelectedUploadId('') }}>
              {blocks.map(b => {
                const val = b.area_id || b.id
                const fc = project && b.name.trim() === project.name.trim()
                return <option key={b.id} value={val}>{b.name}{fc ? ' (TOP)' : ''}</option>
              })}
            </select>
          </label>

          <label className="fp-select-field">
            <span>Floorplan 버전 (FP_VER)</span>
            <select value={selectedFpId} onChange={e => setSelectedFpId(e.target.value)} disabled={!workspace?.versions.length}>
              {!workspace?.versions.length && <option value="">업로드된 FP_VER 없음</option>}
              {workspace?.versions.map(v => (
                <option key={v.id} value={v.id}>
                  {v.fp_ver}{v.linked_net_ver ? ` · ${v.linked_net_ver}` : ''} [{v.overall_verdict}]
                </option>
              ))}
            </select>
          </label>

          {(workspace?.versions.length || 0) > 1 && (
            <label className="fp-select-field">
              <span>비교 FP_VER</span>
              <select value={compareFpId} onChange={e => setCompareFpId(e.target.value)}>
                <option value="">비교 안 함</option>
                {workspace?.versions.filter(v => v.id !== selectedFpId).map(v => (
                  <option key={v.id} value={v.id}>{v.fp_ver} 윤곽 겹침</option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="fp-topbar-actions">
          {/* [TEMP_ARTIFACT_FALLBACK] */}
          {workspace?.storage_mode === 'temp_tracking_fallback' && (
            <Badge variant="outline" title="di_design_artifact 테이블 생성 전까지 di_area_power_tracking_record 임시 저장 경로로 동작 중">
              임시 DB 우회 모드
            </Badge>
          )}
          {detail && (
            <>
              <Badge variant="outline">{activeIsTop ? 'TOP Full-chip' : 'Sub-block'}</Badge>
              {detail.fp_version.linked_net_ver && <Badge variant="secondary">{detail.fp_version.linked_net_ver}</Badge>}
              <Button size="sm" onClick={() => setPublishOpen(true)}>
                <Share2 size={14} />Confluence 게시 · HTML 내보내기
              </Button>
            </>
          )}
        </div>
      </header>

      {error && <div className="fp-error-banner" role="alert">{error}</div>}
      {notice && <div className="fp-notice-banner" role="status">{notice}</div>}

      {!project ? (
        <section className="fp-empty-state">
          <h2>프로젝트를 선택하세요</h2>
          <p>프로젝트와 블록을 선택하면 독립 Floorplan 버전(FP_VER)별 도면, Memory BIST, Data-path, Area·LVT·Drive 지표를 함께 검토할 수 있습니다.</p>
        </section>
      ) : !areaProjectId && !atlas.demo ? (
        <section className="fp-empty-state">
          <h2>Area/Power 워크스페이스 연결 필요</h2>
          <p>홈 또는 프로젝트 비교 탭에서 이 프로젝트를 Area/Power 카탈로그에 먼저 연결한 뒤 VWP에서 Floorplan을 업로드하세요.</p>
        </section>
      ) : !detail || !detail.scene ? (
        <section className="fp-empty-state">
          <Terminal size={24} />
          <h2>{loading ? 'Floorplan 자료를 불러오는 중…' : '등록된 Floorplan 버전(FP_VER)이 없습니다'}</h2>
          <p>VWP 환경에서 로컬 DEF/LEF로부터 경량 SVG와 메타데이터만 추출해 업로드하세요 (원본 DEF/LEF는 DB로 전송되지 않습니다).</p>
          <pre className="fp-cli-hint">
            {`python -m vwp.floorplan --project ${project.name} --block ${selectedBlock?.name || 'BLOCK'} \\\n  --fp-ver FP_v1.0 --scope-kind ${isTopBlock ? 'top' : 'subblock'} --def ./block.def.gz --lef ./macros.lef`}
          </pre>
        </section>
      ) : (
        <div className="fp-workspace-grid">
          <FloorplanCanvas
            scene={detail.scene}
            compareScene={detail.compare_scene}
            tool={tool}
            onToolChange={setTool}
            selectedInst={selectedInst}
            onSelectInst={setSelectedInst}
            selectedModule={selectedModule}
            moduleLayers={moduleLayers}
            onToggleModuleLayer={toggleModuleLayer}
            onFocusModule={setSelectedModule}
            selectedBistGroup={selectedBistGroup}
            overrides={overrides}
            onOverridesChange={setOverrides}
            datapaths={datapaths}
            reviewPins={reviewPins}
            onAddPinAt={handleAddPinAt}
            canWrite={canWrite}
            onlyChangedTcl={onlyChangedTcl}
            onOnlyChangedTclChange={setOnlyChangedTcl}
            onSaveProposal={handleSaveProposal}
          />
          <FloorplanInspector
            scene={detail.scene}
            isTop={activeIsTop}
            selectedInst={selectedInst}
            onSelectInst={setSelectedInst}
            selectedModule={selectedModule}
            onSelectModule={handleSelectModule}
            moduleLayers={moduleLayers}
            onToggleModuleLayer={toggleModuleLayer}
            onClearModuleLayers={() => { setModuleLayers([]); setSelectedModule('') }}
            selectedBistGroup={selectedBistGroup}
            onSelectBistGroup={setSelectedBistGroup}
            overrides={overrides}
            onOverridesChange={setOverrides}
            onEnsureSandboxMode={() => { if (tool !== 'sandbox') setTool('sandbox') }}
            onLoadProposalOverrides={list => {
              setOverrides(new Map(list.map(ov => [ov.inst_name, ov])))
              setTool('sandbox')
            }}
            onlyChangedTcl={onlyChangedTcl}
            proposals={detail.proposals}
            metrics={detail.metrics}
            availableUploads={blockUploads}
            selectedUploadId={selectedUploadId}
            onSelectUploadId={setSelectedUploadId}
            overallVerdict={overallVerdict}
            onOverallVerdictChange={setOverallVerdict}
            summaryNote={summaryNote}
            onSummaryNoteChange={setSummaryNote}
            checklistTemplate={activeChecklistTpl}
            onSaveChecklistTemplate={handleSaveChecklistTemplate}
            checklistVerdicts={checklistVerdicts}
            onChecklistVerdictsChange={setChecklistVerdicts}
            reviewPins={reviewPins}
            onReviewPinsChange={setReviewPins}
            datapaths={datapaths}
            onDatapathsChange={setDatapaths}
            onSaveReview={handleSaveReview}
            canWrite={canWrite}
            demo={atlas.demo}
          />
        </div>
      )}

      {detail && workspace && (
        <ConfluencePublishDialog
          open={publishOpen}
          onOpenChange={setPublishOpen}
          projectName={project?.name || ''}
          blockName={selectedBlock?.name || ''}
          blockId={selectedBlockId}
          fpVer={detail.fp_version.fp_ver}
          projectConfluence={workspace.project_confluence}
          blockConfluence={workspace.block_confluence[selectedBlockId]}
          patStatus={workspace.confluence_pat}
          proposals={detail.proposals}
          publishes={detail.publishes}
          canWrite={canWrite}
          onSaveTargetConfig={async (scope, spaceKey, urlOrId) => {
            await floorplanRequest(auth.request, atlas.demo, atlas.catalog, `/projects/${encodeURIComponent(workspace.project_id)}/confluence-config`, {
              block_id: scope === 'block' ? selectedBlockId : null,
              space_key: spaceKey,
              parent_url_or_id: urlOrId,
            })
            await loadWorkspace()
          }}
          onSavePat={async token => {
            if (token === null) {
              await floorplanRequest(auth.request, atlas.demo, atlas.catalog, '/pat/remove', { expected_revision: workspace.confluence_pat.revision })
            } else {
              await floorplanRequest(auth.request, atlas.demo, atlas.catalog, '/pat', { token, expected_revision: workspace.confluence_pat.revision })
            }
            await loadWorkspace()
          }}
          onPreview={opts => floorplanRequest(auth.request, atlas.demo, atlas.catalog, `/versions/${encodeURIComponent(detail.fp_version.id)}/confluence-preview`, opts)}
          onPublish={async opts => {
            await floorplanRequest(auth.request, atlas.demo, atlas.catalog, `/versions/${encodeURIComponent(detail.fp_version.id)}/confluence-publish`, opts)
            await loadDetail(detail.fp_version.id)
          }}
        />
      )}
    </main>
  )
}
