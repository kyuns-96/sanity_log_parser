import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { ArrowRight, CheckCircle2, Clock3, FolderKanban, LogOut, RefreshCw, ShieldCheck } from 'lucide-react'
import { Button } from './components/ui/button'
import { Textarea } from './components/ui/textarea'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import type { Session } from './types'
import { accessError, requestStatus, roleLabel, scopeName } from './accessApi'
import type { AccessApi, AccessCatalog, AccessPage, AccessRole, MyAccess } from './accessApi'
import './accessManagement.css'

export type AccessScreenProps = {
  session: Session; api: AccessApi; onNavigate: (page: AccessPage) => void
  onLogout: () => Promise<void>; onRefreshSession: () => Promise<Session>; signingOut: boolean; notice?: string
}

export function AccessLayout({ session, page, title, description, onNavigate, onLogout, signingOut, children }: {
  session: Session; page: AccessPage; title: string; description: string; onNavigate: (page: AccessPage) => void
  onLogout: () => Promise<void>; signingOut: boolean; children: ReactNode
}) {
  return <div className="am-root">
    <a className="skip-link" href="#access-main">본문으로 건너뛰기</a>
    <header className="am-header"><WorkspaceBrand /><div className="am-account"><span><strong>{session.reviewer}</strong><small>SSO 로그인됨</small></span><Button variant="ghost" size="sm" disabled={signingOut} onClick={() => void onLogout()}><LogOut size={16} />로그아웃</Button></div></header>
    <nav className="am-navigation" aria-label="서비스 메뉴">
      <button aria-current={page === 'welcome' ? 'page' : undefined} onClick={() => onNavigate('welcome')}>내 프로젝트·권한</button>
      {session.can_read_business === true && <button onClick={() => onNavigate('review')}>검토 대장<ArrowRight size={14} /></button>}
      {session.is_admin === true && <button aria-current={page === 'admin' ? 'page' : undefined} onClick={() => onNavigate('admin')}>관리자</button>}
    </nav>
    <main id="access-main" className="am-main" tabIndex={-1}><div className="am-heading"><div className="eyebrow">{page === 'admin' ? 'ACCESS ADMINISTRATION' : 'YOUR PROJECTS'}</div><h1>{title}</h1><p>{description}</p></div>{children}</main>
  </div>
}

export default function AccessWelcome(props: AccessScreenProps) {
  const { api, onRefreshSession } = props
  const [catalog, setCatalog] = useState<AccessCatalog>({ projects: [], blocks: [] })
  const [mine, setMine] = useState<MyAccess | null>(null)
  const [role, setRole] = useState<AccessRole>('OWNER')
  const [projectId, setProjectId] = useState('')
  const [blockId, setBlockId] = useState('')
  const [reason, setReason] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([api<AccessCatalog>('/api/access/catalog'), api<MyAccess>('/api/access/me')])
      .then(([nextCatalog, nextMine]) => { if (active) { setCatalog(nextCatalog); setMine(nextMine) } })
      .catch(error => { if (active) setError(accessError(error)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api])

  async function reload() {
    setBusy(true); setError('')
    try {
      const [nextCatalog, nextMine] = await Promise.all([api<AccessCatalog>('/api/access/catalog'), api<MyAccess>('/api/access/me'), onRefreshSession()])
      setCatalog(nextCatalog); setMine(nextMine)
    } catch (error) { setError(accessError(error)) }
    finally { setBusy(false) }
  }
  const edited = () => { setRequestId(crypto.randomUUID()); setNotice('') }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!projectId || role === 'OWNER' && !blockId || !reason.trim()) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api('/api/access/requests', { request_id: requestId, role, project_id: projectId, block_id: role === 'OWNER' ? blockId : null, reason: reason.trim() })
      setMine(await api<MyAccess>('/api/access/me'))
      setReason(''); setRequestId(crypto.randomUUID()); setNotice('권한을 요청했습니다. 아래에서 승인 상태를 확인할 수 있습니다.')
    } catch (error) { setError(accessError(error)) }
    finally { setBusy(false) }
  }
  const availableBlocks = catalog.blocks.filter(block => block.project_id === projectId)
  return <AccessLayout {...props} page="welcome" title="내 프로젝트·권한" description="담당할 프로젝트와 블록을 선택하세요. 승인 후 검사 결과를 확인할 수 있습니다.">
    {(error || props.notice) && <p className="am-error" role="alert">{error || props.notice}</p>}
    {notice && <p className="am-notice" role="status"><CheckCircle2 size={17} />{notice}</p>}
    <div className="am-section-heading"><span className="am-login-status"><ShieldCheck size={16} />회사 계정 연결 완료</span><Button variant="outline" size="sm" onClick={() => void reload()} disabled={busy || loading}><RefreshCw size={15} />권한 상태 새로고침</Button></div>
    {loading ? <p role="status" className="am-loading">프로젝트와 내 권한을 확인하고 있습니다.</p> : <div className="am-columns">
      <section className="am-panel"><div className="am-panel-heading"><h2>권한 요청</h2><span>1. 범위 선택 → 2. 관리자 승인</span></div>
        {!catalog.projects.length ? <div className="am-empty"><FolderKanban size={28} /><h3>아직 등록된 프로젝트가 없습니다</h3><p>관리자가 프로젝트와 블록을 등록하면 권한을 요청할 수 있습니다.</p>{props.session.is_admin && <Button onClick={() => props.onNavigate('admin')}>프로젝트 등록하기<ArrowRight size={15} /></Button>}</div> : <form className="am-form" onSubmit={submit}>
          <fieldset className="am-role-picker"><legend>어떤 작업을 담당하나요?</legend>{(['OWNER', 'TOP'] as const).map(value => <label key={value} className={role === value ? 'is-selected' : ''}><input type="radio" name="access-role" value={value} checked={role === value} disabled={busy} onChange={() => { setRole(value); edited() }} /><span><strong>{value === 'OWNER' ? '블록 담당자 · OWNER' : '프로젝트 조회 · TOP'}</strong><small>{value === 'OWNER' ? '담당 블록의 결과 조회·검토·수정' : '프로젝트의 모든 블록 조회'}</small></span></label>)}</fieldset>
          <label>프로젝트<select aria-label="프로젝트" value={projectId} required disabled={busy} onChange={event => { setProjectId(event.target.value); setBlockId(''); edited() }}><option value="">프로젝트를 선택하세요</option>{catalog.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
          {role === 'OWNER' && <label>블록<select aria-label="블록" value={blockId} required disabled={busy || !projectId} onChange={event => { setBlockId(event.target.value); edited() }}><option value="">블록을 선택하세요</option>{availableBlocks.map(block => <option key={block.id} value={block.id}>{block.name}</option>)}</select>{projectId && !availableBlocks.length && <small>등록된 블록이 없습니다. 관리자에게 블록 등록을 요청해 주세요.</small>}</label>}
          <label>요청 사유<Textarea aria-label="요청 사유" value={reason} onChange={event => { setReason(event.target.value); edited() }} rows={3} maxLength={1000} required disabled={busy} placeholder="담당 업무와 필요한 권한을 적어 주세요." /></label>
          <Button type="submit" disabled={busy || !projectId || role === 'OWNER' && !blockId || !reason.trim()}>{busy ? '처리 중…' : '권한 요청 보내기'}<ArrowRight size={15} /></Button>
        </form>}
      </section>
      <div className="am-stack"><section className="am-panel"><div className="am-panel-heading"><h2>내 담당 범위</h2><span>{mine?.grants.length || 0}개</span></div>{mine?.grants.length ? <><ul className="am-list">{mine.grants.map(grant => <li key={grant.grant_id}><div><strong>{scopeName(catalog, grant)}</strong><span>{roleLabel(grant.role)}</span></div><ShieldCheck size={18} /></li>)}</ul><div className="am-panel-footer"><Button onClick={() => { void onRefreshSession().then(() => props.onNavigate('review')).catch(error => setError(accessError(error))) }}>검토 대장 열기<ArrowRight size={15} /></Button></div></> : <div className="am-empty am-empty-small"><ShieldCheck size={25} /><h3>아직 승인된 권한이 없습니다</h3><p>권한 요청에서 담당 범위를 선택해 주세요.</p></div>}</section>
        <section className="am-panel"><div className="am-panel-heading"><h2>내 요청 현황</h2><span>{mine?.requests.length || 0}건</span></div>{mine?.requests.length ? <ul className="am-list am-request-list">{mine.requests.map(request => <li key={request.request_id}><div><div className="am-request-title"><strong>{scopeName(catalog, request)}</strong><span className={`am-status am-status-${request.status.toLowerCase()}`}>{requestStatus[request.status]}</span></div><span>{roleLabel(request.role)}</span><p>{request.reason}</p>{request.decision_reason && <p className="am-decision-note">관리자 답변: {request.decision_reason}</p>}</div></li>)}</ul> : <div className="am-empty am-empty-small"><Clock3 size={24} /><p>보낸 권한 요청이 없습니다.</p></div>}</section>
      </div>
    </div>}
  </AccessLayout>
}
