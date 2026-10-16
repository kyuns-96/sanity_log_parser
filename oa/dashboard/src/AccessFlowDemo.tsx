import { useState } from 'react'
import { ArrowLeft, ArrowRight, ArrowUpRight, Boxes, Check, CheckCircle2, ChevronRight, CircleHelp, ClipboardList, FolderKanban, History, LockKeyhole, Plus, RotateCcw, ShieldCheck, Sparkles, Trash2, UserRound, Users, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import { Input } from './components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { canManageProject, checkLabels, checkTemplates, createDemoProject, demoUsers, initialAccessState, newProjectDraft, person, roleLabel, saveDemoGrants, validateProject, visibleBlocks, visibleProjects } from './accessDemoModel'
import type { AccessState, DemoBlock, DemoCheck, DemoProject, DemoUser, ProjectDraft, ScopeGrant } from './accessDemoModel'
import './accessFlow.css'

type View = 'projects' | 'members' | 'history'
const toggle = (values: string[], value: string) => values.includes(value) ? values.filter(item => item !== value) : [...values, value]
const names = (ids: string[]) => ids.map(id => person(id).name).join(', ') || '미배정'
function Avatar({ user, small = false }: { user: DemoUser; small?: boolean }) {
  return <span className={`af-avatar af-avatar-${user.id} ${small ? 'af-avatar-small' : ''}`}>{user.name.slice(0, 1)}</span>
}
function Result({ result }: { result: DemoCheck['result'] }) {
  return <span className={`af-result af-result-${result.toLowerCase()}`}><span />{checkLabels[result]}</span>
}

export default function AccessFlowDemo() {
  const [state, setState] = useState(initialAccessState)
  const [userId, setUserId] = useState('u-admin')
  const [view, setView] = useState<View>('projects')
  const [selectedId, setSelectedId] = useState('p-atlas')
  const [creating, setCreating] = useState(false)
  const [managing, setManaging] = useState<string | null>(null)
  const [inspect, setInspect] = useState<{ projectId: string; blockId: string; checkId: string } | null>(null)
  const [notice, setNotice] = useState('')
  const [showHelp, setShowHelp] = useState(true)
  const user = person(userId)
  const projects = visibleProjects(state, user)
  const manageable = projects.filter(project => canManageProject(state, user, project.id))
  const available = view === 'members' ? manageable : projects
  const project = available.find(project => project.id === selectedId) || available[0]
  const blocks = project ? visibleBlocks(state, user, project) : []
  const allVisibleBlocks = projects.flatMap(project => visibleBlocks(state, user, project))
  const checks = allVisibleBlocks.flatMap(block => block.checks)
  const grants = project ? state.grants.filter(grant => grant.projectId === project.id) : []
  const isTop = grants.some(grant => grant.userId === user.id && grant.role === 'top')
  const managedProject = state.projects.find(project => project.id === managing && canManageProject(state, user, project.id))
  const inspectedProject = projects.find(project => project.id === inspect?.projectId)
  const inspectedBlock = inspectedProject && visibleBlocks(state, user, inspectedProject).find(block => block.id === inspect?.blockId)
  const inspectedCheck = inspectedBlock?.checks.find(check => check.id === inspect?.checkId)
  const history = state.events.filter(event => canManageProject(state, user, event.projectId))
  const label = roleLabel(state, user)

  function switchUser(id: string) {
    setUserId(id); setView('projects'); setCreating(false); setManaging(null); setInspect(null); setNotice('')
    const next = visibleProjects(state, person(id))
    setSelectedId(previous => next.some(project => project.id === previous) ? previous : next[0]?.id || '')
  }
  function reset() {
    setState(initialAccessState()); setUserId('u-admin'); setView('projects'); setSelectedId('p-atlas')
    setCreating(false); setManaging(null); setInspect(null); setNotice('데모를 처음 상태로 되돌렸습니다.')
  }
  function create(draft: ProjectDraft) {
    const next = createDemoProject(state, user, draft)
    setState(next); setSelectedId(next.projects.at(-1)!.id); setCreating(false); setView('projects')
    setNotice(`${draft.name.trim()} 프로젝트가 생성되었습니다. 위에서 담당자 계정으로 전환해 배정 결과를 확인해 보세요.`)
  }
  function save(grants: ScopeGrant[]) {
    if (!managedProject) throw new Error('프로젝트 관리 권한이 없습니다.')
    const next = saveDemoGrants(state, user, managedProject.id, grants)
    setState(next); setManaging(null)
    if (!user.admin && !next.grants.some(grant => grant.userId === user.id && grant.role === 'manager')) setView('projects')
    setNotice('담당 범위를 저장했습니다. 위에서 해당 사용자로 전환하면 변경된 화면을 확인할 수 있습니다.')
  }

  return <div className="af-root"><a className="skip-link" href="#workspace-main">본문으로 건너뛰기</a>
    <section className="af-demo-bar" aria-label="데모 체험 도구">
      <div className="af-demo-intro"><span className="af-demo-tag"><Sparkles size={13} />FLOW DEMO</span><strong>계정을 바꿔보세요</strong><span>같은 데이터, 서로 다른 담당 범위</span></div>
      <div className="af-personas" role="group" aria-label="데모 접속 사용자">
        {demoUsers.map(account => <button key={account.id} className={`af-persona ${account.id === userId ? 'is-active' : ''}`} aria-pressed={account.id === userId} onClick={() => switchUser(account.id)} aria-label={`${account.name} 계정으로 전환`}><Avatar user={account} small /><span><strong>{account.name}</strong><small>{roleLabel(state, account)}</small></span>{account.id === userId && <Check size={14} />}</button>)}
      </div>
      <button className="af-reset" onClick={reset} aria-label="데모 초기화"><RotateCcw size={15} /><span>초기화</span></button>
    </section>
    <div className="af-layout">
      <aside className="af-sidebar">
        <WorkspaceBrand href="/?demo=1&flow=access" />
        <div className="af-workspace-label"><span>OA</span><div>검토 워크스페이스<small>PROJECT WORKSPACE</small></div></div>
        <div className="af-nav-label">WORKSPACE</div>
        <nav aria-label="워크스페이스 메뉴">
          <button className={view === 'projects' ? 'is-active' : ''} aria-current={view === 'projects' ? 'page' : undefined} onClick={() => setView('projects')}><FolderKanban size={17} />{user.admin ? '프로젝트 현황' : '내 프로젝트'}<span>{projects.length}</span></button>
          {manageable.length > 0 && <><div className="af-nav-label af-management-label">MANAGEMENT</div><button className={view === 'members' ? 'is-active' : ''} aria-current={view === 'members' ? 'page' : undefined} onClick={() => setView('members')}><Users size={17} />담당자 배정</button><button className={view === 'history' ? 'is-active' : ''} aria-current={view === 'history' ? 'page' : undefined} onClick={() => setView('history')}><History size={17} />변경 이력</button></>}
        </nav>
        <div className="af-sidebar-note"><ShieldCheck size={21} /><strong>역할에 맞는 작업 공간</strong><p>프로젝트와 블록의 담당 범위에 따라 화면이 달라집니다.</p><a href="/?demo=1&flow=versions">PRE · POST · ECO · PoR <ArrowUpRight size={13} /></a><a href="/?demo=1&dataset=gca">기존 GCA 데모 <ArrowUpRight size={13} /></a></div>
        <div className="af-current-user"><Avatar user={user} /><div><strong>{user.name}</strong><span>{label}</span></div></div>
      </aside>
      <div className="af-main-shell">
        <header className="af-topbar"><div>워크스페이스 <ChevronRight size={13} /><strong>{view === 'members' ? '담당자 배정' : view === 'history' ? '변경 이력' : '프로젝트'}</strong></div><span><i />데모 데이터 · 새로고침 시 초기화</span></header>
        <main className="af-main" id="workspace-main" tabIndex={-1}>
          <div className="af-page-heading"><div><div className="af-eyebrow">PROJECT & PEOPLE</div><h1>{view === 'members' ? '담당자를 연결하세요' : view === 'history' ? '배정의 변화를 한눈에' : user.admin ? '프로젝트, 사람, 그리고 담당 범위' : `${user.name}님의 프로젝트`}</h1><p>{view === 'members' ? '계정을 선택하고, 그 사람이 맡을 프로젝트와 블록을 지정합니다.' : view === 'history' ? '프로젝트 생성과 담당 범위의 변경을 기록합니다.' : user.admin ? '프로젝트를 만들고 담당자를 연결하면, 각자의 작업 공간이 완성됩니다.' : '배정받은 범위의 블록과 sanity 결과를 확인하세요.'}</p></div><div className="af-heading-actions"><Button variant="outline" size="sm" aria-expanded={showHelp} onClick={() => setShowHelp(!showHelp)}><CircleHelp />흐름 안내</Button>{user.admin && <Button onClick={() => setCreating(true)}><Plus />프로젝트 만들기</Button>}</div></div>

          {showHelp && <section className="af-guide" aria-label="프로젝트 등록과 배정 흐름"><div className="af-guide-heading"><span><Sparkles size={15} />이렇게 사용합니다</span><button onClick={() => setShowHelp(false)} aria-label="흐름 안내 닫기"><X size={15} /></button></div><div className="af-steps">{[
            ['01', '관리자가 프로젝트 등록', '프로젝트 이름과 TOP 담당자를 선택합니다.'],
            ['02', '블록별 담당자 연결', '사용자 계정에 블록과 sanity item을 배정합니다.'],
            ['03', '각자의 대시보드로', 'TOP은 프로젝트 전체, 담당자는 자신의 블록을 봅니다.'],
          ].map(([number, title, description], index) => <div className="af-step" key={number}><span>{number}</span><div><strong>{title}</strong><p>{description}</p></div>{index < 2 && <ArrowRight size={16} />}</div>)}</div><p className="af-guide-foot">직접 해보기: 정민수로 프로젝트를 만든 뒤, 위의 이영희와 김철수 계정을 차례로 눌러보세요.</p></section>}

          {notice && <div className="af-notice" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="안내 닫기"><X size={15} /></button></div>}

          <section className="af-identity" aria-label="현재 접속 계정과 권한"><Avatar user={user} /><div><strong>{user.name}<span>{label}</span></strong><p>{user.team} · {user.employee}</p></div><div className="af-identity-scope"><LockKeyhole size={15} /><span>{user.admin ? '전체 프로젝트 조회 · 생성 · 담당자 관리' : label === '미배정 사용자' ? '로그인 완료 · 담당 범위 배정 대기' : manageable.length ? '배정된 범위 조회 · 허용된 프로젝트 담당자 관리' : '배정된 범위 조회 · 프로젝트 관리 권한 없음'}</span></div></section>

          {view === 'history' ? <section className="af-panel af-history-panel"><div className="af-panel-heading"><h2>프로젝트 변경 이력</h2><span>{history.length}건</span></div>{history.length ? <ol className="af-history">{history.map(entry => <li key={entry.id}><span className="af-history-icon"><History size={16} /></span><div><strong>{entry.text}</strong><p>{entry.actor} · {new Date(entry.at).toLocaleTimeString('ko-KR')}</p></div></li>)}</ol> : <div className="af-empty"><History size={30} /><h2>아직 변경 이력이 없습니다</h2><p>프로젝트를 만들거나 담당자를 변경하면 여기에 기록됩니다.</p></div>}</section> : <>
            {view === 'projects' && projects.length > 0 && <div className="af-stats"><div><span><FolderKanban size={16} />{user.admin ? '전체 프로젝트' : '내 프로젝트'}</span><strong>{projects.length}<small>개</small></strong></div><div><span><Boxes size={16} />조회 가능한 블록</span><strong>{allVisibleBlocks.length}<small>개</small></strong></div><div><span><ClipboardList size={16} />확인할 FAIL</span><strong className="af-red">{checks.filter(check => check.result === 'FAIL').length}<small>개 항목</small></strong></div><div><span><History size={16} />보고서 미수신</span><strong>{checks.filter(check => check.result === 'MISSING').length}<small>개 항목</small></strong></div></div>}

            {!projects.length ? <section className="af-panel af-empty af-unassigned"><span className="af-empty-symbol"><UserRound size={32} /></span><span className="af-pill">계정 등록 완료</span><h2>아직 배정된 프로젝트가 없습니다</h2><p>관리자가 프로젝트나 블록을 배정하면<br />이곳에 나의 작업 공간이 나타납니다.</p><div className="af-waiting-steps"><span><CheckCircle2 size={16} />SSO 로그인</span><ChevronRight size={14} /><span><CheckCircle2 size={16} />계정 연결</span><ChevronRight size={14} /><span>담당 범위 배정 대기</span></div><div className="af-demo-hint"><Sparkles size={15} />데모에서 확인하기: 정민수 → 담당자 배정 → 최지우에게 블록 선택 → 다시 최지우로 전환</div></section> : <div className="af-content-grid">
              <section className="af-panel af-project-list" aria-label="프로젝트 목록"><div className="af-panel-heading"><h2>{view === 'members' ? '관리할 프로젝트' : user.admin ? '프로젝트 목록' : '내 프로젝트'}</h2><span>{available.length}</span></div>{available.map(item => { const count = visibleBlocks(state, user, item).length; return <button key={item.id} className={`af-project-card ${project?.id === item.id ? 'is-active' : ''}`} onClick={() => setSelectedId(item.id)} aria-pressed={project?.id === item.id} aria-label={`${item.name} 프로젝트 선택`}><div><span className={`af-project-mark ${item.key === 'boreal' ? 'af-mark-violet' : ''}`}>{item.name.slice(0, 1)}</span><div><strong>{item.name}</strong><small>{item.key}</small></div><ChevronRight size={16} /></div><p>{item.description || '등록된 프로젝트'}</p><footer><span>{count}개 블록</span><span>{state.grants.some(grant => grant.projectId === item.id && grant.userId === user.id && grant.scope === 'project') || user.admin ? '프로젝트 전체' : '담당 블록'}</span></footer></button> })}<div className="af-project-list-foot"><LockKeyhole size={13} />내 권한에 포함된 프로젝트만 표시됩니다.</div></section>

              {project && <section className="af-panel af-project-detail" aria-label={`${project.name} 프로젝트 상세`}><div className="af-detail-heading"><div><div className="af-eyebrow">{project.key.toUpperCase()} / PROJECT</div><h2>{project.name}<span className="af-pill">{user.admin ? '관리자' : isTop ? 'TOP · 전체 블록' : canManageProject(state, user, project.id) ? '프로젝트 관리' : '블록 담당'}</span></h2><p>TOP 담당자 <strong>{names(grants.filter(grant => grant.role === 'top').map(grant => grant.userId))}</strong></p></div>{canManageProject(state, user, project.id) && <Button variant="outline" size="sm" onClick={() => setManaging(project.id)}><Users />담당자 설정</Button>}</div>
                {view === 'members' ? <div className="af-member-summary"><div className="af-section-caption"><span>사용자와 담당 범위</span><span>{new Set(grants.map(grant => grant.userId)).size}명</span></div>{demoUsers.filter(account => grants.some(grant => grant.userId === account.id)).map(account => <div className="af-member-row" key={account.id}><Avatar user={account} small /><div><strong>{account.name}</strong><small>{account.team} · {account.employee}</small></div><div className="af-scope-chips">{grants.filter(grant => grant.userId === account.id).map(grant => <span className={grant.role === 'top' ? 'af-chip-top' : ''} key={`${grant.role}-${grant.blockId || ''}`}>{grant.role === 'top' ? 'TOP · 전체 블록' : grant.role === 'manager' ? '담당자 관리' : project.blocks.find(block => block.id === grant.blockId)?.name}</span>)}</div></div>)}<div className="af-members-help"><ShieldCheck size={18} /><p><strong>전체 조회와 관리 권한은 별개입니다.</strong><br />TOP에 배정하면 모든 블록을 볼 수 있습니다. 담당자를 변경하려면 프로젝트 관리 권한도 별도로 지정합니다.</p></div></div> : <><div className="af-section-caption"><span>블록별 sanity 현황</span><span>조회 범위 {blocks.length}개 블록</span></div><div className="af-blocks">{blocks.map(block => <BlockCard key={block.id} block={block} owners={grants.filter(grant => grant.scope === 'block' && grant.blockId === block.id).map(grant => grant.userId)} onInspect={check => setInspect({ projectId: project.id, blockId: block.id, checkId: check.id })} />)}</div><div className="af-detail-foot"><ShieldCheck size={14} />{user.admin ? '관리자는 모든 프로젝트의 등록 정보와 담당 범위를 관리합니다.' : isTop ? 'TOP 권한은 이 프로젝트의 모든 블록과 sanity item에 적용됩니다.' : '현재 사용자에게 배정된 범위의 블록과 sanity item입니다.'}</div></>}
              </section>}
            </div>}
          </>}
          <footer className="af-footer"><span>SANITY <b>/</b> WORKFLOW PREVIEW</span><span>가상 계정으로 동작하는 화면 데모입니다. 실제 SSO·DB·서버 권한 검증은 연결되지 않았습니다.</span></footer>
        </main>
      </div>
    </div>
    {creating && user.admin && <CreateProjectDialog state={state} onClose={() => setCreating(false)} onCreate={create} />}
    {managedProject && <ManageGrantsDialog project={managedProject} current={state.grants.filter(grant => grant.projectId === managedProject.id)} onClose={() => setManaging(null)} onSave={save} />}
    <Dialog open={!!inspectedCheck} onOpenChange={open => { if (!open) setInspect(null) }}><DialogContent className="af-dialog af-inspect-dialog"><DialogHeader><DialogTitle>{inspectedBlock?.name} / {inspectedCheck?.name}</DialogTitle><DialogDescription>{inspectedProject?.name} 프로젝트 · 예시 검사 결과</DialogDescription></DialogHeader>{inspectedCheck && <><Result result={inspectedCheck.result} /><div className="af-report-preview"><ClipboardList size={23} /><h3>{inspectedCheck.result === 'MISSING' ? '보고서 수신을 기다리고 있습니다' : inspectedCheck.result === 'FAIL' ? '확인이 필요한 항목이 있습니다' : '예시 검사를 통과했습니다'}</h3><p>{inspectedCheck.result === 'MISSING' ? '프로젝트 등록으로 생성된 검사 목록입니다. 실제 보고서가 연결되면 결과가 표시됩니다.' : '담당 범위 안의 sanity item을 열어보는 화면 예시입니다. 실제 도구에서 실행한 결과가 아닙니다.'}</p></div><div className="af-linked-account"><ShieldCheck size={17} /><span>{user.name}님의 {inspectedProject?.name} / {inspectedBlock?.name} 조회 범위에 포함됩니다.</span></div></>}<DialogFooter><Button onClick={() => setInspect(null)}>확인</Button></DialogFooter></DialogContent></Dialog>
  </div>
}

function BlockCard({ block, owners, onInspect }: { block: DemoBlock; owners: string[]; onInspect: (check: DemoCheck) => void }) {
  const passed = block.checks.filter(check => check.result === 'PASS').length
  return <article className="af-block-card" aria-label={`${block.name} 블록`}><header><span className="af-block-icon"><Boxes size={19} /></span><div><h3>{block.name}</h3><p>담당 {names(owners)}</p></div><span>{passed}/{block.checks.length} PASS</span></header><div className="af-block-progress" aria-hidden="true">{block.checks.map(check => <span key={check.id} className={`af-segment-${check.result.toLowerCase()}`} />)}</div><div className="af-checks">{block.checks.map(check => <button key={check.id} onClick={() => onInspect(check)} aria-label={`${block.name} ${check.name} 상세 보기`}><span><ClipboardList size={14} />{check.name}</span><Result result={check.result} /><ChevronRight size={14} /></button>)}</div></article>
}

function PeoplePicker({ title, selected, onChange }: { title: string; selected: string[]; onChange: (ids: string[]) => void }) {
  return <fieldset className="af-people-picker"><legend>{title}</legend><div>{demoUsers.filter(user => !user.admin).map(user => <label key={user.id} className={selected.includes(user.id) ? 'is-selected' : ''}><input type="checkbox" checked={selected.includes(user.id)} onChange={() => onChange(toggle(selected, user.id))} /><Avatar user={user} small /><span><strong>{user.name}</strong><small>{user.team} · {user.employee}</small></span></label>)}</div></fieldset>
}

function CreateProjectDialog({ state, onClose, onCreate }: { state: AccessState; onClose: () => void; onCreate: (draft: ProjectDraft) => void }) {
  const [draft, setDraft] = useState(newProjectDraft)
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  function advance() {
    const problem = validateProject(state, draft, step > 0)
    if (problem) { setError(problem); return }
    setError(''); setStep(step + 1)
  }
  function submit() {
    try { onCreate(draft) } catch (error) { setError(error instanceof Error ? error.message : '등록하지 못했습니다.') }
  }
  const updateBlock = (id: string, change: Partial<ProjectDraft['blocks'][number]>) => setDraft({ ...draft, blocks: draft.blocks.map(block => block.id === id ? { ...block, ...change } : block) })
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}><DialogContent className="af-dialog af-create-dialog"><DialogHeader><div className="af-eyebrow">NEW PROJECT</div><DialogTitle>프로젝트 만들기</DialogTitle><DialogDescription>기본 정보부터 담당자 배정까지, 세 단계면 준비됩니다.</DialogDescription></DialogHeader><div className="af-wizard-steps" aria-label="프로젝트 등록 단계">{['프로젝트와 TOP', '블록과 담당자', '등록 확인'].map((title, index) => <div key={title} className={index === step ? 'is-active' : index < step ? 'is-complete' : ''} aria-current={index === step ? 'step' : undefined}><span>{index < step ? <Check size={13} /> : index + 1}</span>{title}</div>)}</div>
    <div className="af-wizard-body">
      {error && <div className="af-error" role="alert">{error}</div>}
      {step === 0 && <><div className="af-form-grid"><label>프로젝트 이름<Input aria-label="프로젝트 이름" value={draft.name} maxLength={60} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label><label>프로젝트 키<Input aria-label="프로젝트 키" value={draft.key} maxLength={40} onChange={event => setDraft({ ...draft, key: event.target.value })} /><small>VWP 데이터와 연결할 고유 키입니다.</small></label></div><label className="af-field">설명<Input value={draft.description} maxLength={160} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label><PeoplePicker title="TOP 담당자 · 한 명 이상" selected={draft.tops} onChange={tops => setDraft({ ...draft, tops })} /><div className="af-form-note"><ShieldCheck size={16} />TOP은 프로젝트 전체를 조회합니다. 담당자 관리 권한은 등록 후 별도로 지정할 수 있습니다.</div></>}
      {step === 1 && <><div className="af-form-note"><Boxes size={16} />블록마다 담당자와 확인할 sanity item을 지정하세요. 담당자는 여러 명 선택할 수 있습니다.</div>{draft.blocks.map((block, index) => <section className="af-block-draft" key={block.id}><div className="af-block-draft-title"><label>블록 {index + 1} 이름<Input aria-label={`블록 ${index + 1} 이름`} value={block.name} maxLength={40} onChange={event => updateBlock(block.id, { name: event.target.value })} /></label><Button variant="ghost" size="icon" aria-label={`블록 ${index + 1} 삭제`} disabled={draft.blocks.length === 1} onClick={() => setDraft({ ...draft, blocks: draft.blocks.filter(item => item.id !== block.id) })}><Trash2 /></Button></div><fieldset className="af-owner-options"><legend>{block.name || `블록 ${index + 1}`} 담당자</legend>{demoUsers.filter(user => !user.admin).map(user => <label key={user.id}><input type="checkbox" checked={block.owners.includes(user.id)} onChange={() => updateBlock(block.id, { owners: toggle(block.owners, user.id) })} />{user.name}</label>)}</fieldset><fieldset className="af-owner-options"><legend>{block.name || `블록 ${index + 1}`} sanity item</legend>{checkTemplates.map(check => <label key={check}><input type="checkbox" checked={block.checks.includes(check)} onChange={() => updateBlock(block.id, { checks: toggle(block.checks, check) })} />{check}</label>)}</fieldset></section>)}<Button variant="outline" onClick={() => setDraft({ ...draft, blocks: [...draft.blocks, { id: crypto.randomUUID(), name: '', owners: [], checks: [...checkTemplates] }] })}><Plus />블록 추가</Button></>}
      {step === 2 && <><div className="af-confirm-title"><span className="af-project-mark">{draft.name.slice(0, 1)}</span><div><h3>{draft.name}</h3><p>{draft.key} · {draft.description}</p></div></div><div className="af-confirm-top"><ShieldCheck size={18} /><div><strong>TOP · {names(draft.tops)}</strong><p>아래 모든 블록과 sanity item을 조회할 수 있습니다.</p></div></div>{draft.blocks.map(block => <div className="af-confirm-block" key={block.id}><Boxes size={17} /><strong>{block.name}</strong><span>{names(block.owners)}</span><small>{block.checks.join(' · ')}</small></div>)}<div className="af-form-note"><History size={16} />등록 직후에는 모든 검사가 ‘미수신’입니다. 담당자 계정으로 전환해 조회 범위를 확인해 보세요.</div></>}
    </div><DialogFooter className="af-wizard-footer"><span>{step + 1} / 3 단계</span><Button variant="outline" onClick={() => { if (step === 0) onClose(); else { setStep(step - 1); setError('') } }}>{step > 0 && <ArrowLeft />}{step === 0 ? '취소' : '이전'}</Button>{step < 2 ? <Button onClick={advance}>다음<ArrowRight /></Button> : <Button onClick={submit}><Check />프로젝트 등록</Button>}</DialogFooter></DialogContent></Dialog>
}

function ManageGrantsDialog({ project, current, onClose, onSave }: { project: DemoProject; current: ScopeGrant[]; onClose: () => void; onSave: (grants: ScopeGrant[]) => void }) {
  const [grants, setGrants] = useState(() => current.map(grant => ({ ...grant })))
  const [error, setError] = useState('')
  function has(userId: string, role: ScopeGrant['role'], blockId?: string) { return grants.some(grant => grant.userId === userId && grant.role === role && grant.blockId === blockId) }
  function change(userId: string, role: ScopeGrant['role'], blockId?: string) {
    setGrants(old => has(userId, role, blockId) ? old.filter(grant => !(grant.userId === userId && grant.role === role && grant.blockId === blockId)) : [...old, { userId, projectId: project.id, role, scope: blockId ? 'block' : 'project', ...(blockId ? { blockId } : {}) }])
  }
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}><DialogContent className="af-dialog af-manage-dialog"><DialogHeader><div className="af-eyebrow">PEOPLE & SCOPE</div><DialogTitle>{project.name} 담당자 설정</DialogTitle><DialogDescription>사용자 계정에 담당 범위를 연결합니다. 이 프로젝트에만 적용됩니다.</DialogDescription></DialogHeader><div className="af-wizard-body">{error && <div role="alert" className="af-error">{error}</div>}<div className="af-form-note"><Sparkles size={16} />최지우에게 블록을 배정하고, 저장한 뒤 최지우 계정으로 전환해 보세요.</div><div className="af-grant-users">{demoUsers.filter(user => !user.admin).map(user => <fieldset className="af-grant-user" key={user.id}><legend><Avatar user={user} small /><span><strong>{user.name}</strong><small>{user.team} · {user.employee}</small></span></legend><div className="af-grant-options"><label className="af-top-option"><input type="checkbox" aria-label={`${user.name} TOP`} checked={has(user.id, 'top')} onChange={() => change(user.id, 'top')} />TOP · 전체 블록</label>{project.blocks.map(block => <label key={block.id}><input type="checkbox" aria-label={`${user.name} ${block.name} 담당`} checked={has(user.id, 'reviewer', block.id)} onChange={() => change(user.id, 'reviewer', block.id)} />{block.name}</label>)}</div><label className="af-manager-option"><input type="checkbox" aria-label={`${user.name} 프로젝트 관리 허용`} checked={has(user.id, 'manager')} onChange={() => change(user.id, 'manager')} /><ShieldCheck size={13} />이 프로젝트 관리 허용 <small>전체 조회 · 담당자 배정</small></label></fieldset>)}</div><p className="af-dialog-note">TOP은 한 명 이상 유지합니다. 프로젝트 관리 권한으로는 새 프로젝트를 생성할 수 없습니다.</p></div><DialogFooter><Button variant="outline" onClick={onClose}>취소</Button><Button onClick={() => { try { onSave(grants) } catch (error) { setError(error instanceof Error ? error.message : '저장하지 못했습니다.') } }}><Check />배정 저장</Button></DialogFooter></DialogContent></Dialog>
}
