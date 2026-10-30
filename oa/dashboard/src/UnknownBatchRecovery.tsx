import { useEffect, useState } from 'react'
import { Button } from './studio/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { WorkspaceError, workspaceRequest } from './workspaceApi'
import type { CodexJob, UnknownRecovery } from './workspaceApi'
import './unknown-batch-recovery.css'

// Retain a request ID across dialog closes and in-app navigation after an
// ambiguous response. A different checkpoint always needs a new confirmation.
const requests = new Map<string, { event: string; step: string; members: string; id: string }>()
const phases: Record<string, string> = { TEXT: '보존 본문 분석', TEXT_MERGE: '본문 분석 통합', CLASSIFY: '로그 분류', MERGE: '그룹 통합', REVIEW: '그룹 판정', REVIEW_MERGE: '판정 통합' }
const reasons: Record<string, string> = { READ_TIMEOUT: '응답 읽기 시간 초과', WRITE_TIMEOUT: '요청 전송 시간 초과', TRANSPORT_ERROR: '연결 중 오류', RESPONSE_DEADLINE: '응답 기한 초과', RESERVED_EXPIRED: '작업자 응답 기한 초과' }
const hash = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value)
const validRecovery = (ref: UnknownRecovery) => !!ref && hash(ref.event_sha256) && hash(ref.step_sha256) && typeof ref.call_token === 'string' && !!ref.call_token &&
  typeof ref.phase === 'string' && Number.isSafeInteger(ref.round) && ref.round >= 0 && Number.isSafeInteger(ref.index) &&
  Number.isSafeInteger(ref.total) && ref.index >= 0 && ref.index < ref.total && (ref.input_bytes == null || Number.isSafeInteger(ref.input_bytes) && ref.input_bytes >= 0)

export function canRetryUnknown(job?: CodexJob | null): job is CodexJob {
  const ref = job?.unknown_recovery
  return !!job && job.status === 'UNKNOWN' && job.can_retry_unknown === true && job.progress?.mode === 'BATCH' && !!ref &&
    validRecovery(ref) && (job.progress.execution !== 'parallel-v1' || Array.isArray(job.unknown_recoveries) && job.unknown_recoveries.length > 0 &&
      job.unknown_recoveries.length <= 8 && job.unknown_recoveries.every(validRecovery) &&
      new Set(job.unknown_recoveries.map(value => `${value.phase}:${value.round}:${value.index}`)).size === job.unknown_recoveries.length)
}

export default function UnknownBatchRecovery({ job, onRefresh, onAccessDenied, disabled = false }: {
  job: CodexJob; onRefresh: () => void; onAccessDenied?: () => void; disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [opened, setOpened] = useState<{ job: string; event: string; step: string; members: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const ref = job.unknown_recovery
  const eligible = canRetryUnknown(job)
  const eventHash = ref?.event_sha256
  const stepHash = ref?.step_sha256
  const parallel = job.progress?.execution === 'parallel-v1'
  const members = parallel ? JSON.stringify(job.unknown_recoveries || []) : ''
  useEffect(() => {
    const saved = requests.get(job.id)
    if (saved && (!eligible || saved.event !== eventHash || saved.step !== stepHash || saved.members !== members)) requests.delete(job.id)
    if (!eligible || opened && (opened.job !== job.id || opened.event !== eventHash || opened.step !== stepHash || opened.members !== members)) setOpen(false)
  }, [job.id, eventHash, stepHash, members, eligible, opened])
  if (!eligible || !ref || !job.progress) return notice ? <span role="alert" className="unknown-batch-inline-error">{notice}</span> : null
  const progress = job.progress
  async function confirm() {
    if (busy || disabled || !ref || !canRetryUnknown(job) || opened?.job !== job.id || opened.event !== ref.event_sha256 || opened.step !== ref.step_sha256 || opened.members !== members) return
    let saved = requests.get(job.id)
    if (!saved || saved.event !== ref.event_sha256 || saved.step !== ref.step_sha256 || saved.members !== members) {
      saved = { event: ref.event_sha256, step: ref.step_sha256, members, id: crypto.randomUUID() }
      requests.set(job.id, saved)
    }
    setBusy(true); setError('')
    try {
      await workspaceRequest(`/ai/jobs/${encodeURIComponent(job.id)}/retry-unknown`, { body: { request_id: saved.id,
        expected_event_sha256: saved.event, expected_step_sha256: saved.step, acknowledge_duplicate: true } })
      requests.delete(job.id); setOpen(false); onRefresh()
    } catch (failure) {
      if (failure instanceof WorkspaceError && failure.status != null && failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
        requests.delete(job.id); setOpen(false)
        if (failure.status === 401 || failure.status === 403 || failure.status === 404) onAccessDenied?.()
        else setNotice(`${failure.message} 최신 작업 상태를 확인해 주세요.`)
        onRefresh()
      } else {
        setError('재시도 요청 결과를 확인하지 못했습니다. 다시 확인하면 같은 요청으로 처리합니다.')
      }
    } finally { setBusy(false) }
  }
  return <>
    <Button size="sm" variant="outline" disabled={disabled || busy} onClick={() => { setError(''); setNotice(''); setOpened({ job: job.id, event: ref.event_sha256, step: ref.step_sha256, members }); setOpen(true) }}>결과 불명 배치 재시도</Button>
    {notice && <span role="alert" className="unknown-batch-inline-error">{notice}</span>}
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}>
      <DialogContent className="unknown-batch-dialog" showCloseButton={!busy}>
        <DialogHeader><DialogTitle>결과 불명 배치를 다시 시도할까요?</DialogTitle><DialogDescription>확인한 작업과 배치에만 재시도를 요청합니다.</DialogDescription></DialogHeader>
        <dl className="unknown-batch-details"><div><dt>작업</dt><dd>{job.id}<br />{job.connection_label ? `${job.connection_label} / ` : ''}{job.model}</dd></div>
          <div><dt>저장된 완료</dt><dd>{progress.completed_records.toLocaleString()} / {progress.total_records.toLocaleString()}건 · {progress.completed_calls.toLocaleString()}회 호출</dd></div>
          {parallel ? <div><dt>재시도 대상</dt><dd><ul className="unknown-batch-list">{job.unknown_recoveries!.map(item => <li key={`${item.phase}:${item.round}:${item.index}`}>{phases[item.phase] || item.phase} · 라운드 {item.round + 1} · 배치 {item.index + 1} / {item.total}{item.input_bytes != null && ` · ${item.input_bytes.toLocaleString()} bytes`}</li>)}</ul></dd></div> : <><div><dt>재시도 대상</dt><dd>{phases[ref.phase] || ref.phase} · 라운드 {ref.round + 1} · 배치 {ref.index + 1} / {ref.total}</dd></div>
          {ref.input_bytes != null && <div><dt>요청 크기</dt><dd>{ref.input_bytes.toLocaleString()} bytes</dd></div>}</>}
          {job.unknown_reason && reasons[job.unknown_reason] && <div><dt>확인되지 않은 이유</dt><dd>{reasons[job.unknown_reason]}</dd></div>}</dl>
        <p className="unknown-batch-warning">{parallel ? '원격 AI가 이 호출들을 이미 실행했을 수 있어 다시 시도하면 중복 계산이나 비용이 발생할 수 있습니다. 완료된 배치는 다시 호출하지 않고 유지합니다.' : '원격 AI가 이 호출을 이미 실행했을 수 있어 다시 시도하면 중복 계산이나 비용이 발생할 수 있습니다. 완료된 배치는 유지합니다.'}</p>
        {error && <p role="alert" className="ws-error">{error}</p>}
        <DialogFooter><Button type="button" variant="outline" disabled={busy} onClick={() => setOpen(false)}>취소</Button><Button type="button" disabled={busy} onClick={() => void confirm()}>{busy ? '요청 확인 중…' : parallel ? `확인하고 미확인 ${job.unknown_recoveries!.length}개 재시도` : '확인하고 이 배치 재시도'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </>
}
