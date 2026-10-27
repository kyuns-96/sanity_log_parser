import { useEffect, useMemo, useState } from 'react'
import { ArrowDownToLine, ArrowUpRight, Check, ChevronLeft, ChevronRight, Database, FileSearch, Layers3, LoaderCircle, Search, ShieldCheck, Users, X } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Badge } from './studio/components/ui/badge'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import { ColorModeControl } from './components/ColorModeControl'
import { Input } from './studio/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './components/ui/dialog'
import './reportsPreview.css'

type Family = 'GCA' | 'VCLP' | 'SPYGLASS'
type User = { id: string; name: string; role: string }
type Project = { id: string; name: string; top_user: string }
type Block = { id: string; project_id: string; name: string; owner: string; net_ver: string; revision: string; layout: string; eco: string }
type Record = { id: string; fields: { [key: string]: unknown }; check_status: string; raw: string; line_start: number; line_end: number; reasons: string[] }
type Item = { id: string; project_id: string; block_id: string; family: Family; rule: string; check_status: string; parse_status: string; parser: string; raw_report: string | null; record_count: number; records: Record[]; blockers: string[]; analysis_ready: boolean; report: string; bundle_id: string }
type Data = { schema_version: number; dataset_sha256: string; users: User[]; projects: Project[]; blocks: Block[]; items: Item[]; sources: { [key in Family]: { title: string; url: string } }; summary: { items: number; users: number; projects: number; blocks: number; bundles: number; stored_records: number; db_ingestion_verified: boolean; actual_tools_run: boolean; origin: string; tool_counts: { [key in Family]: number } } }
const label: { [key: string]: string } = { GCA: 'GCA', VCLP: 'VC LP', SPYGLASS: 'SpyGlass', PASS: 'PASS', FAIL: 'FAIL', UNKNOWN: '판정 보류', COMPLETE: '완료', PARTIAL: '일부 해석', MISSING: '미수신' }
const number = (n: number) => n.toLocaleString('ko-KR')
const families: Family[] = ['GCA', 'VCLP', 'SPYGLASS']
const pageSize = 25

function Result({ value }: { value: string }) { return <Badge variant="outline" className={`rp-result rp-${value.toLowerCase()}`}><i />{label[value] || value}</Badge> }
function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export default function ReportsPreview() {
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState('')
  const [userId, setUserId] = useState('user-01')
  const [projectId, setProjectId] = useState('project-1')
  const [blockId, setBlockId] = useState('all')
  const [family, setFamily] = useState('all')
  const [status, setStatus] = useState('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<Item | null>(null)
  useEffect(() => {
    let active = true
    fetch('/sample-data/reports-library.json', { cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error('검사 예시 데이터를 불러오지 못했습니다. 샘플 데이터 생성 상태를 확인해 주세요.')
      const value = await response.json() as Data
      if (value.schema_version !== 1 || value.summary.origin !== 'SYNTHETIC_PUBLIC_REFERENCE'
        || !value.summary.db_ingestion_verified || value.summary.actual_tools_run
        || value.items.length !== value.summary.items || value.users.length !== 10
        || value.projects.length !== 3 || value.blocks.length !== 9) throw new Error('샘플 데이터 검증 정보가 올바르지 않습니다.')
      if (active) setData(value)
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : '샘플 조회 실패') })
    return () => { active = false }
  }, [])
  const user = data?.users.find(item => item.id === userId)
  const allowedBlocks = useMemo(() => data?.blocks.filter(block => block.owner === userId
    || data.projects.some(project => project.id === block.project_id && project.top_user === userId)) || [], [data, userId])
  const projects = data?.projects.filter(project => allowedBlocks.some(block => block.project_id === project.id)) || []
  const blocks = allowedBlocks.filter(block => block.project_id === projectId)
  const scope = useMemo(() => data?.items.filter(item => item.project_id === projectId
    && allowedBlocks.some(block => block.id === item.block_id) && (blockId === 'all' || item.block_id === blockId)) || [], [data, allowedBlocks, projectId, blockId])
  const filtered = scope.filter(item => (family === 'all' || item.family === family)
    && (status === 'all' || (status === 'MISSING' ? item.parse_status === 'MISSING' : item.check_status === status))
    && `${item.id} ${item.rule} ${item.family} ${label[item.family]} ${item.parser}`.toLowerCase().includes(search.toLowerCase()))
  const rows = filtered.slice(page * pageSize, (page + 1) * pageSize)
  const counts = { pass: scope.filter(item => item.check_status === 'PASS').length,
    fail: scope.filter(item => item.check_status === 'FAIL').length,
    pending: scope.filter(item => item.check_status === 'UNKNOWN').length,
    missing: scope.filter(item => item.parse_status === 'MISSING').length }
  function resetFilters() { setBlockId('all'); setFamily('all'); setStatus('all'); setSearch(''); setPage(0); setSelected(null) }
  function chooseUser(id: string) {
    setUserId(id)
    const visible = data!.projects.filter(project => project.top_user === id
      || data!.blocks.some(block => block.project_id === project.id && block.owner === id))
    setProjectId(visible[0]?.id || ''); resetFilters()
  }
  if (!data) return <main className="rp-loading studio-system">{error ? <><h1>예시 데이터 조회 실패</h1><p role="alert">{error}</p><Button onClick={() => location.reload()}>다시 확인</Button></> : <><LoaderCircle className="spin" /><p>파싱한 검사 데이터를 불러오는 중입니다.</p></>}</main>
  const project = data.projects.find(item => item.id === projectId)
  const block = data.blocks.find(item => item.id === selected?.block_id)
  return <div className="shell rp-shell studio-system">
    <aside className="sidebar rp-sidebar">
      <WorkspaceBrand />
      <div className="workspace-picker"><span className="workspace-icon">EDA</span><div><strong>공개 형식 · 검사 예시</strong><small>SYNTHETIC REPORT LIBRARY</small></div></div>
      <label className="rp-account"><span><Users size={14} />가상 계정 전환</span><select className="studio-native-select" aria-label="가상 사용자" value={userId} onChange={e => chooseUser(e.target.value)}>{data.users.map(item => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}</select><small>{user?.role} · 예시 담당 범위</small></label>
      <div className="nav-label">PROJECTS</div><nav aria-label="예시 프로젝트">{data.projects.map(item => <button key={item.id} aria-label={`${item.name} 프로젝트`} disabled={!projects.some(project => project.id === item.id)} className={projectId === item.id ? 'nav-active' : ''} onClick={() => { setProjectId(item.id); resetFilters() }}><Layers3 size={17} />{item.name}{projectId === item.id && <Check size={14} />}</button>)}</nav>
      <div className="rp-sidebar-note"><ShieldCheck size={21} /><strong>가상 사용자 10명</strong><p>TOP 3명과 블록 담당자 7명으로 구성했습니다. 계정 전환은 실제 SSO 인증이 아닌 예시 탐색 기능입니다.</p></div>
      <div className="rp-sidebar-links"><a href="/?demo=1&flow=versions">버전·PoR 데모 <ArrowUpRight size={13} /></a><a href="/?demo=1&flow=access">프로젝트 배정 데모 <ArrowUpRight size={13} /></a><a href="/?demo=1">기본 검토 대장 <ArrowUpRight size={13} /></a></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb"><span>공개 형식 예시</span><ChevronRight size={13} /><strong>{project?.name || '미배정'}</strong></div><div className="color-mode-actions"><ColorModeControl /><span className="rp-readonly"><Database size={14} />파싱·DB 저장 검증 완료</span></div></header>
      <main className="workspace rp-workspace">
        <div className="rp-banner"><ShieldCheck size={17} /><span><strong>합성 데이터 미리보기</strong> · 공개 문서의 형식을 참고한 보고서입니다. 실제 EDA 도구·사내 AI 실행 결과가 아닙니다.</span></div>
        <div className="page-heading"><div><h1>검사 결과 워크스페이스</h1><p>GCA · VC LP · SpyGlass 원문과 초기 파서의 추출 결과를 함께 확인하세요.</p></div><span className="rp-library-total">전체 예시 <strong>{number(data.summary.items)}</strong> 항목</span></div>
        <div className="rp-dataset-facts" aria-label="전체 예시 구성"><span><Users size={15} />사용자 {data.users.length}명</span><span>프로젝트 {data.projects.length}개</span><span>블록 {data.blocks.length}개</span><span>분석 묶음 {data.summary.bundles}개</span><span>저장된 근거 {number(data.summary.stored_records)}건</span></div>
        <section className="rp-scope" aria-label="담당 블록 선택"><div><span className="eyebrow">{project?.name?.toUpperCase()} / BLOCKS</span><h2>담당 범위의 검사 결과</h2></div><div className="rp-block-buttons"><button aria-pressed={blockId === 'all'} onClick={() => { setBlockId('all'); setPage(0) }}>전체 블록 <span>{blocks.length}</span></button>{blocks.map(item => <button key={item.id} aria-label={`${item.name} 블록`} aria-pressed={blockId === item.id} onClick={() => { setBlockId(item.id); setPage(0) }}>{item.name}<small>{data.users.find(user => user.id === item.owner)?.name}</small></button>)}</div></section>
        <div className="rp-stats" aria-label="현재 담당 범위 집계">{[{ label: '범위 내 전체 검사', value: scope.length, tone: 'total' }, { label: 'PASS', value: counts.pass, tone: 'pass' }, { label: 'FAIL', value: counts.fail, tone: 'fail' }, { label: '판정 보류', value: counts.pending, tone: 'unknown' }, { label: '미수신', value: counts.missing, tone: 'missing' }].map(item => <section className={`rp-stat rp-stat-${item.tone}`} key={item.label}><span>{item.label}</span><strong>{number(item.value)}<small>항목</small></strong></section>)}</div>
        <div className="rp-tools">{families.map(tool => {
          const items = scope.filter(item => item.family === tool)
          const pass = items.filter(item => item.check_status === 'PASS').length
          return <button key={tool} className={`rp-tool rp-tool-${tool.toLowerCase()}`} aria-pressed={family === tool} aria-label={`${label[tool]} 필터`} onClick={() => { setFamily(family === tool ? 'all' : tool); setPage(0) }}><div><span>{label[tool]}</span><strong>{items.length}<small>항목</small></strong></div><div className="rp-tool-bar"><i style={{ width: `${items.length ? pass / items.length * 100 : 0}%` }} /></div><p>PASS {pass} · FAIL {items.filter(item => item.check_status === 'FAIL').length} · 보류 {items.filter(item => item.check_status === 'UNKNOWN').length}</p></button>
        })}</div>
        <section className="rp-ledger" aria-label="초기 파서 검사 대장"><div className="rp-ledger-heading"><div><h2>검사 대장 <span>{number(filtered.length)}</span></h2><p>선택한 범위의 전체 항목을 검색합니다. 누락·미해석 항목도 대장에 남습니다.</p></div><div className="rp-filters"><div className="rp-search"><Search size={15} /><Input aria-label="검사 검색" placeholder="검사 ID, 규칙, 도구 검색" value={search} onChange={e => { setSearch(e.target.value); setPage(0) }} /></div><select className="studio-native-select" aria-label="도구 필터" value={family} onChange={e => { setFamily(e.target.value); setPage(0) }}><option value="all">모든 도구</option>{families.map(tool => <option key={tool} value={tool}>{label[tool]}</option>)}</select><select className="studio-native-select" aria-label="결과 필터" value={status} onChange={e => { setStatus(e.target.value); setPage(0) }}><option value="all">모든 결과</option>{['PASS', 'FAIL', 'UNKNOWN', 'MISSING'].map(value => <option key={value} value={value}>{label[value]}</option>)}</select></div></div>
          <div className="rp-table-wrap"><table><thead><tr><th>검사 항목</th><th>도구</th><th>블록</th><th>규칙</th><th>판정</th><th>파싱</th><th>근거</th><th /></tr></thead><tbody>{rows.map(item => <tr key={item.id}><td><strong>{item.id}</strong><small>NET_001 · chip_finish</small></td><td><span className={`rp-family rp-family-${item.family.toLowerCase()}`}>{label[item.family]}</span></td><td>{data.blocks.find(block => block.id === item.block_id)?.name}</td><td><code>{item.rule}</code></td><td><Result value={item.check_status} /></td><td><Result value={item.parse_status} /></td><td>{item.record_count}건</td><td><Button size="sm" variant="outline" aria-label={`${item.id} 보고서 보기`} onClick={() => setSelected(item)}><FileSearch size={14} />보고서</Button></td></tr>)}</tbody></table>{!rows.length && <div className="rp-empty">이 범위에서 조건에 맞는 검사 항목이 없습니다.</div>}</div>
          <div className="pagination"><span>{filtered.length ? `${number(page * pageSize + 1)}–${number(Math.min((page + 1) * pageSize, filtered.length))}` : '0'} / {number(filtered.length)}</span><Button aria-label="이전 검사 페이지" size="icon" variant="outline" disabled={!page} onClick={() => setPage(page - 1)}><ChevronLeft /></Button><Button aria-label="다음 검사 페이지" size="icon" variant="outline" disabled={(page + 1) * pageSize >= filtered.length} onClick={() => setPage(page + 1)}><ChevronRight /></Button></div>
        </section>
        <section className="rp-sources"><div><h2>형식 참고 자료</h2><p>아래 공급자 문서를 공개 열람본으로 확인했습니다. 보고서 문구·객체·계정은 직접 만든 합성 데이터입니다.</p></div>{families.map(tool => <a key={tool} href={data.sources[tool].url} target="_blank" rel="noreferrer"><strong>{label[tool]}</strong><span>{data.sources[tool].title}</span><ArrowUpRight size={15} /></a>)}</section>
        <footer className="rp-footer"><span>읽기 전용 데이터 스냅샷 · 운영 SSO·프로젝트 권한·PoR 저장은 사내 후속 작업</span><span>DATASET {data.dataset_sha256.slice(0, 12)}</span></footer>
      </main>
    </div>
    <Dialog open={!!selected} onOpenChange={open => { if (!open) setSelected(null) }}><DialogContent className="rp-dialog" showCloseButton={false}><DialogHeader><DialogTitle>{selected?.id} · {selected ? label[selected.family] : ''} 보고서</DialogTitle><DialogDescription>{project?.name} / {block?.name} · NET_001 · {block?.revision} · POST / chip_finish</DialogDescription></DialogHeader>{selected && <><div className="rp-report-meta"><Result value={selected.check_status} /><Result value={selected.parse_status} /><code>{selected.parser}</code></div>{selected.blockers.length > 0 && <div className="rp-blockers"><strong>완료를 막는 사유</strong><ul>{selected.blockers.map(code => <li key={code}>{code}</li>)}</ul></div>}<div className="rp-dialog-scroll"><h3>초기 파서의 추출 근거</h3>{selected.records.length ? selected.records.map(record => <article className="rp-record" key={record.id}><header><Result value={record.check_status} /><span>원문 {record.line_start}–{record.line_end}행</span></header><dl>{Object.entries(record.fields).filter(([key]) => key !== 'message').map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl><pre>{record.raw}</pre></article>) : <p className="rp-empty">원본 report가 미수신되어 추출 근거가 없습니다.</p>}<h3>합성 보고서 전체</h3><p className="tiny">SANITY_SAMPLE · SANITY_ZERO · SANITY_END는 예시 수집용 표식이며 공급자의 native 문법이 아닙니다.</p><pre className="rp-original">{selected.raw_report || '원본 report 미수신 · 예상 검사 목록에는 유지됩니다.'}</pre></div><div className="rp-dialog-actions"><Button variant="outline" disabled={!selected.raw_report} onClick={() => download(`${selected.id}-${selected.family.toLowerCase()}-synthetic.rpt`, selected.raw_report!)}><ArrowDownToLine />합성 보고서 다운로드</Button><Button onClick={() => setSelected(null)}><X size={14} />닫기</Button></div></>}</DialogContent></Dialog>
  </div>
}
