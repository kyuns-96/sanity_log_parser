import { useEffect, useRef, useState } from 'react'
import { Button } from './studio/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './components/ui/dialog'
import { WorkspaceError, workspaceRequest as request, workspaceTime as time } from './workspaceApi'
import './submission-intake.css'
import PreservedSources, { type PreservedSource } from './PreservedSources'

type Scope = { project_key: string; block_key: string; net_ver: string; submission_revision: string; layout: string; target_eco: string | null; pre_revision: string | null; profile_id: string; profile_version: string }
type Entry = { id: string; revision: number; scope: Scope; reported_by: string; delivery_state: string; created_at: string; blockers: string[]; decision: null | { user_id: string; confirmed_at: string; reason: string; decision: string } }
type Listing = { items: Entry[]; total: number; can_confirm: boolean }
type Unit = { review_unit_id: string; item_id: string; report: string; applicable: boolean; exclusion?: unknown; condition: unknown; execution_status: string; parse_status: string; record_count: number; blockers: string[]; source: { layout: string; revision: string; eco: string | null; mode: string; tool: string; tool_version: string; execution_ref: string; execution_order: number } }
type Detail = Entry & { can_confirm: boolean; content_sha256: string; scope_sha256: string | null; manifest_sha256: string | null; total_units: number; offset: number; limit: number; units: Unit[]; global_blockers?: string[]; preserved_files?: PreservedSource[] }
const stateName: Record<string, string> = { IMPORTING: '수신 중', AWAITING_CONFIRMATION: '담당자 확인 대기', OA_STORED: '접수 확정', REJECTED: '반려' }

export default function SubmissionIntake({ blockId, refresh, onComplete }: { blockId: string; refresh: number; onComplete: () => void }) {
  const [listing, setListing] = useState<Listing | null>(null)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<Detail | null>(null)
  const [unitOffset, setUnitOffset] = useState(0)
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const command = useRef<{ key: string; id: string } | null>(null)
  const base = `/blocks/${blockId}/intake`
  useEffect(() => {
    const abort = new AbortController()
    request<Listing>(`${base}?offset=${offset}&limit=25`, { signal: abort.signal }).then(setListing).catch(e => {
      if (!abort.signal.aborted) setError(e instanceof Error ? e.message : '제출 목록을 불러오지 못했습니다.')
    })
    return () => abort.abort()
  }, [base, offset, refresh])
  useEffect(() => {
    if (!selected) return
    const abort = new AbortController()
    setLoading(true)
    request<Detail>(`${base}/${selected}?offset=${unitOffset}&limit=50`, { signal: abort.signal }).then(value => {
      setDetail(old => {
        if (old && old.revision !== value.revision) { setConfirmed(false); command.current = null }
        return value
      })
    }).catch(e => {
      if (!abort.signal.aborted) setError(e instanceof Error ? e.message : '제출 상세를 불러오지 못했습니다.')
    }).finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [base, selected, unitOffset, refresh, reload])
  function open(id: string) {
    setSelected(id); setDetail(null); setUnitOffset(0); setReason(''); setConfirmed(false); setError(''); command.current = null
  }
  async function decide(decision: 'CONFIRM' | 'REJECT') {
    if (!detail || loading || busy || !reason.trim() || (decision === 'CONFIRM' && !confirmed)) return
    const value = { decision, expected_revision: detail.revision, reason: reason.trim(), confirmed_scope: confirmed,
      content_sha256: detail.content_sha256, scope_sha256: detail.scope_sha256, manifest_sha256: detail.manifest_sha256 }
    const key = JSON.stringify([selected, value])
    if (command.current?.key !== key) command.current = { key, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try {
      await request(`${base}/${selected}`, { body: { ...value, request_id: command.current.id } })
      setNotice(decision === 'CONFIRM' ? '검사 범위를 확정하고 접수했습니다. 로그 분류와 판정은 검토 화면에서 진행하세요.' : '제출을 반려했습니다. 원본과 반려 근거는 보존됩니다.')
      setSelected(''); onComplete()
    } catch (e) {
      if (e instanceof WorkspaceError && e.code === 'REVISION_CONFLICT') {
        setError('제출 상태가 바뀌었습니다. 최신 범위를 다시 확인해 주세요.')
        setConfirmed(false); command.current = null; setReload(value => value + 1)
      } else setError(e instanceof Error ? e.message : '접수 처리를 완료하지 못했습니다. 상태를 확인한 뒤 다시 시도하세요.')
    }
    finally { setBusy(false) }
  }
  const pending = detail?.delivery_state === 'AWAITING_CONFIRMATION'
  return <section className="ws-panel submission-intake" aria-label="VWP 제출 확인">
    <details>
      <summary><strong>VWP 제출 확인</strong><span>{listing ? `${listing.total}건` : '불러오는 중'}</span></summary>
      <p>VWP에서 토큰 없이 받은 제출입니다. 로그인한 블록 담당자가 검사 범위를 확정하면 보고서를 검토할 수 있습니다.</p>
      {listing?.items.length === 0 && <p>아직 접수된 제출이 없습니다.</p>}
      {listing && listing.items.length > 0 && <div className="ws-table-scroll"><table><thead><tr><th>버전·ECO</th><th>제출자 표시 · 미확인</th><th>수신 시각</th><th>상태</th><th>상세</th></tr></thead><tbody>{listing.items.map(item => <tr key={item.id}>
        <td>{item.scope.net_ver} / {item.scope.submission_revision}<small>{item.scope.layout} · {item.scope.target_eco || 'ECO 없음'}</small><small title={item.id}>접수 {item.id.slice(0, 8)}</small></td>
        <td>{item.reported_by}</td><td>{time(item.created_at)}</td><td>{stateName[item.delivery_state] || item.delivery_state}</td>
        <td><Button variant="outline" size="sm" onClick={() => open(item.id)}>범위 확인</Button></td>
      </tr>)}</tbody></table></div>}
      {listing && listing.total > 25 && <div className="submission-intake-actions"><Button variant="outline" disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>이전 제출</Button><span>{offset + 1}–{Math.min(offset + 25, listing.total)} / {listing.total}</span><Button variant="outline" disabled={offset + 25 >= listing.total} onClick={() => setOffset(v => v + 25)}>다음 제출</Button></div>}
    </details>
    {notice && <p role="status">{notice}</p>}{error && !selected && <p className="ws-error" role="alert">{error}</p>}
    <Dialog open={!!selected} onOpenChange={value => { if (!busy && !value) setSelected('') }}><DialogContent className="submission-intake-dialog">
      <DialogHeader><DialogTitle>제출 검사 범위 확인</DialogTitle><DialogDescription>제출자 표시는 인증된 계정이 아닙니다. 현재 블록 담당자의 계정으로 전체 예상 범위와 누락·제외 근거를 확정합니다.</DialogDescription></DialogHeader>
      {!detail ? <p role="status">제출을 불러오는 중입니다.</p> : <>
        <p><strong>{detail.scope.net_ver} · {detail.scope.submission_revision}</strong><br />{detail.scope.layout} · ECO {detail.scope.target_eco || '없음'} · 연결 PRE {detail.scope.pre_revision || '없음'}<br />프로필 {detail.scope.profile_id} / {detail.scope.profile_version}<br /><small>접수 {detail.id}</small></p>
        <p>제출자 표시(미확인): {detail.reported_by} · {stateName[detail.delivery_state] || detail.delivery_state}</p>
        <p>전체 예상 검사 {detail.total_units}개 · 현재 {detail.total_units ? detail.offset + 1 : 0}–{Math.min(detail.offset + detail.limit, detail.total_units)}개 표시</p>
        {detail.global_blockers?.includes('UNEXPECTED_REPORTS') && <p className="ws-error">예상 목록 밖의 파일이 있습니다. 범위를 확정해도 이 차단 사유는 남습니다.</p>}
        <div className="ws-table-scroll submission-intake-units" aria-busy={loading}><table><thead><tr><th>항목·실행 조건</th><th>원천 버전</th><th>실행 / 파싱</th><th>로그 수·적용</th></tr></thead><tbody>{detail.units.map(unit => <tr key={unit.review_unit_id}>
          <td>{unit.item_id} · {unit.source.mode}<details><summary>원천·조건·제외 근거</summary><p>{unit.report}<br />{unit.source.tool} {unit.source.tool_version}<br />실행 {unit.source.execution_ref} · 순서 {unit.source.execution_order}</p><pre>{JSON.stringify({ condition: unit.condition, exclusion: unit.exclusion || null }, null, 2)}</pre></details></td>
          <td>{unit.source.layout} · {unit.source.revision}<small>{unit.source.eco || 'ECO 없음'}</small></td><td>{unit.execution_status}<br />{unit.parse_status}{unit.blockers.length > 0 && <small>{unit.blockers.join(', ')}</small>}</td><td>{unit.record_count}건<br />{unit.applicable ? '검사 대상' : '적용 제외'}</td>
        </tr>)}</tbody></table></div>
        {detail.total_units > 50 && <div className="submission-intake-actions"><Button variant="outline" disabled={loading || busy || !unitOffset} onClick={() => setUnitOffset(v => Math.max(0, v - 50))}>이전 범위</Button><Button variant="outline" disabled={loading || busy || unitOffset + 50 >= detail.total_units} onClick={() => setUnitOffset(v => v + 50)}>다음 범위</Button></div>}
        {detail.decision && <p>처리: {detail.decision.user_id} · {time(detail.decision.confirmed_at)}<br />{detail.decision.reason}</p>}
        <PreservedSources files={detail.preserved_files} />
        {pending && detail.can_confirm && <>
          <label className="submission-intake-confirm"><input type="checkbox" checked={confirmed} disabled={busy || loading} onChange={e => setConfirmed(e.target.checked)} />전체 {detail.total_units}개의 예상 검사 범위와 누락·제외 항목{detail.preserved_files?.length ? ` 및 보존 파일 ${detail.preserved_files.length}개의 역할·귀속` : ''}을 확인했습니다.</label>
          <label className="ws-reason-label">확인·반려 사유<textarea value={reason} maxLength={2000} disabled={busy} onChange={e => setReason(e.target.value)} /></label>
          <p className="ws-muted">범위 확정은 검사 PASS나 사람 판정 완료를 뜻하지 않습니다. 파싱 누락·실행 미완료 등의 차단은 유지됩니다.</p>
          <div className="submission-intake-actions"><Button variant="outline" disabled={busy || loading || !reason.trim()} onClick={() => decide('REJECT')}>반려</Button><Button disabled={busy || loading || !confirmed || !reason.trim()} onClick={() => decide('CONFIRM')}>{busy ? '처리 중…' : '범위 확정 후 접수'}</Button></div>
        </>}
        {pending && !detail.can_confirm && <p>현재 블록 담당자만 접수를 확정하거나 반려할 수 있습니다.</p>}
      </>}
      {error && <p className="ws-error" role="alert">{error}</p>}
    </DialogContent></Dialog>
  </section>
}
