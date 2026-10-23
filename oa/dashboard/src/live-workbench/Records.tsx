import { useEffect, useRef, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, Code2, Copy, GripVertical, LoaderCircle, Search } from 'lucide-react'
import { Badge } from '../studio/components/ui/badge'
import { Button } from '../studio/components/ui/button'
import { Checkbox } from '../studio/components/ui/checkbox'
import { Input } from '../studio/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../studio/components/ui/table'
import type { LogMove, useLogDrag } from '../useLogDrag'
import { workspaceLabels as labels, workspaceRequest as request } from '../workspaceApi'
import type { GroupRecords } from '../workspaceApi'

const n = (value: number) => value.toLocaleString('ko-KR')
const fieldText = (value: unknown) => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)

export default function Records({ base, revision, groupId, eventId, selected, selectEnabled = false, drag, moveForRecord, onSelect, onLoaded, onError }: {
  base: string; revision: number; groupId?: string; eventId?: string | null; selected: Set<string>; selectEnabled?: boolean;
  drag?: ReturnType<typeof useLogDrag>; moveForRecord?: (id: string, preview: string) => LogMove;
  onSelect: (ids: string[], checked: boolean) => void; onLoaded?: (value: GroupRecords) => void; onError: (value: unknown) => void
}) {
  const [data, setData] = useState<GroupRecords | null>(null)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [recordId, setRecordId] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const [copyNotice, setCopyNotice] = useState('')
  const loaded = useRef(onLoaded)
  loaded.current = onLoaded
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ expected_revision: String(revision), offset: String(offset), limit: '50', search })
    if (groupId) params.set('group_id', groupId)
    if (eventId) params.set('event_id', eventId)
    setLoading(true)
    setLoadFailed(false)
    const timer = setTimeout(() => request<GroupRecords>(`${base}/clustering/records?${params}`, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return
      setData(value)
      setRecordId(previous => value.items.some(record => record.id === previous) ? previous : value.items[0]?.id || '')
      setLoading(false)
      loaded.current?.(value)
    }).catch(value => { if (!controller.signal.aborted) { setData(null); setRecordId(''); setLoading(false); setLoadFailed(true); onError(value) } }), 120)
    return () => { clearTimeout(timer); controller.abort() }
  }, [base, revision, groupId, eventId, offset, search, retry])
  useEffect(() => {
    setCopyNotice('')
  }, [recordId])
  const record = data?.items.find(value => value.id === recordId)
  const pageSelection = data?.items.filter(value => selected.has(value.id)).length || 0
  const allSelected = !!data?.items.length && pageSelection === data.items.length
  const label = groupId ? '그룹 원문' : '전체 원문'
  const items = data?.items || []

  async function copyOriginal() {
    if (!record || loading) return
    try {
      await navigator.clipboard.writeText(record.raw)
      setCopyNotice('표시된 원문을 복사했습니다.')
    } catch {
      setCopyNotice('복사를 허용하지 않았습니다. 아래 원문을 선택해 복사할 수 있습니다.')
    }
  }

  return <div className="ws-record-pages" aria-busy={loading} data-loading={loading}>
    <header>
      <div><h3>{groupId ? '로그 목록' : '원본 로그'}<span className="ws-log-pane-count">{!loading && data ? n(data.total) : loading ? '…' : '—'}</span></h3></div>
      <label className="ws-record-search"><Search size={15} aria-hidden="true" /><span className="sr-only">로그 검색</span><Input aria-label={groupId ? '그룹 원문 검색' : '전체 원문 검색'} value={search} maxLength={100} placeholder="대상 · 모드 · 로그 검색" onChange={event => { setLoading(true); setSearch(event.target.value); setOffset(0) }} /></label>
    </header>
      <div className="ws-record-table-caption">
        <span>{loading ? '로그 목록과 원문 조회' : loadFailed ? '로그 조회 실패' : search ? '선택 범위에서 검색한 결과' : groupId ? '선택 그룹의 전체 로그' : '보고서의 전체 로그'}{!loading && data ? ` · ${n(data.total)}건` : ''}</span>
        {loading ? <span className="ws-record-loading-status" role="status"><LoaderCircle size={13} className="spin" aria-hidden="true" />로그와 원문을 가져오는 중…</span> : selectEnabled && data && <span className="ws-record-page-select">현재 페이지 {data.items.length}건 · 선택 {pageSelection}건</span>}
      </div>
      <div className="ws-record-list-region">
      <div className="ws-paged-record-list">
        <Table className="ws-record-table" aria-label={groupId ? '선택 그룹 로그 목록' : '전체 원본 로그 목록'}>
          <TableHeader><TableRow>
            {drag && moveForRecord && <TableHead className="ws-record-table-grip"><span className="sr-only">로그 이동</span></TableHead>}
            {selectEnabled && <TableHead className="ws-record-table-check"><Checkbox aria-label="현재 페이지 로그 50건 이하 선택" checked={allSelected || (pageSelection ? 'indeterminate' : false)} disabled={loading || !items.length} onCheckedChange={value => { if (!loading) onSelect(items.map(value => value.id), value === true) }} /></TableHead>}
            <TableHead className="ws-record-table-target">대상 / 로그</TableHead><TableHead className="ws-record-table-mode">모드</TableHead><TableHead className="ws-record-table-line">원문 행</TableHead><TableHead className="ws-record-table-result">도구 판정</TableHead>
          </TableRow></TableHeader>
          <TableBody>{!data && loading ? Array.from({ length: 6 }, (_, index) => <TableRow key={index} className="ws-record-loading-skeleton" aria-hidden="true">
            {drag && moveForRecord && <TableCell className="ws-record-table-grip"><span className="ws-record-skeleton" /></TableCell>}
            {selectEnabled && <TableCell className="ws-record-table-check"><span className="ws-record-skeleton" /></TableCell>}
            <TableCell className="ws-record-table-target"><span className="ws-record-skeleton" /></TableCell><TableCell className="ws-record-table-mode"><span className="ws-record-skeleton" /></TableCell><TableCell className="ws-record-table-line"><span className="ws-record-skeleton" /></TableCell><TableCell className="ws-record-table-result"><span className="ws-record-skeleton" /></TableCell>
          </TableRow>) : items.map(value => {
            const preview = String(value.fields.message || value.raw.split('\n').find(line => line.trim()) || value.fields.object || value.id).slice(0, 240)
            const target = fieldText(value.fields.object ?? value.fields.check_point ?? value.fields.target ?? value.id)
            return <TableRow key={value.id} data-record-id={value.id} aria-label={`${value.id} 원문 보기`} aria-selected={value.id === recordId} aria-disabled={loading || undefined} tabIndex={loading ? -1 : 0} className={`ws-paged-record${value.id === recordId ? ' ws-record-active' : ''}${selected.has(value.id) ? ' ws-record-selected' : ''}${drag?.drag?.ids.includes(value.id) ? ' ws-record-dragged' : ''}`} onClick={() => { if (!loading) setRecordId(value.id) }} onKeyDown={event => { if (loading || event.currentTarget !== event.target) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setRecordId(value.id) } }}>
              {drag && moveForRecord && <TableCell className="ws-record-table-grip"><button type="button" className="ws-log-grip" aria-label={`${value.id} 로그 이동`} aria-describedby={drag.helpId} disabled={loading} onClick={event => event.stopPropagation()} onPointerDown={event => { if (!loading) drag.pointerDown(event, moveForRecord(value.id, preview)) }} onKeyDown={event => { if (!loading) drag.keyDown(event, moveForRecord(value.id, preview)) }}><GripVertical size={16} /></button></TableCell>}
              {selectEnabled && <TableCell className="ws-record-table-check" onClick={event => event.stopPropagation()}><Checkbox aria-label={`${value.id} 로그 선택`} checked={selected.has(value.id)} disabled={loading} onCheckedChange={checked => { if (!loading) onSelect([value.id], checked === true) }} /></TableCell>}
              <TableCell className="ws-record-table-target"><div className="ws-record-line"><strong title={target}>{target}</strong><span className="ws-record-table-message ws-log-preview" title={preview}>{preview}</span></div></TableCell>
              <TableCell className="ws-record-table-mode" title={fieldText(value.fields.mode)}>{fieldText(value.fields.mode)}</TableCell>
              <TableCell className="ws-record-table-line">{value.line_start === value.line_end ? n(value.line_start) : `${n(value.line_start)}–${n(value.line_end)}`}</TableCell>
              <TableCell className="ws-record-table-result"><Badge variant="outline" className={`ws-status ws-status-${value.check_status.toLowerCase()}`}>{labels[value.check_status] || value.check_status}</Badge></TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
      </div>
        {data && loading && <div className="ws-record-table-pending" aria-hidden="true"><LoaderCircle size={20} className="spin" /></div>}
        {loadFailed && <div className="ws-record-load-error" role="alert"><p>로그와 원문을 가져오지 못했습니다.</p><Button variant="outline" size="sm" onClick={() => { setLoading(true); setRetry(value => value + 1) }}>로그 다시 조회</Button></div>}
      </div>
      {data && !loading && !data.total && <p className="ws-muted">검색 조건에 맞는 로그가 없습니다.</p>}
      <div className="ws-pagination"><span>{data ? `${data.total ? `${n(data.offset + 1)}–${n(Math.min(data.offset + data.limit, data.total))}` : '0'} / ${n(data.total)}` : '— / —'}</span><Button variant="outline" size="icon-sm" aria-label={`이전 ${label} 페이지`} disabled={loading || !data || !offset} onClick={() => { if (data) { setLoading(true); setOffset(Math.max(0, offset - data.limit)) } }}><ChevronLeft /></Button><Button variant="outline" size="icon-sm" aria-label={`다음 ${label} 페이지`} disabled={loading || !data || offset + data.limit >= data.total} onClick={() => { if (data) { setLoading(true); setOffset(offset + data.limit) } }}><ChevronRight /></Button></div>
      <section className="ws-record-original" aria-label="선택 로그 원문" data-loading={loading}>
        <header className="ws-record-original-toolbar"><div><Code2 size={15} /><h3>원문 보기</h3>{loading ? <span className="ws-record-original-loading" aria-hidden="true"><LoaderCircle size={13} className="spin" />가져오는 중</span> : record && <span>{record.line_start === record.line_end ? `${n(record.line_start)}행` : `${n(record.line_start)}–${n(record.line_end)}행`}</span>}</div><Button variant="ghost" size="icon-sm" aria-label="선택 로그 원문 복사" disabled={!record || loading} onClick={copyOriginal}>{copyNotice.startsWith('표시된') ? <Check size={15} /> : <Copy size={15} />}</Button></header>
        {record ? <><code className="ws-record-original-id">{record.id}</code><pre tabIndex={loading ? -1 : 0}>{record.raw}</pre>{record.raw_display_truncated && <p className="ws-record-truncation" role="note">화면 표시 한도를 초과했습니다. 생략된 부분은 원본 보고서에서 확인하세요. AI의 전체 입력 처리 범위와 별개입니다.</p>}<details className="ws-record-original-meta"><summary>파싱된 필드와 도구 근거</summary><dl>{Object.entries(record.fields).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{fieldText(value)}</dd></div>)}</dl>{record.reasons?.map((reason, index) => <p key={index}>{reason}</p>)}</details></> : <p className="ws-muted">{loading ? '로그 목록과 함께 원문을 불러오고 있습니다.' : loadFailed ? '로그 조회를 다시 시도하세요.' : '목록에서 로그를 선택하면 해당 원문을 확인할 수 있습니다.'}</p>}
        {copyNotice && <p role="status" className="ws-muted">{copyNotice}</p>}
      </section>
  </div>
}
