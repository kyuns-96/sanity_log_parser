import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CalendarClock, CheckCircle2 } from 'lucide-react'
import { cornerKey, format, formatCornerLabel, object, request, text, type Auth, type Upload } from './api'
import { Badge, Button } from './components/workspace'
import type { AtlasProject } from '@/features/atlas/atlasContext'
import {
  SLOT_STATE_LABEL,
  trackingMailSnapshotSchema,
  type TrackingBlockRow,
  type TrackingMailSnapshot,
  type TrackingOverviewData,
  type TrackingRound,
} from './trackingApi'
import {
  formatSignedPct,
  resolveBaselineVersionMeta,
  versionEcoNumbers,
  type ResolvedVersionMeta,
} from './tracking/trackingUtils'
import { TrackingMailSnapshotBanner } from './tracking/TrackingMailSnapshotBanner'
import { TrackingSummaryStrip } from './tracking/TrackingSummaryStrip'

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

  function describeBlockBaseline(blockAreaId: string): { label: string; emptyHint: string; version: ResolvedVersionMeta | null; configured: boolean } {
    const cfg = blockConfigMap.get(blockAreaId)
    const kind = text(cfg?.comparison_kind)
    if (kind === 'previous_version') {
      const version = resolveBaselineVersionMeta(cfg, project, blockAreaId)
      return { label: '같은 블록 이전 버전', emptyHint: '기준 버전 미지정', version, configured: Boolean(version) }
    }
    const baseBlockId = text(cfg?.baseline_block_id)
    const baseBlock = baselineProject?.blocks.find(b => b.area_id === baseBlockId)
    if (baseBlock) {
      const version = resolveBaselineVersionMeta(cfg, baselineProject, baseBlockId)
      return {
        label: `${baselineProject?.name || baselineName} · ${baseBlock.name}`,
        emptyHint: '기준 버전 미지정',
        version,
        configured: Boolean(version),
      }
    }
    if (baselineStatus === 'available') return { label: baselineName, emptyHint: '블록 기준 미지정', version: null, configured: false }
    return { label: '기준 프로젝트 미설정', emptyHint: '', version: null, configured: false }
  }

  const latestUploadByBlock = useMemo(() => {
    const map = new Map<string, { version: ResolvedVersionMeta | null; corners: { key: string; label: string }[]; latestUploadId: string }>()
    for (const block of project.blocks) {
      const areaId = block.area_id
      const blkUploads = areaId
        ? projectUploads.filter(u => u.block_id === areaId).sort((a, b) => text(b.created_at).localeCompare(text(a.created_at)))
        : []
      const latestUpload = blkUploads[0]
      const latestVer = (latestUpload && block.versions.find(v => v.area_id === latestUpload.version_id))
        || (block.por?.version_id ? block.versions.find(v => v.id === block.por?.version_id) : undefined)
        || block.versions[0]
      const cornerMap = new Map<string, string>()
      for (const u of blkUploads) {
        if (latestVer?.area_id && u.version_id !== latestVer.area_id) continue
        const key = cornerKey(u)
        if (key && !cornerMap.has(key)) cornerMap.set(key, formatCornerLabel(u))
      }
      const corners = [...cornerMap.entries()].map(([key, label]) => ({ key, label }))
      map.set(areaId || block.id, {
        version: latestVer
          ? {
              netVer: latestVer.net_ver || '',
              revision: latestVer.revision || '',
              layout: latestVer.layout || '',
              ecos: versionEcoNumbers(latestVer, latestUpload),
              isPor: Boolean(block.por?.version_id && latestVer.id === block.por.version_id),
            }
          : null,
        corners,
        latestUploadId: latestUpload?.id || '',
      })
    }
    return map
  }, [project.blocks, projectUploads])

  const uploadById = useMemo(() => new Map(uploads.map(u => [u.id, u])), [uploads])

  function resolveRoundVersionMeta(row: TrackingBlockRow, roundNetVer: string): ResolvedVersionMeta {
    const blk = project.blocks.find(b => b.area_id === row.block_id)
    if (blk) {
      const cornerUpload = row.corners
        .map(c => (c.upload_id ? uploadById.get(c.upload_id) : undefined))
        .find((u): u is Upload => Boolean(u))
      const matched = (cornerUpload && blk.versions.find(v => v.area_id === cornerUpload.version_id))
        || blk.versions.find(v => v.net_ver === roundNetVer && (!row.revision_label || v.revision === row.revision_label))
        || blk.versions.find(v => v.net_ver === roundNetVer)
      if (matched) {
        const ecos = versionEcoNumbers(matched, cornerUpload)
        const fallbackEcos = !ecos.length && row.eco_label ? row.eco_label.split(',').map(v => v.trim()).filter(Boolean) : ecos
        return {
          netVer: matched.net_ver || roundNetVer,
          revision: matched.revision || row.revision_label || '',
          layout: matched.layout || '',
          ecos: fallbackEcos,
          isPor: Boolean(blk.por?.version_id && matched.id === blk.por.version_id),
        }
      }
    }
    const rowEcos = row.eco_label ? row.eco_label.split(',').map(v => v.trim()).filter(Boolean) : []
    return { netVer: roundNetVer, revision: row.revision_label || '', layout: '', ecos: rowEcos, isPor: false }
  }

  function renderVersionMeta(meta: ResolvedVersionMeta | null, emptyText: string) {
    if (!meta || (!meta.netVer && !meta.revision)) {
      return emptyText ? <small className="text-muted">{emptyText}</small> : null
    }
    return (
      <dl className="tracking-ver-meta">
        <dt>NET_VER</dt>
        <dd>
          <strong>{meta.netVer || '—'}</strong>
          {meta.layout && <span className="tracking-layout-pill">{meta.layout}</span>}
          {meta.isPor && <span className="tracking-por-pill">PoR</span>}
        </dd>
        <dt>REVISION</dt>
        <dd>{meta.revision || <span className="text-muted">대기</span>}</dd>
        <dt>ECO_NUM</dt>
        <dd>
          {meta.ecos.length > 0
            ? meta.ecos.map(eco => <span key={eco} className="tracking-eco-pill">{eco}</span>)
            : <span className="text-muted">—</span>}
        </dd>
      </dl>
    )
  }

  useEffect(() => {
    if (!mailSnapshotId) {
      setMailSnapshot(null); setMailSnapshotError('')
      return
    }
    const controller = new AbortController()
    request(auth, `/tracking/mails/${encodeURIComponent(mailSnapshotId)}`, trackingMailSnapshotSchema, undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) { setMailSnapshot(value); setMailSnapshotError('') } })
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
        run: () => onOpenResult({ blockId: row.block_id, uploadId: text(next.upload_id) || undefined, baselineUploadId: text(next.baseline_upload_id) || undefined, openMapping: true }),
      }
    }
    if (kind === 'result' && text(next.comparison_id)) {
      return {
        label: text(next.label) || '결과 보기',
        run: () => onOpenResult({ blockId: row.block_id, comparisonId: text(next.comparison_id), uploadId: text(next.upload_id) || undefined, baselineUploadId: text(next.baseline_upload_id) || undefined }),
      }
    }
    const fallbackCorner = row.corners.find(c => c.comparison_id)
    if (fallbackCorner?.comparison_id) {
      return { label: '결과 보기', run: () => onOpenResult({ blockId: row.block_id, comparisonId: fallbackCorner.comparison_id! }) }
    }
    const latestUpload = latestUploadByBlock.get(row.block_id)?.latestUploadId
    if (latestUpload) {
      return { label: '직접 비교', run: () => onOpenResult({ blockId: row.block_id, uploadId: latestUpload }) }
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
        <TrackingMailSnapshotBanner
          mailSnapshot={mailSnapshot}
          onClose={() => { setMailSnapshotId(''); onClearMailId() }}
          onOpenComparison={comparisonId => onOpenResult({ comparisonId })}
        />
      )}

      <TrackingSummaryStrip
        baselineName={baselineName}
        baselineStatus={baselineStatus}
        currentProcess={currentProcess}
        baselineProcess={baselineProcess}
        rounds={rounds}
        activeRound={activeRound}
        onSelectRound={setSelectedRoundId}
        latestDetectedNet={latestDetectedNet}
        progressPct={progressPct}
        blockCount={project.blocks.length}
        uploadCount={projectUploads.length}
      />

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
                  <th>비교 기준 (NET_VER · REVISION · ECO)</th>
                  <th>현재 버전 (NET_VER · REVISION · ECO)</th>
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
                        {row.post_top_mail_revised && <div className="tracking-tags"><Badge value="발송 후 수정" /></div>}
                      </td>
                      <td className="tracking-ver-cell">
                        <div className="tracking-ver-scope">
                          <span className={baseInfo.configured ? '' : 'text-muted'}>{baseInfo.label}</span>
                        </div>
                        {renderVersionMeta(baseInfo.version, baseInfo.emptyHint)}
                      </td>
                      <td className="tracking-ver-cell">
                        {renderVersionMeta(resolveRoundVersionMeta(row, activeRound.net_ver), '리비전 대기')}
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
                              {target.status === 'exceeded' && <span className="tracking-alert-text"><AlertTriangle size={13} aria-hidden="true" /> 목표 +{format(target.exceeded_mw)} mW 초과</span>}
                              {target.status === 'met' && <span className="tracking-ok-text"><CheckCircle2 size={13} aria-hidden="true" /> 목표 달성 ({format(target.target_mw)} mW)</span>}
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
                          {rowAction && <Button variant="outline" size="sm" onClick={rowAction.run}>{rowAction.label} →</Button>}
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
                    <th>비교 기준 (NET_VER · REVISION · ECO)</th>
                    <th>최신 버전 (NET_VER · REVISION · ECO)</th>
                    <th>업로드된 코너 (PVT · PEX)</th>
                    <th>바로가기</th>
                  </tr>
                </thead>
                <tbody>
                  {project.blocks.map(block => {
                    const isFc = block.name.trim() === project.name.trim()
                    const latest = latestUploadByBlock.get(block.area_id || block.id)
                    const baseInfo = block.area_id
                      ? describeBlockBaseline(block.area_id)
                      : { label: '미연결', emptyHint: '', version: null, configured: false }
                    return (
                      <tr key={block.id}>
                        <td>
                          <div className="tracking-block-name">
                            <strong>{isFc ? `${project.name} (Full-chip)` : block.name}</strong>
                            {isFc && <span className="side-tag">Full-chip</span>}
                          </div>
                        </td>
                        <td className="tracking-ver-cell">
                          <div className="tracking-ver-scope">
                            <span className={baseInfo.configured ? '' : 'text-muted'}>{baseInfo.label}</span>
                          </div>
                          {renderVersionMeta(baseInfo.version, baseInfo.emptyHint)}
                        </td>
                        <td className="tracking-ver-cell">
                          {renderVersionMeta(latest?.version || null, '업로드 없음')}
                        </td>
                        <td>
                          <div className="tracking-corner-chips">
                            {latest?.corners.length ? (
                              <>
                                {latest.corners.slice(0, 4).map(corner => (
                                  <span key={corner.key} className="tracking-chip">{corner.label}</span>
                                ))}
                                {latest.corners.length > 4 && (
                                  <span className="tracking-chip">+{latest.corners.length - 4}</span>
                                )}
                              </>
                            ) : (
                              <small className="text-muted">CSV 없음</small>
                            )}
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
