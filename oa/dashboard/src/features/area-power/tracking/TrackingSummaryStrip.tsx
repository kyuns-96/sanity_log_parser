import type { TrackingRound } from '../trackingApi'

export const LATEST_VIEW_ID = '__latest__'

interface TrackingSummaryStripProps {
  baselineName: string
  baselineStatus: 'unset' | 'available' | 'unavailable'
  currentProcess: string
  baselineProcess: string
  rounds: TrackingRound[]
  selectedRoundId: string
  activeRound?: TrackingRound
  onSelectRound: (roundId: string) => void
  latestDetectedNet: string
  progressPct: number
  blockCount: number
  uploadCount: number
}

export function TrackingSummaryStrip({
  baselineName,
  baselineStatus,
  currentProcess,
  baselineProcess,
  rounds,
  selectedRoundId,
  activeRound,
  onSelectRound,
  latestDetectedNet,
  progressPct,
  blockCount,
  uploadCount,
}: TrackingSummaryStripProps) {
  const skippedCount = activeRound?.carried_over_blocks?.length || 0
  return (
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
          <span>보기 기준 (NET_VER)</span>
        </div>
        {rounds.length > 0 ? (
          <select
            className="summary-round-select"
            aria-label="버전 선택"
            value={selectedRoundId || activeRound?.id || LATEST_VIEW_ID}
            onChange={event => onSelectRound(event.target.value)}
          >
            {rounds.map(r => (
              <option key={r.id} value={r.id}>
                {r.net_ver} (마감 {r.deadline_date || '—'})
              </option>
            ))}
            <option value={LATEST_VIEW_ID}>전체 최신 (블록별 최종 NET_VER)</option>
          </select>
        ) : (
          <strong>{latestDetectedNet || '등록된 버전 없음'}</strong>
        )}
        <small>
          {activeRound
            ? `도착 ${activeRound.netlist_arrived_at || '—'} · 마감 ${activeRound.deadline_date || '—'} ${activeRound.deadline_time || ''}`
            : rounds.length > 0
              ? '블록마다 다른 최종 NET_VER를 한 표에서 확인'
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
            {skippedCount > 0 && <small>이번 회차 제외 {skippedCount}개 (이전 NET_VER 유지)</small>}
          </>
        ) : (
          <>
            <strong>블록 {blockCount}개</strong>
            <small>업로드 완료 CSV {uploadCount}건</small>
          </>
        )}
      </article>
    </section>
  )
}
