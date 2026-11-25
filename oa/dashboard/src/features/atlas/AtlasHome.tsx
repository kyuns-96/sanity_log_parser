import { useState } from 'react'
import { ArrowRight, Boxes, Check, CheckCircle2, ChevronRight, FolderPlus, Link2, Plus, RefreshCw, ShieldCheck } from 'lucide-react'
import { useAtlas } from './atlasContext'
import type { AtlasBlock, AtlasProject, AtlasVersion, UnboundArea } from './atlasContext'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import { useDashboardNavigation } from '@/lib/dashboardMode'
import ProjectProcessField from './ProjectProcessField'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'

export default function AtlasHome() {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const navigate = useDashboardNavigation()
  const [kind, setKind] = useState<'project' | 'block' | null>(null)
  const [porOpen, setPorOpen] = useState(false)
  const [busyAction, setBusyAction] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const catalog = atlas.catalog
  const canWrite = !!atlas.project?.can_write || !!catalog?.can_manage_catalog
  const open = (path: string) => { void navigate(atlas.href(path), auth.session?.user_id).catch(cause => setError(String(cause))) }
  const logsPath = catalog?.local_mode ? '/?page=review' : '/?page=workbench'
  const select = (scope: { projectId: string; blockId: string; versionId: string }) => { setError(''); void atlas.selectScope(scope).catch(cause => setError(cause instanceof Error ? cause.message : String(cause))) }

  const unboundAreaCount = catalog?.area_enabled && catalog.area_status === 'ready'
    ? catalog.projects.reduce((sum, project) => sum + (project.area_id ? 0 : 1) + project.blocks.filter(block => !block.area_id).length, 0)
    : 0
  const selectedPorId = atlas.block?.por?.version_id || null
  const isSelectedPor = Boolean(atlas.version && selectedPorId && atlas.version.id === selectedPorId)
  const selectedProjectStatus = atlas.project?.status === 'completed' ? 'completed' : 'active'

  async function syncAreaScopes() {
    setBusyAction(true); setError(''); setNotice('')
    try {
      const result = await auth.request<{ projects_created: number; blocks_created: number }>('/api/atlas/area-scope/sync', {})
      await atlas.reload()
      setNotice(`기존 미연결 항목을 Area/Power에 일괄 연결했습니다. (프로젝트 ${result.projects_created}개 · 블록 ${result.blocks_created}개)`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusyAction(false)
    }
  }

  async function toggleProjectStatus() {
    if (!atlas.project) return
    const next = selectedProjectStatus === 'completed' ? 'active' : 'completed'
    setBusyAction(true); setError(''); setNotice('')
    try {
      await auth.request(`/api/atlas/projects/${encodeURIComponent(atlas.project.id)}/status`, { status: next })
      await atlas.reload()
      setNotice(`프로젝트 상태를 ‘${next === 'completed' ? '완료(기준 프로젝트)' : '진행 중'}’으로 변경했습니다.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusyAction(false)
    }
  }

  const formatVersionLabel = (v: AtlasVersion) =>
    [v.net_ver, v.revision, v.layout, v.eco_num].filter(Boolean).join(' · ')

  const blockPorLabel = (block: AtlasBlock) => {
    const porId = block.por?.version_id
    if (!porId) return null
    const found = block.versions.find(v => v.id === porId)
    return found ? formatVersionLabel(found) : porId
  }

  return <main className="atlas-home"><section className="atlas-home-heading"><div><h1>프로젝트</h1></div><div className="atlas-actions">{catalog?.can_manage_catalog && unboundAreaCount > 0 && <button className="atlas-button secondary" disabled={busyAction || atlas.loading} onClick={() => void syncAreaScopes()}><Link2 size={15} />미연결 {unboundAreaCount}개 일괄 연결</button>}<button className="atlas-button secondary" disabled={atlas.loading} onClick={() => void atlas.reload().catch(() => undefined)}><RefreshCw size={16} />새로고침</button></div></section>
    {catalog?.local_mode && <div className="atlas-message">로컬 모드 · 변경사항은 DB에 저장됩니다. 로그 검토는 회사 SSO가 필요합니다.</div>}
    {error && <p className="atlas-message error" role="alert">{error}</p>}{notice && <p className="atlas-message" role="status">{notice}</p>}
    <section className="atlas-selection" aria-label="선택한 범위">
      <div className="atlas-selection-path"><span>선택한 범위</span>
        <ol>
          <li className={atlas.project ? undefined : 'is-empty'}>{atlas.project?.name || '프로젝트 선택 전'}{atlas.project && <span className={`atlas-status-badge ${selectedProjectStatus === 'completed' ? 'is-completed' : ''}`}>{selectedProjectStatus === 'completed' ? '완료' : '진행 중'}</span>}</li>
          <li className={atlas.block ? undefined : 'is-empty'}>{atlas.block?.name || '블록 선택 전'}</li>
          <li className={atlas.version ? undefined : 'is-empty'}>{atlas.version ? formatVersionLabel(atlas.version) : '버전 선택 전'}{isSelectedPor && <span className="atlas-por-badge"><ShieldCheck size={12} aria-hidden="true" />PoR</span>}</li>
        </ol>
        {atlas.version && <small>{atlas.version.eco_num ? `ECO ${atlas.version.eco_num} · ` : ''}수신 ECO {atlas.version.ecos.filter(Boolean).length}개</small>}
      </div>
      <div className="atlas-actions">
        {atlas.project && canWrite && <button type="button" className="atlas-button secondary" disabled={busyAction} onClick={() => void toggleProjectStatus()}><CheckCircle2 size={15} />{selectedProjectStatus === 'completed' ? '진행 중으로 전환' : '완료로 전환'}</button>}
        <button className="atlas-button" onClick={() => open(logsPath)}>{catalog?.local_mode ? '수신 로그 대장 열기' : '로그 검토 열기'}<ArrowRight size={16} /></button>
        {catalog && !catalog.area_enabled ? <span className="atlas-selection-note">수치 분석 서비스를 사용할 수 없습니다. {catalog.area_error || ''}</span> : <button className="atlas-button secondary" onClick={() => open('/?page=area-power')}>프로젝트 비교 열기<ArrowRight size={16} /></button>}
      </div>
    </section>
    <section className="atlas-picker" aria-label="프로젝트 · 블록 · 버전">
      <div className="atlas-picker-column">
        <header><h2>프로젝트{catalog && <span>{catalog.projects.length}</span>}</h2>{catalog?.can_manage_catalog && <button className="atlas-button secondary" onClick={() => setKind('project')}><FolderPlus size={15} />프로젝트 등록</button>}</header>
        {atlas.loading && !catalog ? <p className="atlas-picker-empty" role="status">불러오는 중…</p> : !catalog ? <p className="atlas-picker-empty">프로젝트를 조회할 수 없습니다. 연결 상태를 확인하고 새로고침하세요.</p> : !catalog.projects.length ? <p className="atlas-picker-empty">등록된 프로젝트가 없습니다. {catalog.can_manage_catalog ? '프로젝트를 등록하세요.' : '관리자에게 프로젝트 등록을 요청하세요.'}</p> : <ul>{catalog.projects.map(project => <li key={project.id}><button type="button" className="atlas-picker-item" aria-pressed={atlas.scope.projectId === project.id} onClick={() => select({ projectId: project.id, blockId: '', versionId: '' })}>
          <Boxes size={18} /><span className="atlas-picker-text"><strong>{project.name}</strong>{project.description && <small>{project.description}</small>}<small>{project.process && `공정 ${project.process} · `}블록 {project.blocks.length} · 버전 {project.blocks.reduce((count, block) => count + block.versions.length, 0)}</small></span>{project.status === 'completed' && <em className="is-completed">완료</em>}<em>{project.can_write ? '읽기 · 쓰기' : '읽기'}</em><ChevronRight size={16} />
        </button></li>)}</ul>}
      </div>
      <div className="atlas-picker-column">
        <header><h2>블록{atlas.project && <span>{atlas.project.blocks.length}</span>}</h2>{catalog?.can_manage_catalog && atlas.project && <button className="atlas-button secondary" onClick={() => setKind('block')}><Plus size={15} />블록 등록</button>}</header>
        {!atlas.project ? <p className="atlas-picker-empty">프로젝트를 선택하면 블록이 표시됩니다.</p> : !atlas.project.blocks.length ? <p className="atlas-picker-empty">등록된 블록이 없습니다.</p> : <ul>{atlas.project.blocks.map(block => { const porText = blockPorLabel(block); return <li key={block.id}><button type="button" className="atlas-picker-item" aria-pressed={atlas.block?.id === block.id} onClick={() => select({ projectId: atlas.project!.id, blockId: block.id, versionId: '' })}>
          <span className="atlas-picker-text"><strong>{block.name}</strong><small>{porText ? `PoR: ${porText} · ` : 'PoR 미지정 · '}버전 {block.versions.length}</small></span><ChevronRight size={16} />
        </button></li> })}</ul>}
      </div>
      <div className="atlas-picker-column">
        <header><h2>버전{atlas.block && <span>{atlas.block.versions.length}</span>}</h2>{canWrite && atlas.block && atlas.block.versions.length > 0 && <button type="button" className="atlas-button secondary" disabled={busyAction} onClick={() => setPorOpen(true)}><ShieldCheck size={15} />PoR 지정</button>}</header>
        {!atlas.block ? <p className="atlas-picker-empty">블록을 선택하면 버전이 표시됩니다.</p> : !atlas.block.versions.length ? <p className="atlas-picker-empty">VWP에서 업로드된 버전이 없습니다. VWP에서 데이터를 업로드하면 이곳에 표시되며 PoR을 지정할 수 있습니다.</p> : <ul>{atlas.block.versions.map(version => { const isPor = version.id === selectedPorId; const receivedCount = version.ecos.filter(Boolean).length; return <li key={version.id}><button type="button" className="atlas-picker-item" aria-pressed={atlas.version?.id === version.id} onClick={() => select({ projectId: atlas.project!.id, blockId: atlas.block!.id, versionId: version.id })}>
          <span className="atlas-picker-text"><strong className="atlas-picker-version">{version.net_ver}<span>{version.revision}</span></strong><small>{version.eco_num ? `ECO ${version.eco_num}${receivedCount ? ` · 수신 ${receivedCount}건` : ''}` : version.layout === 'POST' ? `수신 ECO ${receivedCount}개` : 'ECO 미기록'}</small></span>{isPor && <em className="is-por">PoR</em>}<em>{version.layout}</em>{atlas.version?.id === version.id && <Check size={16} />}
        </button></li> })}</ul>}
      </div>
    </section>
    {catalog?.local_mode && catalog.unbound_area && <LocalBinding key={`${atlas.scope.projectId}:${atlas.scope.blockId}:${atlas.scope.versionId}`} />}
    {kind && <CatalogForm kind={kind} onClose={() => setKind(null)} onCreated={message => { setKind(null); setNotice(message) }} />}
    {porOpen && atlas.project && atlas.block && atlas.block.versions.length > 0 && <PorAssignDialog project={atlas.project} block={atlas.block} initialVersion={atlas.version} onClose={() => setPorOpen(false)} onAssigned={message => { setPorOpen(false); setNotice(message) }} />}
  </main>
}

function PorAssignDialog({ project, block, initialVersion, onClose, onAssigned }: {
  project: AtlasProject
  block: AtlasBlock
  initialVersion?: AtlasVersion
  onClose: () => void
  onAssigned: (message: string) => void
}) {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const currentPorId = block.por?.version_id || null
  const currentPor = block.versions.find(v => v.id === currentPorId)
  const defaultTargetId = (initialVersion && initialVersion.id !== currentPorId ? initialVersion.id : '')
    || block.versions.find(v => v.id !== currentPorId)?.id
    || initialVersion?.id
    || block.versions[0]?.id
    || ''
  const [targetVersionId, setTargetVersionId] = useState(defaultTargetId)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [requestId] = useState(() => crypto.randomUUID())
  const targetVersion = block.versions.find(v => v.id === targetVersionId)
  const sameAsPor = Boolean(targetVersion && currentPorId && targetVersion.id === currentPorId)
  const formatVer = (v: AtlasVersion) => [v.net_ver, v.revision, v.layout, v.eco_num].filter(Boolean).join(' · ')
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!targetVersion || sameAsPor || !reason.trim()) return
    setBusy(true); setError('')
    try {
      await auth.request(`/api/atlas/blocks/${encodeURIComponent(block.id)}/por`, {
        project_id: project.id,
        version_id: targetVersion.id,
        expected_revision: block.por?.revision ?? 0,
        request_id: requestId,
        reason: reason.trim(),
      })
      await atlas.reload()
      onAssigned(`${block.name}의 PoR을 ${formatVer(targetVersion)}로 지정했습니다.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="atlas-modal" showCloseButton={!busy}>
      <DialogHeader>
        <DialogTitle>{block.name}의 PoR 지정</DialogTitle>
        <DialogDescription>VWP에서 업로드된 버전 중 기준 버전(PoR)을 선택합니다. 로그 검토와 Area/Power 기준 프로젝트 참조에 공통 적용됩니다.</DialogDescription>
      </DialogHeader>
      <form onSubmit={submit}>
        <p><strong>현재 PoR:</strong> {currentPor ? formatVer(currentPor) : '미지정'}</p>
        <label>PoR로 지정할 버전<select required disabled={busy} value={targetVersionId} onChange={event => setTargetVersionId(event.target.value)}>{block.versions.map(v => <option key={v.id} value={v.id}>{formatVer(v)}{v.id === currentPorId ? ' (현재 PoR)' : ''}</option>)}</select></label>
        {sameAsPor && <p className="atlas-message">이미 현재 PoR로 지정된 버전입니다. 다른 버전을 선택하세요.</p>}
        {project.status === 'completed' && <p className="atlas-message">완료된 프로젝트입니다. PoR 변경 시 이 프로젝트를 기준으로 참조하는 신규 Area/Power 설정의 기본 기준 버전이 갱신됩니다.</p>}
        <label>변경 사유<textarea autoFocus required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} placeholder="예: 최종 ECO 확정 / 마일스톤 기준 버전 지정" disabled={busy} /></label>
        {error && <p role="alert" className="atlas-message error">{error}</p>}
        <div className="atlas-actions"><button className="atlas-button secondary" type="button" disabled={busy} onClick={onClose}>취소</button><button className="atlas-button" disabled={busy || !targetVersion || sameAsPor || !reason.trim()}>{busy ? '저장 중…' : 'PoR 지정 저장'}</button></div>
      </form>
    </DialogContent>
  </Dialog>
}

function CatalogForm({ kind, onClose, onCreated }: { kind: 'project' | 'block'; onClose: () => void; onCreated: (message: string) => void }) {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const [fields, setFields] = useState({ name: '', description: '', process: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [requestId] = useState(() => crypto.randomUUID())
  const field = (key: keyof typeof fields, value: string) => setFields(previous => ({ ...previous, [key]: value }))
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const path = kind === 'project' ? '/api/atlas/projects' : '/api/atlas/blocks'
      const body = kind === 'project'
        ? { request_id: requestId, name: fields.name, description: fields.description, process: fields.process || null }
        : { request_id: requestId, project_id: atlas.scope.projectId, name: fields.name }
      const result = await auth.request<{ status: string; project_id?: string; block_id?: string; area_prepared?: boolean; area_error?: string }>(path, body)
      await atlas.reload()
      await atlas.selectScope({ projectId: result.project_id || atlas.scope.projectId, blockId: kind === 'project' ? '' : result.block_id || atlas.scope.blockId, versionId: '' })
      const areaNote = result.area_prepared === false ? ' (Area/Power 자동 연결은 대기 중입니다. 프로젝트 비교 화면에서 다시 연결해 주세요.)' : result.area_prepared === true ? ' (Area/Power 범위도 함께 연결했습니다.)' : ''
      onCreated(`공통 카탈로그에 등록했습니다.${areaNote}`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}><DialogContent className="atlas-modal" showCloseButton={!busy}><DialogHeader><DialogTitle>{kind === 'project' ? '공통 프로젝트 등록' : '공통 블록 등록'}</DialogTitle><DialogDescription>{atlas.demo ? '현재 탭의 합성 카탈로그에 저장합니다. 실제 DB나 권한에는 반영하지 않으며 새로고침 시 초기화됩니다.' : '실제 공통 카탈로그에 저장합니다. 기존 분석 데이터와 인증 권한은 유지됩니다.'}</DialogDescription></DialogHeader><form onSubmit={submit}>
    {kind === 'block' && <p>프로젝트: {atlas.project?.name}</p>}
    <label>{kind === 'project' ? '프로젝트 이름' : '블록 이름'}<input autoFocus required maxLength={100} value={fields.name} onChange={event => field('name', event.target.value)} /><small>같은 프로젝트 이름은 같은 프로젝트이며, 같은 프로젝트의 같은 블록 이름은 기존 블록을 사용합니다. 앞뒤 공백만 제거하고 대소문자와 내부 공백은 구분합니다.</small></label>
    {kind === 'project' && <><ProjectProcessField processes={atlas.catalog?.processes || []} value={fields.process} onChange={name => field('process', name)} api={auth.request} reload={atlas.reload} disabled={busy} /><label>설명<textarea value={fields.description} onChange={event => field('description', event.target.value)} /></label></>}
    {error && <p role="alert" className="atlas-message error">{error}</p>}<div className="atlas-actions"><button className="atlas-button secondary" type="button" disabled={busy} onClick={onClose}>취소</button><button className="atlas-button" disabled={busy}>{busy ? '저장 중…' : '등록'}</button></div>
  </form></DialogContent></Dialog>
}

function LocalBinding() {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const [project, setProject] = useState('')
  const [block, setBlock] = useState('')
  const [version, setVersion] = useState('')
  const [previous, setPrevious] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const rows = atlas.catalog!.bindable_area || atlas.catalog!.unbound_area!
  const unbound = atlas.catalog!.unbound_area!
  const projectId = atlas.project?.area_id || project
  const blockId = atlas.block?.area_id || block
  const label = (row: UnboundArea) => String(row.payload?.name || row.name || [row.payload?.net_ver, row.payload?.revision, row.payload?.layout, row.payload?.eco_num].filter(Boolean).join(' · ') || '이름 없는 항목')
  const needsLineage = !!atlas.version && !Object.hasOwn(atlas.version, 'previous_version_id')
  const total = unbound.projects.length + unbound.blocks.length + unbound.versions.length
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      await auth.request('/api/atlas/bind-area', { project_id: atlas.project!.id, area_project_id: projectId, ...(atlas.block ? { block_id: atlas.block.id, area_block_id: blockId } : {}), ...(atlas.version ? { version_id: atlas.version.id, area_version_id: version } : {}), ...(needsLineage ? { previous_version_id: JSON.parse(previous) as string | null } : {}) })
      await atlas.reload(); setNotice('실제 기존 데이터의 명시적 연결을 저장했습니다.')
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }
  return <details className="atlas-panel atlas-local-binding"><summary>로컬 운영자: 기존 수치 데이터 연결 (미연결 {total}개)</summary>
    <p>기존 분석 ID와 이력은 보존됩니다. 이름으로 자동 연결하지 않습니다. 위에서 프로젝트·블록·버전을 선택한 후 실제 소유 관계가 일치하는 기존 데이터를 선택해 연결하세요. 프로젝트 공통의 과거 버전 ID는 여러 블록에 명시적으로 연결할 수 있습니다.</p>
    {!atlas.project ? <p>공통 프로젝트를 먼저 선택하세요.</p> : <form onSubmit={submit}><div className="atlas-form-grid">
      <label>기존 수치 프로젝트{atlas.project.area_id ? <span>연결됨</span> : <select required value={project} onChange={event => { setProject(event.target.value); setBlock(''); setVersion('') }}><option value="">프로젝트 선택</option>{rows.projects.map(row => <option key={row.id} value={row.id}>{label(row)}</option>)}</select>}</label>
      {atlas.block && <label>기존 수치 블록{atlas.block.area_id ? <span>연결됨</span> : <select required value={block} onChange={event => { setBlock(event.target.value); setVersion('') }}><option value="">블록 선택</option>{rows.blocks.filter(row => row.project_id === projectId).map(row => <option key={row.id} value={row.id}>{label(row)}</option>)}</select>}</label>}
      {atlas.version && <label>기존 수치 버전<select required value={version} onChange={event => setVersion(event.target.value)}><option value="">버전 선택</option>{rows.versions.filter(row => row.project_id === projectId && (!row.block_id || row.block_id === blockId)).map(row => <option key={row.id} value={row.id}>{label(row)}</option>)}</select></label>}
      {needsLineage && <label>명시적 수치 계보<select required value={previous} onChange={event => setPrevious(event.target.value)}><option value="">계보 시작 또는 이전 버전 선택</option><option value="null">이 버전에서 계보 시작</option>{atlas.block?.versions.filter(item => item.id !== atlas.version?.id).map(item => <option key={item.id} value={JSON.stringify(item.id)}>{item.net_ver} · {item.revision} · {item.layout}</option>)}</select></label>}
    </div>{error && <p className="atlas-message error" role="alert">{error} 화면을 새로고침한 후 필요한 경우 직접 다시 연결하세요. 자동 재시도하지 않습니다.</p>}{notice && <p className="atlas-message" role="status">{notice}</p>}<button className="atlas-button secondary" disabled={busy || !!atlas.version?.area_id || needsLineage && !previous}>{busy ? '연결 중…' : atlas.version?.area_id ? '이미 연결된 버전' : '선택한 실제 데이터 연결'}</button></form>}
  </details>
}
