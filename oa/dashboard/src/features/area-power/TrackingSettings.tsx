import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import type { z } from 'zod'
import { allRecords, allUploads, request, revision, text, type Auth, type TrackingRecord, type Upload } from './api'
import { Button, Empty, Field, useAction } from './components/workspace'
import { useAtlas, type AtlasCatalog, type AtlasProject } from '@/features/atlas/atlasContext'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import { trackingOverviewSchema, type CornerSpec, type TrackingOverviewData } from './trackingApi'
import { baselineSchema } from './tracking/trackingUtils'
import { TrackingRoundsCard } from './tracking/TrackingRoundsCard'
import { ProjectTrackingConfigCard } from './tracking/ProjectTrackingConfigCard'
import { BlockTrackingConfigCard } from './tracking/BlockTrackingConfigCard'

/** Admin-only Area/Power baseline, NET_VER round, automation, and mail recipient management. */
export function AreaTrackingAdmin() {
  const host = useDashboardAuth(true)
  const atlas = useAtlas()
  const action = useAction()
  const auth = useMemo<Auth>(
    () => ({ actor: host.session?.reviewer || '', request: host.request, demo: atlas?.demo }),
    [host.session?.reviewer, host.request, atlas?.demo],
  )

  const [fallbackCatalog, setFallbackCatalog] = useState<AtlasCatalog | null>(null)
  const [uploads, setUploads] = useState<Upload[]>([])
  const [records, setRecords] = useState<TrackingRecord[]>([])
  const [workspaceLoading, setWorkspaceLoading] = useState(false)
  const [workspaceError, setWorkspaceError] = useState('')
  const [refreshTick, setRefreshTick] = useState(0)

  const catalog = atlas?.catalog || fallbackCatalog
  const available = catalog?.area_enabled === true && catalog.area_status !== 'unavailable'
  const projects = catalog?.projects || []
  const [projectId, setProjectId] = useState(() => atlas?.scope.projectId || '')

  useEffect(() => {
    if (!projectId && projects.length) setProjectId(projects[0].id)
  }, [projectId, projects])

  const loadWorkspace = useCallback(async (signal?: AbortSignal) => {
    setWorkspaceLoading(true)
    setWorkspaceError('')
    try {
      let currentCatalog = atlas?.catalog || null
      if (!currentCatalog) {
        currentCatalog = await host.request<AtlasCatalog>('/api/atlas/catalog', undefined, false, signal)
        if (!signal?.aborted) setFallbackCatalog(currentCatalog)
      }
      if (currentCatalog?.area_enabled === true && currentCatalog.area_status !== 'unavailable') {
        const [u, projRecs, blkRecs] = await Promise.all([
          allUploads(auth, signal),
          allRecords(auth, 'project', { latest: 'false' }, signal),
          allRecords(auth, 'block', { latest: 'false' }, signal),
        ])
        if (!signal?.aborted) {
          setUploads(u)
          setRecords([...projRecs, ...blkRecs])
        }
      }
    } catch (cause) {
      if (!signal?.aborted) setWorkspaceError(cause instanceof Error ? cause.message : 'Area/Power 관리 자료를 불러오지 못했습니다.')
    } finally {
      if (!signal?.aborted) setWorkspaceLoading(false)
    }
  }, [atlas?.catalog, auth, host.request])

  useEffect(() => {
    const controller = new AbortController()
    void loadWorkspace(controller.signal)
    return () => controller.abort()
  }, [loadWorkspace, refreshTick])

  const projectRecords = useMemo(
    () => new Map(records.filter(row => row.record_type === 'project').map(row => [text(row.payload.sanity_project_id), row])),
    [records],
  )
  const project = projects.find(p => p.id === projectId)
  const areaProjectId = project?.area_id || (project ? projectRecords.get(project.id)?.id : '') || ''

  const [baseline, setBaseline] = useState<z.infer<typeof baselineSchema> | null>(null)
  const [baselineChoice, setBaselineChoice] = useState('')
  const [processDraft, setProcessDraft] = useState('')
  const [baselineEditing, setBaselineEditing] = useState(false)
  const [trackingData, setTrackingData] = useState<TrackingOverviewData | null>(null)
  const [trackingLoading, setTrackingLoading] = useState(false)
  const [trackingError, setTrackingError] = useState('')
  const ctrlRef = useRef<AbortController | null>(null)

  const reloadProjectTracking = useCallback(async () => {
    ctrlRef.current?.abort()
    if (!areaProjectId) {
      setBaseline(null); setTrackingData(null); setTrackingError('')
      return
    }
    const controller = new AbortController()
    ctrlRef.current = controller
    setTrackingLoading(true); setTrackingError('')
    try {
      const [b, ov] = await Promise.all([
        request(auth, `/projects/${encodeURIComponent(areaProjectId)}/baseline`, baselineSchema, undefined, controller.signal),
        request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/overview`, trackingOverviewSchema, undefined, controller.signal),
      ])
      if (!controller.signal.aborted) {
        setBaseline(b)
        setProcessDraft(b.process || '')
        const matched = projects.find(row => (row.area_id || projectRecords.get(row.id)?.id) === b.baseline_project_id)
        setBaselineChoice(matched?.id || '')
        setTrackingData(ov)
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setTrackingError(cause instanceof Error ? cause.message : '프로젝트 자동 분석 설정을 조회하지 못했습니다.')
      }
    } finally {
      if (!controller.signal.aborted) setTrackingLoading(false)
    }
  }, [auth, areaProjectId, projects, projectRecords])

  useEffect(() => {
    void reloadProjectTracking()
    return () => ctrlRef.current?.abort()
  }, [reloadProjectTracking, refreshTick])

  const baselineCandidates = projects.filter(row => row.id !== project?.id)
  const baselineProject = useMemo(
    () => projects.find(row => (row.area_id || projectRecords.get(row.id)?.id) === baseline?.baseline_project_id),
    [projects, projectRecords, baseline?.baseline_project_id],
  )
  const choiceProject = baselineCandidates.find(row => row.id === baselineChoice)
  const choiceAreaId = choiceProject?.area_id || (choiceProject ? projectRecords.get(choiceProject.id)?.id : '') || ''

  async function prepareProject(target: AtlasProject) {
    await auth.request('/api/atlas/area-scope', { project_id: target.id }, false)
    if (atlas) await atlas.reload()
    setRefreshTick(v => v + 1)
  }

  async function saveBaselineProject(targetAreaId: string | null) {
    if (!areaProjectId || !baseline) throw new Error('현재 프로젝트 연결과 기준 조회가 필요합니다.')
    const value = await request(auth, `/projects/${encodeURIComponent(areaProjectId)}/baseline`, baselineSchema, {
      baseline_project_id: targetAreaId,
      expected_row_version: revision(baseline.row_version),
    })
    setBaseline(value); setBaselineEditing(false); setRefreshTick(v => v + 1)
  }

  async function saveProjectProcess() {
    if (!areaProjectId || !baseline) throw new Error('현재 프로젝트 연결과 기준 조회가 필요합니다.')
    const value = await request(auth, `/projects/${encodeURIComponent(areaProjectId)}/process`, baselineSchema, {
      process: processDraft.trim() || null,
      expected_row_version: revision(baseline.row_version),
    })
    setBaseline(value); setProcessDraft(value.process || '')
    if (atlas) await atlas.reload()
    setRefreshTick(v => v + 1)
  }

  async function reloadAll() {
    if (atlas) await atlas.reload()
    setRefreshTick(v => v + 1)
  }

  return (
    <div className="area-power area-tracking-admin">
      {action.feedback}
      {workspaceError && <div className="notice error" role="alert">{workspaceError}</div>}
      {!available && !workspaceLoading ? (
        <Empty title="Area/Power 수치 분석 서비스가 비활성 상태입니다">
          Area/Power DB 및 서비스 연결이 준비되면 여기에서 프로젝트별 비교 기준, 자동 분석, 회차, 메일 수신자를 관리할 수 있습니다.
        </Empty>
      ) : (
        <>
          <section className="panel comparison-setup-bar project-context-bar" aria-label="관리 대상 프로젝트 및 비교 기준">
            <div className="baseline-summary">
              <label className="setup-project">
                <span>대상 프로젝트</span>
                <select value={projectId} onChange={e => { setProjectId(e.target.value); setBaselineEditing(false) }}>
                  <option value="">프로젝트 선택</option>
                  {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              {project && (
                <>
                  <span className="context-pill">{(baseline ? baseline.process : project.process) || '공정 미등록'}</span>
                  <span className="setup-arrow" aria-hidden="true">↔</span>
                  <span className="setup-baseline">
                    <small>비교 기준</small>
                    <strong className={baseline?.status !== 'available' ? 'is-unset' : ''}>
                      {baseline?.status === 'available'
                        ? text(baseline.baseline_project?.payload.name) || baselineProject?.name
                        : baseline?.status === 'unavailable' ? '기준 조회 불가' : '미설정'}
                    </strong>
                    {baseline?.status === 'available' && baseline.baseline_process && (
                      <span className="context-pill subtle">{baseline.baseline_process}</span>
                    )}
                  </span>
                </>
              )}
              {project && areaProjectId && (
                <Button variant="outline" size="sm" disabled={trackingLoading || !project.can_write} onClick={() => setBaselineEditing(v => !v)}>
                  {baselineEditing ? '기준 닫기' : '기준 프로젝트 · 공정 변경'}
                </Button>
              )}
              <Button variant="outline" size="sm" disabled={workspaceLoading || trackingLoading} onClick={() => setRefreshTick(v => v + 1)}>
                <RefreshCw size={14} className={workspaceLoading || trackingLoading ? 'spin' : ''} />새로고침
              </Button>
            </div>

            {project && areaProjectId && baselineEditing && (
              <div className="baseline-editor">
                {baseline?.status === 'unavailable' && (
                  <p className="notice warning">저장된 기준 프로젝트에 접근할 수 없습니다. 다른 기준으로 교체하거나 해제하세요.</p>
                )}
                <Field label="기준 프로젝트">
                  <select value={baselineChoice} onChange={e => setBaselineChoice(e.target.value)}>
                    <option value="">기준 프로젝트 선택</option>
                    {baselineCandidates.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
                  </select>
                </Field>
                {choiceProject && !choiceAreaId && (
                  <Button size="sm" disabled={action.busy || !choiceProject.can_write} onClick={() => action.run(() => prepareProject(choiceProject), '기준 프로젝트를 연결했습니다.')}>
                    선택한 기준 프로젝트 연결
                  </Button>
                )}
                <Button size="sm" disabled={action.busy || trackingLoading || !baseline || !choiceAreaId || !project.can_write} onClick={() => action.run(() => saveBaselineProject(choiceAreaId), '기준 프로젝트를 저장했습니다.')}>
                  기준 저장
                </Button>
                <Button variant="outline" size="sm" disabled={action.busy || !baseline || baseline.status === 'unset' || !project.can_write} onClick={() => action.run(() => saveBaselineProject(null), '기준 프로젝트를 해제했습니다.')}>
                  기준 해제
                </Button>
                <div className="process-editor">
                  <Field label="현재 프로젝트 공정">
                    <select value={processDraft} onChange={e => setProcessDraft(e.target.value)}>
                      <option value="">미지정</option>
                      {baseline?.process && !catalog?.processes?.some(item => item.name === baseline.process) && (
                        <option value={baseline.process}>{baseline.process} · 기존 공정</option>
                      )}
                      {catalog?.processes?.map(item => (
                        <option key={item.name} value={item.name}>{item.name} · {item.unit_area} µm²</option>
                      ))}
                    </select>
                  </Field>
                  <Button variant="outline" size="sm" disabled={action.busy || trackingLoading || !baseline || !project.can_write || processDraft.trim() === (baseline.process || '')} onClick={() => action.run(saveProjectProcess, '공정을 저장했습니다.')}>
                    공정 저장
                  </Button>
                  <small>공정과 코너가 같으면 같은 library로 판단합니다.{baseline?.status === 'available' && <> 기준 공정 · {baseline.baseline_process || '미등록'}</>}</small>
                </div>
              </div>
            )}
          </section>

          {!project ? (
            <Empty title="관리할 프로젝트를 선택하세요">프로젝트를 선택하면 기준 프로젝트·블록별 기준 버전, 버전 회차, 자동 분석, 메일 수신자를 관리할 수 있습니다.</Empty>
          ) : !areaProjectId ? (
            <section className="panel">
              <p className="notice">이 프로젝트는 아직 Area/Power 서비스에 연결되지 않았습니다.</p>
              <Button size="sm" disabled={action.busy || !project.can_write} onClick={() => action.run(() => prepareProject(project), '프로젝트를 Area/Power에 연결했습니다.')}>
                프로젝트 연결
              </Button>
            </section>
          ) : (
            <>
              {!project.can_write && (
                <p className="notice warning">
                  이 프로젝트의 설정을 저장하려면 ‘프로젝트·권한’ 탭에서 본인의 프로젝트 멤버 권한도 함께 승인해 주세요.
                </p>
              )}
              {trackingError && <div className="notice error" role="alert">{trackingError}</div>}
              {trackingLoading && !trackingData ? (
                <div className="loading-state" role="status"><LoaderCircle className="spin" size={20} />설정을 불러오는 중…</div>
              ) : (
                <TrackingSettings
                  auth={auth}
                  project={project}
                  baselineProject={baselineProject}
                  areaProjectId={areaProjectId}
                  baselineAreaProjectId={baseline?.baseline_project_id}
                  uploads={uploads}
                  data={trackingData}
                  onReload={reloadAll}
                />
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}

interface TrackingSettingsProps {
  auth: Auth
  project: AtlasProject
  baselineProject?: AtlasProject
  areaProjectId: string
  baselineAreaProjectId?: string | null
  uploads: Upload[]
  data: TrackingOverviewData | null
  initialBlockId?: string
  onReload: () => Promise<void>
}

export function TrackingSettings({
  auth,
  project,
  baselineProject,
  areaProjectId,
  baselineAreaProjectId,
  uploads,
  data,
  initialBlockId,
  onReload,
}: TrackingSettingsProps) {
  const action = useAction()
  const areaBlocks = useMemo(() => project.blocks.filter(b => Boolean(b.area_id)), [project.blocks])
  const [defaultCorners, setDefaultCorners] = useState<CornerSpec[]>([])
  const [deadlineDays, setDeadlineDays] = useState('7')
  const [deadlineTime, setDeadlineTime] = useState('18:00')

  return (
    <div className="tracking-settings">
      {action.feedback}
      <TrackingRoundsCard
        auth={auth}
        project={project}
        areaProjectId={areaProjectId}
        areaBlocks={areaBlocks}
        rounds={data?.rounds || []}
        deadlineDays={deadlineDays}
        deadlineTime={deadlineTime}
        busy={action.busy}
        runAction={action.run}
        onReload={onReload}
      />
      <ProjectTrackingConfigCard
        auth={auth}
        project={project}
        areaProjectId={areaProjectId}
        data={data}
        defaultCorners={defaultCorners}
        setDefaultCorners={setDefaultCorners}
        deadlineDays={deadlineDays}
        setDeadlineDays={setDeadlineDays}
        deadlineTime={deadlineTime}
        setDeadlineTime={setDeadlineTime}
        busy={action.busy}
        runAction={action.run}
        onReload={onReload}
      />
      <BlockTrackingConfigCard
        auth={auth}
        project={project}
        baselineProject={baselineProject}
        areaProjectId={areaProjectId}
        baselineAreaProjectId={baselineAreaProjectId}
        areaBlocks={areaBlocks}
        uploads={uploads}
        data={data}
        defaultCorners={defaultCorners}
        initialBlockId={initialBlockId}
        busy={action.busy}
        runAction={action.run}
        onReload={onReload}
      />
    </div>
  )
}
