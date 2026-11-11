import { useEffect, useState } from 'react'
import { CheckCircle2, Download, FileJson, Files } from 'lucide-react'
import { date, jsonObject, object, pretty, request, revision, text, unknownSchema, uploadName, type AreaAccess, type Auth, type Filters, type TrackingRecord, type Upload } from './api'
import { Badge, Button, CopyButton, Empty, Field, Json, JsonEditor, Modal, RecordOptions, Submit, prevent, useAction } from './components/workspace'
import { ScopeBinding, ScopeFields } from './ConfigViews'
import { useAtlas } from '../atlasContext'

export interface UploadProps { auth: Auth; access: AreaAccess; uploads: Upload[]; records: TrackingRecord[]; filters: Filters; refresh: () => Promise<void> }
export function Uploads({ auth, access, uploads, records, filters, refresh }: UploadProps) {
  const [corner, setCorner] = useState('')
  const [execution, setExecution] = useState('')
  const [detail, setDetail] = useState<Upload | null>(null)
  const [metadata, setMetadata] = useState(false)
  const action = useAction()
  const scoped = uploads.filter(upload => Object.entries(filters).every(([key, value]) => !value || upload[key] === value))
  const cornerKey = (upload: Upload) => [upload.process_label, upload.voltage_v, upload.temp_c, upload.pex].map(value => value ?? '?').join(' / ')
  const corners = [...new Set(scoped.map(cornerKey))]
  const filtered = scoped.filter(upload => (!corner || cornerKey(upload) === corner) && (!execution || upload.id === execution))
  const selections = records.filter(record => record.record_type === 'selection')
  async function select(upload: Upload) {
    const current = selections.find(record => record.snapshot_id === upload.snapshot_id && record.condition_key === upload.condition_key && record.selection_scope === 'shared')
    await request(auth, '/selections', unknownSchema, { upload_id: upload.id, selection_scope: 'shared', ...(current ? { expected_row_version: revision(current.row_version) } : {}) })
    await refresh()
  }
  async function download(upload: Upload) {
    const blob = await auth.request<Blob>(`/api/area-power/uploads/${encodeURIComponent(upload.id)}/download`, undefined, 'blob')
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${upload.id}.csv.gz`; anchor.click(); URL.revokeObjectURL(url)
  }
  return <><section className="panel"><div className="section-heading"><div><span className="eyebrow">IMMUTABLE INPUTS</span><h2>업로드와 실행 파일</h2><p>Corner는 CSV에서 읽습니다. 같은 조건의 재실행도 별도로 보관하며 최신 파일을 자동 선택하지 않습니다.</p></div><Button onClick={() => setMetadata(true)}><FileJson size={16} />업로드 메타데이터 만들기</Button></div><div className="inline-filters"><Field label="CSV corner"><select value={corner} onChange={event => { setCorner(event.target.value); setExecution('') }}><option value="">모든 corner</option>{corners.map(value => <option key={value}>{value}</option>)}</select></Field><Field label="실행 파일"><select value={execution} onChange={event => setExecution(event.target.value)}><option value="">모든 실행</option>{scoped.filter(upload => !corner || cornerKey(upload) === corner).map(upload => <option key={upload.id} value={upload.id}>{uploadName(upload)}</option>)}</select></Field></div>{action.feedback}<div className="table-caption"><span><Files size={14} /> {filtered.length}개 파일</span><small>선택은 snapshot · condition 단위의 shared 선택에 반영됩니다.</small></div>{filtered.length ? <div className="table-scroll"><table><thead><tr><th>실행 / 원본</th><th>구조 / 범위</th><th>Corner</th><th>상태 / 등록</th><th>명시적 선택</th><th>작업</th></tr></thead><tbody>{filtered.map(upload => { const selected = selections.some(record => record.upload_id === upload.id && record.selection_scope === 'shared'); return <tr key={upload.id}><td><strong>{upload.execution_name || upload.original_filename || upload.filename || '이름 미확인'}</strong><code>{upload.id}</code></td><td><code>{upload.snapshot_id || '미확정'}</code><small>{text(object(object(upload.input_contract).metadata).design_stage) || '설계 단계 미확인'}</small></td><td className="mono">{cornerKey(upload)}<small>process / V / °C / PEX</small></td><td><Badge value={upload.state} /><small>{date(upload.created_at)}</small></td><td>{selected ? <span className="selected-label"><CheckCircle2 size={15} />선택됨</span> : <Button size="sm" variant="outline" disabled={upload.state !== 'ready' || action.busy} onClick={() => action.run(() => select(upload), '해당 snapshot·condition의 실행 파일을 명시적으로 선택했습니다.')}>이 실행 선택</Button>}</td><td><div className="actions"><Button variant="ghost" size="sm" onClick={() => setDetail(upload)}>메타데이터</Button><Button variant="ghost" size="icon-sm" aria-label={`${upload.id} 원본 gzip 다운로드`} disabled={action.busy} onClick={() => action.run(() => download(upload), '원본 다운로드를 요청했습니다.')}><Download size={16} /></Button></div></td></tr> })}</tbody></table></div> : <Empty title="선택 범위에 업로드가 없습니다">카탈로그 ID와 메타데이터를 사용자 PC의 vwp CLI에 전달하세요. 브라우저는 vwp_server에 직접 업로드하지 않습니다.</Empty>}</section>{detail && <Modal title="업로드 메타데이터" onClose={() => setDetail(null)}><div className="id-line"><code>{detail.id}</code><CopyButton value={detail.id} label="Upload ID 복사" /></div><CopyButton value={pretty(detail)} label="메타데이터 JSON 복사" /><Json value={detail} open /><Json value={object(detail.root_summary).quality} label="입력 품질과 분류 coverage" /></Modal>}{metadata && <MetadataBuilder access={access} records={records} filters={filters} onClose={() => setMetadata(false)} />}</>
}
function MetadataBuilder({ access, records, filters, onClose }: { access: AreaAccess; records: TrackingRecord[]; filters: Filters; onClose: () => void }) {
  const demo = useAtlas()?.demo === true
  const [snapshotId, setSnapshotId] = useState(filters.snapshot_id)
  const scope = { ...filters, snapshot_id: snapshotId }
  const [execution, setExecution] = useState('')
  const [stage, setStage] = useState('')
  const [separator, setSeparator] = useState('')
  const [optional, setOptional] = useState({ area_unit: '', area_basis: '', library: '', classification: '' })
  const [context, setContext] = useState('{}')
  const [conditions, setConditions] = useState('{}')
  const [tolerances, setTolerances] = useState('{}')
  const [output, setOutput] = useState('')
  useEffect(() => { setSnapshotId(filters.snapshot_id); setOutput('') }, [filters.project_id, filters.block_id, filters.version_id, filters.snapshot_id])
  const action = useAction()
  return <Modal title={demo ? '합성 업로드 JSON 예시 · 실제 VWP 전송 금지' : 'vwp CLI용 업로드 메타데이터'} onClose={onClose}><form onSubmit={event => prevent(event, () => action.run(async () => {
    if (!scope.project_id || !scope.version_id || !scope.block_id) throw new Error('공통 프로젝트·Block·버전을 선택하고 수치 분석 범위를 준비하세요.')
    const toleranceValues = jsonObject(tolerances)
    if (Object.keys(toleranceValues).some(key => !['pct_tolerance', 'power_tolerance_mw'].includes(key))) throw new Error('허용 오차는 pct_tolerance / power_tolerance_mw만 허용합니다.')
    setOutput(pretty({ project_id: scope.project_id, version_id: scope.version_id, block_id: scope.block_id, ...(scope.snapshot_id ? { snapshot_id: scope.snapshot_id } : {}), execution_name: execution, design_stage: stage, hierarchy_separator: separator, ...Object.fromEntries(Object.entries(optional).filter(([, value]) => value)), constraint_context: jsonObject(context), analysis_conditions: jsonObject(conditions), ...toleranceValues }))
  }, demo ? '합성 UUID의 JSON 예시입니다. 실제 VWP로 전송하지 마세요.' : '메타데이터를 생성했습니다. PC의 vwp CLI에 JSON 파일로 전달하세요.'))}><p className="muted">{demo ? '합성 범위의 JSON 형식만 보여줍니다. 이 UUID는 실제 DB에 없으며 실제 CSV 업로드에 사용할 수 없습니다.' : '공통 선택 범위의 업로드 컨텍스트만 생성합니다. CLI JSON의 project_id/block_id/version_id는 준비된 내부 Area UUID입니다. PVT · PEX는 CSV에서 검증하며 DB 접속 정보나 인증 토큰은 포함하지 않습니다.'}</p><ScopeBinding access={access} records={records} projectId={scope.project_id} blockId={scope.block_id} versionId={scope.version_id} /><ScopeFields records={records} value={scope} showSnapshot={false} /><Field label="기존 Snapshot 선택 (선택)" hint="새 구조라면 비워두세요. 기존 snapshot 재사용 시 전체 구조 일치가 검증됩니다."><select value={scope.snapshot_id} onChange={event => { setSnapshotId(event.target.value); setOutput('') }}><option value="">새 snapshot 생성</option><RecordOptions records={records.filter(record => record.record_type === 'snapshot' && record.project_id === scope.project_id && record.version_id === scope.version_id && record.block_id === scope.block_id)} /></select></Field><div className="form-grid"><Field label="구현 / 분석 실행 이름"><input required value={execution} onChange={event => setExecution(event.target.value)} /></Field><Field label="설계 단계"><input required value={stage} onChange={event => setStage(event.target.value)} /></Field><Field label="Hierarchy 구분자" hint="파일에 사용한 실제 경로 구분자. 정규식이 아닌 문자열입니다."><input required value={separator} onChange={event => setSeparator(event.target.value)} /></Field>{Object.entries(optional).map(([key, value]) => <Field key={key} label={`${key} (선택)`}><input value={value} onChange={event => setOptional({ ...optional, [key]: event.target.value })} /></Field>)}</div><JsonEditor label="Constraint context JSON" value={context} onChange={setContext} hint="파일에 없는 constraint 조건. 미확인 값은 추측하지 마세요." /><JsonEditor label="Analysis conditions JSON" value={conditions} onChange={setConditions} hint="mode, clocks, activity, 도구 버전 등 비교 의미에 영향을 주는 실제 조건." /><JsonEditor label="명시적 허용 오차 JSON (선택, 기본 {})" value={tolerances} onChange={setTolerances} hint="pct_tolerance, power_tolerance_mw: 0 이상의 숫자. 비워두면 보고서 정밀도에 따른 검증을 사용합니다." />{action.feedback}{output && <div className="notice"><div className="card-line"><strong>{demo ? '합성 메타데이터 예시' : 'CLI 입력 메타데이터'}</strong><CopyButton value={output} label="JSON 복사" /></div><pre>{output}</pre></div>}<div className="form-actions"><Button type="button" variant="outline" onClick={onClose}>닫기</Button><Submit busy={action.busy}>JSON 생성</Submit></div></form></Modal>
}
