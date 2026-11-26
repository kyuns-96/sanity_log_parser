import { useEffect, useRef, useState, type RefObject } from 'react'
import { Download, Plus, Trash2, Upload as UploadIcon } from 'lucide-react'
import { date, recordSchema, request, type Auth, type TrackingRecord, type Upload } from '../api'
import { Button, Field } from '../components/workspace'
import { mappingCsv, readMappingCsv, validateMappingDraft, type MappingDraft } from '../mappingCsv'
import { MappingSuggestions } from '../MappingSuggestions'
import type { Hierarchy } from './hierarchyUtils'
import { PathPicker } from './PathPicker'

interface HierarchyLoadState {
  id: string
  data: Hierarchy | null
  error: string
  loading: boolean
}

interface CorrespondencePanelProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  auth: Auth
  canWrite: boolean
  busy: boolean
  runAction: (action: () => Promise<void>, success?: string) => Promise<void>
  beginMutation: () => AbortSignal
  alive: RefObject<boolean>
  selectedCurrent: Upload
  selectedBefore: Upload
  beforeHierarchy: HierarchyLoadState
  currentHierarchy: HierarchyLoadState
  maxHierarchyDepth: number
  mappings: TrackingRecord[]
  mappingId: string
  onMappingIdChange: (id: string) => void
  mappingDraft: MappingDraft[]
  onMappingDraftChange: (draft: MappingDraft[]) => void
  onReloadHierarchy: () => void
  onRefresh: () => Promise<void>
}

export function CorrespondencePanel({
  open,
  onOpenChange,
  auth,
  canWrite,
  busy,
  runAction,
  beginMutation,
  alive,
  selectedCurrent,
  selectedBefore,
  beforeHierarchy,
  currentHierarchy,
  maxHierarchyDepth,
  mappings,
  mappingId,
  onMappingIdChange,
  mappingDraft,
  onMappingDraftChange,
  onReloadHierarchy,
  onRefresh,
}: CorrespondencePanelProps) {
  const detailsRef = useRef<HTMLDetailsElement>(null)
  const mappingFile = useRef<HTMLInputElement>(null)
  const mappingInputs = useRef('')
  mappingInputs.current = `${selectedBefore.id}:${selectedCurrent.id}`

  const [baselinePath, setBaselinePath] = useState('')
  const [currentPath, setCurrentPath] = useState('')
  const [mappingScope, setMappingScope] = useState<'node' | 'subtree'>('subtree')
  const [mappingDraftPage, setMappingDraftPage] = useState(0)

  useEffect(() => {
    setBaselinePath('')
    setCurrentPath('')
    setMappingDraftPage(0)
  }, [selectedCurrent.id, selectedBefore.id])

  useEffect(() => {
    if (open) {
      window.requestAnimationFrame(() => detailsRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
    }
  }, [open])

  const validBaselinePath = !beforeHierarchy.loading && !beforeHierarchy.error && !!beforeHierarchy.data?.items.some(row => row.path === baselinePath)
  const validCurrentPath = !currentHierarchy.loading && !currentHierarchy.error && !!currentHierarchy.data?.items.some(row => row.path === currentPath)

  async function saveMapping() {
    if (!mappingDraft.length) throw new Error('양쪽 실행과 대응 경로를 선택하세요.')
    if (!beforeHierarchy.data || !currentHierarchy.data || beforeHierarchy.loading || currentHierarchy.loading || beforeHierarchy.error || currentHierarchy.error) {
      throw new Error('양쪽 경로 조회를 완료한 뒤 저장하세요.')
    }
    validateMappingDraft(mappingDraft, new Set(beforeHierarchy.data.items.map(row => row.path)), new Set(currentHierarchy.data.items.map(row => row.path)))
    const payload: Record<string, unknown> = {
      rules: mappingDraft.map(row => ({ kind: 'one_to_one', scope: row.scope, baseline_paths: [row.baselinePath], current_paths: [row.currentPath] })),
    }
    for (const [side, upload] of [['current', selectedCurrent], ['baseline', selectedBefore]] as const) {
      for (const key of ['project_id', 'block_id', 'version_id'] as const) payload[`${side}_${key}`] = upload[key]
    }
    const input = mappingInputs.current
    const value = await request(auth, '/config', recordSchema, {
      record_type: 'mapping',
      project_id: selectedCurrent.project_id,
      block_id: selectedCurrent.block_id,
      version_id: selectedCurrent.version_id,
      payload,
    }, beginMutation())
    if (!alive.current || input !== mappingInputs.current) return
    onMappingIdChange(value.id)
    onMappingDraftChange([])
    await onRefresh()
  }

  function exportMappingTemplate() {
    const rows = mappingDraft.length ? mappingDraft : baselinePath || currentPath ? [{ baselinePath, currentPath, scope: mappingScope }] : []
    const url = URL.createObjectURL(new Blob([mappingCsv(rows)], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'instance_mapping_template.csv'
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  async function importMapping(file: File) {
    if (!beforeHierarchy.data || !currentHierarchy.data || beforeHierarchy.loading || currentHierarchy.loading || beforeHierarchy.error || currentHierarchy.error) {
      throw new Error('양쪽 실행을 선택하고 경로 조회를 완료하세요.')
    }
    const input = mappingInputs.current
    let source: string
    try {
      source = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
    } catch {
      throw new Error('UTF-8 CSV 파일을 읽지 못했습니다. UTF-8 CSV로 저장한 뒤 다시 선택하세요.')
    }
    if (!alive.current) return
    if (input !== mappingInputs.current) throw new Error('실행 선택이 바뀌었습니다. 현재 양쪽 실행에서 CSV를 다시 가져오세요.')
    const rows = readMappingCsv(source)
    validateMappingDraft(rows, new Set(beforeHierarchy.data.items.map(row => row.path)), new Set(currentHierarchy.data.items.map(row => row.path)))
    onMappingDraftChange(rows)
    setMappingDraftPage(0)
    onMappingIdChange('')
  }

  const draftPageSize = 50
  const draftLastPage = Math.max(0, Math.ceil(mappingDraft.length / draftPageSize) - 1)
  const draftPage = Math.min(mappingDraftPage, draftLastPage)
  const draftStart = draftPage * draftPageSize

  return (
    <details ref={detailsRef} open={open} onToggle={event => onOpenChange(event.currentTarget.open)} className="correspondence-settings">
      <summary>구조 대응 · AI 자동 매핑 설정 (다른 Block / hierarchy 연결)</summary>
      <p className="muted">규칙이 없는 instance는 양쪽 design 루트 아래 상대 경로가 같으면 자동으로 대응합니다. 이름이나 위치가 달라진 경로만 규칙을 추가하세요. 경로 일부를 검색하거나 전체 경로를 붙여넣고, ‘하위 트리 전체’는 상대 경로가 같은 자식까지 함께 대응합니다.</p>
      <MappingSuggestions
        key={JSON.stringify([selectedCurrent.id, selectedBefore.id, mappingId, mappingDraft])}
        auth={auth}
        current={selectedCurrent.id}
        baseline={selectedBefore.id}
        mapping={mappingId}
        draft={mappingDraft}
        maxAvailableDepth={maxHierarchyDepth}
        disabled={!canWrite || busy}
        onInspect={(before, after) => { setBaselinePath(before); setCurrentPath(after); setMappingScope('node') }}
        onSaved={async id => { onMappingIdChange(id); onMappingDraftChange([]); setMappingDraftPage(0); await onRefresh() }}
      />
      <div className="mapping-actions">
        <Button variant="outline" onClick={exportMappingTemplate}><Download size={15} />Export template</Button>
        <Button variant="outline" disabled={busy || !canWrite || beforeHierarchy.loading || currentHierarchy.loading || !!beforeHierarchy.error || !!currentHierarchy.error || !beforeHierarchy.data || !currentHierarchy.data} onClick={() => mappingFile.current?.click()}><UploadIcon size={15} />Import mapping CSV</Button>
        <input ref={mappingFile} type="file" accept=".csv,text/csv" hidden aria-label="Mapping CSV 파일" onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void runAction(() => importMapping(file), 'CSV 대응을 불러왔습니다. 내용을 확인한 뒤 대응 저장을 누르세요.') }} />
      </div>
      <p className="muted">UTF-8 CSV · baseline_path,current_path,scope · 한 행에 1:1 대응 하나, scope는 node 또는 subtree입니다. 템플릿에는 작성 중인 대응 또는 선택한 경로가 포함됩니다. 가져오기는 작성 중인 목록 전체를 교체하며, 모든 행을 검증한 뒤 적용합니다. 대응 저장을 눌러야 서버에 저장됩니다.</p>
      <div className="mapping-controls">
        <PathPicker key={`baseline:${selectedBefore.id}`} label="기준 경로" hierarchy={beforeHierarchy} value={baselinePath} onChange={setBaselinePath} onReload={onReloadHierarchy} disabled={busy} />
        <PathPicker key={`current:${selectedCurrent.id}`} label="현재 경로" hierarchy={currentHierarchy} value={currentPath} onChange={setCurrentPath} onReload={onReloadHierarchy} disabled={busy} />
      </div>
      <div className="mapping-actions">
        <Field label="대응 범위">
          <select value={mappingScope} onChange={event => setMappingScope(event.target.value as 'node' | 'subtree')}>
            <option value="subtree">하위 트리 전체</option>
            <option value="node">선택한 instance만</option>
          </select>
        </Field>
        <Button variant="outline" disabled={busy || !canWrite || !validBaselinePath || !validCurrentPath} onClick={() => { onMappingDraftChange([...mappingDraft, { baselinePath, currentPath, scope: mappingScope }]); setBaselinePath(''); setCurrentPath('') }}><Plus size={15} />대응 추가</Button>
      </div>
      {mappingDraft.length > 0 && (
        <div className="draft-mappings">
          {mappingDraft.slice(draftStart, draftStart + draftPageSize).map((row, position) => {
            const index = draftStart + position
            return (
              <div key={index}>
                <span>{index + 1}. {row.baselinePath} → {row.currentPath} · {row.scope}</span>
                <Button variant="ghost" disabled={busy || !canWrite} aria-label={`${index + 1}번 대응 삭제`} onClick={() => onMappingDraftChange(mappingDraft.filter((_, pos) => pos !== index))}><Trash2 size={15} /></Button>
              </div>
            )
          })}
          <div className="mapping-actions">
            {mappingDraft.length > draftPageSize && (
              <>
                <Button variant="outline" disabled={busy || !draftPage} onClick={() => setMappingDraftPage(draftPage - 1)}>이전 대응</Button>
                <small>{draftStart + 1}–{Math.min(draftStart + draftPageSize, mappingDraft.length)} / {mappingDraft.length.toLocaleString()}</small>
                <Button variant="outline" disabled={busy || draftPage === draftLastPage} onClick={() => setMappingDraftPage(draftPage + 1)}>다음 대응</Button>
              </>
            )}
            <Button disabled={busy || !canWrite} onClick={() => runAction(saveMapping)}>대응 {mappingDraft.length}개 저장</Button>
          </div>
        </div>
      )}
      <Field label="저장된 구조 대응">
        <select value={mappingId} onChange={event => onMappingIdChange(event.target.value)}>
          <option value="">대응 미선택 · 비교 불가</option>
          {mappings.map(row => <option key={row.id} value={row.id}>{date(row.created_at)} · 규칙 {Array.isArray(row.payload.rules) ? row.payload.rules.length : 0}개 · r{row.revision}</option>)}
        </select>
      </Field>
    </details>
  )
}
