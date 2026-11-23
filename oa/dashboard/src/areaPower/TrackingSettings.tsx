import { useEffect, useMemo, useState } from 'react'
import { Eye, Plus, Sparkles, Trash2 } from 'lucide-react'
import { array, format, object, request, text, unknownSchema, uploadName, type Auth, type TrackingRecord, type Upload } from './api'
import { Badge, Button, Field, useAction } from './components/workspace'
import type { AtlasProject } from '../atlasContext'
import {
  cornerSpecId,
  trackingPreviewSchema,
  type CornerSpec,
  type TrackingOverviewData,
  type TrackingPreviewData,
} from './trackingApi'

interface TrackingSettingsProps {
  auth: Auth
  project: AtlasProject
  baselineProject?: AtlasProject
  areaProjectId: string
  baselineAreaProjectId?: string | null
  uploads: Upload[]
  records: TrackingRecord[]
  data: TrackingOverviewData | null
  initialBlockId?: string
  onReload: () => Promise<void>
}

export function TrackingSettings({
  auth,
  project,
  baselineProject,
  areaProjectId,
  baselineAreaProjectId,
  uploads,
  records,
  data,
  initialBlockId,
  onReload,
}: TrackingSettingsProps) {
  const action = useAction()
  const projCfg = object(data?.project_config)

  // Project-level tracking settings state
  const [enabled, setEnabled] = useState(false)
  const [deadlineDays, setDeadlineDays] = useState('7')
  const [deadlineTime, setDeadlineTime] = useState('18:00')
  const [deadlineTz, setDeadlineTz] = useState('Asia/Seoul')
  const [topRecipients, setTopRecipients] = useState('')
  const [areaPct, setAreaPct] = useState('10')
  const [powerPct, setPowerPct] = useState('10')
  const [minArea, setMinArea] = useState('50')
  const [defaultCorners, setDefaultCorners] = useState<CornerSpec[]>([])
  const [newCornerLabel, setNewCornerLabel] = useState('')
  const [newCornerProc, setNewCornerProc] = useState('')
  const [newCornerVolt, setNewCornerVolt] = useState('')
  const [newCornerTemp, setNewCornerTemp] = useState('')
  const [newCornerPex, setNewCornerPex] = useState('')
  const [powerTargets, setPowerTargets] = useState<Array<{ effective_net_ver: string; target_mw: string; label: string }>>([])
  const [targetNet, setTargetNet] = useState('')
  const [targetMw, setTargetMw] = useState('')
  const [typicalSpecs, setTypicalSpecs] = useState<Array<{ effective_net_ver: string; corner: CornerSpec; label: string }>>([])
  const [typNet, setTypNet] = useState('')
  const [typProc, setTypProc] = useState('')
  const [typVolt, setTypVolt] = useState('')

  useEffect(() => {
    setEnabled(projCfg.enabled === true)
    setDeadlineDays(text(projCfg.deadline_days ?? 7))
    setDeadlineTime(text(projCfg.deadline_time) || '18:00')
    setDeadlineTz(text(projCfg.deadline_timezone) || 'Asia/Seoul')
    setTopRecipients(array(projCfg.top_recipients).map(text).join(', '))
    const th = object(projCfg.thresholds)
    setAreaPct(text(th.area_increase_pct ?? 10))
    setPowerPct(text(th.power_increase_pct ?? 10))
    setMinArea(text(th.min_area ?? 50))
    setDefaultCorners(array(projCfg.default_required_corners).map(item => {
      const o = object(item)
      return {
        label: text(o.label),
        process_label: text(o.process_label),
        voltage_v: text(o.voltage_v) || null,
        temp_c: text(o.temp_c) || null,
        pex: text(o.pex) || null,
      }
    }))
    setPowerTargets(array(projCfg.power_targets).map(item => {
      const o = object(item)
      return { effective_net_ver: text(o.effective_net_ver), target_mw: text(o.target_mw), label: text(o.label) }
    }))
    setTypicalSpecs(array(projCfg.typical_specs).map(item => {
      const o = object(item)
      const c = object(o.corner)
      return {
        effective_net_ver: text(o.effective_net_ver),
        label: text(o.label),
        corner: {
          label: text(c.label) || text(c.process_label),
          process_label: text(c.process_label),
          voltage_v: text(c.voltage_v) || null,
          temp_c: text(c.temp_c) || null,
          pex: text(c.pex) || null,
        },
      }
    }))
  }, [data?.project_config?.id, data?.project_config?.row_version])

  // Block-level tracking settings state
  const areaBlocks = useMemo(() => project.blocks.filter(b => Boolean(b.area_id)), [project.blocks])
  const [selectedBlockId, setSelectedBlockId] = useState(initialBlockId || '')
  useEffect(() => {
    if (initialBlockId) setSelectedBlockId(initialBlockId)
    else if (!selectedBlockId && areaBlocks.length) setSelectedBlockId(areaBlocks[0].area_id!)
  }, [initialBlockId, areaBlocks])

  const selectedBlock = areaBlocks.find(b => b.area_id === selectedBlockId)
  const blockCfg = useMemo(
    () => (data?.block_configs || []).find(row => text(row.block_id) === selectedBlockId),
    [data?.block_configs, selectedBlockId],
  )
  const [blockEnabled, setBlockEnabled] = useState(true)
  const [blockKind, setBlockKind] = useState<'previous_project' | 'previous_version'>('previous_project')
  const [baselineBlockId, setBaselineBlockId] = useState('')
  const [designStage, setDesignStage] = useState('')
  const [blockRecipients, setBlockRecipients] = useState('')
  const [blockCorners, setBlockCorners] = useState<CornerSpec[]>([])
  const [blockCornerLabel, setBlockCornerLabel] = useState('')
  const [blockCornerProc, setBlockCornerProc] = useState('')
  const [approvedBaselines, setApprovedBaselines] = useState<Record<string, string>>({})
  const [approvedMappingId, setApprovedMappingId] = useState('')
  const [noChangeNets, setNoChangeNets] = useState('')

  useEffect(() => {
    const cfg = object(blockCfg)
    setBlockEnabled(cfg.enabled !== false)
    setBlockKind(text(cfg.comparison_kind) === 'previous_version' ? 'previous_version' : 'previous_project')
    setBaselineBlockId(text(cfg.baseline_block_id))
    setDesignStage(text(cfg.design_stage))
    setBlockRecipients(array(cfg.recipients).map(text).join(', '))
    setBlockCorners(array(cfg.required_corners).map(item => {
      const o = object(item)
      return {
        label: text(o.label),
        process_label: text(o.process_label),
        voltage_v: text(o.voltage_v) || null,
        temp_c: text(o.temp_c) || null,
        pex: text(o.pex) || null,
      }
    }))
    const baseMap: Record<string, string> = {}
    for (const item of array(cfg.approved_baselines).map(object)) {
      if (text(item.corner_id) && text(item.upload_id)) baseMap[text(item.corner_id)] = text(item.upload_id)
    }
    setApprovedBaselines(baseMap)
    setApprovedMappingId(text(cfg.approved_mapping_id))
    setNoChangeNets(array(cfg.no_change_net_vers).map(text).join(', '))
  }, [selectedBlockId, blockCfg?.id, blockCfg?.row_version])

  const effectiveBlockCorners = blockCorners.length ? blockCorners : defaultCorners
  const candidateBaselineUploads = useMemo(() => {
    const targetProjId = blockKind === 'previous_version' ? areaProjectId : baselineAreaProjectId
    const targetBlkId = blockKind === 'previous_version' ? selectedBlockId : baselineBlockId
    return uploads.filter(u =>
      u.state === 'ready'
      && (!targetProjId || u.project_id === targetProjId)
      && (!targetBlkId || u.block_id === targetBlkId),
    )
  }, [uploads, blockKind, areaProjectId, baselineAreaProjectId, selectedBlockId, baselineBlockId])

  const blockMappings = useMemo(
    () => records.filter(row => row.record_type === 'mapping' && text(row.payload.current_block_id) === selectedBlockId),
    [records, selectedBlockId],
  )

  // Design info expected changes
  const [expText, setExpText] = useState('')
  const [expNet, setExpNet] = useState('')
  const [expPath, setExpPath] = useState('')
  const [expMetric, setExpMetric] = useState<'logic_area' | 'pwr_all_total_mw'>('logic_area')
  const [expCond, setExpCond] = useState<'any' | 'typical' | 'same_condition'>('any')
  const [expDir, setExpDir] = useState<'increase' | 'decrease'>('increase')
  const [expMin, setExpMin] = useState('')
  const [expMax, setExpMax] = useState('')
  const [expQuote, setExpQuote] = useState('')
  const [preview, setPreview] = useState<TrackingPreviewData | null>(null)

  const blockExpectations = useMemo(
    () => (data?.expectations || []).filter(item => text(item.block_id) === selectedBlockId),
    [data?.expectations, selectedBlockId],
  )

  async function saveProjectSettings() {
    const days = Number(deadlineDays)
    if (!Number.isInteger(days) || days < 1 || days > 90) throw new Error('마감 기간은 1~90일 정수여야 합니다.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/config`, unknownSchema, {
      enabled,
      default_required_corners: defaultCorners,
      deadline_days: days,
      deadline_time: deadlineTime.trim(),
      deadline_timezone: deadlineTz.trim(),
      top_recipients: topRecipients.split(/[,;\s]+/).filter(Boolean),
      thresholds: {
        area_increase_pct: areaPct.trim(),
        power_increase_pct: powerPct.trim(),
        min_area: minArea.trim(),
      },
      typical_specs: typicalSpecs.map(s => ({ effective_net_ver: s.effective_net_ver, corner: s.corner, analysis_conditions: {}, label: s.label })),
      power_targets: powerTargets.map(t => ({ effective_net_ver: t.effective_net_ver, target_mw: t.target_mw, label: t.label })),
      ...(projCfg.row_version ? { expected_row_version: Number(projCfg.row_version) } : {}),
    })
    await onReload()
  }

  async function saveBlockSettings() {
    if (!selectedBlockId) throw new Error('설정할 블록을 선택하세요.')
    const baselines = Object.entries(approvedBaselines)
      .filter(([, uploadId]) => Boolean(uploadId))
      .map(([corner_id, upload_id]) => ({ corner_id, upload_id }))
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/config`, unknownSchema, {
      enabled: blockEnabled,
      comparison_kind: blockKind,
      baseline_project_id: blockKind === 'previous_version' ? areaProjectId : (baselineAreaProjectId || null),
      baseline_block_id: blockKind === 'previous_version' ? selectedBlockId : (baselineBlockId || null),
      design_stage: designStage.trim() || null,
      required_corners: blockCorners,
      recipients: blockRecipients.split(/[,;\s]+/).filter(Boolean),
      approved_baselines: baselines,
      approved_mapping_id: approvedMappingId || null,
      no_change_net_vers: noChangeNets.split(/[,;\s]+/).filter(Boolean),
      ...(blockCfg?.row_version ? { expected_row_version: Number(blockCfg.row_version) } : {}),
    })
    await onReload()
  }

  async function saveExpectation() {
    if (!selectedBlockId) throw new Error('블록을 먼저 선택하세요.')
    if (!expPath.trim()) throw new Error('대상 instance 경로를 입력하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/expectations`, unknownSchema, {
      effective_net_ver: expNet.trim() || null,
      target_paths: [expPath.trim()],
      scope: 'subtree',
      metric: expMetric,
      comparison_condition: expCond,
      direction: expDir,
      min_pct: expMin.trim() ? expMin.trim() : null,
      max_pct: expMax.trim() ? expMax.trim() : null,
      quote: expQuote.trim(),
    })
    setExpPath(''); setExpMin(''); setExpMax(''); setExpQuote('')
    await onReload()
  }

  async function proposeExpectation() {
    if (!selectedBlockId) throw new Error('블록을 먼저 선택하세요.')
    if (!expText.trim()) throw new Error('Design info 원문을 입력하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/expectations/propose`, unknownSchema, {
      request_id: crypto.randomUUID(),
      effective_net_ver: expNet.trim() || null,
      source_text: expText.trim(),
    })
    await onReload()
  }

  async function loadPreview() {
    const value = await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/preview`, trackingPreviewSchema, {
      net_ver: data?.rounds[0]?.net_ver || null,
    })
    setPreview(value)
  }

  const aiModel = data?.ai_model

  return (
    <div className="tracking-settings">
      {action.feedback}

      <section className="panel" aria-label="프로젝트 추적 설정">
        <div className="section-heading">
          <div>
            <h2>프로젝트 자동 추적 설정 · {project.name}</h2>
            <p>기본 필수 corner, 마감 기간·시각, TOP 수신자, Logic Area·Total Power 기준과 대표 조건·목표 전력을 관리합니다.</p>
          </div>
          <label className="checkbox">
            <input type="checkbox" checked={enabled} disabled={!project.can_write} onChange={event => setEnabled(event.target.checked)} />
            <span>자동 추적 활성화</span>
          </label>
        </div>

        {aiModel && (
          <p className={aiModel.ready ? 'notice' : 'notice warning'}>
            중앙 VWP 프로젝트 비교 AI: {aiModel.model ? `${aiModel.site === 'vwp' ? 'VWP' : 'OA'} · ${aiModel.connection_label} · ${aiModel.model}` : '미설정'}
            {!aiModel.ready && ` · 활성화 시 추적 담당자의 VWP AI 연결과 본인 인증값이 필요합니다 (${aiModel.error || 'AI_VWP_CONNECTION_REQUIRED'})`}
          </p>
        )}

        <div className="tracking-settings-grid">
          <Field label="마감 기간 (실제 netlist 도착일 + N일)">
            <input type="number" min={1} max={90} value={deadlineDays} disabled={!project.can_write} onChange={event => setDeadlineDays(event.target.value)} />
          </Field>
          <Field label="마감 시각 (HH:MM)">
            <input type="time" value={deadlineTime} disabled={!project.can_write} onChange={event => setDeadlineTime(event.target.value)} />
          </Field>
          <Field label="마감 시간대 (IANA)">
            <input type="text" value={deadlineTz} disabled={!project.can_write} onChange={event => setDeadlineTz(event.target.value)} placeholder="Asia/Seoul" />
          </Field>
          <Field label="관리자 · TOP 배포 목록 (쉼표 구분)">
            <input type="text" value={topRecipients} disabled={!project.can_write} onChange={event => setTopRecipients(event.target.value)} placeholder="lead@example.com, top@example.com" />
          </Field>
          <Field label="Logic Area 증가 후보 기준 (%)">
            <input type="text" value={areaPct} disabled={!project.can_write} onChange={event => setAreaPct(event.target.value)} />
          </Field>
          <Field label="Total Power 증가 후보 기준 (%)">
            <input type="text" value={powerPct} disabled={!project.can_write} onChange={event => setPowerPct(event.target.value)} />
          </Field>
          <Field label="Logic Area 최소 크기">
            <input type="text" value={minArea} disabled={!project.can_write} onChange={event => setMinArea(event.target.value)} />
          </Field>
        </div>

        <div className="tracking-subblock">
          <h3>프로젝트 기본 필수 corner</h3>
          <div className="tracking-chip-list">
            {defaultCorners.map((c, idx) => (
              <span key={cornerSpecId(c)} className="tracking-chip">
                {c.label} ({c.process_label}{c.voltage_v ? ` / ${c.voltage_v}V` : ''}{c.temp_c ? ` / ${c.temp_c}°C` : ''}{c.pex ? ` / ${c.pex}` : ''})
                {project.can_write && (
                  <button type="button" aria-label={`${c.label} 삭제`} onClick={() => setDefaultCorners(rows => rows.filter((_, i) => i !== idx))}>
                    <Trash2 size={12} />
                  </button>
                )}
              </span>
            ))}
            {!defaultCorners.length && <small>등록된 기본 corner가 없습니다.</small>}
          </div>
          {project.can_write && (
            <div className="tracking-inline-add">
              <input placeholder="표시명 (예: TT)" value={newCornerLabel} onChange={e => setNewCornerLabel(e.target.value)} />
              <input placeholder="process_label (예: TT)" value={newCornerProc} onChange={e => setNewCornerProc(e.target.value)} />
              <input placeholder="전압 V (선택)" value={newCornerVolt} onChange={e => setNewCornerVolt(e.target.value)} />
              <input placeholder="온도 °C (선택)" value={newCornerTemp} onChange={e => setNewCornerTemp(e.target.value)} />
              <input placeholder="PEX (선택)" value={newCornerPex} onChange={e => setNewCornerPex(e.target.value)} />
              <Button variant="outline" size="sm" onClick={() => {
                if (!newCornerLabel.trim() || !newCornerProc.trim()) return
                setDefaultCorners(rows => [...rows, {
                  label: newCornerLabel.trim(),
                  process_label: newCornerProc.trim(),
                  voltage_v: newCornerVolt.trim() || null,
                  temp_c: newCornerTemp.trim() || null,
                  pex: newCornerPex.trim() || null,
                }])
                setNewCornerLabel(''); setNewCornerProc(''); setNewCornerVolt(''); setNewCornerTemp(''); setNewCornerPex('')
              }}><Plus size={14} />corner 추가</Button>
            </div>
          )}
        </div>

        <div className="tracking-subblock two-columns">
          <div>
            <h3>전체 목표 전력 (Full-chip Typical 루트 · 버전 관리)</h3>
            <ul className="tracking-mini-list">
              {powerTargets.map((t, idx) => (
                <li key={t.effective_net_ver}>
                  <span>{t.effective_net_ver}부터 · <strong>{t.target_mw} mW 이하</strong></span>
                  {project.can_write && <button type="button" onClick={() => setPowerTargets(rows => rows.filter((_, i) => i !== idx))}><Trash2 size={12} /></button>}
                </li>
              ))}
              {!powerTargets.length && <li className="muted">미설정</li>}
            </ul>
            {project.can_write && (
              <div className="tracking-inline-add">
                <input placeholder="적용 NET_VER" value={targetNet} onChange={e => setTargetNet(e.target.value)} />
                <input placeholder="목표 mW (예: 85)" value={targetMw} onChange={e => setTargetMw(e.target.value)} />
                <Button variant="outline" size="sm" onClick={() => {
                  if (!targetNet.trim() || !targetMw.trim()) return
                  setPowerTargets(rows => [...rows.filter(r => r.effective_net_ver !== targetNet.trim()), { effective_net_ver: targetNet.trim(), target_mw: targetMw.trim(), label: '' }])
                  setTargetNet(''); setTargetMw('')
                }}><Plus size={14} />추가</Button>
              </div>
            )}
          </div>
          <div>
            <h3>Typical 대표 조건 (버전 관리)</h3>
            <ul className="tracking-mini-list">
              {typicalSpecs.map((s, idx) => (
                <li key={s.effective_net_ver}>
                  <span>{s.effective_net_ver}부터 · <strong>{s.corner.process_label}{s.corner.voltage_v ? ` @ ${s.corner.voltage_v} V` : ''}</strong></span>
                  {project.can_write && <button type="button" onClick={() => setTypicalSpecs(rows => rows.filter((_, i) => i !== idx))}><Trash2 size={12} /></button>}
                </li>
              ))}
              {!typicalSpecs.length && <li className="muted">미설정</li>}
            </ul>
            {project.can_write && (
              <div className="tracking-inline-add">
                <input placeholder="적용 NET_VER" value={typNet} onChange={e => setTypNet(e.target.value)} />
                <input placeholder="process (예: TT)" value={typProc} onChange={e => setTypProc(e.target.value)} />
                <input placeholder="전압 V (예: 0.70)" value={typVolt} onChange={e => setTypVolt(e.target.value)} />
                <Button variant="outline" size="sm" onClick={() => {
                  if (!typNet.trim() || !typProc.trim()) return
                  setTypicalSpecs(rows => [...rows.filter(r => r.effective_net_ver !== typNet.trim()), {
                    effective_net_ver: typNet.trim(),
                    label: `${typProc.trim()} ${typVolt.trim() ? typVolt.trim() + 'V' : ''}`.trim(),
                    corner: { label: typProc.trim(), process_label: typProc.trim(), voltage_v: typVolt.trim() || null, temp_c: null, pex: null },
                  }])
                  setTypNet(''); setTypProc(''); setTypVolt('')
                }}><Plus size={14} />추가</Button>
              </div>
            )}
          </div>
        </div>

        <div className="form-actions">
          <Button variant="outline" size="sm" disabled={action.busy} onClick={() => action.run(loadPreview, '활성화 전 미리보기를 생성했습니다.')}>
            <Eye size={14} />활성화 전 메일 미리보기
          </Button>
          <Button size="sm" disabled={action.busy || !project.can_write} onClick={() => action.run(saveProjectSettings, '프로젝트 추적 설정을 저장했습니다.')}>
            프로젝트 설정 저장
          </Button>
        </div>
      </section>

      {preview && (
        <section className="panel tracking-preview-panel" aria-label="활성화 전 미리보기">
          <h3>활성화 전 메일 미리보기 {preview.round ? `· ${preview.round.net_ver}` : ''}</h3>
          {!preview.top_mail_preview ? (
            <p className="muted">등록된 NET_VER 회차가 없어 미리보기를 만들 수 없습니다.</p>
          ) : (
            <div className="tracking-preview-grid">
              <div>
                <strong>TOP 요약 메일 · {preview.top_mail_preview.subject}</strong>
                <pre>{preview.top_mail_preview.body}</pre>
              </div>
              {preview.block_mail_previews.map(bp => (
                <div key={bp.block_id}>
                  <strong>블록 메일 ({bp.block_name}) · {bp.subject}</strong>
                  <pre>{bp.body}</pre>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="panel" aria-label="블록별 추적 설정">
        <div className="section-heading">
          <div>
            <h2>블록별 추적 설정 · 기준 실행 고정 · 예상 변화</h2>
            <p>CSV가 없어도 기준 블록 연결을 먼저 저장할 수 있습니다. 기준 실행을 승인하면 기준 쪽에 새 CSV가 올라와도 기존 기준을 유지합니다.</p>
          </div>
          <Field label="설정할 블록">
            <select value={selectedBlockId} onChange={e => setSelectedBlockId(e.target.value)}>
              {areaBlocks.map(b => (
                <option key={b.area_id!} value={b.area_id!}>
                  {b.name}{b.name.trim() === project.name.trim() ? ' (Full-chip)' : ''}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {selectedBlock && (
          <>
            <div className="tracking-settings-grid">
              <label className="checkbox">
                <input type="checkbox" checked={blockEnabled} disabled={!project.can_write} onChange={e => setBlockEnabled(e.target.checked)} />
                <span>이 블록 자동 추적 사용</span>
              </label>
              <Field label="비교 기준">
                <select value={blockKind} disabled={!project.can_write} onChange={e => setBlockKind(e.target.value as 'previous_project' | 'previous_version')}>
                  <option value="previous_project">지정 기준 프로젝트 ({baselineProject?.name || '미설정'})</option>
                  <option value="previous_version">같은 프로젝트의 이전 버전</option>
                </select>
              </Field>
              {blockKind === 'previous_project' && (
                <Field label="기준 블록 (CSV 없어도 먼저 연결 가능)">
                  <select value={baselineBlockId} disabled={!project.can_write || !baselineProject} onChange={e => setBaselineBlockId(e.target.value)}>
                    <option value="">기준 블록 선택</option>
                    {(baselineProject?.blocks || []).filter(b => Boolean(b.area_id)).map(b => (
                      <option key={b.area_id!} value={b.area_id!}>{b.name}</option>
                    ))}
                  </select>
                </Field>
              )}
              <Field label="설계 단계 필터 (선택)">
                <input type="text" value={designStage} disabled={!project.can_write} onChange={e => setDesignStage(e.target.value)} placeholder="예: post_route (비우면 전체)" />
              </Field>
              <Field label="블록 담당자 개인 주소 (쉼표 구분)">
                <input type="text" value={blockRecipients} disabled={!project.can_write} onChange={e => setBlockRecipients(e.target.value)} placeholder="owner@example.com" />
              </Field>
              <Field label="승인된 구조 대응 (매핑)">
                <select value={approvedMappingId} disabled={!project.can_write} onChange={e => setApprovedMappingId(e.target.value)}>
                  <option value="">기본 상대 경로 대응 사용</option>
                  {blockMappings.map(m => (
                    <option key={m.id} value={m.id}>
                      r{m.revision} · 규칙 {array(m.payload.rules).length}개 · {m.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="담당자 확인 ‘변경 없음’ NET_VER (메모 미입력과 구분)">
                <input type="text" value={noChangeNets} disabled={!project.can_write} onChange={e => setNoChangeNets(e.target.value)} placeholder="예: NET_022, NET_023" />
              </Field>
            </div>

            <div className="tracking-subblock">
              <h3>블록 필수 corner (비우면 프로젝트 기본 corner 상속)</h3>
              <div className="tracking-chip-list">
                {blockCorners.map((c, idx) => (
                  <span key={cornerSpecId(c)} className="tracking-chip">
                    {c.label} ({c.process_label})
                    {project.can_write && (
                      <button type="button" onClick={() => setBlockCorners(rows => rows.filter((_, i) => i !== idx))}>
                        <Trash2 size={12} />
                      </button>
                    )}
                  </span>
                ))}
                {!blockCorners.length && <small>프로젝트 기본 필수 corner ({defaultCorners.map(c => c.label).join(', ') || '없음'}) 상속 중</small>}
              </div>
              {project.can_write && (
                <div className="tracking-inline-add">
                  <input placeholder="표시명 (예: SS)" value={blockCornerLabel} onChange={e => setBlockCornerLabel(e.target.value)} />
                  <input placeholder="process_label (예: SS)" value={blockCornerProc} onChange={e => setBlockCornerProc(e.target.value)} />
                  <Button variant="outline" size="sm" onClick={() => {
                    if (!blockCornerLabel.trim() || !blockCornerProc.trim()) return
                    setBlockCorners(rows => [...rows, { label: blockCornerLabel.trim(), process_label: blockCornerProc.trim(), voltage_v: null, temp_c: null, pex: null }])
                    setBlockCornerLabel(''); setBlockCornerProc('')
                  }}><Plus size={14} />블록 corner 추가</Button>
                </div>
              )}
            </div>

            <div className="tracking-subblock">
              <h3>corner별 승인된 기준 실행 고정</h3>
              <div className="tracking-settings-grid">
                {effectiveBlockCorners.map(c => {
                  const cid = cornerSpecId(c)
                  return (
                    <Field key={cid} label={`${c.label} 승인 기준 실행`}>
                      <select
                        value={approvedBaselines[cid] || ''}
                        disabled={!project.can_write}
                        onChange={e => setApprovedBaselines(prev => ({ ...prev, [cid]: e.target.value }))}
                      >
                        <option value="">기준 실행 미승인</option>
                        {candidateBaselineUploads.map(u => (
                          <option key={u.id} value={u.id}>{uploadName(u)}</option>
                        ))}
                      </select>
                    </Field>
                  )
                })}
                {!effectiveBlockCorners.length && <small>필수 corner를 먼저 하나 이상 지정하세요.</small>}
              </div>
            </div>

            <div className="form-actions">
              <Button size="sm" disabled={action.busy || !project.can_write} onClick={() => action.run(saveBlockSettings, '블록 추적 설정을 저장했습니다. 대기 중인 CSV가 있으면 자동으로 분석을 이어갑니다.')}>
                블록 설정 저장
              </Button>
            </div>

            <div className="tracking-subblock">
              <h3>Design info 승인 예상 변화 (코드 대조 적용)</h3>
              <p className="muted">AI가 제안하거나 담당자가 승인한 증감 범위는 업로드 시 코드가 자동 대조합니다. 범위 안 변화는 ‘설명됨’으로 집계하며, 목표 전력 초과는 별도로 알립니다.</p>
              <ul className="tracking-mini-list">
                {blockExpectations.map(exp => (
                  <li key={text(exp.id)}>
                    <span>
                      <strong>{array(exp.target_paths).map(text).join(', ')}</strong> · {exp.metric === 'logic_area' ? 'Logic Area' : 'Total Power'} · {exp.direction === 'increase' ? '증가' : '감소'}{' '}
                      {exp.min_pct != null || exp.max_pct != null ? `${format(exp.min_pct ?? 0)}~${format(exp.max_pct ?? '?')}%` : '(방향만)'}
                      {text(exp.effective_net_ver) ? ` · ${text(exp.effective_net_ver)}부터` : ''}
                      {text(exp.quote) ? ` · "${text(exp.quote)}"` : ''}
                    </span>
                  </li>
                ))}
                {!blockExpectations.length && <li className="muted">승인된 예상 변화가 없습니다.</li>}
              </ul>

              {project.can_write && (
                <div className="tracking-expectation-editor">
                  <div className="tracking-inline-add">
                    <input placeholder="적용 NET_VER (선택)" value={expNet} onChange={e => setExpNet(e.target.value)} />
                    <input placeholder="대상 경로 (예: GPU/u_cache)" value={expPath} onChange={e => setExpPath(e.target.value)} />
                    <select value={expMetric} onChange={e => setExpMetric(e.target.value as 'logic_area' | 'pwr_all_total_mw')}>
                      <option value="logic_area">Logic Area</option>
                      <option value="pwr_all_total_mw">Total Power</option>
                    </select>
                    <select value={expCond} onChange={e => setExpCond(e.target.value as 'any' | 'typical' | 'same_condition')}>
                      <option value="any">모든 비교 조건</option>
                      <option value="typical">Typical 대표</option>
                      <option value="same_condition">동일 조건</option>
                    </select>
                    <select value={expDir} onChange={e => setExpDir(e.target.value as 'increase' | 'decrease')}>
                      <option value="increase">증가</option>
                      <option value="decrease">감소</option>
                    </select>
                    <input placeholder="최소 %" value={expMin} onChange={e => setExpMin(e.target.value)} />
                    <input placeholder="최대 %" value={expMax} onChange={e => setExpMax(e.target.value)} />
                    <input placeholder="근거 문구" value={expQuote} onChange={e => setExpQuote(e.target.value)} />
                    <Button variant="outline" size="sm" disabled={action.busy} onClick={() => action.run(saveExpectation, '승인 예상 변화를 저장했습니다.')}>
                      <Plus size={14} />승인 등록
                    </Button>
                  </div>
                  <div className="tracking-ai-propose">
                    <textarea rows={2} value={expText} onChange={e => setExpText(e.target.value)} placeholder="Design info 원문을 붙여넣으면 중앙 VWP AI가 예상 증감 범위를 제안합니다." />
                    <Button variant="outline" size="sm" disabled={action.busy || !expText.trim()} onClick={() => action.run(proposeExpectation, 'AI 예상 범위 제안을 요청했습니다.')}>
                      <Sparkles size={14} />AI 제안 요청
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  )
}
