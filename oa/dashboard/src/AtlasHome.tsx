import { useState } from 'react'
import { ArrowRight, Boxes, FolderPlus, Layers3, Plus, RefreshCw } from 'lucide-react'
import { useAtlas } from './atlasContext'
import type { UnboundArea } from './atlasContext'
import { useDashboardAuth } from './useDashboardAuth'
import { useDashboardNavigation } from './dashboardMode'
import AtlasPrepare from './AtlasPrepare'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './components/ui/dialog'

export default function AtlasHome() {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const navigate = useDashboardNavigation()
  const [kind, setKind] = useState<'project' | 'block' | 'version' | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const catalog = atlas.catalog
  const canWrite = !!atlas.project?.can_write || !!catalog?.can_manage_catalog
  const open = (path: string) => { void navigate(atlas.href(path), auth.session?.user_id).catch(cause => setError(String(cause))) }
  return <main className="atlas-home"><section className="atlas-home-heading"><div><span className="atlas-eyebrow">ONE DESIGN. CONNECTED EVIDENCE.</span><h1>설계의 맥락을 한곳에.</h1><p>프로젝트부터 로그 검토, 구성·전력 분석까지 같은 범위로 이어갑니다.</p></div><button className="atlas-button secondary" disabled={atlas.loading} onClick={() => void atlas.reload().catch(() => undefined)}><RefreshCw size={16} />새로고침</button></section>
    {catalog?.local_mode && <div className="atlas-message">실제 로컬 개발 모드입니다. 공통 카탈로그와 수치 데이터는 실제 DB에 저장됩니다. 회사 SSO 전용 로그 워크스페이스는 이 모드에서 사용할 수 없습니다.</div>}
    {error && <p className="atlas-message error" role="alert">{error}</p>}{notice && <p className="atlas-message" role="status">{notice}</p>}
    <section className="atlas-summary-grid"><article><span>접근 가능한 프로젝트</span><strong>{catalog ? catalog.projects.length : '—'}</strong><small>현재 인증과 권한 기준</small></article><article><span>선택한 블록</span><strong>{atlas.block?.name || '선택 전'}</strong><small>{atlas.project?.name || '공통 프로젝트를 선택하세요'}</small></article><article><span>선택한 버전</span><strong>{atlas.version ? `${atlas.version.net_ver} · ${atlas.version.layout}` : '선택 전'}</strong><small>{atlas.version ? `Revision ${atlas.version.revision} · 실제 수신 ECO ${atlas.version.ecos.filter(Boolean).length}개` : '등록과 실제 결과 수신은 별개입니다'}</small></article></section>
    <section className="atlas-panel"><div className="atlas-section-heading"><div><span className="atlas-eyebrow">COMMON CATALOG</span><h2>프로젝트 · 블록 · 버전</h2><p>하나의 카탈로그를 사용합니다. 분석별로 다시 등록할 필요가 없습니다.</p></div><div className="atlas-actions">{catalog?.can_manage_catalog && <button className="atlas-button" onClick={() => setKind('project')}><FolderPlus size={16} />프로젝트 등록</button>}{catalog?.can_manage_catalog && atlas.project && <button className="atlas-button secondary" onClick={() => setKind('block')}><Plus size={16} />블록 등록</button>}{canWrite && atlas.block && <button className="atlas-button secondary" onClick={() => setKind('version')}><Layers3 size={16} />버전 선언</button>}</div></div>
      {atlas.loading && !catalog ? <p role="status">실제 카탈로그를 불러오는 중입니다…</p> : !catalog ? <div className="atlas-empty">카탈로그를 조회할 수 없습니다. 연결·권한을 확인하고 새로고침하세요. 예시 데이터로 대체하지 않습니다.</div> : !catalog.projects.length ? <div className="atlas-empty">접근 가능한 프로젝트가 없습니다. {catalog.can_manage_catalog ? '프로젝트를 등록해 시작하세요.' : '관리자에게 프로젝트 권한을 요청하세요.'}</div> : <div className="atlas-project-grid">{catalog.projects.map(project => <article key={project.id} className={atlas.scope.projectId === project.id ? 'selected' : ''}><div><Boxes size={21} /><span>{project.can_write ? '읽기 · 쓰기' : '읽기'}</span></div><h3>{project.name}</h3><p>{project.description || '등록된 설명이 없습니다.'}</p><code>{project.id}</code><footer><span>블록 {project.blocks.length} · 버전 {project.blocks.reduce((count, block) => count + block.versions.length, 0)}</span><button className="atlas-button secondary" onClick={() => void atlas.selectScope({ projectId: project.id, blockId: '', versionId: '' }).catch(cause => setError(String(cause)))}>프로젝트 선택<ArrowRight size={14} /></button></footer></article>)}</div>}
      {atlas.project && <div className="atlas-block-list"><h3>{atlas.project.name}의 블록</h3>{atlas.project.blocks.length ? atlas.project.blocks.map(block => <button key={block.id} className={atlas.block?.id === block.id ? 'selected' : ''} onClick={() => void atlas.selectScope({ projectId: atlas.project!.id, blockId: block.id, versionId: '' }).catch(cause => setError(String(cause)))}><strong>{block.name}</strong><code>{block.id}</code><span>버전 {block.versions.length}</span><ArrowRight size={16} /></button>) : <p>아직 블록이 없습니다. 카탈로그 관리자가 등록할 수 있습니다.</p>}</div>}
    </section>
    <section className="atlas-feature-grid"><article className="atlas-panel"><span className="atlas-eyebrow">LOG EVIDENCE</span><h2>로그 검토 · Sanity 검사</h2><p>실제 수신된 검사 결과와 원본 근거를 확인합니다. 버전 선언만으로 결과나 AI 판정이 생기지 않습니다.</p><button className="atlas-button" onClick={() => open(catalog?.local_mode ? '/?page=review' : '/?page=workbench')}>{catalog?.local_mode ? '수신 로그 대장' : '로그 워크스페이스'}<ArrowRight size={16} /></button></article><article className="atlas-panel"><span className="atlas-eyebrow">AREA / POWER</span><h2>프로젝트 비교</h2><p>프로젝트별 기준을 지정하고 명시적으로 선택한 실행의 계층별 Area·Power 차이를 확인합니다.</p>{!catalog?.area_enabled ? <p className="atlas-message">수치 분석 서비스가 비활성화되었거나 설정되지 않았습니다. {catalog?.area_error || ''}</p> : !atlas.project ? <p>공통 프로젝트를 먼저 선택하세요.</p> : <button className="atlas-button" onClick={() => open('/?page=area-power')}>프로젝트 비교 열기<ArrowRight size={16} /></button>}</article></section>
    {catalog?.local_mode && catalog.unbound_area && <LocalBinding key={`${atlas.scope.projectId}:${atlas.scope.blockId}:${atlas.scope.versionId}`} />}
    {kind && <CatalogForm kind={kind} onClose={() => setKind(null)} onCreated={message => { setKind(null); setNotice(message) }} />}
  </main>
}

function CatalogForm({ kind, onClose, onCreated }: { kind: 'project' | 'block' | 'version'; onClose: () => void; onCreated: (message: string) => void }) {
  const atlas = useAtlas()!
  const auth = useDashboardAuth(true)
  const [fields, setFields] = useState({ id: '', name: '', description: '', source_namespace: '', net_ver: '', revision: '', layout: 'PRE', ecos: '', source_pre_id: '', previous_version_id: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [requestId] = useState(() => crypto.randomUUID())
  const field = (key: keyof typeof fields, value: string) => setFields(previous => ({ ...previous, [key]: value }))
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      let path: string; let body: Record<string, unknown>
      if (kind === 'project') { path = '/api/atlas/projects'; body = { request_id: requestId, project_id: fields.id, name: fields.name, description: fields.description } }
      else if (kind === 'block') { path = '/api/atlas/blocks'; body = { request_id: requestId, project_id: atlas.scope.projectId, block_id: fields.id, name: fields.name } }
      else {
        const identity = { project_id: atlas.scope.projectId, block_id: atlas.scope.blockId, source_namespace: fields.source_namespace, net_ver: fields.net_ver, revision: fields.revision, layout: fields.layout }
        const result = await auth.request<{ version_id: string }>('/api/atlas/version-identity', identity)
        path = '/api/atlas/versions'; body = { ...identity, request_id: requestId, version_id: result.version_id, ecos: fields.layout === 'PRE' ? [null] : fields.ecos.split('\n').map(value => value.trim()).filter(Boolean), ...(fields.layout === 'POST' && fields.source_pre_id ? { source_pre_id: fields.source_pre_id } : {}), previous_version_id: fields.previous_version_id || null }
      }
      const result = await auth.request<{ status: string; project_id?: string; block_id?: string; version_id?: string }>(path, body)
      await atlas.reload()
      await atlas.selectScope({ projectId: result.project_id || atlas.scope.projectId, blockId: kind === 'project' ? '' : result.block_id || atlas.scope.blockId, versionId: result.version_id || '' })
      onCreated(`${kind === 'version' ? '버전을 선언했습니다. 실제 보고서·AI 결과는 아직 수신되지 않았습니다.' : '공통 카탈로그에 등록했습니다.'}${result.version_id ? ` ${result.version_id}` : ''}`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}><DialogContent className="atlas-modal" showCloseButton={!busy}><DialogHeader><DialogTitle>{kind === 'project' ? '공통 프로젝트 등록' : kind === 'block' ? '공통 블록 등록' : '네이티브 버전 선언'}</DialogTitle><DialogDescription>{atlas.demo ? '현재 탭의 합성 카탈로그에 저장합니다. 실제 DB나 권한에는 반영하지 않으며 새로고침 시 초기화됩니다.' : '실제 공통 카탈로그에 저장합니다. 기존 분석 데이터와 인증 권한은 유지됩니다.'}</DialogDescription></DialogHeader><form onSubmit={submit}>
    {kind !== 'version' ? <>
      <label>{kind === 'project' ? '프로젝트' : '블록'} ID<input autoFocus required maxLength={100} value={fields.id} onChange={event => field('id', event.target.value)} /><small>수집 도구와 공유하는 명시적 ID입니다. 이름에서 생성하지 않습니다.</small></label>
      <label>표시 이름<input required maxLength={100} value={fields.name} onChange={event => field('name', event.target.value)} /></label>
      {kind === 'project' && <label>설명<textarea value={fields.description} onChange={event => field('description', event.target.value)} /></label>}
    </> : <>
      <p>소속: {atlas.project?.name} / {atlas.block?.name}</p>
      <label>Source namespace<input autoFocus required maxLength={1000} value={fields.source_namespace} onChange={event => field('source_namespace', event.target.value)} /><small>실제 수집 소스의 namespace와 일치해야 합니다.</small></label>
      <div className="atlas-form-grid"><label>NET_VER<input required maxLength={1000} value={fields.net_ver} onChange={event => field('net_ver', event.target.value)} /></label><label>REVISION<input required maxLength={1000} value={fields.revision} onChange={event => field('revision', event.target.value)} /></label><label>Layout<select value={fields.layout} onChange={event => field('layout', event.target.value)}><option>PRE</option><option>POST</option></select></label></div>
      {fields.layout === 'POST' && <>
        <label>선언할 ECO (선택 · 한 줄에 하나)<textarea value={fields.ecos} onChange={event => field('ecos', event.target.value)} /><small>실제 수신 ECO와 별개입니다. 결과를 수신한 것으로 표시하지 않습니다.</small></label>
        <label>Source PRE (선택)<select value={fields.source_pre_id} onChange={event => field('source_pre_id', event.target.value)}><option value="">연결 없음</option>{atlas.block?.versions.filter(version => version.layout === 'PRE' && version.net_ver === fields.net_ver).map(version => <option key={version.id} value={version.id}>{version.net_ver} · {version.revision} · {version.id}</option>)}</select><small>PRE→POST 수집 소스 관계이며 수치 비교 기준이 아닙니다.</small></label>
      </>}
      <label>이전 수치 버전 (선택)<select value={fields.previous_version_id} onChange={event => field('previous_version_id', event.target.value)}><option value="">명시적 이전 버전 없음</option>{atlas.block?.versions.map(version => <option key={version.id} value={version.id}>{version.net_ver} · {version.revision} · {version.layout}</option>)}</select><small>수치 계보만 지정합니다. 이름·정렬·최신 버전으로 추측하지 않습니다.</small></label>
      <p className="atlas-message">Version ID는 위 네이티브 필드로 서버의 실제 identity API에서 계산하여 등록합니다. 임의 이름이나 가상 ID를 만들지 않습니다.</p>
    </>}
    {error && <p role="alert" className="atlas-message error">{error}</p>}<div className="atlas-actions"><button className="atlas-button secondary" type="button" disabled={busy} onClick={onClose}>취소</button><button className="atlas-button" disabled={busy}>{busy ? '저장 중…' : kind === 'version' ? '버전 선언' : '등록'}</button></div>
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
  const label = (row: UnboundArea) => `${String(row.payload?.name || row.name || row.id)} · ${row.id}`
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
    <p>기존 분석 ID와 이력은 보존됩니다. 이름으로 자동 연결하지 않습니다. 위 공통 범위를 선택한 후 실제 소유 관계가 일치하는 기존 ID를 명시적으로 연결하세요. 프로젝트 공통의 과거 버전 ID는 여러 블록에 명시적으로 연결할 수 있습니다.</p>
    {!atlas.project ? <p>공통 프로젝트를 먼저 선택하세요.</p> : <form onSubmit={submit}><div className="atlas-form-grid">
      <label>기존 수치 프로젝트{atlas.project.area_id ? <code>{atlas.project.area_id}</code> : <select required value={project} onChange={event => { setProject(event.target.value); setBlock(''); setVersion('') }}><option value="">실제 ID 선택</option>{rows.projects.map(row => <option key={row.id} value={row.id}>{label(row)}</option>)}</select>}</label>
      {atlas.block && <label>기존 수치 블록{atlas.block.area_id ? <code>{atlas.block.area_id}</code> : <select required value={block} onChange={event => { setBlock(event.target.value); setVersion('') }}><option value="">실제 ID 선택</option>{rows.blocks.filter(row => row.project_id === projectId).map(row => <option key={row.id} value={row.id}>{label(row)}</option>)}</select>}</label>}
      {atlas.version && <label>기존 수치 버전<select required value={version} onChange={event => setVersion(event.target.value)}><option value="">실제 ID 선택</option>{rows.versions.filter(row => row.project_id === projectId && (!row.block_id || row.block_id === blockId)).map(row => <option key={row.id} value={row.id}>{label(row)}</option>)}</select></label>}
      {needsLineage && <label>명시적 수치 계보<select required value={previous} onChange={event => setPrevious(event.target.value)}><option value="">계보 시작 또는 이전 버전 선택</option><option value="null">이 버전에서 계보 시작</option>{atlas.block?.versions.filter(item => item.id !== atlas.version?.id).map(item => <option key={item.id} value={JSON.stringify(item.id)}>{item.net_ver} · {item.revision} · {item.layout}</option>)}</select></label>}
    </div>{error && <p className="atlas-message error" role="alert">{error} 공통 범위를 새로고침한 후 필요한 경우 직접 다시 연결하세요. 자동 재시도하지 않습니다.</p>}{notice && <p className="atlas-message" role="status">{notice}</p>}<button className="atlas-button secondary" disabled={busy || !!atlas.version?.area_id || needsLineage && !previous}>{busy ? '연결 중…' : atlas.version?.area_id ? '이미 연결된 버전' : '선택한 실제 데이터 연결'}</button></form>}
  </details>
}
