import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CalendarClock, CheckCircle2, Mail, Plus, RefreshCw, Settings2 } from 'lucide-react'
import { array, date, format, object, request, text, unknownSchema, type Auth } from './api'
import { Badge, Button, Empty, Field, useAction } from './components/workspace'
import type { AtlasProject } from '../atlasContext'
import {
  MAIL_STATE_LABEL,
  SLOT_STATE_LABEL,
  trackingMailSnapshotSchema,
  type TrackingBlockRow,
  type TrackingMailSnapshot,
  type TrackingOverviewData,
  type TrackingRound,
} from './trackingApi'

interface TrackingOverviewProps {
  auth: Auth
  project: AtlasProject
  areaProjectId: string
  data: TrackingOverviewData | null
  loading: boolean
  error: string
  initialMailId: string
  onClearMailId: () => void
  onReload: () => Promise<void>
  onOpenSettings: (blockId?: string) => void
  onOpenResult: (target: { comparisonId?: string; uploadId?: string; baselineUploadId?: string; openMapping?: boolean; blockId?: string }) => void
}

export function TrackingOverview({
  auth,
  project,
  areaProjectId,
  data,
  loading,
  error,
  initialMailId,
  onClearMailId,
  onReload,
  onOpenSettings,
  onOpenResult,
}: TrackingOverviewProps) {
  const action = useAction()
  const [selectedRoundId, setSelectedRoundId] = useState('')
  const [addingRound, setAddingRound] = useState(false)
  const [netVerDraft, setNetVerDraft] = useState('')
  const [arrivedDraft, setArrivedDraft] = useState(() => new Date().toISOString().slice(0, 10))
  const [targetBlocks, setTargetBlocks] = useState<string[]>([])
  const [mailSnapshotId, setMailSnapshotId] = useState(initialMailId)
  const [mailSnapshot, setMailSnapshot] = useState<TrackingMailSnapshot | null>(null)
  const [mailSnapshotError, setMailSnapshotError] = useState('')

  useEffect(() => {
    if (initialMailId) setMailSnapshotId(initialMailId)
  }, [initialMailId])

  const rounds = data?.rounds || []
  const activeRound: TrackingRound | undefined = useMemo(() => {
    if (!rounds.length) return undefined
    return rounds.find(r => r.id === selectedRoundId) || rounds[0]
  }, [rounds, selectedRoundId])

  useEffect(() => {
    if (activeRound && activeRound.id !== selectedRoundId) setSelectedRoundId(activeRound.id)
  }, [activeRound?.id])

  const knownNetVers = useMemo(() => {
    const set = new Set<string>()
    for (const block of project.blocks) {
      for (const version of block.versions) {
        if (version.net_ver) set.add(version.net_ver)
      }
    }
    return [...set]
  }, [project.blocks])

  const areaBlocks = useMemo(
    () => project.blocks.filter(block => Boolean(block.area_id)),
    [project.blocks],
  )

  useEffect(() => {
    if (addingRound && !targetBlocks.length && areaBlocks.length) {
      setTargetBlocks(areaBlocks.map(b => b.area_id!))
    }
  }, [addingRound, areaBlocks, targetBlocks.length])

  useEffect(() => {
    if (!mailSnapshotId) {
      setMailSnapshot(null)
      setMailSnapshotError('')
      return
    }
    const controller = new AbortController()
    request(auth, `/tracking/mails/${encodeURIComponent(mailSnapshotId)}`, trackingMailSnapshotSchema, undefined, controller.signal)
      .then(value => {
        if (!controller.signal.aborted) {
          setMailSnapshot(value)
          setMailSnapshotError('')
        }
      })
      .catch(cause => {
        if (!controller.signal.aborted) {
          setMailSnapshot(null)
          setMailSnapshotError(cause instanceof Error ? cause.message : '발송 스냅샷을 조회하지 못했습니다.')
        }
      })
    return () => controller.abort()
  }, [auth, mailSnapshotId])

  async function submitRound() {
    const trimmed = netVerDraft.trim()
    if (!trimmed) throw new Error('NET_VER를 입력하거나 선택하세요.')
    if (!arrivedDraft) throw new Error('실제 netlist 도착일을 입력하세요.')
    if (!targetBlocks.length) throw new Error('대상 블록을 하나 이상 선택하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/rounds`, unknownSchema, {
      net_ver: trimmed,
      netlist_arrived_at: arrivedDraft,
      target_block_ids: targetBlocks,
    })
    setAddingRound(false)
    setNetVerDraft('')
    await onReload()
  }

  async function reconcile(roundId: string) {
    await request(auth, `/tracking/rounds/${encodeURIComponent(roundId)}/reconcile`, unknownSchema, {})
    await onReload()
  }

  function handleBlockAction(row: TrackingBlockRow) {
    const next = object(row.next_action)
    const kind = text(next.kind)
    if (kind === 'configure') {
      onOpenSettings(row.block_id)
      return
    }
    if (kind === 'mapping') {
      onOpenResult({
        blockId: row.block_id,
        uploadId: text(next.upload_id) || undefined,
        baselineUploadId: text(next.baseline_upload_id) || undefined,
        openMapping: true,
      })
      return
    }
    if (kind === 'result' && text(next.comparison_id)) {
      onOpenResult({
        blockId: row.block_id,
        comparisonId: text(next.comparison_id),
        uploadId: text(next.upload_id) || undefined,
        baselineUploadId: text(next.baseline_upload_id) || undefined,
      })
      return
    }
    const fallbackCorner = row.corners.find(c => c.comparison_id)
    if (fallbackCorner?.comparison_id) {
      onOpenResult({ blockId: row.block_id, comparisonId: fallbackCorner.comparison_id })
    } else {
      onOpenSettings(row.block_id)
    }
  }

  const enabled = data?.project_config?.enabled === true

  return (
    <div className="tracking-overview">
      {action.feedback}
      {error && <div className="notice error" role="alert">{error}</div>}

      {mailSnapshotError && (
        <div className="notice error" role="alert">
          <span>{mailSnapshotError}</span>
          <Button variant="outline" size="sm" onClick={() => { setMailSnapshotId(''); onClearMailId() }}>닫기</Button>
        </div>
      )}

      {mailSnapshot && (
        <section className="panel tracking-snapshot-banner" aria-label="메일 발송 당시 고정 결과">
          <div className="tracking-snapshot-head">
            <div>
              <strong>
                <Mail size={15} aria-hidden="true" />{' '}
                {mailSnapshot.kind === 'tracking_top_mail' ? 'TOP 요약 메일 발송 당시 고정 결과' : '블록 결과 메일 발송 당시 고정 결과'}
              </strong>
              <small>
                발송 고정 시각 {date(text(mailSnapshot.snapshot.frozen_at) || mailSnapshot.created_at)} · 메일 상태 {MAIL_STATE_LABEL[mailSnapshot.delivery.state] || mailSnapshot.delivery.state}
                {mailSnapshot.has_newer_results && ' · 이후 도착한 수정본이 있습니다 (발송 후 수정)'}
              </small>
            </div>
            <Button variant="outline" size="sm" onClick={() => { setMailSnapshotId(''); onClearMailId() }}>
              최신 현황으로 이동
            </Button>
          </div>
          {mailSnapshot.kind === 'tracking_top_mail' ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>대상</th>
                    <th>필수 corner 도착</th>
                    <th>분석 완료</th>
                    <th>발송 당시 상태</th>
                  </tr>
                </thead>
                <tbody>
                  {array(mailSnapshot.snapshot.blocks).map(object).map(row => (
                    <tr key={text(row.block_id)}>
                      <td>
                        <strong>{row.is_full_chip ? `${text(mailSnapshot.snapshot.project_name)} · Full-chip` : text(row.block_name)}</strong>
                        {text(row.revision_label) && <small>{text(row.revision_label)}</small>}
                      </td>
                      <td className="numeric">{format(row.arrived_corners)}/{format(row.required_corners)}</td>
                      <td className="numeric">{format(row.completed_corners)}/{format(row.required_corners)}</td>
                      <td>{text(row.status_summary)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="tracking-snapshot-corners">
              <p>
                <strong>{text(mailSnapshot.snapshot.block_name)}</strong> · NET_VER {text(mailSnapshot.snapshot.net_ver)}
                {text(mailSnapshot.snapshot.revision_label) ? ` · ${text(mailSnapshot.snapshot.revision_label)}` : ''}
              </p>
              <ul>
                {array(mailSnapshot.snapshot.corners).map(object).map(corner => (
                  <li key={text(corner.corner_id)}>
                    <strong>{text(corner.corner_label)}</strong> · Logic Area {text(corner.logic_area_delta_pct) ? `${text(corner.logic_area_delta_pct)}%` : '—'} · Total Power {text(corner.power_current_mw) ? `${text(corner.power_current_mw)} mW` : '—'} · 이상 {format(corner.unexpected_count ?? 0)}건 · 확인 필요 {format(corner.needs_review_count ?? 0)}건 · 설명됨 {format(corner.expected_count ?? 0)}건
                    {text(corner.comparison_id) && (
                      <Button variant="ghost" size="sm" onClick={() => onOpenResult({ comparisonId: text(corner.comparison_id) })}>
                        당시 비교 열기
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <section className="panel tracking-round-bar" aria-label="NET_VER 현황 요약">
        <div className="tracking-bar-top">
          <div className="tracking-bar-title">
            <Badge value={enabled ? '자동 추적 활성' : '추적 준비 중'} />
            {rounds.length > 0 && (
              <label className="tracking-round-picker">
                <span>NET_VER 회차</span>
                <select value={activeRound?.id || ''} onChange={event => setSelectedRoundId(event.target.value)}>
                  {rounds.map(r => (
                    <option key={r.id} value={r.id}>
                      {r.net_ver} · 마감 {r.deadline_date || '—'} {r.deadline_time || ''} · {MAIL_STATE_LABEL[r.top_mail.state] || r.top_mail.state}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <div className="actions">
            {project.can_write && (
              <Button variant="outline" size="sm" onClick={() => setAddingRound(value => !value)}>
                <Plus size={14} />{addingRound ? '회차 입력 닫기' : 'NET_VER 회차 등록'}
              </Button>
            )}
            {activeRound && project.can_write && (
              <Button variant="outline" size="sm" disabled={action.busy} onClick={() => action.run(() => reconcile(activeRound.id), '회차 상태를 다시 점검하도록 요청했습니다.')}>
                <RefreshCw size={14} />상태 재점검
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => onOpenSettings()}>
              <Settings2 size={14} />추적 설정
            </Button>
          </div>
        </div>

        {!enabled && (
          <p className="notice">
            자동 추적이 비활성 상태입니다. 추적 설정에서 필수 corner, 승인된 기준 실행, 수신자를 확인하고 활성화하세요. 활성화 전 미리보기로 메일 내용을 확인할 수 있습니다.
          </p>
        )}

        {addingRound && (
          <div className="tracking-round-form">
            <div className="tracking-form-row">
              <Field label="NET_VER">
                <input
                  type="text"
                  list="atlas-net-ver-options"
                  value={netVerDraft}
                  onChange={event => setNetVerDraft(event.target.value)}
                  placeholder="예: NET_023"
                />
                <datalist id="atlas-net-ver-options">
                  {knownNetVers.map(nv => <option key={nv} value={nv} />)}
                </datalist>
              </Field>
              <Field label="실제 netlist 도착일">
                <input type="date" value={arrivedDraft} onChange={event => setArrivedDraft(event.target.value)} />
                <small>
                  마감일: 도착일 + {format(data?.project_config?.deadline_days ?? 7)}일 · {text(data?.project_config?.deadline_time) || '18:00'} ({text(data?.project_config?.deadline_timezone) || 'Asia/Seoul'})
                </small>
              </Field>
            </div>
            <div className="tracking-block-checks">
              <span>대상 블록 (프로젝트명과 같은 블록은 Full-chip으로 자동 인식)</span>
              <div className="tracking-check-grid">
                {areaBlocks.map(block => {
                  const id = block.area_id!
                  const checked = targetBlocks.includes(id)
                  const isFc = block.name.trim() === project.name.trim()
                  return (
                    <label key={id} className="checkbox">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={event => {
                          setTargetBlocks(prev => event.target.checked ? [...prev, id] : prev.filter(item => item !== id))
                        }}
                      />
                      <span>{block.name}{isFc ? ' (Full-chip)' : ''}</span>
                    </label>
                  )
                })}
                {!areaBlocks.length && <small>먼저 블록의 수치 분석 준비를 완료해야 회차 대상으로 등록할 수 있습니다.</small>}
              </div>
            </div>
            <div className="actions">
              <Button size="sm" disabled={action.busy || !project.can_write || !areaBlocks.length} onClick={() => action.run(submitRound, 'NET_VER 회차를 저장했습니다.')}>
                회차 저장
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setAddingRound(false)}>취소</Button>
            </div>
          </div>
        )}

        {activeRound && (
          <div className="tracking-round-summary" role="status">
            <span>
              <strong>{activeRound.net_ver}</strong>
            </span>
            <span>
              <CalendarClock size={14} aria-hidden="true" /> 도착 {activeRound.netlist_arrived_at || '—'} · 마감 {activeRound.deadline_date || '—'} {activeRound.deadline_time || ''} ({activeRound.deadline_timezone || 'Asia/Seoul'})
            </span>
            <span>
              Full-chip 도착 <strong>{activeRound.full_chip_counts.arrived}/{activeRound.full_chip_counts.total}</strong> · 완료 <strong>{activeRound.full_chip_counts.completed}/{activeRound.full_chip_counts.total}</strong>
            </span>
            <span>
              하위 블록 도착 <strong>{activeRound.sub_block_counts.arrived}/{activeRound.sub_block_counts.total}</strong> · 완료 <strong>{activeRound.sub_block_counts.completed}/{activeRound.sub_block_counts.total}</strong>
            </span>
            <span>
              <Badge value={MAIL_STATE_LABEL[activeRound.top_mail.state] || activeRound.top_mail.state} />
              {activeRound.has_post_top_revisions && <Badge value="발송 후 수정" />}
              {activeRound.top_mail.mail_id && (
                <Button variant="ghost" size="sm" onClick={() => setMailSnapshotId(activeRound.top_mail.mail_id!)}>
                  발송 당시 TOP 요약 보기
                </Button>
              )}
            </span>
          </div>
        )}
      </section>

      {loading && !data ? (
        <p className="muted">NET_VER 현황을 불러오는 중…</p>
      ) : !activeRound ? (
        <Empty title="등록된 NET_VER 회차가 없습니다">
          상단의 ‘NET_VER 회차 등록’에서 실제 netlist 도착일과 대상 블록을 등록하면 필수 corner 도착과 자동 분석 현황을 한눈에 볼 수 있습니다.
        </Empty>
      ) : (
        <section className="panel tracking-table-panel" aria-label="대상 블록 현황">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>대상</th>
                  <th>REVISION</th>
                  <th>필수 corner 도착</th>
                  <th>분석 완료</th>
                  <th>전력 · 목표 / corner 상세</th>
                  <th>현재 상태와 동작</th>
                </tr>
              </thead>
              <tbody>
                {activeRound.blocks.map(row => {
                  const typ = object(object(row.power_evaluation).typical)
                  const target = object(typ.target)
                  const next = object(row.next_action)
                  return (
                    <tr key={row.block_id} className={row.needs_attention ? 'tracking-row-attention' : ''}>
                      <td>
                        <strong>
                          {row.is_full_chip ? `${project.name} · Full-chip` : row.block_name}
                        </strong>
                        <div className="tracking-tags">
                          {row.is_full_chip && <span className="side-tag">Full-chip</span>}
                          {row.post_top_mail_revised && <Badge value="발송 후 수정" />}
                          {row.block_mail?.mail_id && (
                            <button
                              type="button"
                              className="tracking-link-inline"
                              onClick={() => setMailSnapshotId(row.block_mail!.mail_id!)}
                            >
                              블록 메일 ({MAIL_STATE_LABEL[row.block_mail.state] || row.block_mail.state})
                            </button>
                          )}
                        </div>
                      </td>
                      <td>{row.revision_label || '—'}</td>
                      <td className="numeric">{row.arrived_corners}/{row.required_corners}</td>
                      <td className="numeric">{row.completed_corners}/{row.required_corners}</td>
                      <td>
                        {row.is_full_chip && typ.current_mw != null && (
                          <div className="tracking-power-line">
                            대표 전력 <strong>{format(typ.current_mw)} mW</strong>
                            {typ.delta_pct != null && ` (${text(typ.delta_pct).startsWith('-') ? '' : '+'}${text(typ.delta_pct)}%)`}
                            {target.status === 'exceeded' && (
                              <span className="tracking-alert-text">
                                <AlertTriangle size={13} aria-hidden="true" /> 목표 {format(target.exceeded_mw)} mW 초과
                              </span>
                            )}
                            {target.status === 'met' && (
                              <span className="tracking-ok-text">
                                <CheckCircle2 size={13} aria-hidden="true" /> 목표 달성 ({format(target.target_mw)} mW 이하)
                              </span>
                            )}
                          </div>
                        )}
                        <div className="tracking-corner-chips">
                          {row.corners.map(c => (
                            <span key={c.corner_id} className={`tracking-chip status-${c.status}`} title={c.status_reason || ''}>
                              {c.corner_label}: {SLOT_STATE_LABEL[c.status] || c.status}
                              {c.last_failed_upload ? ' (수정본 실패 · 직전본 유지)' : ''}
                            </span>
                          ))}
                          {!row.corners.length && <small>필수 corner 미지정</small>}
                        </div>
                      </td>
                      <td>
                        <div className="tracking-status-action">
                          <span>{row.status_summary}</span>
                          <Button variant="outline" size="sm" onClick={() => handleBlockAction(row)}>
                            {text(next.label) || '상세 보기'} →
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
