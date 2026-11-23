import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CalendarClock, CheckCircle2, Mail, Plus, RefreshCw, Settings2 } from 'lucide-react'
import { array, cornerKey, date, format, object, request, text, unknownSchema, type Auth, type Upload } from './api'
import { Badge, Button, Field, useAction } from './components/workspace'
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
  baselineProject?: AtlasProject
  baselineName: string
  baselineStatus: 'unset' | 'available' | 'unavailable'
  currentProcess: string
  baselineProcess: string
  areaProjectId: string
  uploads: Upload[]
  data: TrackingOverviewData | null
  loading: boolean
  error: string
  initialMailId: string
  onClearMailId: () => void
  onReload: () => Promise<void>
  onEditBaseline: () => void
  onOpenSettings: (blockId?: string) => void
  onOpenResult: (target: { comparisonId?: string; uploadId?: string; baselineUploadId?: string; openMapping?: boolean; blockId?: string }) => void
}

function formatSignedPct(raw: unknown): string {
  const s = text(raw)
  if (!s) return ''
  return `${s.startsWith('-') || s.startsWith('+') ? '' : '+'}${s}%`
}

export function TrackingOverview({
  auth,
  project,
  baselineProject,
  baselineName,
  baselineStatus,
  currentProcess,
  baselineProcess,
  areaProjectId,
  uploads,
  data,
  loading,
  error,
  initialMailId,
  onClearMailId,
  onReload,
  onEditBaseline,
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

  const projectUploads = useMemo(
    () => uploads.filter(u => u.state === 'ready' && u.project_id === areaProjectId),
    [uploads, areaProjectId],
  )

  const defaultCornersCount = array(object(data?.project_config).default_required_corners).length

  const blockConfigMap = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const cfg of data?.block_configs || []) {
      const bid = text(cfg.block_id)
      if (bid) map.set(bid, cfg)
    }
    return map
  }, [data?.block_configs])

  function describeBlockBaseline(blockAreaId: string) {
    const cfg = blockConfigMap.get(blockAreaId)
    const kind = text(cfg?.comparison_kind)
    const requiredCorners = array(cfg?.required_corners).length || defaultCornersCount
    const pinnedCount = array(cfg?.approved_baselines).filter(item => Boolean(text(object(item).upload_id))).length
    if (kind === 'previous_version') {
      return {
        label: '같은 블록 이전 버전',
        pinnedText: requiredCorners ? `기준 ${pinnedCount}/${requiredCorners} 고정` : '',
        configured: requiredCorners > 0 && pinnedCount >= requiredCorners,
      }
    }
    const baseBlockId = text(cfg?.baseline_block_id)
    const baseBlock = baselineProject?.blocks.find(b => b.area_id === baseBlockId)
    if (baseBlock) {
      return {
        label: `${baselineProject?.name || baselineName} · ${baseBlock.name}`,
        pinnedText: requiredCorners ? `기준 ${pinnedCount}/${requiredCorners} 고정` : '',
        configured: requiredCorners > 0 && pinnedCount >= requiredCorners,
      }
    }
    if (baselineStatus === 'available') {
      return {
        label: `${baselineName} · 블록 미지정`,
        pinnedText: pinnedCount ? `기준 ${pinnedCount}건 고정` : '기준 실행 미고정',
        configured: false,
      }
    }
    return {
      label: '기준 프로젝트 미설정',
      pinnedText: '',
      configured: false,
    }
  }

  const latestUploadByBlock = useMemo(() => {
    const map = new Map<string, { netVer: string; revision: string; corners: string[]; latestUploadId: string }>()
    for (const block of project.blocks) {
      const areaId = block.area_id
      const blkUploads = areaId
        ? projectUploads
          .filter(u => u.block_id === areaId)
          .sort((a, b) => text(b.created_at).localeCompare(text(a.created_at)))
        : []
      const latestUpload = blkUploads[0]
      const latestVer = (latestUpload && block.versions.find(v => v.area_id === latestUpload.version_id))
        || block.versions[block.versions.length - 1]
      const corners = [...new Set(
        blkUploads
          .filter(u => !latestVer?.area_id || u.version_id === latestVer.area_id)
          .map(u => cornerKey(u))
          .filter(Boolean),
      )]
      map.set(areaId || block.id, {
        netVer: latestVer?.net_ver || '',
        revision: latestVer ? [latestVer.revision, latestVer.layout].filter(Boolean).join(' ') : '',
        corners,
        latestUploadId: latestUpload?.id || '',
      })
    }
    return map
  }, [project.blocks, projectUploads])

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
    if (!trimmed) throw new Error('버전(NET_VER)을 입력하거나 선택하세요.')
    if (!arrivedDraft) throw new Error('Netlist 도착일을 입력하세요.')
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
  const totalBlocks = activeRound ? activeRound.full_chip_counts.total + activeRound.sub_block_counts.total : 0
  const completedBlocks = activeRound ? activeRound.full_chip_counts.completed + activeRound.sub_block_counts.completed : 0
  const progressPct = totalBlocks > 0 ? Math.round((completedBlocks / totalBlocks) * 100) : 0
  const latestDetectedNet = knownNetVers.length ? knownNetVers[knownNetVers.length - 1] : ''

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
                {mailSnapshot.kind === 'tracking_top_mail' ? 'TOP 요약 메일 발송 당시 결과' : '블록 결과 메일 발송 당시 결과'}
              </strong>
              <small>
                발송 시각 {date(text(mailSnapshot.snapshot.frozen_at) || mailSnapshot.created_at)} · {MAIL_STATE_LABEL[mailSnapshot.delivery.state] || mailSnapshot.delivery.state}
                {mailSnapshot.has_newer_results && ' · 발송 이후 수정본이 도착했습니다'}
              </small>
            </div>
            <Button variant="outline" size="sm" onClick={() => { setMailSnapshotId(''); onClearMailId() }}>
              최신 현황으로 돌아가기
            </Button>
          </div>
          {mailSnapshot.kind === 'tracking_top_mail' ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>대상</th>
                    <th>필수 코너 도착</th>
                    <th>분석 완료</th>
                    <th>발송 당시 상태</th>
                  </tr>
                </thead>
                <tbody>
                  {array(mailSnapshot.snapshot.blocks).map(object).map(row => (
                    <tr key={text(row.block_id)}>
                      <td>
                        <strong>{row.is_full_chip ? `${text(mailSnapshot.snapshot.project_name)} · Full-chip` : text(row.block_name)}</strong>
                        {text(row.revision_label) && <small> · {text(row.revision_label)}</small>}
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
                <strong>{text(mailSnapshot.snapshot.block_name)}</strong> · {text(mailSnapshot.snapshot.net_ver)}
                {text(mailSnapshot.snapshot.revision_label) ? ` · ${text(mailSnapshot.snapshot.revision_label)}` : ''}
              </p>
              <ul>
                {array(mailSnapshot.snapshot.corners).map(object).map(corner => (
                  <li key={text(corner.corner_id)}>
                    <strong>{text(corner.corner_label)}</strong> · Logic Area {text(corner.logic_area_delta_pct) ? formatSignedPct(corner.logic_area_delta_pct) : '—'} · Total Power {text(corner.power_current_mw) ? `${text(corner.power_current_mw)} mW` : '—'} · 이상 {format(corner.unexpected_count ?? 0)} · 확인 {format(corner.needs_review_count ?? 0)} · 설명됨 {format(corner.expected_count ?? 0)}
                    {text(corner.comparison_id) && (
                      <Button variant="ghost" size="sm" onClick={() => onOpenResult({ comparisonId: text(corner.comparison_id) })}>
                        비교 열기
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {/* 4-card at-a-glance project summary strip */}
      <section className="project-summary-strip" aria-label="프로젝트 핵심 요약">
        <article className="summary-card">
          <div className="summary-card-head">
            <span>비교 기준</span>
            {project.can_write && (
              <button type="button" className="tracking-link-inline" onClick={onEditBaseline}>변경</button>
            )}
          </div>
          <strong className={baselineStatus !== 'available' ? 'is-muted' : ''}>{baselineName}</strong>
          <small>
            공정 {currentProcess}
            {baselineStatus === 'available' ? ` ↔ 기준 ${baselineProcess || '미등록'}` : ' · 기준 프로젝트를 연결하세요'}
          </small>
        </article>

        <article className="summary-card">
          <div className="summary-card-head">
            <span>현재 버전 (NET_VER)</span>
            {project.can_write && (
              <button type="button" className="tracking-link-inline" onClick={() => setAddingRound(value => !value)}>
                {addingRound ? '닫기' : '+ 새 회차'}
              </button>
            )}
          </div>
          {rounds.length > 1 ? (
            <select
              className="summary-round-select"
              aria-label="추적 중인 버전 선택"
              value={activeRound?.id || ''}
              onChange={event => setSelectedRoundId(event.target.value)}
            >
              {rounds.map(r => (
                <option key={r.id} value={r.id}>
                  {r.net_ver} (마감 {r.deadline_date || '—'})
                </option>
              ))}
            </select>
          ) : (
            <strong>{activeRound?.net_ver || latestDetectedNet || '등록된 버전 없음'}</strong>
          )}
          <small>
            {activeRound
              ? `도착 ${activeRound.netlist_arrived_at || '—'} · 마감 ${activeRound.deadline_date || '—'} ${activeRound.deadline_time || ''}`
              : latestDetectedNet
                ? '추적 회차 미등록 · 최근 업로드 기준 표시'
                : '버전 회차를 등록하면 마감이 자동 계산됩니다'}
          </small>
        </article>

        <article className="summary-card">
          <div className="summary-card-head">
            <span>블록 진행 현황</span>
            {activeRound && <span className="summary-pct">{progressPct}%</span>}
          </div>
          {activeRound ? (
            <>
              <strong>
                {activeRound.full_chip_counts.total > 0
                  ? `Full-chip ${activeRound.full_chip_counts.completed}/${activeRound.full_chip_counts.total} · 블록 ${activeRound.sub_block_counts.completed}/${activeRound.sub_block_counts.total}`
                  : `블록 ${activeRound.sub_block_counts.completed}/${activeRound.sub_block_counts.total} 완료`}
              </strong>
              <div className="summary-progress" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100}>
                <span style={{ width: `${progressPct}%` }} />
              </div>
            </>
          ) : (
            <>
              <strong>블록 {project.blocks.length}개</strong>
              <small>업로드 완료 CSV {projectUploads.length}건</small>
            </>
          )}
        </article>

        <article className="summary-card">
          <div className="summary-card-head">
            <span>자동 분석 · 결과 메일</span>
            <button type="button" className="tracking-link-inline" onClick={() => onOpenSettings()}>설정</button>
          </div>
          <div className="summary-mail-row">
            <Badge value={enabled ? '자동 분석 켜짐' : '자동 분석 꺼짐'} />
            {activeRound && (
              <Badge value={MAIL_STATE_LABEL[activeRound.top_mail.state] || activeRound.top_mail.state} />
            )}
          </div>
          <small>
            {activeRound?.top_mail.mail_id ? (
              <button type="button" className="tracking-link-inline" onClick={() => setMailSnapshotId(activeRound.top_mail.mail_id!)}>
                발송된 TOP 요약 메일 보기 →
              </button>
            ) : enabled ? (
              activeRound?.has_post_top_revisions ? '발송 후 수정본 도착' : '필수 코너 완료 시 블록 메일 자동 발송'
            ) : (
              '자동 분석 설정에서 필수 코너와 수신자를 지정하세요'
            )}
          </small>
        </article>
      </section>

      {addingRound && (
        <section className="panel tracking-round-form-panel" aria-label="새 버전 회차 등록">
          <div className="section-heading">
            <div>
              <h2>새 버전 회차 등록</h2>
              <p>Netlist 도착일을 입력하면 마감일시(도착일 + {format(data?.project_config?.deadline_days ?? 7)}일 {text(data?.project_config?.deadline_time) || '18:00'})가 자동 설정됩니다.</p>
            </div>
          </div>
          <div className="tracking-form-row">
            <Field label="버전 (NET_VER)">
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
            <Field label="Netlist 도착일">
              <input type="date" value={arrivedDraft} onChange={event => setArrivedDraft(event.target.value)} />
            </Field>
          </div>
          <div className="tracking-block-checks">
            <span>추적 대상 블록</span>
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
              {!areaBlocks.length && <small>먼저 블록이 연결되어야 회차 대상으로 선택할 수 있습니다.</small>}
            </div>
          </div>
          <div className="actions">
            <Button size="sm" disabled={action.busy || !project.can_write || !areaBlocks.length} onClick={() => action.run(submitRound, '버전 회차를 등록했습니다.')}>
              회차 등록
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setAddingRound(false)}>취소</Button>
          </div>
        </section>
      )}

      {loading && !data ? (
        <p className="muted">프로젝트 현황을 불러오는 중…</p>
      ) : activeRound ? (
        <section className="panel tracking-table-panel" aria-label="블록별 진행 현황">
          <div className="tracking-table-head">
            <div>
              <h2>
                <span>{`${activeRound.net_ver} 블록별 현황`}</span>
                {activeRound.has_post_top_revisions && <Badge value="발송 후 수정" />}
              </h2>
              <small>
                <CalendarClock size={13} aria-hidden="true" /> 마감 {activeRound.deadline_date || '—'} {activeRound.deadline_time || ''} · 각 블록의 기준, 현재 리비전, 코너별 분석 결과를 한눈에 확인합니다.
              </small>
            </div>
            <div className="actions">
              {project.can_write && (
                <Button variant="outline" size="sm" disabled={action.busy} onClick={() => action.run(() => reconcile(activeRound.id), '회차 상태를 다시 점검했습니다.')}>
                  <RefreshCw size={14} />상태 재점검
                </Button>
              )}
              {project.can_write && (
                <Button variant="outline" size="sm" onClick={() => setAddingRound(value => !value)}>
                  <Plus size={14} />새 버전 등록
                </Button>
              )}
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>블록</th>
                  <th>비교 기준</th>
                  <th>현재 버전</th>
                  <th>코너 진행</th>
                  <th>Area · Power 요약</th>
                  <th>상태 · 바로가기</th>
                </tr>
              </thead>
              <tbody>
                {activeRound.blocks.map(row => {
                  const typ = object(object(row.power_evaluation).typical)
                  const target = object(typ.target)
                  const next = object(row.next_action)
                  const baseInfo = describeBlockBaseline(row.block_id)
                  const completedCornerMetrics = row.corners.filter(c => c.logic_area_delta_pct || c.power_current_mw)
                  const unexpected = Number(row.totals?.unexpected ?? 0)
                  const needsReview = Number(row.totals?.needs_review ?? 0)
                  const expected = Number(row.totals?.expected ?? 0)
                  return (
                    <tr key={row.block_id} className={row.needs_attention ? 'tracking-row-attention' : ''}>
                      <td>
                        <div className="tracking-block-name">
                          <strong>{row.is_full_chip ? `${project.name} (Full-chip)` : row.block_name}</strong>
                          {row.is_full_chip && <span className="side-tag">Full-chip</span>}
                        </div>
                        <div className="tracking-tags">
                          {row.post_top_mail_revised && <Badge value="발송 후 수정" />}
                          {row.block_mail?.mail_id && (
                            <button
                              type="button"
                              className="tracking-link-inline"
                              onClick={() => setMailSnapshotId(row.block_mail!.mail_id!)}
                            >
                              메일 ({MAIL_STATE_LABEL[row.block_mail.state] || row.block_mail.state})
                            </button>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="tracking-cell-stack">
                          <span className={baseInfo.configured ? '' : 'text-muted'}>{baseInfo.label}</span>
                          {baseInfo.pinnedText && <small>{baseInfo.pinnedText}</small>}
                        </div>
                      </td>
                      <td>
                        <div className="tracking-cell-stack">
                          <strong>{activeRound.net_ver}</strong>
                          <small>{row.revision_label || '리비전 대기'}</small>
                        </div>
                      </td>
                      <td>
                        <div className="tracking-corner-chips">
                          {row.corners.map(c => (
                            <span key={c.corner_id} className={`tracking-chip status-${c.status}`} title={c.status_reason || ''}>
                              {c.corner_label} · {SLOT_STATE_LABEL[c.status] || c.status}
                              {c.last_failed_upload ? ' (수정 실패)' : ''}
                            </span>
                          ))}
                          {!row.corners.length && <small>필수 코너 미지정</small>}
                        </div>
                      </td>
                      <td>
                        <div className="tracking-cell-stack">
                          {row.is_full_chip && typ.current_mw != null && (
                            <div className="tracking-power-line">
                              대표 전력 <strong>{format(typ.current_mw)} mW</strong>
                              {typ.delta_pct != null && ` (${formatSignedPct(typ.delta_pct)})`}
                              {target.status === 'exceeded' && (
                                <span className="tracking-alert-text">
                                  <AlertTriangle size={13} aria-hidden="true" /> 목표 +{format(target.exceeded_mw)} mW 초과
                                </span>
                              )}
                              {target.status === 'met' && (
                                <span className="tracking-ok-text">
                                  <CheckCircle2 size={13} aria-hidden="true" /> 목표 달성 ({format(target.target_mw)} mW)
                                </span>
                              )}
                            </div>
                          )}
                          {completedCornerMetrics.length > 0 ? (
                            <div className="tracking-metric-lines">
                              {completedCornerMetrics.map(c => (
                                <small key={c.corner_id}>
                                  <strong>{c.corner_label}</strong>
                                  {c.logic_area_delta_pct ? ` Logic ${formatSignedPct(c.logic_area_delta_pct)}` : ''}
                                  {c.power_current_mw ? ` · Power ${format(c.power_current_mw)} mW` : ''}
                                  {c.power_delta_pct ? ` (${formatSignedPct(c.power_delta_pct)})` : ''}
                                </small>
                              ))}
                            </div>
                          ) : !row.is_full_chip || typ.current_mw == null ? (
                            <small className="text-muted">분석 결과 대기</small>
                          ) : null}
                          {(unexpected > 0 || needsReview > 0 || expected > 0) && (
                            <div className="tracking-verdict-summary">
                              {unexpected > 0 && <span className="verdict-pill is-danger">이상 {unexpected}</span>}
                              {needsReview > 0 && <span className="verdict-pill is-warn">확인 {needsReview}</span>}
                              {expected > 0 && <span className="verdict-pill is-ok">설명됨 {expected}</span>}
                            </div>
                          )}
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
      ) : (
        <section className="panel tracking-table-panel" aria-label="블록별 최신 버전 현황">
          <div className="tracking-table-head">
            <div>
              <h2>블록별 최신 버전 현황</h2>
              <small>아직 자동 추적 회차가 등록되지 않았습니다. 아래에서 각 블록의 현재 버전을 바로 보거나 ‘새 버전 등록’으로 마감·자동 메일을 시작할 수 있습니다.</small>
            </div>
            <div className="actions">
              {project.can_write && (
                <Button size="sm" onClick={() => setAddingRound(true)}>
                  <Plus size={14} />새 버전 회차 등록
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={() => onOpenSettings()}>
                <Settings2 size={14} />자동 분석 설정
              </Button>
            </div>
          </div>
          {project.blocks.length > 0 ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>블록</th>
                    <th>비교 기준</th>
                    <th>최신 버전 · 리비전</th>
                    <th>업로드된 코너</th>
                    <th>바로가기</th>
                  </tr>
                </thead>
                <tbody>
                  {project.blocks.map(block => {
                    const isFc = block.name.trim() === project.name.trim()
                    const latest = latestUploadByBlock.get(block.area_id || block.id)
                    const baseInfo = block.area_id ? describeBlockBaseline(block.area_id) : { label: '미연결', pinnedText: '', configured: false }
                    return (
                      <tr key={block.id}>
                        <td>
                          <div className="tracking-block-name">
                            <strong>{isFc ? `${project.name} (Full-chip)` : block.name}</strong>
                            {isFc && <span className="side-tag">Full-chip</span>}
                          </div>
                        </td>
                        <td>
                          <div className="tracking-cell-stack">
                            <span className={baseInfo.configured ? '' : 'text-muted'}>{baseInfo.label}</span>
                            {baseInfo.pinnedText && <small>{baseInfo.pinnedText}</small>}
                          </div>
                        </td>
                        <td>
                          <div className="tracking-cell-stack">
                            <strong>{latest?.netVer || '업로드 없음'}</strong>
                            <small>{latest?.revision || '—'}</small>
                          </div>
                        </td>
                        <td>
                          <div className="tracking-corner-chips">
                            {latest?.corners.length
                              ? latest.corners.slice(0, 4).map(corner => <span key={corner} className="tracking-chip">{corner}</span>)
                              : <small className="text-muted">CSV 없음</small>}
                          </div>
                        </td>
                        <td>
                          <div className="actions">
                            {latest?.latestUploadId && (
                              <Button variant="outline" size="sm" onClick={() => onOpenResult({ blockId: block.area_id || undefined, uploadId: latest.latestUploadId })}>
                                직접 비교 →
                              </Button>
                            )}
                            {block.area_id && (
                              <Button variant="ghost" size="sm" onClick={() => onOpenSettings(block.area_id!)}>
                                기준 설정
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">이 프로젝트에 등록된 블록이 없습니다.</p>
          )}
        </section>
      )}
    </div>
  )
}
