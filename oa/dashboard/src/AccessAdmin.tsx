import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Check, FolderKanban, Plus, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { Input } from './studio/components/ui/input'
import { Textarea } from './studio/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { AccessLayout } from './AccessWelcome'
import type { AccessScreenProps } from './AccessWelcome'
import { accessError, requestStatus, roleLabel, scopeName } from './accessApi'
import type { AccessCatalog, AccessGrant, AccessRequest, AdminAccess } from './accessApi'

type Decision = { kind: 'request'; value: AccessRequest; decision: 'APPROVE' | 'REJECT' } | { kind: 'revoke'; value: AccessGrant }
const idPattern = '[A-Za-z0-9][A-Za-z0-9_.-]{0,99}'

export default function AccessAdmin(props: AccessScreenProps) {
  const { api } = props
  const [catalog, setCatalog] = useState<AccessCatalog>({ projects: [], blocks: [] })
  const [access, setAccess] = useState<AdminAccess | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [projectId, setProjectId] = useState('')
  const [projectName, setProjectName] = useState('')
  const [description, setDescription] = useState('')
  const [blockProject, setBlockProject] = useState('')
  const [blockId, setBlockId] = useState('')
  const [blockName, setBlockName] = useState('')
  const [projectRequest, setProjectRequest] = useState(() => crypto.randomUUID())
  const [blockRequest, setBlockRequest] = useState(() => crypto.randomUUID())
  const [decision, setDecision] = useState<Decision | null>(null)
  const [reason, setReason] = useState('')
  const [commandId, setCommandId] = useState(() => crypto.randomUUID())

  useEffect(() => {
    let active = true
    Promise.all([api<AccessCatalog>('/api/access/catalog'), api<AdminAccess>('/api/admin/access')])
      .then(([nextCatalog, nextAccess]) => { if (active) { setCatalog(nextCatalog); setAccess(nextAccess) } })
      .catch(error => { if (active) setError(accessError(error)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api])

  async function refresh() {
    const [nextCatalog, nextAccess] = await Promise.all([api<AccessCatalog>('/api/access/catalog'), api<AdminAccess>('/api/admin/access'), props.onRefreshSession()])
    setCatalog(nextCatalog); setAccess(nextAccess)
  }
  async function command(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() }
    catch (error) { setError(accessError(error)) }
    finally { setBusy(false) }
  }
  function createProject(event: FormEvent) {
    event.preventDefault()
    void command(async () => {
      await api('/api/admin/projects', { request_id: projectRequest, project_id: projectId, name: projectName.trim(), description: description.trim() })
      await refresh(); setProjectId(''); setProjectName(''); setDescription(''); setProjectRequest(crypto.randomUUID()); setNotice('프로젝트를 등록했습니다. 이어서 블록을 추가해 주세요.')
    })
  }
  function createBlock(event: FormEvent) {
    event.preventDefault()
    void command(async () => {
      await api(`/api/admin/projects/${encodeURIComponent(blockProject)}/blocks`, { request_id: blockRequest, block_id: blockId, name: blockName.trim() })
      await refresh(); setBlockId(''); setBlockName(''); setBlockRequest(crypto.randomUUID()); setNotice('블록을 등록했습니다.')
    })
  }
  function openDecision(value: Decision) { setDecision(value); setReason(''); setCommandId(crypto.randomUUID()); setError('') }
  function decide(event: FormEvent) {
    event.preventDefault()
    if (!decision || !access || !reason.trim()) return
    void command(async () => {
      const expected_revision = access.revision
      if (decision.kind === 'request') await api(`/api/admin/requests/${encodeURIComponent(decision.value.request_id)}/decision`, { command_id: commandId, decision: decision.decision, reason: reason.trim(), expected_revision })
      else await api(`/api/admin/grants/${encodeURIComponent(decision.value.grant_id)}/revoke`, { command_id: commandId, reason: reason.trim(), expected_revision })
      setDecision(null); await refresh(); setNotice('권한 변경을 저장했습니다.')
    })
  }
  const userName = (userId: string) => access?.users.find(user => user.user_id === userId)?.display_name || userId
  const pending = access?.requests.filter(request => request.status === 'PENDING') || []
  const completed = access?.requests.filter(request => request.status !== 'PENDING') || []
  return <AccessLayout {...props} page="admin" title="프로젝트·권한 관리" description="프로젝트와 블록을 등록하고, 사용자의 담당 범위를 승인하거나 회수합니다.">
    <p className="am-admin-note"><ShieldCheck size={17} />관리자는 등록과 권한을 관리합니다. 검사 결과를 보려면 본인의 TOP 또는 OWNER 권한도 승인해 주세요.</p>
    {error && !decision && <p className="am-error" role="alert">{error}</p>}{notice && <p className="am-notice" role="status">{notice}</p>}
    <div className="am-section-heading"><h2>승인 대기 <span>{pending.length}건</span></h2><Button variant="outline" size="sm" disabled={busy || loading} onClick={() => void command(refresh)}><RefreshCw size={15} />관리 상태 새로고침</Button></div>
    {loading ? <p role="status" className="am-loading">프로젝트와 권한 요청을 확인하고 있습니다.</p> : <>
      <section className="am-panel" aria-label="승인 대기 요청">{pending.length ? <ul className="am-list am-admin-requests">{pending.map(request => <li key={request.request_id}><div><strong>{userName(request.user_id)}{request.user_id === props.session.user_id && <Badge variant="secondary" className="am-self">내 요청</Badge>}</strong><span>{scopeName(catalog, request)} · {roleLabel(request.role)}</span><p>{request.reason}</p></div><div className="am-row-actions"><Button variant="outline" size="sm" disabled={busy} onClick={() => openDecision({ kind: 'request', value: request, decision: 'REJECT' })}><X size={14} />거절</Button><Button size="sm" disabled={busy} onClick={() => openDecision({ kind: 'request', value: request, decision: 'APPROVE' })}><Check size={14} />승인</Button></div></li>)}</ul> : <div className="am-empty am-empty-small"><Check size={25} /><p>승인을 기다리는 요청이 없습니다.</p></div>}</section>
      <div className="am-columns am-admin-columns"><section className="am-panel"><div className="am-panel-heading"><div><h2>프로젝트 등록</h2><p className="studio-section-description">프로젝트를 만든 뒤 담당 블록을 추가하세요.</p></div><FolderKanban size={18} /></div><form className="am-form" onSubmit={createProject}>
        <label>프로젝트 ID<Input aria-label="프로젝트 ID" value={projectId} pattern={idPattern} required maxLength={100} disabled={busy} placeholder="예: project-alpha" onChange={event => { setProjectId(event.target.value); setProjectRequest(crypto.randomUUID()) }} /><small>등록 후 바뀌지 않는 고유 ID입니다. 영문·숫자·마침표·밑줄·하이픈을 사용하세요.</small></label>
        <label>프로젝트 이름<Input aria-label="프로젝트 이름" value={projectName} required maxLength={100} disabled={busy} onChange={event => { setProjectName(event.target.value); setProjectRequest(crypto.randomUUID()) }} /></label>
        <label>설명<Textarea aria-label="설명" value={description} maxLength={1000} disabled={busy} rows={2} onChange={event => { setDescription(event.target.value); setProjectRequest(crypto.randomUUID()) }} /></label><Button type="submit" disabled={busy || !projectId || !projectName.trim()}><Plus size={15} />프로젝트 등록</Button>
      </form></section>
      <section className="am-panel"><div className="am-panel-heading"><div><h2>블록 등록</h2><p className="studio-section-description">선택한 프로젝트에 검토 단위를 등록하세요.</p></div><Plus size={18} /></div><form className="am-form" onSubmit={createBlock}>
        <label>프로젝트<select className="studio-native-select" aria-label="프로젝트" required value={blockProject} disabled={busy || !catalog.projects.length} onChange={event => { setBlockProject(event.target.value); setBlockRequest(crypto.randomUUID()) }}><option value="">프로젝트를 선택하세요</option>{catalog.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label>블록 ID<Input aria-label="블록 ID" value={blockId} pattern={idPattern} required maxLength={100} disabled={busy} placeholder="예: alpha-cpu" onChange={event => { setBlockId(event.target.value); setBlockRequest(crypto.randomUUID()) }} /></label>
        <label>블록 이름<Input aria-label="블록 이름" value={blockName} required maxLength={100} disabled={busy} onChange={event => { setBlockName(event.target.value); setBlockRequest(crypto.randomUUID()) }} /></label><Button type="submit" disabled={busy || !blockProject || !blockId || !blockName.trim()}><Plus size={15} />블록 등록</Button>
      </form></section></div>
      <section className="am-panel"><div className="am-panel-heading"><h2>등록된 프로젝트·블록</h2><span>{catalog.projects.length}개 프로젝트</span></div>{catalog.projects.length ? <ul className="am-list">{catalog.projects.map(project => <li key={project.id}><div><strong>{project.name}<code>{project.id}</code></strong>{project.description && <p>{project.description}</p>}<div className="am-block-tags">{catalog.blocks.filter(block => block.project_id === project.id).map(block => <span key={block.id}>{block.name}<code>{block.id}</code></span>)}{!catalog.blocks.some(block => block.project_id === project.id) && <span>아직 등록된 블록이 없습니다.</span>}</div></div></li>)}</ul> : <div className="am-empty am-empty-small"><p>등록된 프로젝트가 없습니다.</p></div>}</section>
      <section className="am-panel"><div className="am-panel-heading"><h2>부여된 권한</h2><span>{access?.grants.length || 0}개</span></div>{access?.grants.length ? <ul className="am-list">{access.grants.map(grant => <li key={grant.grant_id}><div><strong>{userName(grant.user_id)}</strong><span>{scopeName(catalog, grant)} · {roleLabel(grant.role)}</span></div><Button size="sm" variant="outline" disabled={busy} onClick={() => openDecision({ kind: 'revoke', value: grant })}>권한 회수</Button></li>)}</ul> : <div className="am-empty am-empty-small"><p>부여된 권한이 없습니다.</p></div>}</section>
      {completed.length > 0 && <section className="am-panel"><div className="am-panel-heading"><h2>처리한 요청</h2><span>{completed.length}건</span></div><ul className="am-list">{completed.map(request => <li key={request.request_id}><div><strong>{userName(request.user_id)}</strong><span>{scopeName(catalog, request)} · {roleLabel(request.role)}</span>{request.decision_reason && <p>{request.decision_reason}</p>}</div><Badge variant="outline" className={`am-status am-status-${request.status.toLowerCase()}`}>{requestStatus[request.status]}</Badge></li>)}</ul></section>}
    </>}
    <Dialog open={!!decision} onOpenChange={open => { if (!open && !busy) setDecision(null) }}><DialogContent><DialogHeader><DialogTitle>{decision?.kind === 'revoke' ? '권한 회수' : decision?.decision === 'APPROVE' ? '권한 요청 승인' : '권한 요청 거절'}</DialogTitle><DialogDescription>{decision && `${userName(decision.value.user_id)} · ${scopeName(catalog, decision.value)} · ${roleLabel(decision.value.role)}`}</DialogDescription></DialogHeader><form className="am-form am-dialog-form" onSubmit={decide}>{error && <p className="am-error" role="alert">{error}</p>}<label>처리 사유<Textarea aria-label="처리 사유" value={reason} required rows={3} maxLength={1000} disabled={busy} onChange={event => { setReason(event.target.value); setCommandId(crypto.randomUUID()) }} /></label><DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={() => setDecision(null)}>취소</Button><Button type="submit" disabled={busy || !reason.trim()}>{busy ? '저장 중…' : decision?.kind === 'revoke' ? '회수 확정' : decision?.decision === 'APPROVE' ? '승인 확정' : '거절 확정'}</Button></DialogFooter></form></DialogContent></Dialog>
  </AccessLayout>
}
