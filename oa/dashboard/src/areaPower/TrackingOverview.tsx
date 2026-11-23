import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CalendarClock, CheckCircle2, Mail } from 'lucide-react'
import { array, cornerKey, date, format, object, request, text, type Auth, type Upload } from './api'
import { Badge, Button } from './components/workspace'
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
  onOpenResult,
}: TrackingOverviewProps) {
  const [selectedRoundId, setSelectedRoundId] = useState('')
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

  const projectUploads = useMemo(
    () => uploads.filter(u => u.state === 'ready' && u.project_id === areaProjectId),
    [uploads, areaProjectId],
  )

  const blockConfigMap = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const cfg of data?.block_configs || []) {
      const bid = text(cfg.block_id)
      if (bid) map.set(bid, cfg)
    }
    return map
  }, [data?.block_configs])

  function resolveBaselineVersionLabel(cfg: Record<string, unknown> | undefined, targetProject: AtlasProject | undefined, targetBlockAreaId: string): string {
    if (!cfg) return ''
    const info = object(cfg.baseline_version_info)
    if (text(info.net_ver)) {
      const rev = [text(info.revision), text(info.layout)].filter(Boolean).join(' ')
      return rev ? `${text(info.net_ver)} · ${rev}` : text(info.net_ver)
    }
    const bvid = text(cfg.baseline_version_id)
      || text(object(array(cfg.approved_baselines)[0]).version_id)
    if (!bvid || !targetProject) return ''
    const blk = targetProject.blocks.find(b => b.area_id === targetBlockAreaId)
    const ver = blk?.versions.find(v => v.area_id === bvid)
    if (!ver) return ''
    const rev = [ver.revision, ver.layout].filter(Boolean).join(' ')
    return rev ? `${ver.net_ver} · ${rev}` : ver.net_ver
  }

  function describeBlockBaseline(blockAreaId: string) {
    const cfg = blockConfigMap.get(blockAreaId)
    const kind = text(cfg?.comparison_kind)
    if (kind === 'previous_version') {
      const versionLabel = resolveBaselineVersionLabel(cfg, project, blockAreaId)
      return {
        label: '같은 블록 이전 버전',
        versionLabel: versionLabel || '기준 버전 미지정',
        configured: Boolean(versionLabel),
      }
    }
    const baseBlockId = text(cfg?.baseline_block_id)
    const baseBlock = baselineProject?.blocks.find(b => b.area_id === baseBlockId)
    if (baseBlock) {
      const versionLabel = resolveBaselineVersionLabel(cfg, baselineProject, baseBlockId)
      return {
        label: `${baselineProject?.name || baselineName} · ${baseBlock.name}`,
        versionLabel: versionLabel || '기준 버전 미지정',
        configured: Boolean(versionLabel),
      }
    }
    if (baselineStatus === 'available') {
      return {
        label: baselineName,
        versionLabel: '블록 기준 미지정',
        configured: false,
      }
    }
    return {
      label: '기준 프로젝트 미설정',
      versionLabel: '',
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
          setMailSnapshotError(cause instanceof Error ? cause.message : '발송 당시 결과를 조회하지 못했습니다.')
        }
      })
    return () => controller.abort()
  }, [auth, mailSnapshotId])

  function resolveRowAction(row: TrackingBlockRow): { label: string; run: () => void } | null {
    const next = object(row.next_action)
    const kind = text(next.kind)
    if (kind === 'mapping') {
      return {
        label: '매핑 확인',
        run: () => onOpenResult({
          blockId: row.block_id,
          uploadId: text(next.upload_id) || undefined,
          baselineUploadId: text(next.baseline_upload_id) || undefined,
          openMapping: true,
        }),
      }
    }
    if (kind === 'result' && text(next.comparison_id)) {
      return {
        label: text(next.label) || '결과 보기',
        run: () => onOpenResult({
          blockId: row.block_id,
          comparisonId: text(next.comparison_id),
          uploadId: text(next.upload_id) || undefined,
          baselineUploadId: text(next.baseline_upload_id) || undefined,
        }),
      }
    }
    const fallbackCorner = row.corners.find(c => c.comparison_id)
    if (fallbackCorner?.comparison_id) {
      return {
        label: '결과 보기',
        run: () => onOpenResult({ blockId: row.block_id, comparisonId: fallbackCorner.comparison_id! }),
      }
    }
    const latestUpload = latestUploadByBlock.get(row.block_id)?.latestUploadId
    if (latestUpload) {
      return {
        label: '직접 비교',
        run: () => onOpenResult({ blockId: row.block_id, uploadId: latestUpload }),
      }
    }
    return null
  }

  const totalBlocks = activeRound ? activeRound.full_chip_counts.total + activeRound.sub_block_counts.total : 0
  const completedBlocks = activeRound ? activeRound.full_chip_counts.completed + activeRound.sub_block_counts.completed : 0
  const progressPct = totalBlocks > 0 ? Math.round((completedBlocks / totalBlocks) * 100) : 0
  const latestDetectedNet = knownNetVers.length ? knownNetVers[knownNetVers.length - 1] : ''

  return (
    <div className="tracking-overview">
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
                {mailSnapshot.kind === 'tracking_top_mail' ? 'TOP 요약 발송 당시 결과' : '블록 결과 메일 발송 당시 결과'}
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
          {mailSnapshot.body && (
            <details className="settings-advanced">
              <summary><span>메일 원문 보기</span>{mailSnapshot.subject && <small>{mailSnapshot.subject}</small>}</summary>
              <div className="settings-advanced-body">
                <pre className="report-mail-body">{mailSnapshot.body}</pre>
              </div>
            </details>
          )}
        </section>
      )}

      {/* 3-card at-a-glance project summary strip (read-only user view) */}
      <section className="project-summary-strip cols-3" aria-label="프로젝트 핵심 요약">
        <article className="summary-card">
          <div className="summary-card-head">
            <span>비교 기준</span>
          </div>
          <strong className={baselineStatus !== 'available' ? 'is-muted' : ''}>{baselineName}</strong>
          <small>
            공정 {currentProcess}
            {baselineStatus === 'available' ? ` ↔ 기준 ${baselineProcess || '미등록'}` : ' · 관리자가 기준 프로젝트를 설정합니다'}
          </small>
        </article>

        <article className="summary-card">
          <div className="summary-card-head">
            <span>현재 버전 (NET_VER)</span>
          </div>
          {rounds.length > 1 ? (
            <select
              className="summary-round-select"
              aria-label="버전 선택"
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
                ? '최근 업로드 버전 기준'
                : '아직 업로드된 버전이 없습니다'}
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
      </section>

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
                <CalendarClock size={13} aria-hidden="true" /> 마감 {activeRound.deadline_date || '—'} {activeRound.deadline_time || ''} · 각 블록의 기준 버전, 현재 리비전, 코너별 분석 결과를 확인합니다.
              </small>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>블록</th>
                  <th>비교 기준 (블록 · NET_VER · REV)</th>
                  <th>현재 버전</th>
                  <th>코너 진행</th>
                  <th>Area · Power 요약</th>
                  <th>상태 · 결과</th>
                </tr>
              </thead>
              <tbody>
                {activeRound.blocks.map(row => {
                  const typ = object(object(row.power_evaluation).typical)
                  const target = object(typ.target)
                  const baseInfo = describeBlockBaseline(row.block_id)
                  const completedCornerMetrics = row.corners.filter(c => c.logic_area_delta_pct || c.power_current_mw)
                  const unexpected = Number(row.totals?.unexpected ?? 0)
                  const needsReview = Number(row.totals?.needs_review ?? 0)
                  const expected = Number(row.totals?.expected ?? 0)
                  const rowAction = resolveRowAction(row)
                  return (
                    <tr key={row.block_id} className={row.needs_attention ? 'tracking-row-attention' : ''}>
                      <td>
                        <div className="tracking-block-name">
                          <strong>{row.is_full_chip ? `${project.name} (Full-chip)` : row.block_name}</strong>
                          {row.is_full_chip && <span className="side-tag">Full-chip</span>}
                        </div>
                        {row.post_top_mail_revised && (
                          <div className="tracking-tags">
                            <Badge value="발송 후 수정" />
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="tracking-cell-stack">
                          <span className={baseInfo.configured ? '' : 'text-muted'}>{baseInfo.label}</span>
                          {baseInfo.versionLabel && (
                            <small className={baseInfo.configured ? 'tracking-version-tag' : 'text-muted'}>
                              {baseInfo.versionLabel}
                            </small>
                          )}
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
                          {!row.corners.length && <small className="text-muted">필수 코너 미지정</small>}
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
                          {rowAction && (
                            <Button variant="outline" size="sm" onClick={rowAction.run}>
                              {rowAction.label} →
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
        </section>
      ) : (
        <section className="panel tracking-table-panel" aria-label="블록별 최신 버전 현황">
          <div className="tracking-table-head">
            <div>
              <h2>블록별 최신 버전 현황</h2>
              <small>각 블록의 비교 기준과 최신 업로드 버전을 확인하고 바로 비교할 수 있습니다.</small>
            </div>
          </div>
          {project.blocks.length > 0 ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>블록</th>
                    <th>비교 기준 (블록 · NET_VER · REV)</th>
                    <th>최신 버전 · 리비전</th>
                    <th>업로드된 코너</th>
                    <th>바로가기</th>
                  </tr>
                </thead>
                <tbody>
                  {project.blocks.map(block => {
                    const isFc = block.name.trim() === project.name.trim()
                    const latest = latestUploadByBlock.get(block.area_id || block.id)
                    const baseInfo = block.area_id ? describeBlockBaseline(block.area_id) : { label: '미연결', versionLabel: '', configured: false }
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
                            {baseInfo.versionLabel && (
                              <small className={baseInfo.configured ? 'tracking-version-tag' : 'text-muted'}>
                                {baseInfo.versionLabel}
                              </small>
                            )}
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
                          {latest?.latestUploadId ? (
                            <Button variant="outline" size="sm" onClick={() => onOpenResult({ blockId: block.area_id || undefined, uploadId: latest.latestUploadId })}>
                              직접 비교 →
                            </Button>
                          ) : (
                            <small className="text-muted">—</small>
                          )}
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
