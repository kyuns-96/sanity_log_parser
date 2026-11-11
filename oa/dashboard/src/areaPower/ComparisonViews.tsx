import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, GitCompareArrows, Play, RefreshCw, ShieldAlert } from 'lucide-react'
import { allRecords, array, comparisonCreatedSchema, date, detailSchema, format, jsonObject, nameOf, object, pretty, request, text, unknownSchema, uploadName, type Auth, type ComparisonDetail, type Filters, type TrackingRecord, type Upload } from './api'
import { Badge, Button, CopyButton, Empty, Field, Json, JsonEditor, Modal, MultiRecords, RecordOptions, Submit, prevent, useAction } from './components/workspace'

interface ComparisonProps { auth: Auth; uploads: Upload[]; records: TrackingRecord[]; comparisons: TrackingRecord[]; filters: Filters; refresh: () => Promise<void> }
export function Comparisons(props: ComparisonProps) {
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState('')
  if (selected) return <ComparisonResult auth={props.auth} id={selected} records={props.records} onBack={() => setSelected('')} />
  return <><section className="panel"><div className="section-heading"><div><span className="eyebrow">SNAPSHOT COMPARISON</span><h2>비교 분석</h2><p>명시적으로 선택한 파일과 설정 revision을 고정합니다. 기준이 없는 결과는 0이 아닌 unavailable로 유지합니다.</p></div><Button onClick={() => setCreating(true)}><GitCompareArrows size={16} />비교 요청</Button></div>{props.comparisons.length ? <div className="table-scroll"><table><thead><tr><th>비교 / ID</th><th>현재 ↔ 기준</th><th>범위</th><th>상태</th><th>등록 시각</th><th>결과</th></tr></thead><tbody>{props.comparisons.map(record => { const input = object(record.payload.request); return <tr key={record.id}><td><strong>{text(input.comparison_kind) || '비교'}</strong><code>{record.id}</code></td><td><code>{text(input.current_upload_id || record.upload_id)}</code><small>기준 · {text(input.baseline_upload_id || record.baseline_upload_id) || '없음 (unavailable)'}</small></td><td><Badge value={object(input.scope).kind || 'all'} /><small>{array(object(input.scope).target_ids).length ? `${array(object(input.scope).target_ids).length} targets` : '전체 hierarchy'}</small></td><td><Badge value={record.state} /></td><td>{date(record.created_at)}</td><td><Button variant="outline" size="sm" onClick={() => setSelected(record.id)}>상세 보기<ArrowRight size={14} /></Button></td></tr> })}</tbody></table></div> : <Empty title="아직 요청한 비교가 없습니다">업로드를 준비하고 현재 파일·기준 파일·검출 정책을 명시적으로 선택하세요.</Empty>}</section>{creating && <ComparisonForm {...props} onClose={() => setCreating(false)} onCreated={id => { setCreating(false); setSelected(id) }} />}</>
}
function ComparisonForm({ auth, uploads, records, filters, refresh, onClose, onCreated }: ComparisonProps & { onClose: () => void; onCreated: (id: string) => void }) {
  const [current, setCurrent] = useState('')
  const [baseline, setBaseline] = useState('')
  const [kind, setKind] = useState('previous_version')
  const [scope, setScope] = useState('all')
  const [targets, setTargets] = useState<string[]>([])
  const [descendants, setDescendants] = useState(false)
  const [mapping, setMapping] = useState('')
  const [changes, setChanges] = useState<string[]>([])
  const [policy, setPolicy] = useState('{\n  "metrics": {},\n  "operating_limits": {}\n}')
  const [powerPairs, setPowerPairs] = useState('{\n  "power_pairs": []\n}')
  const [recipients, setRecipients] = useState('')
  const action = useAction()
  const [revisions, setRevisions] = useState<TrackingRecord[]>([])
  const [revisionLoading, setRevisionLoading] = useState(true)
  const [revisionError, setRevisionError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    Promise.all(['mapping', 'change'].map(type => allRecords(auth, type, { latest: 'false' }, controller.signal)))
      .then(groups => { if (!controller.signal.aborted) { setRevisions(groups.flat()); setRevisionError('') } })
      .catch(cause => { if (!controller.signal.aborted) setRevisionError(cause instanceof Error ? cause.message : '설정 revision을 조회할 수 없습니다.') })
      .finally(() => { if (!controller.signal.aborted) setRevisionLoading(false) })
    return () => controller.abort()
  }, [auth])
  const ready = uploads.filter(upload => upload.state === 'ready')
  const currentOptions = ready.filter(upload => Object.entries(filters).every(([key, value]) => !value || upload[key] === value))
  return <Modal title="새 비교 요청" onClose={onClose}><form onSubmit={event => prevent(event, () => action.run(async () => {
    if (revisionLoading || revisionError) throw new Error(revisionError || '설정 revision을 불러오는 중입니다.')
    const pairs = jsonObject(powerPairs)
    if (!Array.isArray(pairs.power_pairs) || Object.keys(pairs).some(key => key !== 'power_pairs')) throw new Error('Power pairs JSON은 power_pairs 배열만 포함해야 합니다.')
    const result = await request(auth, '/comparisons', comparisonCreatedSchema, {
      current_upload_id: current, baseline_upload_id: baseline || null, comparison_kind: kind,
      scope: { kind: scope, target_ids: scope === 'targets' ? targets : [], include_descendants: descendants },
      mapping_id: mapping || null, change_ids: changes, policy: jsonObject(policy),
      recipients: recipients.split(/[\n,;]/).map(value => value.trim()).filter(Boolean), power_pairs: pairs.power_pairs,
    })
    await refresh(); onCreated(result.comparison.id)
  }))}><div className="notice">현재·기준 선택은 아래에서 명시적으로 수행합니다. 업로드 화면의 shared 선택이나 가장 최근 파일을 자동으로 사용하지 않습니다.</div><Field label="비교 기준 종류"><select value={kind} onChange={event => setKind(event.target.value)}><option value="previous_version">이전 버전 · previous_version</option><option value="previous_project">이전 프로젝트 · previous_project</option></select></Field><Field label="현재 업로드 (필수)"><select required value={current} onChange={event => setCurrent(event.target.value)}><option value="">현재 파일을 선택하세요</option>{currentOptions.map(upload => <option key={upload.id} value={upload.id}>{uploadName(upload)}</option>)}</select></Field><Field label="기준 업로드 (선택)" hint="없음을 선택하면 기준 측은 unavailable입니다. 이전 버전/프로젝트 계보를 검증합니다."><select value={baseline} onChange={event => setBaseline(event.target.value)}><option value="">기준 파일 없음 · unavailable</option>{ready.filter(upload => upload.id !== current).map(upload => <option key={upload.id} value={upload.id}>{uploadName(upload)}</option>)}</select></Field>{current && <Json value={uploads.find(upload => upload.id === current)} label="현재 파일 조건 확인" />}{baseline && <Json value={uploads.find(upload => upload.id === baseline)} label="기준 파일 조건 확인" />}<div className="form-grid"><Field label="분석 범위"><select value={scope} onChange={event => setScope(event.target.value)}><option value="all">전체 hierarchy</option><option value="targets">선택한 Targets</option></select></Field><Field label="매핑 revision (선택)"><select value={mapping} onChange={event => setMapping(event.target.value)}><option value="">명시적 매핑 없음</option><RecordOptions records={revisions.filter(record => record.record_type === 'mapping')} /></select></Field></div>{scope === 'targets' && <><MultiRecords label="분석할 Target revisions" records={records.filter(record => record.record_type === 'target')} value={targets} onChange={setTargets} /><label className="checkbox"><input type="checkbox" checked={descendants} onChange={event => setDescendants(event.target.checked)} />하위 노드의 행도 분석 (부모·자식 누적값을 합산하지 않음)</label></>}<MultiRecords label="설명에 사용할 변경사항 revisions" records={revisions.filter(record => record.record_type === 'change')} value={changes} onChange={setChanges} hint="과거 revision도 선택할 수 있습니다. ID가 정확히 고정됩니다. Ctrl / ⌘ 또는 Shift로 다중 선택하세요." /><div className="schema-guide"><strong>검출 정책: 기본 임계값을 임의로 적용하지 않습니다.</strong><ul><li>metrics: 지표 키 → absolute_change, relative_change_pct, pct_point_change, min_denominator (선택, 0 이상 숫자)</li><li>operating_limits: 지표 키 → lower, upper, min_denominator</li><li>비율 변화는 %p입니다. 빈 정책이면 전체 수치를 계산하되 정상 판정을 하지 않고 policy_unconfigured 경고를 표시합니다.</li></ul></div><JsonEditor label="Detection policy JSON" value={policy} onChange={setPolicy} hint="숫자는 입력한 정밀도를 유지합니다." /><JsonEditor label="추가 조건별 Power pairs JSON (선택)" value={powerPairs} onChange={setPowerPairs} hint="power_pairs: [{current_upload_id, baseline_upload_id?, label?}]. 양쪽 선택 snapshot에 속한 실행 파일 ID만 허용합니다." /><Field label="알림 수신자 (선택)" hint={auth.demo ? '합성 이력에만 기록합니다. 실제 SMTP나 메일 발송은 실행하지 않습니다.' : '이메일 주소를 쉼표·세미콜론·줄바꿈으로 구분하세요. SMTP 설정은 서버에서 관리합니다.'}><textarea rows={2} value={recipients} onChange={event => setRecipients(event.target.value)} /></Field>{action.feedback}<div className="form-actions"><Button type="button" variant="outline" onClick={onClose}>취소</Button><><Submit busy={action.busy || revisionLoading}><Play size={15} />{auth.demo ? '합성 수치 계산' : 'DB 작업 큐에 요청'}</Submit>{revisionError && <div role="alert" className="notice error">{revisionError} · 대화상자를 닫고 다시 열어 조회를 재시도하세요.</div>}</></div></form></Modal>
}
function ComparisonResult({ auth, id, records, onBack }: { auth: Auth; id: string; records: TrackingRecord[]; onBack: () => void }) {
  const [detail, setDetail] = useState<ComparisonDetail | null>(null)
  const [changeHistory, setChangeHistory] = useState<TrackingRecord[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [refreshIndex, setRefreshIndex] = useState(0)
  const [poll, setPoll] = useState(false)
  const [search, setSearch] = useState('')
  const [changes, setChanges] = useState<string[]>([])
  const [recipients, setRecipients] = useState('')
  const action = useAction()
  useEffect(() => {
    const controller = new AbortController()
    let timer: number | undefined
    const load = async () => {
      setLoading(true)
      try {
        const [result, history] = await Promise.all([request(auth, `/comparisons/${encodeURIComponent(id)}`, detailSchema, undefined, controller.signal), allRecords(auth, 'change', { latest: 'false' }, controller.signal)])
        if (!controller.signal.aborted) { setDetail(result); setChangeHistory(history); setError('') }
      } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '결과를 조회할 수 없습니다.') }
      finally { if (!controller.signal.aborted) { setLoading(false); if (poll) timer = window.setTimeout(load, 10000) } }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [auth, id, refreshIndex, poll])
  const comparison = detail?.comparison
  const input = object(comparison?.payload.request)
  const coverage = object(comparison?.payload.coverage)
  const results = detail?.results || []
  const visible = results.filter(record => [text(record.payload.hier_name), text(record.payload.metric), text(record.payload.reason), text(record.payload.comparability)].join(' ').toLowerCase().includes(search.toLowerCase()))
  const frozenIds = new Set([...array(input.change_ids), ...(detail?.ai_runs || []).flatMap(run => array(run.payload.change_ids))].map(text))
  const frozenChanges = changeHistory.filter(record => frozenIds.has(record.id))
  return <><div className="detail-toolbar"><Button variant="ghost" onClick={onBack}><ArrowLeft size={16} />비교 목록</Button><div className="actions"><label className="checkbox"><input type="checkbox" checked={poll} onChange={event => setPoll(event.target.checked)} />10초 자동 갱신</label><Button variant="outline" disabled={loading} onClick={() => setRefreshIndex(value => value + 1)}><RefreshCw size={15} className={loading ? 'spin' : ''} />갱신</Button></div></div>{error && <div role="alert" className="notice error">{error}{detail && ' 마지막으로 받은 결과를 표시합니다.'}</div>}{loading && !detail && <div className="notice" role="status">비교 결과를 불러오는 중입니다…</div>}{comparison && <><section className="panel"><div className="section-heading"><div><span className="eyebrow">FROZEN ANALYSIS</span><h2>비교 결과 <Badge value={comparison.state} /></h2><div className="id-line"><code>{id}</code><CopyButton value={id} /></div></div><small>{date(comparison.created_at)}</small></div>{comparison.payload.policy_unconfigured === true && <div className="notice warning"><ShieldAlert size={19} /><div><strong>검출 정책 미설정 · policy_unconfigured</strong><p>임계값이 지정되지 않았습니다. 계산된 수치를 정상으로 간주하지 마세요. 데이터 품질·대응 불가 항목은 별도 검토 대상입니다.</p></div></div>}<div className="coverage-grid">{['expected_scopes', 'processed_scopes', 'expected_metrics', 'processed_metrics'].map(key => <div key={key}><small>{key}</small><strong>{format(coverage[key])}</strong></div>)}</div><Json value={coverage} label="전체 처리 coverage · 누락/미처리 사유" /><Json value={comparison.payload} label="고정된 요청 · 매핑/업로드 revision · 정책 확인" /><div className="table-caption"><span>전체 결과 {results.length}개 · 현재 표시 {visible.length}개</span><Field label="결과 검색"><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Hierarchy, 지표, 사유" /></Field></div>{visible.length ? <div className="table-scroll result-table"><table><thead><tr><th>Hierarchy / 지표</th><th>기준</th><th>현재</th><th>Δ 절대 변화</th><th>Δ %p</th><th>상대 변화 %</th><th>비교 가능성</th><th>AI 판단 / 근거</th></tr></thead><tbody>{visible.map(result => {
    const metric = result.payload
    const judgements = (detail?.judgements || []).filter(judgement => judgement.payload.metric_result_id === result.id)
    return <tr key={result.id}><td><strong>{text(metric.hier_name) || text(metric.group_id)}</strong><code>{text(metric.metric)}</code><small>{text(metric.unit) || '단위 미확인'}</small><Json value={{ baseline_paths: metric.baseline_paths, current_paths: metric.current_paths, baseline_numerator: metric.baseline_numerator, baseline_denominator: metric.baseline_denominator, current_numerator: metric.current_numerator, current_denominator: metric.current_denominator, power_pair: metric.power_pair, evidence: metric.evidence }} label="경로 · 분모 · 조건 · 증거" /></td><td className="numeric">{format(metric.baseline)}</td><td className="numeric">{format(metric.current)}</td><td className="numeric">{format(metric.delta)}</td><td className="numeric">{format(metric.pct_point_change)}</td><td className="numeric">{format(metric.relative_change_pct)}</td><td><Badge value={metric.comparability || result.state} />{metric.is_candidate === true && <span className="candidate-label">검토 후보</span>}<p className="reason">{text(metric.reason)}</p></td><td className="judgement-cell">{judgements.length ? judgements.map(judgement => <div key={judgement.id} className="judgement"><Badge value={judgement.payload.verdict} /><p className="reason">{text(judgement.payload.reason)}</p><Json value={judgement.payload} label="설명된 부분 · 미설명 부분 · 적용 변경 ID" /></div>) : <small>{metric.is_candidate === true ? 'AI 판정 대기 / 결과 없음' : 'AI 후보 대상 아님'}</small>}</td></tr>
  })}</tbody></table></div> : <Empty title={search ? '검색 조건에 맞는 결과가 없습니다' : '아직 저장된 수치 결과가 없습니다'}>작업 상태와 coverage를 확인하세요. 처리되지 않은 결과를 정상으로 표시하지 않습니다.</Empty>}</section><section className="panel"><div className="section-heading"><div><span className="eyebrow">EXPLANATION PROVENANCE</span><h2>AI 근거와 변경 이력</h2><p>수치는 고정하고 새로운 변경사항 revision으로 설명을 다시 요청할 수 있습니다.</p></div><Badge value={comparison.payload.ai_state || 'not_available'} /></div><div className="two-columns"><div><h3>실제로 선택된 변경사항 revisions</h3>{frozenChanges.length ? frozenChanges.map(change => <article className="history-item" key={change.id}><div className="card-line"><strong>r{change.revision}</strong><CopyButton value={change.id} /></div><code>{change.id}</code><p className="prose">{text(change.payload.text)}</p><Json value={change.payload} label="정확한 원문과 적용 메타데이터" /></article>) : <p className="muted">선택된 변경사항이 없습니다. 요청 payload의 change_ids를 함께 확인하세요.</p>}{frozenIds.size > frozenChanges.length && <div className="notice warning">일부 변경 revision 원문을 조회하지 못했습니다. 고정된 ID: {[...frozenIds].join(', ')}</div>}</div><div><h3>{auth.demo ? '합성 재검토 이력 · AI 호출 없음' : 'AI 실행 이력'} · {detail?.ai_runs.length || 0}회</h3>{detail?.ai_runs.length ? detail.ai_runs.map(run => <article className="history-item" key={run.id}><div className="card-line"><code>{run.id}</code><Badge value={run.state} /></div><small>{date(run.created_at)}</small><Json value={run.payload} label="실행 입력 · 프롬프트 버전 · coverage · 오류" /><Json value={detail.judgements.filter(judgement => judgement.parent_id === run.id)} label="이 실행의 전체 판단 이력" /></article>) : <p className="muted">AI 실행 기록이 없습니다.</p>}</div></div><form className="rejudge-form" onSubmit={event => prevent(event, () => action.run(async () => { await request(auth, `/comparisons/${encodeURIComponent(id)}/rejudge`, unknownSchema, { change_ids: changes, recipients: recipients.split(/[\n,;]/).map(value => value.trim()).filter(Boolean) }); setRefreshIndex(value => value + 1) }, auth.demo ? '합성 재검토 이력을 저장했습니다. 실제 AI·메일은 호출하지 않았으며 needs_review로 남습니다.' : '기존 수치 결과에 대한 AI 재판정을 요청했습니다.'))}><MultiRecords label="재판정에 사용할 정확한 변경사항 revisions" records={changeHistory} value={changes} onChange={setChanges} /><Field label="재판정 알림 수신자 (선택)"><textarea rows={2} value={recipients} onChange={event => setRecipients(event.target.value)} placeholder="이메일을 쉼표로 구분" /></Field>{action.feedback}<><Submit busy={action.busy || comparison.state !== 'completed'}>{auth.demo ? '합성 재검토 기록' : '이 비교를 AI 재판정'}</Submit>{comparison.state !== 'completed' && <p className="muted">수치 비교가 completed 상태가 된 뒤 재판정할 수 있습니다.</p>}</></form></section></>}</>
}
