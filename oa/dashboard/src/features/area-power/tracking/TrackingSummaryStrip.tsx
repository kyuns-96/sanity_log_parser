import type { TrackingRound } from '../trackingApi'

interface TrackingSummaryStripProps {
  baselineName: string
  baselineStatus: 'unset' | 'available' | 'unavailable'
  currentProcess: string
  baselineProcess: string
  rounds: TrackingRound[]
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
  activeRound,
  onSelectRound,
  latestDetectedNet,
  progressPct,
  blockCount,
  uploadCount,
}: TrackingSummaryStripProps) {
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
          <span>현재 버전 (NET_VER)</span>
        </div>
        {rounds.length > 1 ? (
          <select
            className="summary-round-select"
            aria-label="버전 선택"
            value={activeRound?.id || ''}
            onChange={event => onSelectRound(event.target.value)}
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
            <strong>블록 {blockCount}개</strong>
            <small>업로드 완료 CSV {uploadCount}건</small>
          </>
        )}
      </article>
    </section>
  )
}
