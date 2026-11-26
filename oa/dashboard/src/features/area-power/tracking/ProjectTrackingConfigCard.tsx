import { useEffect, useState } from 'react'
import { ChevronRight, Eye, Plus, Trash2 } from 'lucide-react'
import { array, object, request, text, unknownSchema, type Auth } from '../api'
import { Button, Field } from '../components/workspace'
import type { AtlasProject } from '@/features/atlas/atlasContext'
import {
  trackingPreviewSchema,
  type CornerSpec,
  type TrackingOverviewData,
  type TrackingPreviewData,
} from '../trackingApi'
import { CornerChipListEditor } from './CornerChipListEditor'
import { parseCornerSpec, parseCornerSpecList } from './trackingUtils'

interface ProjectTrackingConfigCardProps {
  auth: Auth
  project: AtlasProject
  areaProjectId: string
  data: TrackingOverviewData | null
  busy: boolean
  runAction: (fn: () => Promise<void>, success?: string) => Promise<void>
  onReload: () => Promise<void>
}

export function ProjectTrackingConfigCard({
  auth,
  project,
  areaProjectId,
  data,
  busy,
  runAction,
  onReload,
}: ProjectTrackingConfigCardProps) {
  const projCfg = object(data?.project_config)
  const [enabled, setEnabled] = useState(false)
  const [defaultCorners, setDefaultCorners] = useState<CornerSpec[]>([])
  const [deadlineDays, setDeadlineDays] = useState('7')
  const [deadlineTime, setDeadlineTime] = useState('18:00')
  const [deadlineTz, setDeadlineTz] = useState('Asia/Seoul')
  const [topRecipients, setTopRecipients] = useState('')
  const [areaPct, setAreaPct] = useState('10')
  const [powerPct, setPowerPct] = useState('10')
  const [noiseSharePct, setNoiseSharePct] = useState('0.1')
  const [minArea, setMinArea] = useState('50')
  const [powerTargets, setPowerTargets] = useState<Array<{ effective_net_ver: string; target_mw: string; label: string }>>([])
  const [targetNet, setTargetNet] = useState('')
  const [targetMw, setTargetMw] = useState('')
  const [typicalSpecs, setTypicalSpecs] = useState<Array<{ effective_net_ver: string; corner: CornerSpec; label: string }>>([])
  const [typNet, setTypNet] = useState('')
  const [typProc, setTypProc] = useState('')
  const [typVolt, setTypVolt] = useState('')
  const [preview, setPreview] = useState<TrackingPreviewData | null>(null)

  useEffect(() => {
    setEnabled(projCfg.enabled === true)
    setDeadlineDays(text(projCfg.deadline_days ?? 7))
    setDeadlineTime(text(projCfg.deadline_time) || '18:00')
    setDeadlineTz(text(projCfg.deadline_timezone) || 'Asia/Seoul')
    setTopRecipients(array(projCfg.top_recipients).map(text).join(', '))
    const th = object(projCfg.thresholds)
    setAreaPct(text(th.area_increase_pct ?? 10))
    setPowerPct(text(th.power_increase_pct ?? 10))
    setNoiseSharePct(text(th.noise_share_pct ?? '0.1'))
    setMinArea(text(th.min_area ?? 50))
    setDefaultCorners(parseCornerSpecList(projCfg.default_required_corners))
    setPowerTargets(array(projCfg.power_targets).map(item => {
      const o = object(item)
      return { effective_net_ver: text(o.effective_net_ver), target_mw: text(o.target_mw), label: text(o.label) }
    }))
    setTypicalSpecs(array(projCfg.typical_specs).map(item => {
      const o = object(item)
      return {
        effective_net_ver: text(o.effective_net_ver),
        label: text(o.label),
        corner: parseCornerSpec(o.corner),
      }
    }))
  }, [data?.project_config?.id, data?.project_config?.row_version])

  async function saveProjectSettings() {
    const days = Number(deadlineDays)
    if (!Number.isInteger(days) || days < 1 || days > 90) throw new Error('마감 기간은 1~90일 정수여야 합니다.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/config`, unknownSchema, {
      enabled,
      default_required_corners: defaultCorners,
      deadline_days: days,
      deadline_time: deadlineTime.trim(),
      deadline_timezone: deadlineTz.trim() || 'Asia/Seoul',
      top_recipients: topRecipients.split(/[,;\s]+/).filter(Boolean),
      thresholds: {
        area_increase_pct: areaPct.trim(),
        power_increase_pct: powerPct.trim(),
        noise_share_pct: noiseSharePct.trim() || '0.1',
        min_area: minArea.trim(),
      },
      typical_specs: typicalSpecs.map(s => ({ effective_net_ver: s.effective_net_ver, corner: s.corner, analysis_conditions: {}, label: s.label })),
      power_targets: powerTargets.map(t => ({ effective_net_ver: t.effective_net_ver, target_mw: t.target_mw, label: t.label })),
      ...(projCfg.row_version ? { expected_row_version: Number(projCfg.row_version) } : {}),
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
  const latestPowerTarget = powerTargets[powerTargets.length - 1]

  return (
    <>
      <section className="panel" aria-label="자동 분석 기본 설정">
        <div className="section-heading">
          <div>
            <h2>자동 분석 · 결과 메일 기본 설정</h2>
            <p>필수 코너가 업로드되면 자동 분석을 실행하고, 완료 시 블록 메일과 마감 시 TOP 요약 메일을 발송합니다.</p>
          </div>
          <label className="settings-toggle">
            <input type="checkbox" checked={enabled} disabled={!project.can_write} onChange={event => setEnabled(event.target.checked)} />
            <span>{enabled ? '자동 분석 켜짐' : '자동 분석 꺼짐'}</span>
          </label>
        </div>

        {aiModel && (
          <p className={aiModel.ready ? 'notice' : 'notice warning'}>
            {aiModel.ready
              ? `자동 분석 AI 연결됨 · ${aiModel.connection_label || 'VWP'} (${aiModel.model})`
              : '자동 분석을 켜려면 ‘AI 연결·모델 관리’에서 중앙 VWP AI 연결과 본인 인증값이 먼저 설정되어 있어야 합니다.'}
          </p>
        )}

        <div className="settings-essential-grid">
          <Field label="마감 기준 (Netlist 도착 후)">
            <div className="settings-deadline-inline">
              <input type="number" min={1} max={90} value={deadlineDays} disabled={!project.can_write} onChange={event => setDeadlineDays(event.target.value)} aria-label="마감 일수" />
              <span>일 뒤</span>
              <input type="time" value={deadlineTime} disabled={!project.can_write} onChange={event => setDeadlineTime(event.target.value)} aria-label="마감 시각" />
            </div>
          </Field>

          <Field label="TOP 요약 메일 수신자 (쉼표 구분)">
            <input type="text" value={topRecipients} disabled={!project.can_write} onChange={event => setTopRecipients(event.target.value)} placeholder="lead@company.com, top@company.com" />
          </Field>

          <div className="field">
            <span className="field-label">기본 필수 코너</span>
            <CornerChipListEditor
              corners={defaultCorners}
              onChange={setDefaultCorners}
              canWrite={project.can_write}
              emptyText="등록된 코너 없음"
              placeholder="코너명 (예: TT, SS)"
              allowPvtDetail
              removeLabel
            />
          </div>
        </div>

        <details className="settings-advanced">
          <summary>
            <ChevronRight size={15} className="chevron" aria-hidden="true" />
            <span>세부 기준 설정</span>
            <small>
              Logic Area +{areaPct}% · Total Power +{powerPct}% · 노이즈 {noiseSharePct}% · 최소 {minArea}
              {latestPowerTarget ? ` · 목표 전력 ${latestPowerTarget.target_mw} mW` : ''}
              {typicalSpecs.length ? ` · 대표 조건 ${typicalSpecs.length}건` : ''}
              {deadlineTz !== 'Asia/Seoul' ? ` · ${deadlineTz}` : ''}
            </small>
          </summary>
          <div className="settings-advanced-body">
            <div className="tracking-settings-grid">
              <Field label="이상 감지 · Logic Area 증가 (%)">
                <input type="text" value={areaPct} disabled={!project.can_write} onChange={event => setAreaPct(event.target.value)} />
              </Field>
              <Field label="이상 감지 · Total Power 증가 (%)">
                <input type="text" value={powerPct} disabled={!project.can_write} onChange={event => setPowerPct(event.target.value)} />
              </Field>
              <Field label="기본 노이즈 비율 (루트 대비 %, 기본 0.1)">
                <input type="text" value={noiseSharePct} disabled={!project.can_write} onChange={event => setNoiseSharePct(event.target.value)} placeholder="0.1" />
              </Field>
              <Field label="최소 Logic Area 크기">
                <input type="text" value={minArea} disabled={!project.can_write} onChange={event => setMinArea(event.target.value)} />
              </Field>
              <Field label="마감 시간대">
                <input type="text" value={deadlineTz} disabled={!project.can_write} onChange={event => setDeadlineTz(event.target.value)} placeholder="Asia/Seoul" />
              </Field>
            </div>

            <div className="tracking-subblock two-columns">
              <div>
                <h3>Full-chip 목표 전력</h3>
                <ul className="tracking-mini-list">
                  {powerTargets.map((t, idx) => (
                    <li key={t.effective_net_ver}>
                      <span>{t.effective_net_ver}부터 · <strong>{t.target_mw} mW 이하</strong></span>
                      {project.can_write && <button type="button" onClick={() => setPowerTargets(rows => rows.filter((_, i) => i !== idx))}><Trash2 size={12} /></button>}
                    </li>
                  ))}
                  {!powerTargets.length && <li className="muted">설정된 목표 전력 없음</li>}
                </ul>
                {project.can_write && (
                  <div className="tracking-inline-add">
                    <input placeholder="적용 시작 버전 (예: NET_023)" value={targetNet} onChange={e => setTargetNet(e.target.value)} />
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
                <h3>대표(Typical) 비교 조건</h3>
                <ul className="tracking-mini-list">
                  {typicalSpecs.map((s, idx) => (
                    <li key={s.effective_net_ver}>
                      <span>{s.effective_net_ver}부터 · <strong>{s.corner.process_label}{s.corner.voltage_v ? ` @ ${s.corner.voltage_v} V` : ''}</strong></span>
                      {project.can_write && <button type="button" onClick={() => setTypicalSpecs(rows => rows.filter((_, i) => i !== idx))}><Trash2 size={12} /></button>}
                    </li>
                  ))}
                  {!typicalSpecs.length && <li className="muted">설정된 대표 조건 없음</li>}
                </ul>
                {project.can_write && (
                  <div className="tracking-inline-add">
                    <input placeholder="적용 시작 버전" value={typNet} onChange={e => setTypNet(e.target.value)} />
                    <input placeholder="코너 (예: TT)" value={typProc} onChange={e => setTypProc(e.target.value)} />
                    <input placeholder="전압 V (선택)" value={typVolt} onChange={e => setTypVolt(e.target.value)} />
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
          </div>
        </details>

        <div className="form-actions">
          <Button variant="outline" size="sm" disabled={busy} onClick={() => runAction(loadPreview, '메일 미리보기를 생성했습니다.')}>
            <Eye size={14} />메일 미리보기
          </Button>
          <Button size="sm" disabled={busy || !project.can_write} onClick={() => runAction(saveProjectSettings, '기본 설정을 저장했습니다.')}>
            기본 설정 저장
          </Button>
        </div>
      </section>

      {preview && (
        <section className="panel tracking-preview-panel" aria-label="메일 미리보기">
          <div className="section-heading">
            <h3>메일 미리보기 {preview.round ? `· ${preview.round.net_ver}` : ''}</h3>
            <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>닫기</Button>
          </div>
          {!preview.top_mail_preview ? (
            <p className="muted">등록된 버전 회차가 없어 미리보기를 만들 수 없습니다.</p>
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
    </>
  )
}
