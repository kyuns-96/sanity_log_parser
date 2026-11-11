import { useEffect, useState } from 'react'
import { Plus, SquareArrowOutUpRight } from 'lucide-react'
import { allRecords, date, jsonObject, nameOf, pretty, recordSchema, request, revision, text, type AreaAccess, type Auth, type Filters, type Payload, type TrackingRecord } from './api'
import { Badge, Button, CopyButton, Empty, Field, Json, JsonEditor, Modal, RecordOptions, Submit, prevent, useAction } from './components/workspace'

export interface ConfigProps { auth: Auth; records: TrackingRecord[]; filters: Filters; refresh: () => Promise<void> }
export function ScopeFields({ records, value, onChange, showSnapshot = true }: { records: TrackingRecord[]; value: Filters; onChange?: (value: Filters) => void; showSnapshot?: boolean }) {
  const scopeLabel = (type: string, id: string) => {
    const record = records.find(item => item.record_type === type && item.id === id)
    return id ? `${record ? nameOf(record) : type} · ${id}` : '공통 범위 미지정'
  }
  return <div className="form-grid">
    <Field label="프로젝트 · 공통 선택 범위"><input readOnly value={scopeLabel('project', value.project_id)} /></Field>
    <Field label="Block · 공통 선택 범위"><input readOnly value={scopeLabel('block', value.block_id)} /></Field>
    <Field label="버전 · 공통 선택 범위"><input readOnly value={scopeLabel('version', value.version_id)} /></Field>
    {showSnapshot && <Field label="Snapshot (선택)" hint="공통 범위에 속하는 수치 분석 구조입니다."><select disabled={!onChange} value={value.snapshot_id} onChange={event => onChange?.({ ...value, snapshot_id: event.target.value })}><option value="">Snapshot 미지정</option><RecordOptions records={records.filter(record => record.record_type === 'snapshot' && (!value.project_id || record.project_id === value.project_id) && (!value.block_id || record.block_id === value.block_id) && (!value.version_id || record.version_id === value.version_id))} /></select></Field>}
  </div>
}
export function ScopeBinding({ access, records, projectId, blockId, versionId }: { access: AreaAccess; records: TrackingRecord[]; projectId: string; blockId?: string; versionId?: string }) {
  const areaProject = records.find(record => record.record_type === 'project' && record.id === projectId)
  const areaBlock = records.find(record => record.record_type === 'block' && record.id === blockId && record.project_id === projectId)
  const areaVersion = records.find(record => record.record_type === 'version' && record.id === versionId && record.project_id === projectId)
  const hostProjectId = text(areaProject?.payload.sanity_project_id)
  const hostBlockId = text(areaBlock?.payload.sanity_block_id)
  const hostVersionId = text(areaVersion?.payload.sanity_version_id)
  const hostProject = access.projects.find(project => project.id === hostProjectId)
  const hostBlock = hostProject?.blocks.find(block => block.id === hostBlockId)
  return <div className="notice">
    <strong>공통 프로젝트·Block·버전의 명시적 ID 연결</strong>
    {access.local_mode && <p>로컬 인증으로 실제 공통 카탈로그와 수치 데이터를 사용합니다. 회사 SSO 사용자나 권한을 대신하지 않습니다.</p>}
    <p>Area Project <code>{projectId || '선택 전'}</code> → 공통 Project {hostProject?.name} <code>{hostProjectId || '연결 미준비'}</code></p>
    {blockId && <p>Area Block <code>{blockId}</code> → 공통 Block {hostBlock?.name} <code>{hostBlockId || '연결 미준비'}</code></p>}
    {versionId && <p>Area Version <code>{versionId}</code> → 공통 Version <code>{hostVersionId || '연결 미준비'}</code></p>}
    <small>저장된 sanity_project_id · sanity_block_id · sanity_version_id를 표시합니다. 이름이나 최신 시각으로 연결을 추측하지 않으며 CLI에는 내부 Area UUID를 사용합니다.</small>
  </div>
}


const schemas: Record<string, { title: string; empty: Payload; guide: string[] }> = {
  target: { title: 'Target', empty: { name: '', paths: [], scope: 'node', metrics: [], bindings: [] }, guide: ['name: 표시 이름 · paths: 전체 hierarchy 경로의 문자열 배열 (그룹 가능)', 'scope: node 또는 subtree · metrics: 추적할 원본/파생 지표 키 배열', 'bindings: [{snapshot_id, paths:[...]}] · snapshot_id: 선택한 구조 기준 (선택)', '복잡한 경로 문자를 그대로 입력하세요. 경로를 쉼표나 공백으로 분리하지 않습니다. 없는 경로는 pending으로 유지됩니다.'] },
  mapping: { title: 'Mapping', empty: { baseline_project_id: '', baseline_version_id: '', baseline_block_id: '', current_project_id: '', current_version_id: '', current_block_id: '', rules: [] }, guide: ['양쪽 baseline_/current_ project_id · version_id · block_id에 카탈로그 UUID를 지정하세요.', 'rules: [{kind, scope, baseline_paths:[], current_paths:[], target_id?, description?}]', 'kind: one_to_one (1:1), split (1:N), merge (N:1), added (0:N), removed (N:0)', 'scope: node | subtree (subtree는 one_to_one만). 경로는 문자열 배열로 입력하며 복잡한 그룹 경로를 그대로 보존합니다.', 'valid_from_version_id / valid_through_version_id: 선택 적용 구간. 업로드 전 매핑은 pending 상태입니다. state/path_validation은 서버가 관리합니다.'] },
  change: { title: '변경사항', empty: {}, guide: ['허용되는 메타데이터 키: applies_from_version_id, applies_through_version_id, baseline_project_id, target_ids, paths, metrics, expected, extraction_evidence', 'target_ids: UUID 배열 · paths: 전체 경로 문자열 배열 · metrics: 지표 키 배열', 'expected와 extraction_evidence: 객체 배열. 그 밖의 추가 키는 허용되지 않습니다.', '설명은 원문으로 저장됩니다. AI가 방향만 보고 임의의 수치나 크기를 추정하지 않습니다.'] },
}
export function Configurations({ mode, ...props }: ConfigProps & { mode: 'targets' | 'changes' }) {
  const [kind, setKind] = useState(mode === 'changes' ? 'change' : 'target')
  const [editing, setEditing] = useState<TrackingRecord | null | undefined>(undefined)
  const [history, setHistory] = useState<TrackingRecord[] | null>(null)
  const [historyTitle, setHistoryTitle] = useState('')
  const historyAction = useAction()
  const entries = props.records.filter(record => record.record_type === kind && (!props.filters.project_id || record.project_id === props.filters.project_id) && (!props.filters.block_id || !record.block_id || record.block_id === props.filters.block_id) && (!props.filters.version_id || !record.version_id || record.version_id === props.filters.version_id))
  return <><section className="panel"><div className="section-heading"><div><span className="eyebrow">IMMUTABLE REVISIONS</span><h2>{mode === 'changes' ? '설계 변경 컨텍스트' : '추적 대상과 구조 대응'}</h2><p>기존 설정을 덮어쓰지 않고 새 revision으로 저장합니다. 비교에는 선택한 정확한 revision을 고정합니다.</p></div><Button onClick={() => setEditing(null)}><Plus size={16} />{schemas[kind].title} 등록</Button></div>{mode === 'targets' && <div className="tabs" role="tablist" aria-label="설정 종류"><button role="tab" aria-selected={kind === 'target'} onClick={() => setKind('target')}>Targets</button><button role="tab" aria-selected={kind === 'mapping'} onClick={() => setKind('mapping')}>Mappings</button></div>}{historyAction.feedback}{entries.length ? <div className="record-list">{entries.map(record => <article key={record.id} className="config-card"><div className="card-line"><div><span className="eyebrow">{record.record_type} · r{record.revision}</span><h3>{kind === 'mapping' ? `${text(record.payload.baseline_version_id).slice(0, 8)} → ${text(record.payload.current_version_id).slice(0, 8)}` : nameOf(record)}</h3></div><div className="actions"><Badge value={record.payload.state || record.state} />{record.payload.path_validation != null && <Badge value={record.payload.path_validation} />}</div></div>{kind === 'change' && <p className="prose">{text(record.payload.text)}</p>}<div className="id-line"><code>{record.id}</code><CopyButton value={record.id} label="Revision ID 복사" /></div><Json value={record.payload} label="설정 · 적용 범위 보기" /><div className="card-footer"><small>{date(record.updated_at || record.created_at)}</small><div className="actions"><Button variant="ghost" size="sm" disabled={historyAction.busy} onClick={() => historyAction.run(async () => { const revisions = await allRecords(props.auth, kind, { latest: 'false' }); setHistory(revisions.filter(item => item.entity_id === record.entity_id)); setHistoryTitle(nameOf(record)) }, '변경 이력을 불러왔습니다.')}>이력</Button><Button variant="outline" size="sm" onClick={() => setEditing(record)}><SquareArrowOutUpRight size={14} />새 revision으로 편집</Button></div></div></article>)}</div> : <Empty title={`등록된 ${schemas[kind].title}이 없습니다`}>실제 구조와 변경 근거를 등록하면 비교 요청에서 선택할 수 있습니다.</Empty>}</section>{editing !== undefined && <ConfigEditor {...props} kind={kind} editing={editing} onClose={() => setEditing(undefined)} />}{history && <Modal title={`Revision 이력 · ${historyTitle}`} onClose={() => setHistory(null)}>{history.length ? history.map(record => <article className="history-item" key={record.id}><h3>Revision {record.revision} <Badge value={record.state} /></h3><p>{date(record.created_at)}</p><code>{record.id}</code><CopyButton value={record.id} /><Json value={record.payload} open /></article>) : <Empty title="이력이 없습니다" />}</Modal>}</>
}
function ConfigEditor({ auth, records, filters, refresh, kind, editing, onClose }: ConfigProps & { kind: string; editing: TrackingRecord | null; onClose: () => void }) {
  const schema = schemas[kind]
  const [snapshotId, setSnapshotId] = useState(editing?.snapshot_id || filters.snapshot_id)
  useEffect(() => { if (!editing) setSnapshotId(filters.snapshot_id) }, [editing, filters.project_id, filters.block_id, filters.version_id, filters.snapshot_id])
  const scope: Filters = editing
    ? { project_id: editing.project_id || '', version_id: editing.version_id || '', block_id: editing.block_id || '', snapshot_id: editing.snapshot_id || '' }
    : { ...filters, snapshot_id: snapshotId }
  const [content, setContent] = useState(text(editing?.payload.text))
  const initial = editing ? Object.fromEntries(Object.entries(editing.payload).filter(([key]) => key !== 'path_validation' && (kind !== 'mapping' || key !== 'state') && (kind !== 'change' || key !== 'text'))) : schema.empty
  const [source, setSource] = useState(pretty(initial))
  const action = useAction()
  return <Modal title={`${schema.title} ${editing ? `r${editing.revision} → 새 revision` : '등록'}`} onClose={onClose}><form onSubmit={event => prevent(event, () => action.run(async () => {
    const payload = jsonObject(source)
    const allowed = kind === 'target' ? ['name', 'paths', 'snapshot_id', 'scope', 'metrics', 'bindings'] : kind === 'change' ? ['applies_from_version_id', 'applies_through_version_id', 'baseline_project_id', 'target_ids', 'paths', 'metrics', 'expected', 'extraction_evidence'] : null
    if (allowed && Object.keys(payload).some(key => !allowed.includes(key))) throw new Error('스키마에 없는 payload 키가 있습니다. 아래 입력 스키마의 허용 필드만 사용하세요.')
    if (kind === 'change') { if ('text' in payload) throw new Error('변경 원문은 본문 입력란에 작성하세요.'); payload.text = content }
    const scopeFields = Object.fromEntries(Object.entries(scope).filter(([, value]) => value))
    await request(auth, '/config', recordSchema, { record_type: kind, ...scopeFields, ...(editing ? { entity_id: editing.entity_id, expected_row_version: revision(editing.row_version) } : {}), payload })
    await refresh(); onClose()
  }))}><fieldset disabled={Boolean(editing)}><legend className="scope-legend">{editing ? '기존 설정의 범위는 고정됩니다. 다른 범위는 새 항목으로 등록하세요.' : '공통 선택 범위에 등록합니다. 프로젝트·Block·버전은 공통 필터에서 변경하세요.'}</legend><ScopeFields records={records} value={scope} onChange={value => setSnapshotId(value.snapshot_id)} /></fieldset>{kind === 'change' && <Field label="변경사항 원문" hint="실제 변경과 이유를 자연어로 작성하세요. 텍스트는 명령이 아닌 분석 데이터로 처리됩니다."><textarea required rows={6} value={content} onChange={event => setContent(event.target.value)} /></Field>}<div className="schema-guide"><strong>입력 스키마</strong><ul>{schema.guide.map(line => <li key={line}>{line}</li>)}</ul></div><JsonEditor label={kind === 'change' ? '부가 메타데이터 JSON (선택, 없으면 {})' : `${schema.title} payload JSON`} value={source} onChange={setSource} hint="JSON 숫자는 원래 정밀도로 전송됩니다. 서버가 cardinality·참조·경로 충돌을 검증합니다." />{action.feedback}<div className="form-actions"><Button type="button" variant="outline" onClick={onClose}>취소</Button><Submit busy={action.busy}>{editing ? '새 revision 저장' : '등록'}</Submit></div></form></Modal>
}
