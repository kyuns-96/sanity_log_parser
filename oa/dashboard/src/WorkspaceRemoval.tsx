import { useEffect, useRef, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { WorkspaceError, workspaceRequest } from './workspaceApi'

type Preview = { kind: 'REPORT' | 'VERSION'; id: string; report_count: number; record_count: number; review_count: number; can_delete: boolean; blockers: string[]; snapshot_sha256: string }
const blockers: Record<string, string> = {
  POR_VERSION_IN_USE: '현재 PoR 버전입니다. 다른 버전을 PoR로 지정한 뒤 삭제하세요.',
  VERSION_REFERENCED: '다른 POST 버전의 PRE 기준으로 사용 중입니다. 연결된 버전을 먼저 정리하세요.',
  DATA_DELETE_NOT_CONFIGURED: '원본과 이력 삭제에 필요한 운영 DB 설정을 확인해 주세요.',
}

export default function WorkspaceRemoval({ base, title, kind, onRemoved, onAccessDenied }: { base: string; title: string; kind: '버전' | '보고서'; onRemoved: () => void; onAccessDenied: () => void }) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const requestId = useRef('')
  const epoch = useRef(0)
  useEffect(() => () => { epoch.current++ }, [base])
  const denied = (e: unknown) => e instanceof WorkspaceError && e.status != null && [401, 403, 404].includes(e.status)
  async function inspect() {
    const current = epoch.current
    setOpen(true); setBusy(true); setError(''); setPreview(null); requestId.current = crypto.randomUUID()
    try { const value = await workspaceRequest<Preview>(`${base}/delete-preview`); if (current === epoch.current) setPreview(value) }
    catch (e) { if (current === epoch.current) { setError(e instanceof Error ? e.message : '삭제 범위를 확인하지 못했습니다.'); if (denied(e)) onAccessDenied() } }
    finally { if (current === epoch.current) setBusy(false) }
  }
  async function remove() {
    if (!preview?.can_delete || busy) return
    const current = epoch.current
    setBusy(true); setError('')
    try {
      await workspaceRequest(`${base}/delete`, { body: { request_id: requestId.current, snapshot_sha256: preview.snapshot_sha256 } })
      if (current === epoch.current) { setOpen(false); onRemoved() }
    } catch (e) {
      if (current !== epoch.current) return
      setError(e instanceof WorkspaceError && e.code === 'NETWORK_ERROR'
        ? '삭제 요청 결과를 확인하지 못했습니다. 다시 누르면 같은 요청의 결과를 확인합니다.'
        : e instanceof Error ? e.message : '삭제하지 못했습니다.')
      if (denied(e)) { setOpen(false); onAccessDenied() }
    } finally { if (current === epoch.current) setBusy(false) }
  }
  return <><Button size="sm" variant="ghost" aria-label={`${title} ${kind} 삭제`} onClick={() => void inspect()}><Trash2 size={14} />{kind} 삭제</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}><DialogContent showCloseButton={!busy}>
      <DialogHeader><DialogTitle>{kind === '보고서' ? '보고서를 삭제할까요?' : '버전을 삭제할까요?'}</DialogTitle><DialogDescription>{title}의 DB 원본·로그·AI 결과와 사람의 확정 이력을 영구 삭제합니다. 삭제한 자료는 복구할 수 없습니다.</DialogDescription></DialogHeader>
      {busy && !preview && <p role="status">삭제 범위를 확인하고 있습니다.</p>}
      {preview && <><dl className="ai-job-structure"><div><dt>대상 보고서</dt><dd>{preview.report_count.toLocaleString()}개</dd></div><div><dt>원천 로그</dt><dd>{preview.record_count.toLocaleString()}건</dd></div><div><dt>분류·판정 상태</dt><dd>{preview.review_count.toLocaleString()}건</dd></div></dl><p>진행 중인 관련 AI 작업도 제거합니다. 다른 보고서가 함께 사용하는 원본 조각은 마지막 참조를 삭제할 때 정리합니다.</p>{preview.blockers.map(code => <p className="ws-error" key={code}>{blockers[code] || code}</p>)}</>}
      {error && <p className="ws-error" role="alert">{error}</p>}
      <DialogFooter><Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>돌아가기</Button>{error && <Button variant="outline" disabled={busy} onClick={() => void inspect()}>삭제 범위 다시 확인</Button>}<Button variant="destructive" disabled={busy || !preview?.can_delete} onClick={() => void remove()}>{busy && preview ? '삭제 중…' : '원본과 이력 영구 삭제'}</Button></DialogFooter>
    </DialogContent></Dialog></>
}
