import { useEffect, useMemo, useState } from 'react'
import { NotebookPen } from 'lucide-react'
import { date, recordSchema, request, revision, text, type Auth, type TrackingRecord } from './api'
import { Button, useAction } from './components/workspace'

// Free-text notes the AI triage reads, and the key instances it always analyses.
// Saving creates a new revision; finished analyses keep the text they used.
export function NotesPanel({ auth, canWrite, projectId, block, netVers, currentNet, baselineProject, records, onSaved }: {
  auth: Auth
  canWrite: boolean
  projectId: string
  block?: { areaId: string; name: string }
  netVers: string[]
  currentNet: string
  baselineProject?: { areaId: string; name: string } | null
  records: TrackingRecord[]
  onSaved: () => Promise<void>
}) {
  const action = useAction()
  const [net, setNet] = useState(currentNet)
  useEffect(() => { if (currentNet) setNet(currentNet) }, [currentNet])
  const scoped = useMemo(() => records.filter(row => row.project_id === projectId && !!block && row.block_id === block.areaId)
    .sort((a, b) => text(b.created_at).localeCompare(text(a.created_at))), [records, projectId, block?.areaId])
  const note = (match: (payload: TrackingRecord['payload']) => boolean) => scoped.find(row => row.record_type === 'change' && row.payload.archived !== true && match(row.payload))
  const netNote = note(payload => payload.note_kind === 'net_ver' && payload.net_ver === net)
  const projectNote = baselineProject ? note(payload => payload.note_kind === 'project_change' && payload.baseline_project_id === baselineProject.areaId) : undefined
  const watch = scoped.find(row => row.record_type === 'target' && row.payload.watch === true)
  const watchPaths = Array.isArray(watch?.payload.paths) ? watch.payload.paths.map(text) : []
  const [netText, setNetText] = useState('')
  const [projectText, setProjectText] = useState('')
  const [watchText, setWatchText] = useState('')
  useEffect(() => setNetText(text(netNote?.payload.text)), [netNote?.id, net])
  useEffect(() => setProjectText(text(projectNote?.payload.text)), [projectNote?.id, baselineProject?.areaId])
  useEffect(() => setWatchText(watchPaths.join('\n')), [watch?.id])

  async function save(kind: 'change' | 'target', existing: TrackingRecord | undefined, payload: Record<string, unknown>) {
    if (!block) throw new Error('현재 Block을 먼저 선택하세요.')
    await request(auth, '/config', recordSchema, { record_type: kind, project_id: projectId, block_id: block.areaId,
      ...(existing ? { entity_id: existing.entity_id, expected_row_version: revision(existing.row_version) } : {}), payload })
    await onSaved()
  }
  const saveNote = (existing: TrackingRecord | undefined, value: string, scope: Record<string, unknown>, archived = false) => {
    const body = archived ? text(existing?.payload.text) : value.trim()
    if (!body) throw new Error('메모 내용을 입력하세요.')
    return save('change', existing, { text: body, ...scope, archived })
  }
  const paths = [...new Set(watchText.split('\n').map(line => line.trim()).filter(Boolean))]
  const saveWatch = () => {
    if (!paths.length && !watch) throw new Error('주요 instance 경로를 한 줄에 하나씩 입력하세요.')
    // An empty list keeps the record but stops watching (a target needs paths).
    return save('target', watch, { name: '주요 instance', scope: 'node', metrics: [], bindings: [], paths: paths.length ? paths : watchPaths, watch: paths.length > 0 })
  }
  const meta = (row?: TrackingRecord) => row ? `마지막 저장 ${date(row.created_at)} · r${row.revision}` : '저장된 메모 없음'
  const disabled = action.busy || !canWrite || !block

  return <details className="panel notes-panel">
    <summary><NotebookPen size={14} aria-hidden="true" />AI 분석용 메모 · 주요 instance{block ? ` · ${block.name}` : ''}</summary>
    {action.feedback}
    {!block ? <p className="muted">현재 Block을 선택하면 그 Block의 메모와 주요 instance를 편집할 수 있습니다.</p> : <div className="notes-grid">
      <section>
        <h3>NET_VER 업데이트 메모</h3>
        <label className="field"><span>NET_VER</span><select value={net} onChange={event => setNet(event.target.value)}><option value="">NET_VER 선택</option>{netVers.map(value => <option key={value}>{value}</option>)}</select></label>
        <textarea value={netText} disabled={disabled || !net} onChange={event => setNetText(event.target.value)} rows={6} placeholder={'이 NET_VER에서 바뀐 내용을 자유롭게 적으세요.\n예) u_dma 버퍼 확장으로 logic area 20~30% 증가 예상. u_cpu는 setup 개선을 위해 LVT 사용을 늘림.'} />
        <div className="notes-actions"><small>{meta(netNote)}</small>{netNote && <Button variant="ghost" size="sm" disabled={disabled} onClick={() => action.run(() => saveNote(netNote, '', { note_kind: 'net_ver', net_ver: net }, true), '메모를 삭제했습니다.')}>삭제</Button>}<Button size="sm" disabled={disabled || !net || !netText.trim()} onClick={() => action.run(() => saveNote(netNote, netText, { note_kind: 'net_ver', net_ver: net }))}>저장</Button></div>
      </section>
      <section>
        <h3>프로젝트 변경 메모</h3>
        {baselineProject ? <>
          <p className="muted">기준 프로젝트 {baselineProject.name}와 비교할 때 고려할 이 Block의 변경 사항입니다.</p>
          <textarea value={projectText} disabled={disabled} onChange={event => setProjectText(event.target.value)} rows={6} placeholder={'프로젝트가 바뀌면서 달라진 점을 적으세요.\n예) 채널 수가 두 배로 늘어 u_fabric 하위 area 증가. SRAM 구성 변경.'} />
          <div className="notes-actions"><small>{meta(projectNote)}</small>{projectNote && <Button variant="ghost" size="sm" disabled={disabled} onClick={() => action.run(() => saveNote(projectNote, '', { note_kind: 'project_change', baseline_project_id: baselineProject.areaId }, true), '메모를 삭제했습니다.')}>삭제</Button>}<Button size="sm" disabled={disabled || !projectText.trim()} onClick={() => action.run(() => saveNote(projectNote, projectText, { note_kind: 'project_change', baseline_project_id: baselineProject.areaId }))}>저장</Button></div>
        </> : <p className="muted">기준 프로젝트를 저장하면 그 프로젝트와의 변경 메모를 적을 수 있습니다.</p>}
      </section>
      <section>
        <h3>주요 instance</h3>
        <p className="muted">변화가 작아도 항상 분석합니다. 현재 프로젝트의 전체 경로를 한 줄에 하나씩 적으세요.</p>
        <textarea value={watchText} disabled={disabled} onChange={event => setWatchText(event.target.value)} rows={6} spellCheck={false} placeholder={'top/u_core/u_alu\ntop/u_dma'} />
        <div className="notes-actions"><small>{paths.length}개 · {meta(watch)}</small><Button size="sm" disabled={disabled} onClick={() => action.run(saveWatch)}>저장</Button></div>
      </section>
    </div>}
  </details>
}
