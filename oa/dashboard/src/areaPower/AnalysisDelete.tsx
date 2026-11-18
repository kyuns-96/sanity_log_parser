import { useState } from 'react'
import { z } from 'zod'
import { request, type Auth } from './api'
import { Button, Modal, useAction } from './components/workspace'

export function AnalysisDelete({ auth, id, active, mapping = false, onDeleted }: {
  auth: Auth; id: string; active: boolean; mapping?: boolean; onDeleted: (removed: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const action = useAction()
  const label = active ? '취소 후 기록 삭제' : '실행 기록 삭제'
  async function remove() {
    const value = await request(auth, '/analyses/' + encodeURIComponent(id) + '/delete',
      z.object({ removed: z.array(z.string()) }), {})
    setOpen(false)
    onDeleted(value.removed)
  }
  return <><Button size="sm" variant="outline" onClick={() => setOpen(true)}>{label}</Button>
    {open && <Modal title={label} onClose={() => { if (!action.busy) setOpen(false) }}>
      <p>{mapping ? '이 매핑 제안과 작업 이력을 삭제합니다. 승인한 매핑은 보존됩니다.' : '이 분석의 재실행 이력, 판정, 메일 요청과 작업 기록을 삭제합니다. 원본 CSV와 수치 비교 결과는 보존됩니다.'}</p>
      <p>진행 중인 재시도는 중단됩니다. 이미 전송된 요청은 외부 서버에서 끝날 수 있습니다. 삭제한 기록은 복구할 수 없습니다.</p>
      {action.feedback}<div className="mapping-actions"><Button variant="outline" disabled={action.busy} onClick={() => setOpen(false)}>닫기</Button>
        <Button disabled={action.busy} onClick={() => void action.run(remove, '')}>기록 삭제 확인</Button></div>
    </Modal>}
  </>
}
