import { useState } from 'react'
import { Download, FileJson } from 'lucide-react'
import { date, jsonObject, nameOf, object, pretty, text, type AreaAccess, type Auth, type Filters, type TrackingRecord, type Upload } from './api'
import { Badge, Button, CopyButton, Empty, Field, Json, JsonEditor, Modal, RecordOptions, Submit, prevent, useAction } from './components/workspace'
import { useAtlas } from '../atlasContext'

export interface UploadProps { auth: Auth; access: AreaAccess; uploads: Upload[]; records: TrackingRecord[]; filters: Filters }
export function Uploads({ auth, access, uploads, records, filters }: UploadProps) {
  const atlas = useAtlas()!
  const [detail, setDetail] = useState<Upload | null>(null)
  const [metadata, setMetadata] = useState(false)
  const action = useAction()
  const scoped = uploads.filter(row => row.project_id === filters.project_id)
  const detailProject = atlas.catalog?.projects.find(project => project.area_id === detail?.project_id)
  const detailBlock = detailProject?.blocks.find(block => block.area_id === detail?.block_id)
  async function download(upload: Upload) {
    const blob = await auth.request<Blob>(`/api/area-power/uploads/${encodeURIComponent(upload.id)}/download`, undefined, 'blob')
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${upload.id}.csv.gz`; anchor.click(); URL.revokeObjectURL(url)
  }
  return <div className="upload-history-content"><div className="section-heading"><p className="muted">프로젝트의 원본과 실행 이력입니다. 비교 입력은 위에서 따로 선택하며 업로드 시각으로 자동 교체하지 않습니다.</p><Button variant="outline" onClick={() => setMetadata(true)}><FileJson size={16} />PC CLI 메타데이터</Button></div>{action.feedback}
    {scoped.length ? <div className="table-scroll"><table><thead><tr><th>실행 / 원본</th><th>Corner</th><th>상태 / 등록</th><th>원본 / 메타데이터</th></tr></thead><tbody>{scoped.map(upload => <tr key={upload.id}><td><strong>{upload.execution_name || upload.original_filename || '이름 미확인'}</strong></td><td>{[upload.process_label, upload.voltage_v, upload.temp_c, upload.pex].map(value => value ?? '?').join(' / ')}</td><td><Badge value={upload.state} /><small>{date(upload.created_at)}</small></td><td><div className="actions"><Button variant="ghost" size="sm" onClick={() => setDetail(upload)}>메타데이터</Button><Button variant="ghost" size="icon-sm" aria-label={`${upload.execution_name || upload.id} 원본 gzip 다운로드`} disabled={action.busy} onClick={() => action.run(() => download(upload), '원본 다운로드를 요청했습니다.')}><Download size={16} /></Button></div></td></tr>)}</tbody></table></div> : <Empty title="이 프로젝트에 업로드가 없습니다">비교할 Block·버전의 수치 연결을 준비한 뒤 PC CLI용 메타데이터를 만드세요. 브라우저에서 VWP 서버로 직접 업로드하지 않습니다.</Empty>}
    {detail && <Modal title="업로드 메타데이터" onClose={() => setDetail(null)}><div className="form-grid"><Field label="프로젝트"><input readOnly value={detailProject?.name || text(records.find(row => row.id === detail.project_id)?.payload.name) || '프로젝트 정보 없음'} /></Field><Field label="블록"><input readOnly value={detailBlock?.name || text(records.find(row => row.id === detail.block_id)?.payload.name) || '블록 정보 없음'} /></Field></div><CopyButton value={pretty(detail)} label="메타데이터 JSON 복사" /><Json label="업로드 상세" value={Object.fromEntries(Object.entries(detail).filter(([key]) => key !== 'project_id' && key !== 'block_id'))} open /><Json value={object(detail.root_summary).quality} label="입력 품질과 분류 coverage" /></Modal>}
    {metadata && <MetadataBuilder access={access} records={records} filters={filters} onClose={() => setMetadata(false)} />}
  </div>
}
function MetadataBuilder({ access, records, filters, onClose }: { access: AreaAccess; records: TrackingRecord[]; filters: Filters; onClose: () => void }) {
  const atlas = useAtlas()!
  const demo = atlas.demo
  const [scope, setScope] = useState(filters)
  const projects = records.filter(row => row.record_type === 'project')
  const blocks = records.filter(row => row.record_type === 'block' && row.project_id === scope.project_id)
  const versions = records.filter(row => row.record_type === 'version' && row.project_id === scope.project_id && row.block_id === scope.block_id)
  const [execution, setExecution] = useState('')
  const [stage, setStage] = useState('')
  const [separator, setSeparator] = useState('')
  const [optional, setOptional] = useState({ area_unit: '', area_basis: '', library: '', classification: '' })
  const [context, setContext] = useState('{}')
  const [conditions, setConditions] = useState('{}')
  const [tolerances, setTolerances] = useState('{}')
  const [output, setOutput] = useState('')
  const action = useAction()
  return <Modal title={demo ? '합성 업로드 JSON 예시 · 실제 VWP 전송 금지' : 'PC CLI용 업로드 메타데이터'} onClose={onClose}><form onSubmit={event => prevent(event, () => action.run(async () => {
    if (!projects.some(row => row.id === scope.project_id) || !blocks.some(row => row.id === scope.block_id) || !versions.some(row => row.id === scope.version_id)) throw new Error('이 창에서 연결된 프로젝트·Block·버전을 선택하세요.')
    const toleranceValues = jsonObject(tolerances)
    if (Object.keys(toleranceValues).some(key => !['pct_tolerance', 'power_tolerance_mw'].includes(key))) throw new Error('허용 오차는 pct_tolerance / power_tolerance_mw만 허용합니다.')
    setOutput(pretty({ project_id: scope.project_id, version_id: scope.version_id, block_id: scope.block_id, ...(scope.snapshot_id ? { snapshot_id: scope.snapshot_id } : {}), execution_name: execution, design_stage: stage, hierarchy_separator: separator, ...Object.fromEntries(Object.entries(optional).filter(([, value]) => value)), constraint_context: jsonObject(context), analysis_conditions: jsonObject(conditions), ...toleranceValues }))
  }, demo ? '합성 UUID의 JSON 예시입니다. 실제 VWP로 전송하지 마세요.' : '메타데이터를 생성했습니다. PC의 vwp CLI에 JSON 파일로 전달하세요.'))}>
    <p className="muted">{demo ? '합성 범위의 형식 예시이며 실제 VWP 전송에는 사용할 수 없습니다.' : '선택한 프로젝트·블록·버전의 연결값은 자동으로 포함합니다. PVT·PEX는 CSV에서 읽으며 인증 토큰이나 DB 접속 정보는 포함하지 않습니다.'}{access.local_mode && ' 명시적 로컬 인증이며 회사 SSO를 대신하지 않습니다.'}</p>
    <div className="form-grid"><Field label="업로드 프로젝트"><select required value={scope.project_id} onChange={event => { setScope({ project_id: event.target.value, block_id: '', version_id: '', snapshot_id: '' }); setOutput('') }}><option value="">프로젝트 선택</option>{projects.map(row => <option key={row.id} value={row.id}>{nameOf(row)}</option>)}</select></Field><Field label="업로드 Block"><select required disabled={!scope.project_id} value={scope.block_id} onChange={event => { setScope({ ...scope, block_id: event.target.value, version_id: '', snapshot_id: '' }); setOutput('') }}><option value="">Block 선택</option>{blocks.map(row => <option key={row.id} value={row.id}>{nameOf(row)}</option>)}</select></Field><Field label="NET_VER · REVISION"><select required disabled={!scope.block_id} value={scope.version_id} onChange={event => { setScope({ ...scope, version_id: event.target.value, snapshot_id: '' }); setOutput('') }}><option value="">버전 선택</option>{versions.map(row => <option key={row.id} value={row.id}>{nameOf(row)}</option>)}</select></Field></div>
    <Field label="기존 Snapshot 선택 (선택)" hint="새 구조라면 비워두세요. 재사용 시 전체 구조 일치를 검증합니다."><select value={scope.snapshot_id} onChange={event => { setScope({ ...scope, snapshot_id: event.target.value }); setOutput('') }}><option value="">새 snapshot 생성</option><RecordOptions records={records.filter(row => row.record_type === 'snapshot' && row.project_id === scope.project_id && row.version_id === scope.version_id && row.block_id === scope.block_id)} /></select></Field>
    <div className="form-grid"><Field label="구현 / 분석 실행 이름"><input required value={execution} onChange={event => setExecution(event.target.value)} /></Field><Field label="설계 단계"><input required value={stage} onChange={event => setStage(event.target.value)} /></Field><Field label="Hierarchy 구분자" hint="파일의 실제 경로 구분자 문자열"><input required value={separator} onChange={event => setSeparator(event.target.value)} /></Field>{Object.entries(optional).map(([key, value]) => <Field key={key} label={`${key} (선택)`}><input value={value} onChange={event => setOptional({ ...optional, [key]: event.target.value })} /></Field>)}</div>
    <JsonEditor label="Constraint context JSON" value={context} onChange={setContext} hint="실제 조건만 입력하고 미확인 값은 추측하지 마세요." /><JsonEditor label="Analysis conditions JSON" value={conditions} onChange={setConditions} hint="mode, clocks, activity, 도구 버전 등 실제 분석 조건" /><JsonEditor label="명시적 허용 오차 JSON (선택)" value={tolerances} onChange={setTolerances} hint="pct_tolerance, power_tolerance_mw. 기본 {}는 원본 정밀도 기준 검증입니다." />
    {action.feedback}{output && <div className="notice"><CopyButton value={output} label="JSON 복사" /></div>}<div className="form-actions"><Button type="button" variant="outline" onClick={onClose}>닫기</Button><Submit busy={action.busy}>JSON 생성</Submit></div>
  </form></Modal>
}
