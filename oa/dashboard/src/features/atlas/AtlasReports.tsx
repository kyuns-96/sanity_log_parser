import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, FileSearch, Files, RefreshCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { appUrl } from '@/lib/appUrl'
import { useDashboardNavigation } from '@/lib/dashboardMode'
import type { Page } from '@/lib/types'
import { useDashboardAuth } from '@/features/auth/useDashboardAuth'
import { useAtlas } from './atlasContext'

type AtlasReport = {
  id: string; project_id: string; block_id: string; version_id: string;
  item_id?: string; family?: string; check_status?: string; record_count?: number; eco?: string | null;
  source_storage?: string; [key: string]: unknown;
}

const number = (v: number) => v.toLocaleString('ko-KR')
const textStatus: Record<string, string> = {
  PASS: 'PASS', FAIL: 'FAIL', UNKNOWN: '판정 보류', NOT_APPLICABLE: '해당 없음', COMPLETE: '완료',
  COMPLETED: '완료', INCOMPLETE: '미완료', MISSING: '누락', PARTIAL: '일부 해석', UNSUPPORTED: '미지원',
  NOT_RUN: '미실행', PENDING: '응답 대기', REQUESTED: '응답 대기', RECEIVED: '수신 완료', EXPIRED: '만료', NOT_CONFIGURED: '미설정',
}

function Status({ value }: { value: string }) {
  return <Badge variant="outline" className={`status status-${value.toLowerCase()}`}>{value === 'PASS' && <Check size={11} />}{textStatus[value] || value}</Badge>
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty"><Files size={26} /><p>{children}</p></div>
}

function Pagination({ offset, total, limit, change }: { offset: number; total: number; limit: number; change: (offset: number) => void }) {
  return (
    <div className="pagination">
      <span>{total ? `${number(offset + 1)}–${number(Math.min(offset + limit, total))}` : '0'} / {number(total)}</span>
      <Button aria-label="이전 페이지" size="icon" variant="outline" disabled={offset === 0} onClick={() => change(Math.max(0, offset - limit))}><ChevronLeft /></Button>
      <Button aria-label="다음 페이지" size="icon" variant="outline" disabled={offset + limit >= total} onClick={() => change(offset + limit)}><ChevronRight /></Button>
    </div>
  )
}

function LoadingDetail() {
  return <div className="loading-state" role="status"><p>검토 결과를 불러오는 중입니다.</p><div aria-hidden="true"><div className="skeleton skeleton-title" />{Array.from({ length: 5 }, (_, row) => <div key={row} className="skeleton-row"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div>)}</div></div>
}

// Page-local scope filters. Selection stays in the URL (project/block/version) so links and the workbench round-trip keep working.
function ScopeFilters() {
  const atlas = useAtlas()
  const [error, setError] = useState('')
  if (!atlas) return null
  const fail = (cause: unknown) => setError(cause instanceof Error ? cause.message : '조회 범위를 변경하지 못했습니다.')
  const choose = (projectId: string, blockId: string, versionId: string) => { setError(''); void atlas.selectScope({ projectId, blockId, versionId }).catch(fail) }
  const netVersions = [...new Set(atlas.block?.versions.map(version => version.net_ver))]
  const revisions = atlas.block?.versions.filter(version => version.net_ver === atlas.netVer) || []
  return <>
    <section className="atlas-scope atlas-scope-inline" aria-label="조회 범위">
      <div className="atlas-scope-fields">
        <label>프로젝트<select value={atlas.scope.projectId} onChange={event => choose(event.target.value, '', '')}><option value="">프로젝트 선택</option>{atlas.catalog?.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label>블록<select disabled={!atlas.project} value={atlas.scope.blockId} onChange={event => choose(atlas.scope.projectId, event.target.value, '')}><option value="">블록 선택</option>{atlas.project?.blocks.map(block => <option key={block.id} value={block.id}>{block.name}</option>)}</select></label>
        <label>NET_VER<select disabled={!atlas.block} value={atlas.netVer} onChange={event => { setError(''); void atlas.selectNetVer(event.target.value).catch(fail) }}><option value="">NET_VER 선택</option>{netVersions.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>REVISION<select disabled={!atlas.block || !atlas.netVer} value={atlas.scope.versionId} onChange={event => choose(atlas.scope.projectId, atlas.scope.blockId, event.target.value)}><option value="">REVISION 선택</option>{revisions.map(version => <option key={version.id} value={version.id}>{[version.revision, version.layout, version.eco_num].filter(Boolean).join(' · ')}</option>)}</select></label>
      </div>
    </section>
    {error && <p className="error-banner" role="alert">{error}</p>}
  </>
}

export function AtlasReports({ onLegacy, canWorkbench }: { onLegacy: () => void; canWorkbench: boolean }) {
  const atlas = useAtlas()
  const auth = useDashboardAuth(true)
  const navigateDashboard = useDashboardNavigation()
  const projectId = atlas?.scope.projectId || ''
  const blockId = atlas?.scope.blockId || ''
  const versionId = atlas?.scope.versionId || ''
  const [reports, setReports] = useState<Page<AtlasReport>>({ items: [], total: 0, offset: 0, limit: 25 })
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<AtlasReport | null>(null)
  const detailRef = useRef<HTMLElement>(null)
  useEffect(() => { if (selected) detailRef.current?.scrollIntoView({ block: 'nearest' }) }, [selected])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useLayoutEffect(() => {
    setOffset(0); setSelected(null); setError('')
    setReports({ items: [], total: 0, offset: 0, limit: 25 })
  }, [projectId, blockId, versionId])
  useEffect(() => {
    // The reports API is scoped to one exact project/block/version.
    if (!projectId || !blockId || !versionId) { setLoading(false); return }
    const controller = new AbortController()
    const params = new URLSearchParams({ project_id: projectId, offset: String(offset), limit: '25' })
    params.set('block_id', blockId)
    params.set('version_id', versionId)
    setLoading(true); setError('')
    auth.request<Page<AtlasReport>>(`/api/atlas/reports?${params}`, undefined, false, controller.signal)
      .then(value => { if (!controller.signal.aborted) { setReports(value); setSelected(null) } })
      .catch(cause => { if (!controller.signal.aborted) { setReports({ items: [], total: 0, offset, limit: 25 }); setSelected(null); setError(cause instanceof Error ? cause.message : '버전 로그를 불러오지 못했습니다.') } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [auth.request, projectId, blockId, versionId, offset, refresh])
  function openWorkbench(report: AtlasReport) {
    const target = new URL(appUrl('/?page=workbench'), location.origin)
    for (const [key, value] of Object.entries({ project: report.project_id, block: report.block_id, version: report.version_id, review: report.id, tab: 'reports', item: report.item_id, eco: report.eco })) {
      if (value) target.searchParams.set(key, value)
    }
    void navigateDashboard(target.pathname + target.search, auth.session?.user_id).catch(cause => setError(cause instanceof Error ? cause.message : '로그 검토를 열지 못했습니다.'))
  }
  return <main className="workspace" id="workspace-main" tabIndex={-1}>
    <div className="page-heading"><div><h1>버전 로그</h1><p>선택한 프로젝트·블록·버전의 {atlas?.demo ? '합성 보고서' : '수신된 로그'}를 조회합니다.</p></div><div className="heading-actions"><Button variant="outline" onClick={() => setRefresh(value => value + 1)} disabled={loading || !projectId || !blockId || !versionId}><RefreshCw className={loading ? 'spin' : ''} />새로고침</Button>{!atlas?.demo && <Button variant="outline" onClick={onLegacy}>기존 접수 실행 검토</Button>}</div></div>
    <ScopeFilters />
    {error && <p className="error-banner" role="alert">{error}</p>}
    {!projectId || !blockId || !versionId ? <Empty>위에서 프로젝트·블록·NET_VER·REVISION을 선택하면 해당 버전에 수신된 로그를 조회합니다.</Empty> : loading ? <LoadingDetail /> : error ? null : <>
      <div className={`ledger-split${selected ? ' has-detail' : ''}`}>
      <section className="detail-panel"><div className="section-heading"><h2>수신된 보고서 <span>{number(reports.total)}</span></h2></div>
        {reports.items.length ? <Table><TableHeader><TableRow><TableHead>보고서</TableHead><TableHead>블록 · 버전</TableHead><TableHead>검사 결과</TableHead><TableHead>기록 수</TableHead><TableHead>근거</TableHead></TableRow></TableHeader><TableBody>{reports.items.map(report => {
          const block = atlas?.project?.blocks.find(item => item.id === report.block_id)
          const version = block?.versions.find(item => item.id === report.version_id)
          return <TableRow key={report.id} className={selected?.id === report.id ? 'selected-row' : ''}><TableCell><strong>{report.id}</strong><div>{report.item_id || '항목 미분류'} · {report.family || '도구 미분류'}</div></TableCell><TableCell>{block?.name || '블록 정보 없음'}<br /><small>{version ? `${version.net_ver} · ${version.revision} · ${version.layout}` : '버전 정보 없음'}</small></TableCell><TableCell>{report.check_status ? <Status value={report.check_status} /> : '미제공'}</TableCell><TableCell>{report.record_count === undefined ? '미제공' : number(report.record_count)}</TableCell><TableCell><Button variant="outline" size="sm" onClick={() => setSelected(report)}>수신 내용 보기</Button></TableCell></TableRow>})}</TableBody></Table> : <Empty>선택한 범위에 수신된 로그가 없습니다. 버전 선언이나 수치 분석 준비만으로 로그가 생성되지는 않습니다.</Empty>}
        <Pagination offset={offset} total={reports.total} limit={25} change={setOffset} />
      </section>
      {selected && <section className="detail-panel ledger-detail" ref={detailRef} aria-label={`${selected.id} 수신 내용`}><div className="detail-header"><div><h2>{selected.id}</h2><p>{atlas?.demo ? '현재 탭의 합성 보고서 payload입니다. 실제 서버나 DB에서 읽은 자료가 아닙니다.' : '서버에 저장된 보고서의 검사 정보입니다. 분할 저장된 원본은 이 목록 API에서 펼치지 않습니다.'}</p></div><Button variant="ghost" onClick={() => setSelected(null)}>닫기</Button></div><div className="unit-detail"><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(Object.fromEntries(Object.entries(selected).filter(([key]) => key !== 'project_id' && key !== 'block_id')), null, 2)}</pre>{canWorkbench ? <Button onClick={() => openWorkbench(selected)}><FileSearch size={16} />로그 분류·판정 열기</Button> : <p className="session-notice">운영 로그 분류·판정은 권한이 확인된 SSO 세션에서만 지원합니다. 로컬 토큰에서 데모로 대체하지 않습니다.</p>}</div></section>}
      </div>
    </>}
    <footer className="workspace-footer"><span>Design Atlas <b>/</b> 로그 검토</span><span>수신된 원본과 검토 권한을 그대로 유지합니다.</span></footer>
  </main>
}
