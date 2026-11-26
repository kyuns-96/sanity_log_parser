import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { AlertTriangle, Eye, EyeOff, Pencil, ShieldAlert, Trash2 } from 'lucide-react'
import type { ProcessDefinition } from '@/features/access/accessApi'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import ProjectProcessField from './ProjectProcessField'

export type ManagedProject = {
  id: string
  name: string
  description?: string
  process?: string | null
  hidden?: boolean
  hidden_reason?: string
}

type DeletePreview = {
  project_id: string
  name: string
  hidden: boolean
  counts: {
    blocks: number
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

export interface ProjectManageDialogProps {
  project: ManagedProject
  processes: ProcessDefinition[]
  apiPrefix: '/api/atlas' | '/api/admin'
  initialTab?: 'edit' | 'visibility' | 'delete'
  api: <T>(path: string, body?: Record<string, unknown>) => Promise<T>
  reload: () => Promise<void>
  onClose: () => void
  onCompleted: (message: string, deleted?: boolean) => void
}

export default function ProjectManageDialog({
  project,
  processes,
  apiPrefix,
  initialTab = 'edit',
  api,
  reload,
  onClose,
  onCompleted,
}: ProjectManageDialogProps) {
  const [tab, setTab] = useState<'edit' | 'visibility' | 'delete'>(initialTab)
  const [name, setName] = useState(project.name)
  const [process, setProcess] = useState(project.process || '')
  const [description, setDescription] = useState(project.description || '')
  const [hideReason, setHideReason] = useState(project.hidden_reason || '')

  const [preview, setPreview] = useState<DeletePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [killSwitchOn, setKillSwitchOn] = useState(false)
  const [confirmName, setConfirmName] = useState('')
  const [deleteReason, setDeleteReason] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setName(project.name)
    setProcess(project.process || '')
    setDescription(project.description || '')
    setHideReason(project.hidden_reason || '')
    setKillSwitchOn(false)
    setConfirmName('')
    setDeleteReason('')
    setError('')
  }, [project.id, project.name, project.process, project.description, project.hidden_reason])

  useEffect(() => {
    if (tab !== 'delete') return
    let active = true
    setPreviewLoading(true)
    setError('')
    api<DeletePreview>(`${apiPrefix}/projects/${encodeURIComponent(project.id)}/delete-preview`)
      .then(data => { if (active) setPreview(data) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)) })
      .finally(() => { if (active) setPreviewLoading(false) })
    return () => { active = false }
  }, [tab, api, apiPrefix, project.id])

  async function submitEdit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    setBusy(true); setError('')
    try {
      await api(`${apiPrefix}/projects/${encodeURIComponent(project.id)}/update`, {
        name: name.trim(),
        description: description.trim(),
        process: process || null,
      })
      await reload()
      onCompleted(`프로젝트 ‘${name.trim()}’ 정보를 수정했습니다.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function submitVisibility(event: FormEvent) {
    event.preventDefault()
    const nextHidden = !project.hidden
    setBusy(true); setError('')
    try {
      await api(`${apiPrefix}/projects/${encodeURIComponent(project.id)}/visibility`, {
        hidden: nextHidden,
        reason: hideReason.trim(),
      })
      await reload()
      onCompleted(nextHidden ? `프로젝트 ‘${project.name}’을 숨김 처리했습니다.` : `프로젝트 ‘${project.name}’ 숨김을 해제했습니다.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function submitDelete(event: FormEvent) {
    event.preventDefault()
    if (!preview) return
    const force = !preview.is_empty && killSwitchOn
    if (confirmName.trim() !== preview.name) {
      setError('프로젝트 이름이 정확히 일치해야 삭제할 수 있습니다.')
      return
    }
    if (force && !deleteReason.trim()) {
      setError('강제 삭제 사유를 입력해 주세요.')
      return
    }
    setBusy(true); setError('')
    try {
      await api(`${apiPrefix}/projects/${encodeURIComponent(project.id)}/delete`, {
        force,
        confirm_name: confirmName.trim(),
        reason: deleteReason.trim(),
      })
      await reload()
      onCompleted(
        force
          ? `프로젝트 ‘${preview.name}’ 및 하위 데이터를 모두 영구 삭제했습니다.`
          : `빈 프로젝트 ‘${preview.name}’을 삭제했습니다.`,
        true,
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const nameMatches = Boolean(preview && confirmName.trim() === preview.name)
  const canSubmitEmptyDelete = Boolean(preview?.can_delete_empty && nameMatches)
  const canSubmitForceDelete = Boolean(preview?.can_force_delete && killSwitchOn && nameMatches && deleteReason.trim())

  return (
    <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>프로젝트 관리 · {project.name}</DialogTitle>
          <DialogDescription>
            프로젝트 정보 수정, 목록 숨김/복구 또는 삭제를 수행합니다. 내부 ID({project.id})는 수정 시 그대로 유지됩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap gap-2 border-b border-border pb-3">
          <Button type="button" size="sm" variant={tab === 'edit' ? 'default' : 'outline'} disabled={busy} onClick={() => { setTab('edit'); setError('') }}>
            <Pencil size={14} />정보 수정
          </Button>
          <Button type="button" size="sm" variant={tab === 'visibility' ? 'default' : 'outline'} disabled={busy} onClick={() => { setTab('visibility'); setError('') }}>
            {project.hidden ? <Eye size={14} /> : <EyeOff size={14} />}
            {project.hidden ? '숨김 해제(복구)' : '숨김 처리'}
          </Button>
          <Button type="button" size="sm" variant={tab === 'delete' ? 'destructive' : 'outline'} disabled={busy} onClick={() => { setTab('delete'); setError('') }}>
            <Trash2 size={14} />삭제 / Kill Switch
          </Button>
        </div>

        {error && <p className="am-error" role="alert">{error}</p>}

        {tab === 'edit' && (
          <form className="am-form am-dialog-form" onSubmit={submitEdit}>
            <label>
              프로젝트 이름
              <Input aria-label="프로젝트 이름" value={name} required maxLength={100} disabled={busy} onChange={e => setName(e.target.value)} />
              <small>표시 이름을 변경해도 기존 블록·버전·로그·Area/Power 연결은 유지됩니다.</small>
            </label>
            <ProjectProcessField
              processes={processes}
              value={process}
              onChange={setProcess}
              api={api}
              reload={reload}
              disabled={busy}
            />
            <label>
              설명
              <Textarea aria-label="설명" value={description} maxLength={1000} rows={3} disabled={busy} onChange={e => setDescription(e.target.value)} />
            </label>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={busy} onClick={onClose}>취소</Button>
              <Button type="submit" disabled={busy || !name.trim()}>{busy ? '저장 중…' : '변경 저장'}</Button>
            </DialogFooter>
          </form>
        )}

        {tab === 'visibility' && (
          <form className="am-form am-dialog-form" onSubmit={submitVisibility}>
            <p className="text-sm text-muted-foreground">
              {project.hidden
                ? '현재 숨김 상태인 프로젝트입니다. 숨김을 해제하면 일반 목록과 작업 화면에 다시 표시됩니다.'
                : '숨김 처리하면 기존 블록·로그·Area/Power 비교 데이터를 보존한 채 일반 목록에서 제외합니다. 관리자는 언제든 복구할 수 있습니다.'}
            </p>
            <label>
              {project.hidden ? '복구 사유 (선택)' : '숨김 사유 (선택)'}
              <Textarea
                aria-label="숨김 사유"
                value={hideReason}
                maxLength={500}
                rows={2}
                disabled={busy}
                placeholder={project.hidden ? '예: 재검토 필요로 복구' : '예: 테스트 프로젝트 정리 / 종료 보관'}
                onChange={e => setHideReason(e.target.value)}
              />
            </label>
            <DialogFooter>
              <Button type="button" variant="outline" disabled={busy} onClick={onClose}>취소</Button>
              <Button type="submit" disabled={busy}>
                {busy ? '처리 중…' : project.hidden ? '숨김 해제(복구)' : '숨김 처리'}
              </Button>
            </DialogFooter>
          </form>
        )}

        {tab === 'delete' && (
          <form className="am-form am-dialog-form" onSubmit={submitDelete}>
            {previewLoading || !preview ? (
              <p role="status" className="text-sm text-muted-foreground">삭제 대상 하위 데이터를 확인하고 있습니다…</p>
            ) : (
              <>
                <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                  <div className="mb-2 font-medium">하위 데이터 현황</div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    <span>블록: <strong>{preview.counts.blocks}</strong>건</span>
                    <span>버전: <strong>{preview.counts.versions}</strong>건</span>
                    <span>로그/리포트: <strong>{preview.counts.reports}</strong>건</span>
                    <span>제출 이력: <strong>{preview.counts.submissions}</strong>건</span>
                    <span>리뷰/클러스터: <strong>{preview.counts.reviews}</strong>건</span>
                    <span>Area/Power CSV: <strong>{preview.counts.area_uploads}</strong>건</span>
                    <span>Area/Power 레코드: <strong>{preview.counts.area_records}</strong>건</span>
                    <span>타 프로젝트 참조: <strong>{preview.counts.external_area_refs}</strong>건</span>
                  </div>
                </div>

                {!preview.delete_available && (
                  <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
                    <strong>DB DELETE 권한 없음:</strong> 현재 DB 계정은 물리 삭제 권한이 없어 완전 삭제할 수 없습니다. 데이터를 숨기려면 상단의 <strong>숨김 처리</strong> 탭을 사용해 주세요.
                  </div>
                )}

                {preview.delete_available && preview.is_empty && (
                  <>
                    <p className="text-sm text-muted-foreground">
                      하위 데이터가 없는 빈 프로젝트입니다. 아래에 프로젝트 이름(<strong>{preview.name}</strong>)을 입력하면 완전 삭제할 수 있습니다.
                    </p>
                    <label>
                      삭제 확인용 프로젝트 이름 입력
                      <Input
                        aria-label="삭제 확인용 프로젝트 이름"
                        value={confirmName}
                        disabled={busy}
                        placeholder={preview.name}
                        onChange={e => setConfirmName(e.target.value)}
                      />
                    </label>
                  </>
                )}

                {preview.delete_available && !preview.is_empty && (
                  <>
                    <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs space-y-2">
                      <div className="flex items-center gap-2 font-medium">
                        <AlertTriangle size={15} />
                        하위 데이터가 있어 일반 삭제가 차단되었습니다 (B안 기본 보호)
                      </div>
                      <p>데이터를 보존하면서 목록에서만 정리하려면 <strong>숨김 처리</strong>를 권장합니다.</p>
                      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => { setTab('visibility'); setError('') }}>
                        <EyeOff size={14} />숨김 처리 탭으로 이동
                      </Button>
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
                          Kill Switch: 하위 데이터 포함 강제 완전 삭제 활성화
                        </label>
                        {killSwitchOn && (
                          <div className="space-y-2 text-xs">
                            <p className="text-destructive">
                              주의: 이 프로젝트의 블록·버전·로그·리뷰·Area/Power CSV 및 권한까지 모두 영구 삭제되며 복구할 수 없습니다.
                              {preview.counts.external_area_refs > 0 && ` 다른 프로젝트의 기준 참조 ${preview.counts.external_area_refs}건도 함께 정리됩니다.`}
                            </p>
                            <label>
                              확인용 프로젝트 이름 (<strong>{preview.name}</strong>)
                              <Input
                                aria-label="강제 삭제 확인용 프로젝트 이름"
                                value={confirmName}
                                disabled={busy}
                                placeholder={preview.name}
                                onChange={e => setConfirmName(e.target.value)}
                              />
                            </label>
                            <label>
                              강제 삭제 사유 (필수)
                              <Textarea
                                aria-label="강제 삭제 사유"
                                value={deleteReason}
                                required
                                maxLength={1000}
                                rows={2}
                                disabled={busy}
                                placeholder="예: 잘못 생성된 테스트 프로젝트 및 더미 데이터 일괄 정리"
                                onChange={e => setDeleteReason(e.target.value)}
                              />
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
                <Button type="submit" variant="destructive" disabled={busy || !canSubmitEmptyDelete}>
                  {busy ? '삭제 중…' : '빈 프로젝트 완전 삭제'}
                </Button>
              ) : (
                <Button type="submit" variant="destructive" disabled={busy || !canSubmitForceDelete}>
                  {busy ? '영구 삭제 중…' : '하위 데이터 포함 완전 삭제'}
                </Button>
              )}
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
