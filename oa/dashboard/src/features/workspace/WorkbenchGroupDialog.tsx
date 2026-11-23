import { LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import type { LogMove } from './useLogDrag'
import type { GroupBrief } from './workspaceApi'

const n = (value: number) => value.toLocaleString('ko-KR')

interface WorkbenchGroupDialogProps {
  editor: 'rename' | 'split' | 'merge' | null
  busy: boolean
  writable: boolean
  error: string
  revision: number
  groupId: string
  groupName: string
  dirtyName: boolean
  newName: string
  editReason: string
  splitMove: LogMove | null
  selectedGroups: Map<string, GroupBrief>
  mergeGroups: GroupBrief[]
  mergeRecordCount: number
  mergeConflict: boolean
  mergeDestination: string
  onMergeDestinationChange: (value: string) => void
  onGroupNameChange: (value: string) => void
  onNewNameChange: (value: string) => void
  onEditReasonChange: (value: string) => void
  onClose: () => void
  onConfirm: () => void
}

export function WorkbenchGroupDialog({
  editor,
  busy,
  writable,
  error,
  groupName,
  dirtyName,
  newName,
  editReason,
  splitMove,
  selectedGroups,
  mergeGroups,
  mergeRecordCount,
  mergeConflict,
  mergeDestination,
  onMergeDestinationChange,
  onGroupNameChange,
  onNewNameChange,
  onEditReasonChange,
  onClose,
  onConfirm,
}: WorkbenchGroupDialogProps) {
  return (
    <Dialog open={!!editor} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent
        className="ws-group-dialog"
        onEscapeKeyDown={event => { if (busy) event.preventDefault() }}
        onInteractOutside={event => { if (busy) event.preventDefault() }}
      >
        <DialogHeader>
          <DialogTitle>{editor === 'rename' ? '그룹 이름 변경' : editor === 'merge' ? '선택 그룹 병합' : '새 그룹으로 분리'}</DialogTitle>
          <DialogDescription>
            {editor === 'rename'
              ? '간단한 그룹 설명을 입력하세요. 저장 시 원본 rule code가 이름 맨 앞에 붙습니다.'
              : editor === 'merge'
                ? `선택한 ${n(selectedGroups.size)}개 그룹의 전체 로그 ${n(mergeRecordCount)}건을 하나로 합칩니다. 같은 rule code끼리만 병합할 수 있습니다.`
                : `선택한 ${n(splitMove?.ids.length || 0)}건을 새 그룹으로 옮깁니다. 서로 다른 rule code는 같은 그룹에 넣을 수 없습니다.`}
          </DialogDescription>
        </DialogHeader>
        {editor === 'merge' && <>
          <label>병합 후 유지할 그룹
            <select autoFocus aria-label="병합 후 유지할 그룹" value={mergeDestination} disabled={busy} onChange={event => onMergeDestinationChange(event.target.value)}>
              {mergeGroups.map(group => <option key={group.id} value={group.id}>{group.label} · {n(group.record_count)}건</option>)}
            </select>
          </label>
          <p className="ws-muted">선택한 그룹의 이름과 ID를 유지합니다. 병합 후 분류 근거를 검토하고 분류를 다시 확정하세요. 이전 구성·근거·판정은 이력으로 보존됩니다.</p>
        </>}
        {editor !== 'merge' && (
          <label>{editor === 'rename' ? '그룹 이름' : '새 그룹 이름'}
            <Input
              autoFocus
              aria-label={editor === 'rename' ? '선택 그룹 이름' : '분리할 새 그룹 이름'}
              maxLength={120}
              value={editor === 'rename' ? groupName : newName}
              disabled={busy}
              onChange={event => { if (editor === 'rename') onGroupNameChange(event.target.value); else onNewNameChange(event.target.value) }}
            />
          </label>
        )}
        <label>변경 사유 <span>(선택)</span>
          <textarea aria-label="이번 변경 사유" value={editReason} maxLength={2000} disabled={busy} placeholder="입력하지 않으면 이동·이름 변경 내역이 기록됩니다." onChange={event => onEditReasonChange(event.target.value)} />
        </label>
        {error && <p className="ws-error" role="alert">{error}</p>}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>취소</Button>
          <Button
            disabled={
              !writable
              || editor === 'rename' && (!groupName.trim() || !dirtyName)
              || editor === 'merge' && (selectedGroups.size < 2 || !selectedGroups.has(mergeDestination) || mergeConflict)
              || editor === 'split' && (!newName.trim() || !splitMove?.ids.length)
            }
            onClick={onConfirm}
          >
            {busy ? <LoaderCircle size={14} className="animate-spin" /> : null}
            {editor === 'rename' ? '그룹 이름 저장' : editor === 'merge' ? '그룹 병합' : '선택 로그를 새 그룹으로 분리'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
