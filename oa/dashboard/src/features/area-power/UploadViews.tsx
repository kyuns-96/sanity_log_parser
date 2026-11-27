import { useEffect, useState } from 'react'
import { Download, FileJson, Trash2 } from 'lucide-react'
import { z } from 'zod'
import { date, formatCornerLabel, isPowerLayerUpload, jsonObject, nameOf, object, pretty, request, text, unknownSchema, type AreaAccess, type Auth, type Filters, type TrackingRecord, type Upload } from './api'
import { Badge, Button, CopyButton, Empty, Field, Json, JsonEditor, Modal, RecordOptions, Submit, prevent, useAction } from './components/workspace'
import { useAtlas } from '@/features/atlas/atlasContext'

const uploadDeletePreviewSchema = z.object({
  upload_id: z.string(),
  execution_name: z.string().nullish(),
  original_filename: z.string().nullish(),
  is_power_layer: z.boolean(),
  counts: z.object({
    dependent_power_layers: z.union([z.string(), z.number()]).transform(Number),
    snapshot_siblings: z.union([z.string(), z.number()]).transform(Number),
    comparisons: z.union([z.string(), z.number()]).transform(Number),
    tracking_records: z.union([z.string(), z.number()]).transform(Number),
  }),
  is_standalone_unused: z.boolean(),
  delete_available: z.boolean(),
  can_delete: z.boolean(),
  can_force_delete: z.boolean(),
  blockers: z.array(z.string()),
})
type UploadDeletePreview = z.infer<typeof uploadDeletePreviewSchema>

export interface UploadProps { auth: Auth; access: AreaAccess; uploads: Upload[]; records: TrackingRecord[]; filters: Filters; canWrite?: boolean; onRefresh?: () => Promise<void> | void }
export function Uploads({ auth, access, uploads, records, filters, canWrite = false, onRefresh }: UploadProps) {
  const atlas = useAtlas()!
  const [detail, setDetail] = useState<Upload | null>(null)
  const [deleting, setDeleting] = useState<Upload | null>(null)
  const [metadata, setMetadata] = useState(false)
  const action = useAction()
  const scoped = uploads.filter(row => row.project_id === filters.project_id)
  const detailProject = atlas.catalog?.projects.find(project => project.area_id === detail?.project_id)
  const detailBlock = detailProject?.blocks.find(block => block.area_id === detail?.block_id)
  const allowDelete = (canWrite || Boolean(atlas.catalog?.can_manage_catalog)) && !auth.demo
  async function download(upload: Upload) {
    const blob = await auth.request<Blob>(`/api/area-power/uploads/${encodeURIComponent(upload.id)}/download`, undefined, 'blob')
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${upload.id}.csv.gz`; anchor.click(); URL.revokeObjectURL(url)
  }
  return <div className="upload-history-content"><div className="section-heading"><p className="muted">프로젝트의 원본과 실행 이력입니다. 비교 입력은 위에서 따로 선택하며 업로드 시각으로 자동 교체하지 않습니다.</p><Button variant="outline" onClick={() => setMetadata(true)}><FileJson size={16} />PC CLI 메타데이터</Button></div>{action.feedback}
    {scoped.length ? <div className="table-scroll"><table><thead><tr><th>실행 / 원본</th><th>ECO_NUM</th><th>Corner</th><th>상태 / 등록</th><th>원본 / 메타데이터</th></tr></thead><tbody>{scoped.map(upload => { const ecoVal = text(object(object(upload.input_contract).metadata).eco_num).trim(); return <tr key={upload.id}><td><strong>{upload.execution_name || upload.original_filename || '이름 미확인'}</strong></td><td>{ecoVal ? <span className="tracking-eco-pill">{ecoVal}</span> : <span className="text-muted">—</span>}</td><td>{formatCornerLabel(upload)}{isPowerLayerUpload(upload) && <span className="tracking-eco-pill" style={{ marginLeft: '0.4rem' }}>Power 레이어</span>}</td><td><Badge value={upload.state} /><small>{date(upload.created_at)}</small></td><td><div className="actions"><Button variant="ghost" size="sm" onClick={() => setDetail(upload)}>메타데이터</Button><Button variant="ghost" size="icon-sm" aria-label={`${upload.execution_name || upload.id} 원본 gzip 다운로드`} disabled={action.busy} onClick={() => action.run(() => download(upload), '원본 다운로드를 요청했습니다.')}><Download size={16} /></Button>{allowDelete && <Button variant="ghost" size="icon-sm" aria-label={`${upload.execution_name || upload.id} CSV 삭제`} disabled={action.busy} onClick={() => setDeleting(upload)}><Trash2 size={15} /></Button>}</div></td></tr> })}</tbody></table></div> : <Empty title="이 프로젝트에 업로드가 없습니다">비교할 Block·버전의 수치 연결을 준비한 뒤 PC CLI용 메타데이터를 만드세요. 브라우저에서 VWP 서버로 직접 업로드하지 않습니다.</Empty>}
    {detail && <Modal title="업로드 메타데이터" onClose={() => setDetail(null)}><div className="form-grid"><Field label="프로젝트"><input readOnly value={detailProject?.name || text(records.find(row => row.id === detail.project_id)?.payload.name) || '프로젝트 정보 없음'} /></Field><Field label="블록"><input readOnly value={detailBlock?.name || text(records.find(row => row.id === detail.block_id)?.payload.name) || '블록 정보 없음'} /></Field></div><CopyButton value={pretty(detail)} label="메타데이터 JSON 복사" /><Json label="업로드 상세" value={Object.fromEntries(Object.entries(detail).filter(([key]) => key !== 'project_id' && key !== 'block_id'))} open /><Json value={object(detail.root_summary).quality} label="입력 품질과 분류 coverage" /></Modal>}
    {deleting && <UploadDeleteModal auth={auth} upload={deleting} onClose={() => setDeleting(null)} onDeleted={async () => { setDeleting(null); await onRefresh?.() }} />}
    {metadata && <MetadataBuilder access={access} records={records} filters={filters} onClose={() => setMetadata(false)} />}
  </div>
}
function UploadDeleteModal({ auth, upload, onClose, onDeleted }: { auth: Auth; upload: Upload; onClose: () => void; onDeleted: () => Promise<void> }) {
  const [preview, setPreview] = useState<UploadDeletePreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [force, setForce] = useState(false)
  const [error, setError] = useState('')
  const action = useAction()
  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setForce(false)
    request(auth, `/uploads/${encodeURIComponent(upload.id)}/delete-preview`, uploadDeletePreviewSchema)
      .then(data => { if (active) setPreview(data) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [auth, upload.id])
  const label = upload.execution_name || upload.original_filename || upload.id
  const canSubmit = Boolean(preview && preview.delete_available && (preview.is_standalone_unused || force))
  return <Modal title={`CSV 삭제 · ${label}`} onClose={onClose}>
    <p className="muted">Corner: <strong>{formatCornerLabel(upload)}</strong>{isPowerLayerUpload(upload) ? ' · Power 레이어' : ''}</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {action.feedback}
    {loading || !preview ? <p className="muted" role="status">연관된 Power 레이어 및 비교 결과를 확인하고 있습니다…</p> : <>
      <div className="notice">
        <div>연관 Power 레이어: <strong>{preview.counts.dependent_power_layers}건</strong> · 동일 snapshot 형제: <strong>{preview.counts.snapshot_siblings}건</strong></div>
        <div>연관 비교 결과: <strong>{preview.counts.comparisons}건</strong> · 추적/작업 레코드: <strong>{preview.counts.tracking_records}건</strong></div>
      </div>
      {!preview.delete_available && <p className="notice error">현재 DB 계정에 Area/Power 테이블 DELETE 권한이 없어 CSV를 삭제할 수 없습니다.</p>}
      {preview.delete_available && preview.is_standalone_unused && <p className="muted">이 CSV를 참조하는 Power 레이어나 저장된 비교 결과가 없습니다. 바로 영구 삭제할 수 있습니다.</p>}
      {preview.delete_available && !preview.is_standalone_unused && (
        <div className="notice">
          <p>이 CSV는 다른 Power 레이어({preview.counts.dependent_power_layers}건) 또는 비교 결과({preview.counts.comparisons}건)에서 참조 중입니다.</p>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem', cursor: 'pointer' }}>
            <input type="checkbox" checked={force} disabled={action.busy} onChange={e => setForce(e.target.checked)} />
            <strong>연관 Power 레이어·비교 결과까지 함께 영구 삭제 (강제 삭제)</strong>
          </label>
        </div>
      )}
    </>}
    <div className="form-actions">
      <Button type="button" variant="outline" disabled={action.busy} onClick={onClose}>취소</Button>
      <Button
        type="button"
        variant="destructive"
        disabled={action.busy || !canSubmit}
        onClick={() => action.run(async () => {
          await request(auth, `/uploads/${encodeURIComponent(upload.id)}/delete`, unknownSchema, { force: Boolean(!preview?.is_standalone_unused && force), reason: '' })
          await onDeleted()
        }, 'CSV 업로드를 삭제했습니다.')}
      >
        {preview && !preview.is_standalone_unused ? '연관 데이터 포함 삭제' : 'CSV 삭제'}
      </Button>
    </div>
  </Modal>
}
function MetadataBuilder({ access, records, filters, onClose }: { access: AreaAccess; records: TrackingRecord[]; filters: Filters; onClose: () => void }) {
  const atlas = useAtlas()!
  const demo = atlas.demo
  const [scope, setScope] = useState(filters)
  const projects = records.filter(row => row.record_type === 'project')
  const blocks = records.filter(row => row.record_type === 'block' && row.project_id === scope.project_id)
  const versions = records.filter(row => row.record_type === 'version' && row.project_id === scope.project_id && (!row.block_id || row.block_id === scope.block_id))
  const resolveVersionEco = (versionId: string) => {
    const rec = versions.find(row => row.id === versionId)
    const fromRec = text(rec?.payload.eco_num).trim()
    if (fromRec) return fromRec
    const atlasVer = atlas.catalog?.projects.flatMap(p => p.blocks).flatMap(b => b.versions).find(v => v.area_id === versionId)
    return atlasVer?.eco_num?.trim() || ''
  }
  const [ecoNum, setEcoNum] = useState(() => resolveVersionEco(filters.version_id))
  const [execution, setExecution] = useState('')
  const [stage, setStage] = useState('')
  const [separator, setSeparator] = useState('')
  const [optional, setOptional] = useState({ area_basis: '', library: '', classification: '' })
  const [context, setContext] = useState('{}')
  const [conditions, setConditions] = useState('{}')
  const [tolerances, setTolerances] = useState('{}')
  const [output, setOutput] = useState('')
  const action = useAction()
  return <Modal title={demo ? '합성 업로드 JSON 예시 · 실제 VWP 전송 금지' : 'PC CLI용 업로드 메타데이터'} onClose={onClose}><form onSubmit={event => prevent(event, () => action.run(async () => {
    if (!projects.some(row => row.id === scope.project_id) || !blocks.some(row => row.id === scope.block_id) || !versions.some(row => row.id === scope.version_id)) throw new Error('이 창에서 연결된 프로젝트·Block·버전을 선택하세요.')
    if (!ecoNum.trim()) throw new Error('ECO_NUM은 모든 업로드에 필수입니다.')
    const toleranceValues = jsonObject(tolerances)
    if (Object.keys(toleranceValues).some(key => !['pct_tolerance', 'power_tolerance_mw'].includes(key))) throw new Error('허용 오차는 pct_tolerance / power_tolerance_mw만 허용합니다.')
    setOutput(pretty({ project_id: scope.project_id, version_id: scope.version_id, block_id: scope.block_id, ...(scope.snapshot_id ? { snapshot_id: scope.snapshot_id } : {}), eco_num: ecoNum.trim(), execution_name: execution, design_stage: stage, hierarchy_separator: separator, area_unit: 'um2', ...Object.fromEntries(Object.entries(optional).filter(([, value]) => value)), constraint_context: jsonObject(context), analysis_conditions: jsonObject(conditions), ...toleranceValues }))
  }, demo ? '합성 UUID의 JSON 예시입니다. 실제 VWP로 전송하지 마세요.' : '메타데이터를 생성했습니다. PC의 vwp CLI에 JSON 파일로 전달하세요.'))}>
    <p className="muted">{demo ? '합성 범위의 형식 예시이며 실제 VWP 전송에는 사용할 수 없습니다.' : '선택한 프로젝트·블록·버전의 연결값과 고정 Area 단위(µm²)는 자동으로 포함합니다. Area 전용 CSV(61컬럼)는 코너/Power를 생략할 수 있고, Power 전용 CSV(26컬럼)나 동일 Area의 85컬럼 CSV는 최신 Area 기준에 Power 레이어로 자동 연결되어 중복 저장을 방지합니다.'}{access.local_mode && ' 명시적 로컬 인증이며 회사 SSO를 대신하지 않습니다.'}</p>
    <div className="form-grid"><Field label="업로드 프로젝트"><select required value={scope.project_id} onChange={event => { setScope({ project_id: event.target.value, block_id: '', version_id: '', snapshot_id: '' }); setEcoNum(''); setOutput('') }}><option value="">프로젝트 선택</option>{projects.map(row => <option key={row.id} value={row.id}>{nameOf(row)}</option>)}</select></Field><Field label="업로드 Block"><select required disabled={!scope.project_id} value={scope.block_id} onChange={event => { setScope({ ...scope, block_id: event.target.value, version_id: '', snapshot_id: '' }); setEcoNum(''); setOutput('') }}><option value="">Block 선택</option>{blocks.map(row => <option key={row.id} value={row.id}>{nameOf(row)}</option>)}</select></Field><Field label="NET_VER · REVISION · ECO"><select required disabled={!scope.block_id} value={scope.version_id} onChange={event => { const nextVid = event.target.value; setScope({ ...scope, version_id: nextVid, snapshot_id: '' }); setEcoNum(resolveVersionEco(nextVid)); setOutput('') }}><option value="">버전 선택</option>{versions.map(row => <option key={row.id} value={row.id}>{nameOf(row)}</option>)}</select></Field></div>
    <Field label="기존 Snapshot 선택 (선택)" hint="비워두면 Area가 동일할 때 최신 기준에 자동 연결하고, Area가 변경되면 새 snapshot을 생성합니다."><select value={scope.snapshot_id} onChange={event => { setScope({ ...scope, snapshot_id: event.target.value }); setOutput('') }}><option value="">자동 연결 / 새 snapshot 생성</option><RecordOptions records={records.filter(row => row.record_type === 'snapshot' && row.project_id === scope.project_id && row.version_id === scope.version_id && row.block_id === scope.block_id)} /></select></Field>
    <div className="form-grid"><Field label="ECO_NUM"><input required maxLength={100} placeholder="예: eco00 / eco01" value={ecoNum} onChange={event => setEcoNum(event.target.value)} /></Field><Field label="구현 / 분석 실행 이름"><input required value={execution} onChange={event => setExecution(event.target.value)} /></Field><Field label="설계 단계"><input required value={stage} onChange={event => setStage(event.target.value)} /></Field><Field label="Hierarchy 구분자" hint="파일의 실제 경로 구분자 문자열"><input required value={separator} onChange={event => setSeparator(event.target.value)} /></Field>{Object.entries(optional).map(([key, value]) => <Field key={key} label={`${key} (선택)`}><input value={value} onChange={event => setOptional({ ...optional, [key]: event.target.value })} /></Field>)}</div>
    <JsonEditor label="Constraint context JSON" value={context} onChange={setContext} hint="실제 조건만 입력하고 미확인 값은 추측하지 마세요." /><JsonEditor label="Analysis conditions JSON" value={conditions} onChange={setConditions} hint="mode, clocks, activity, 도구 버전 등 실제 분석 조건" /><JsonEditor label="명시적 허용 오차 JSON (선택)" value={tolerances} onChange={setTolerances} hint="pct_tolerance, power_tolerance_mw. 기본 {}는 원본 정밀도 기준 검증입니다." />
    {action.feedback}{output && <div className="notice"><CopyButton value={output} label="JSON 복사" /></div>}<div className="form-actions"><Button type="button" variant="outline" onClick={onClose}>닫기</Button><Submit busy={action.busy}>JSON 생성</Submit></div>
  </form></Modal>
}
