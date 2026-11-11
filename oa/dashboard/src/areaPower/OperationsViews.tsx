import { useState } from 'react'
import { CheckCheck, CheckCircle2, Eye, History, RotateCcw } from 'lucide-react'
import { allRecords, date, format, pretty, request, revision, text, unknownSchema, type Auth, type TrackingRecord } from './api'
import { Badge, Button, CopyButton, Empty, Field, Json, Modal, Submit, prevent, useAction } from './components/workspace'

interface OperationProps { auth: Auth; records: TrackingRecord[]; refresh: () => Promise<void> }
export function Alerts({ auth, records, refresh }: OperationProps) {
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState<{ record: TrackingRecord; action: string } | null>(null)
  const [note, setNote] = useState('')
  const [events, setEvents] = useState<TrackingRecord[] | null>(null)
  const action = useAction()
  const historyAction = useAction()
  const alerts = records.filter(record => filter === 'all' || (filter === 'unresolved' ? !record.resolved_at && record.state !== 'resolved' : record.state === filter))
  const actions: Record<string, string> = { read: '읽음 처리', acknowledge: '확인 처리', resolve: '해소 처리' }
  return <><section className="panel"><div className="section-heading"><div><span className="eyebrow">REVIEW & RESPONSE</span><h2>알림 센터</h2><p>알림의 수치와 AI 사유를 검토하고 읽음 · 확인 · 해소를 기록합니다. 모든 변경은 row_version으로 동시성을 보호합니다.</p></div><Field label="알림 상태"><select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">모든 알림</option><option value="unresolved">미해소</option><option value="open">Open</option><option value="resolved">Resolved</option></select></Field></div>{historyAction.feedback}{alerts.length ? <div className="alert-list">{alerts.map(record => <article className="alert-card" key={record.id}><div className="card-line"><div className="actions"><Badge value={record.payload.kind} /><Badge value={record.state} />{record.read_at && <span className="state-label"><Eye size={12} />읽음</span>}{record.acknowledged_at && <span className="state-label"><CheckCheck size={12} />확인</span>}{record.resolved_at && <span className="state-label"><CheckCircle2 size={12} />해소</span>}</div><small>{date(record.created_at)}</small></div><h3>{text(record.payload.summary) || `${text(record.payload.hier_name)} · ${text(record.payload.metric)}`}</h3><p className="prose">{text(record.payload.reason) || '판단 사유가 기록되지 않았습니다. 전체 payload와 분석 작업 상태를 확인하세요.'}</p><div className="alert-metrics"><div><small>기준</small><strong>{format(record.payload.baseline)}</strong></div><span>→</span><div><small>현재</small><strong>{format(record.payload.current)}</strong></div><div><small>Hierarchy / Metric</small><code>{text(record.payload.hier_name)} / {text(record.payload.metric)}</code></div></div><Json value={record.payload} label="판단 근거 · 수신자 · 원본 결과 ID" /><div className="card-footer"><div><code>{record.id}</code><CopyButton value={record.id} /></div><div className="actions"><Button variant="ghost" size="sm" disabled={historyAction.busy} onClick={() => historyAction.run(async () => { setEvents(await allRecords(auth, 'alert_event', { parent_id: record.id })) }, '알림 이력을 조회했습니다.')}><History size={14} />이력</Button>{!record.read_at && <Button variant="outline" size="sm" onClick={() => { setNote(''); setSelected({ record, action: 'read' }) }}><Eye size={14} />읽음</Button>}{!record.acknowledged_at && !record.resolved_at && <Button variant="outline" size="sm" onClick={() => { setNote(''); setSelected({ record, action: 'acknowledge' }) }}><CheckCheck size={14} />확인</Button>}{!record.resolved_at && record.state !== 'resolved' && <Button size="sm" onClick={() => { setNote(''); setSelected({ record, action: 'resolve' }) }}><CheckCircle2 size={14} />해소</Button>}</div></div></article>)}</div> : <Empty title="표시할 알림이 없습니다">알림이 없다는 사실만으로 분석 완료나 정상 여부를 추정하지 않습니다. 비교 coverage와 정책을 확인하세요.</Empty>}</section>{selected && <Modal title={actions[selected.action]} onClose={() => setSelected(null)}><form onSubmit={event => prevent(event, () => action.run(async () => { await request(auth, `/alerts/${encodeURIComponent(selected.record.id)}/state`, unknownSchema, { action: selected.action, expected_row_version: revision(selected.record.row_version), ...(note.trim() ? { note: note.trim() } : {}) }); await refresh(); setSelected(null) }))}><p className="prose">{text(selected.record.payload.summary) || selected.record.id}</p><p className="muted">현재 row_version: {selected.record.row_version}. 다른 사용자의 수정과 충돌하면 새로고침 후 다시 요청하세요.</p><Field label="처리 메모 (선택)"><textarea rows={4} value={note} onChange={event => setNote(event.target.value)} /></Field>{action.feedback}<div className="form-actions"><Button type="button" variant="outline" onClick={() => setSelected(null)}>취소</Button><Submit busy={action.busy}>{actions[selected.action]}</Submit></div></form></Modal>}{events && <Modal title="알림 상태 · 판단 변경 이력" onClose={() => setEvents(null)}>{events.length ? events.map(event => <article className="history-item" key={event.id}><div className="card-line"><code>{event.id}</code><small>{date(event.created_at)}</small></div><Json value={event.payload} open /></article>) : <Empty title="저장된 상태 변경 이력이 없습니다" />}</Modal>}</>
}
export function Jobs({ auth, records, refresh }: OperationProps) {
  const [filter, setFilter] = useState('all')
  const [detail, setDetail] = useState<TrackingRecord | null>(null)
  const [rerun, setRerun] = useState<TrackingRecord | null>(null)
  const action = useAction()
  const jobs = records.filter(record => filter === 'all' || record.state === filter)
  return (
    <>
      <section className="panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">DURABLE WORK QUEUE</span>
            <h2>작업 관제</h2>
            <p>{auth.demo ? '합성 수치 작업의 상태와 명시적 재실행 예시입니다. 실제 DB 큐·AI·메일은 호출하지 않습니다.' : 'DB 작업의 consumer · claim · 오류를 확인하세요. 실패 작업은 명시적으로 새 작업을 요청해야 재실행됩니다.'}</p>
          </div>
          <Field label="작업 상태">
            <select value={filter} onChange={event => setFilter(event.target.value)}>
              <option value="all">모든 작업</option>
              <option value="pending">Pending</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
          </Field>
        </div>
        {action.feedback}
        {jobs.length ? (
          <div className="table-scroll">
            <table>
              <thead><tr><th>작업 종류 / ID</th><th>Consumer</th><th>상태</th><th>Claim</th><th>오류</th><th>등록 / 갱신</th><th>작업</th></tr></thead>
              <tbody>
                {jobs.map(job => (
                  <tr key={job.id}>
                    <td>
                      <strong>{job.job_kind || text(job.payload.job_kind) || '종류 미확인'}</strong>
                      <code>{job.id}</code>
                      {job.previous_id != null && <small>이전 작업 · {text(job.previous_id)}</small>}
                    </td>
                    <td><code>{job.consumer_server || '—'}</code></td>
                    <td><Badge value={job.state} /></td>
                    <td>
                      <code>{job.claim_owner || '미점유'}</code>
                      <small>{date(typeof job.claimed_at === 'string' ? job.claimed_at : null)}</small>
                    </td>
                    <td className="reason">{job.error_detail != null ? pretty(job.error_detail) : job.payload.error != null ? pretty(job.payload.error) : '—'}</td>
                    <td>{date(job.created_at)}<small>{date(job.updated_at)}</small></td>
                    <td>
                      <div className="actions">
                        <Button variant="ghost" size="sm" onClick={() => setDetail(job)}>상세</Button>
                        {job.state === 'failed' && <Button variant="outline" size="sm" disabled={action.busy} onClick={() => setRerun(job)}><RotateCcw size={14} />명시적 재실행</Button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="표시할 작업이 없습니다">{auth.demo ? '현재 탭의 합성 수치 작업만 표시합니다.' : '분석 · AI · 메일 작업은 실제 DB 큐에 기록된 상태만 표시합니다.'}</Empty>
        )}
        <div className="panel-note">Running 작업을 자동 회수하거나 실패 작업을 자동 재시도하지 않습니다. 메일 결과가 불명확하면 재실행 전에 중복 발송 가능성을 확인하세요.</div>
      </section>
      {detail && (
        <Modal title="작업 상세와 오류" onClose={() => setDetail(null)}>
          <CopyButton value={detail.id} />
          <Json value={detail} open label="전체 작업 상태 · timestamps · 입력 · 오류" />
        </Modal>
      )}
      {rerun && (
        <Modal title="실패한 작업을 재실행할까요?" onClose={() => setRerun(null)}>
          <p>기존 실패 기록을 보존하고 같은 대상에 대한 새 작업을 요청합니다. 자동 재시도가 아닙니다.</p>
          <div className="notice warning">{auth.demo ? '합성 수치 작업만 현재 탭에서 다시 계산합니다. 실제 AI나 메일 발송은 실행하지 않습니다.' : '메일 작업은 이미 전달됐을 가능성이 있습니다. 오류와 수신자를 확인한 뒤 재실행하세요.'}</div>
          <Json value={rerun} label="재실행 대상의 입력과 오류" open />
          {action.feedback}
          <div className="form-actions">
            <Button variant="outline" onClick={() => setRerun(null)}>취소</Button>
            <Button disabled={action.busy} onClick={() => action.run(async () => {
              await request(auth, `/jobs/${encodeURIComponent(rerun.id)}/rerun`, unknownSchema, { expected_row_version: revision(rerun.row_version) })
              await refresh()
              setRerun(null)
            }, '새 작업을 요청했습니다. 기존 실패 기록은 보존됩니다.')}><RotateCcw size={15} />새 작업으로 재실행</Button>
          </div>
        </Modal>
      )}
    </>
  )
}
