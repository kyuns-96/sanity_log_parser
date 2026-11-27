import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { AlertTriangle, ShieldAlert, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type BlockDeletePreview = {
  project_id: string
  block_id: string
  name: string
  counts: {
    versions: number
    reports: number
    submissions: number
    reviews: number
    area_uploads: number
    area_records: number
    external_area_refs: number
  }
  is_empty: boolean
  delete_available: boolean
  kill_switch_allowed: boolean
  can_delete_empty: boolean
  can_force_delete: boolean
  blockers: string[]
}

export interface BlockDeleteDialogProps {
  projectId: string
  projectName: string
  block: { id: string; name: string }
  apiPrefix: '/api/atlas' | '/api/admin'
  api: <T>(path: string, body?: Record<string, unknown>) => Promise<T>
  reload: () => Promise<void>
  onClose: () => void
  onDeleted: (message: string) => void
}

export default function BlockDeleteDialog({
  projectId,
  projectName,
  block,
  apiPrefix,
  api,
  reload,
  onClose,
  onDeleted,
}: BlockDeleteDialogProps) {
  const [preview, setPreview] = useState<BlockDeletePreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [killSwitchOn, setKillSwitchOn] = useState(false)
  const [confirmName, setConfirmName] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setKillSwitchOn(false)
    setConfirmName('')
    setReason('')
    api<BlockDeletePreview>(
      `${apiPrefix}/projects/${encodeURIComponent(projectId)}/blocks/${encodeURIComponent(block.id)}/delete-preview`,
    )
      .then(data => { if (active) setPreview(data) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [api, apiPrefix, projectId, block.id])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!preview) return
    const force = !preview.is_empty && killSwitchOn
    if (confirmName.trim() !== preview.name) {
      setError('블록 이름이 정확히 일치해야 삭제할 수 있습니다.')
      return
    }
    if (force && !reason.trim()) {
      setError('강제 삭제 사유를 입력해 주세요.')
      return
    }
    setBusy(true); setError('')
    try {
      await api(
        `${apiPrefix}/projects/${encodeURIComponent(projectId)}/blocks/${encodeURIComponent(block.id)}/delete`,
        { force, confirm_name: confirmName.trim(), reason: reason.trim() },
      )
      await reload()
      onDeleted(
        force
          ? `블록 ‘${preview.name}’ 및 하위 데이터를 모두 영구 삭제했습니다.`
          : `빈 블록 ‘${preview.name}’을 삭제했습니다.`,
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const nameMatches = Boolean(preview && confirmName.trim() === preview.name)
  const canDeleteEmpty = Boolean(preview?.can_delete_empty && nameMatches)
  const canForceDelete = Boolean(preview?.can_force_delete && killSwitchOn && nameMatches && reason.trim())

  return (
    <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Trash2 size={16} />블록 삭제 · {block.name}</DialogTitle>
          <DialogDescription>
            프로젝트 ‘{projectName}’의 블록을 삭제합니다. 빈 블록은 바로 삭제 가능하며, 하위 데이터가 있으면 Kill Switch를 켜야 합니다.
          </DialogDescription>
        </DialogHeader>

        {error && <p className="am-error" role="alert">{error}</p>}

        <form className="am-form am-dialog-form" onSubmit={submit}>
          {loading || !preview ? (
            <p role="status" className="text-sm text-muted-foreground">삭제 대상 하위 데이터를 확인하고 있습니다…</p>
          ) : (
            <>
              <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                <div className="mb-2 font-medium">블록 하위 데이터 현황</div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <span>버전: <strong>{preview.counts.versions}</strong>건</span>
                  <span>로그/리포트: <strong>{preview.counts.reports}</strong>건</span>
                  <span>제출 이력: <strong>{preview.counts.submissions}</strong>건</span>
                  <span>리뷰/클러스터: <strong>{preview.counts.reviews}</strong>건</span>
                  <span>Area/Power CSV: <strong>{preview.counts.area_uploads}</strong>건</span>
                  <span>Area/Power 레코드: <strong>{preview.counts.area_records}</strong>건</span>
                  <span>타 블록/프로젝트 참조: <strong>{preview.counts.external_area_refs}</strong>건</span>
                </div>
              </div>

              {!preview.delete_available && (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
                  <strong>DB DELETE 권한 없음:</strong> 현재 DB 계정은 물리 삭제 권한이 없어 블록을 삭제할 수 없습니다.
                </div>
              )}

              {preview.delete_available && preview.is_empty && (
                <>
                  <p className="text-sm text-muted-foreground">
                    하위 데이터가 없는 빈 블록입니다. 아래에 블록 이름(<strong>{preview.name}</strong>)을 입력하면 완전 삭제할 수 있습니다.
                  </p>
                  <label>
                    삭제 확인용 블록 이름 입력
                    <Input aria-label="삭제 확인용 블록 이름" value={confirmName} disabled={busy} placeholder={preview.name} onChange={e => setConfirmName(e.target.value)} />
                  </label>
                </>
              )}

              {preview.delete_available && !preview.is_empty && (
                <>
                  <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs space-y-1">
                    <div className="flex items-center gap-2 font-medium">
                      <AlertTriangle size={15} />
                      하위 데이터가 있어 일반 삭제가 차단되었습니다 (B안 기본 보호)
                    </div>
                    <p>하위 버전·로그·CSV까지 모두 지우려면 아래 Kill Switch를 활성화하세요.</p>
                  </div>

                  {!preview.kill_switch_allowed ? (
                    <div className="rounded-md border border-border bg-muted/40 p-3 text-xs">
                      서버 설정(<code>ATLAS_DISABLE_FORCE_PROJECT_DELETE</code>)에 의해 강제 삭제(Kill Switch)가 비활성화되어 있습니다.
                    </div>
                  ) : (
                    <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3 space-y-3">
                      <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-destructive">
                        <input
                          type="checkbox"
                          checked={killSwitchOn}
                          disabled={busy}
                          onChange={e => { setKillSwitchOn(e.target.checked); setError('') }}
                        />
                        <ShieldAlert size={15} />
                        Kill Switch: 하위 데이터 포함 블록 강제 완전 삭제 활성화
                      </label>
                      {killSwitchOn && (
                        <div className="space-y-2 text-xs">
                          <p className="text-destructive">
                            주의: 이 블록의 모든 버전·로그·리뷰·Area/Power CSV 및 비교 결과가 영구 삭제되며 복구할 수 없습니다.
                          </p>
                          <label>
                            확인용 블록 이름 (<strong>{preview.name}</strong>)
                            <Input aria-label="강제 삭제 확인용 블록 이름" value={confirmName} disabled={busy} placeholder={preview.name} onChange={e => setConfirmName(e.target.value)} />
                          </label>
                          <label>
                            강제 삭제 사유 (필수)
                            <Textarea aria-label="강제 삭제 사유" value={reason} required maxLength={1000} rows={2} disabled={busy} placeholder="예: 잘못 등록된 테스트 블록 및 데이터 일괄 정리" onChange={e => setReason(e.target.value)} />
                          </label>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>취소</Button>
            {preview?.is_empty ? (
              <Button type="submit" variant="destructive" disabled={busy || !canDeleteEmpty}>
                {busy ? '삭제 중…' : '빈 블록 완전 삭제'}
              </Button>
            ) : (
              <Button type="submit" variant="destructive" disabled={busy || !canForceDelete}>
                {busy ? '영구 삭제 중…' : '하위 데이터 포함 완전 삭제'}
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
