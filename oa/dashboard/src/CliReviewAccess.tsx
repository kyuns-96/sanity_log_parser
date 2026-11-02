import { useEffect, useState } from 'react'
import { Terminal } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './components/ui/dialog'
import { workspaceRequest as request } from './workspaceApi'
import './CliReviewAccess.css'

type Session = { id: string; expires_at: number; created_at: string; revoked?: boolean; secret?: string }

export default function CliReviewAccess({ base, onError }: { base: string; onError: (error: unknown) => void }) {
  const [open, setOpen] = useState(false)
  const [sessions, setSessions] = useState<Session[]>([])
  const [created, setCreated] = useState<Session | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    if (!open) { setCreated(null); return }
    const controller = new AbortController()
    request<{ items: Session[] }>(`${base}/cli-sessions`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setSessions(value.items)
    }).catch(value => { if (!controller.signal.aborted) { setError(value.message); onError(value) } })
    return () => controller.abort()
  }, [base, open, reload])
  async function mutate(id?: string) {
    setBusy(true); setError('')
    try {
      if (id) {
        await request(`${base}/cli-sessions/${id}/revoke`, { body: {} })
        if (created?.id === id) setCreated(null)
      } else {
        const value = await request<Session>(`${base}/cli-sessions`, { body: {} })
        setCreated(value)
      }
      setReload(value => value+1)
    } catch (value) {
      setError(value instanceof Error ? value.message : 'CLI 연결을 변경하지 못했습니다. 목록을 다시 확인하세요.')
      onError(value)
    } finally { setBusy(false) }
  }
  return <>
    <Button variant="outline" size="sm" onClick={() => { setError(''); setOpen(true) }}><Terminal size={15} />VWP CLI 검토</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}>
      <DialogContent className="ws-cli-access"><DialogHeader><DialogTitle>VWP CLI에서 검토</DialogTitle><DialogDescription>이 보고서의 로그 분류와 그룹 판정을 VWP 터미널에서 계속합니다. 저장한 결과는 웹과 같은 검토 이력에 반영됩니다.</DialogDescription></DialogHeader>
        <p>연결은 8시간 동안 유효하며 명령 실행 시 현재 담당 권한을 확인합니다. 연결키는 발급 직후 한 번만 표시됩니다.</p>
        {error && <p role="alert">{error}</p>}
        {created?.secret && <section className="ws-cli-created" aria-label="새 CLI 연결">
          <label>VWP에서 실행할 명령<textarea aria-label="CLI 연결 명령" readOnly rows={3} value={`sanity review connect ${created.id} --expires-at ${created.expires_at}`} /></label>
          <label>연결키<textarea aria-label="CLI 연결키" readOnly autoComplete="off" spellCheck={false} rows={2} value={created.secret} /></label>
          <p>명령 실행 후 연결키 입력란에 붙여 넣으세요. 연결키를 명령 인자·공유 문서에 넣지 마세요. 연결 후 <code>sanity review shell</code>로 검토합니다.</p>
        </section>}
        <Button disabled={busy || !!created} onClick={() => void mutate()}>CLI 연결키 발급</Button>
        <div className="ws-cli-sessions" aria-label="내 CLI 연결 목록">{sessions.filter(session => !session.revoked).map(session => <div key={session.id}><span><code>{session.id}</code><small>{new Date(session.expires_at*1000).toLocaleString('ko-KR')} 만료</small></span><Button variant="outline" disabled={busy} onClick={() => void mutate(session.id)} aria-label={`CLI 연결 ${session.id} 회수`}>회수</Button></div>)}</div>
      </DialogContent>
    </Dialog>
  </>
}
