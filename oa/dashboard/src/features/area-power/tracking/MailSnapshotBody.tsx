import { array, format, object, text } from '../api'
import { Button } from '../components/workspace'
import { formatSignedPct } from './trackingUtils'

interface MailSnapshotBodyProps {
  kind: string
  snapshot: Record<string, unknown>
  onOpenComparison: (comparisonId: string) => void
  className?: string
}

export function MailSnapshotBody({ kind, snapshot, onOpenComparison, className = '' }: MailSnapshotBodyProps) {
  if (kind === 'tracking_top_mail') {
    return (
      <div className={`table-scroll${className ? ` ${className}` : ''}`}>
        <table>
          <thead>
            <tr>
              <th>대상</th>
              <th>코너 도착</th>
              <th>분석 완료</th>
              <th>상태 요약</th>
              <th>결과</th>
            </tr>
          </thead>
          <tbody>
            {array(snapshot.blocks).map(object).map(row => {
              const firstComp = array(row.corners).map(object).find(c => text(c.comparison_id))
              return (
                <tr key={text(row.block_id) || text(row.block_name)}>
                  <td>
                    <div className="tracking-cell-stack">
                      <strong>{row.is_full_chip ? `${text(snapshot.project_name)} (Full-chip)` : text(row.block_name)}</strong>
                      {text(row.revision_label) && <small>{text(row.revision_label)}</small>}
                    </div>
                  </td>
                  <td className="numeric">{format(row.arrived_corners)}/{format(row.required_corners)}</td>
                  <td className="numeric">{format(row.completed_corners)}/{format(row.required_corners)}</td>
                  <td>{text(row.status_summary)}</td>
                  <td>
                    {firstComp && text(firstComp.comparison_id) ? (
                      <Button variant="outline" size="sm" onClick={() => onOpenComparison(text(firstComp.comparison_id))}>
                        비교 열기 →
                      </Button>
                    ) : <small className="text-muted">—</small>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className={`table-scroll${className ? ` ${className}` : ''}`}>
      <table>
        <thead>
          <tr>
            <th>코너</th>
            <th>Logic Area Δ</th>
            <th>Total Power</th>
            <th>판정 요약</th>
            <th>결과</th>
          </tr>
        </thead>
        <tbody>
          {array(snapshot.corners).map(object).map(corner => {
            const counts = object(corner.counts)
            const unexp = corner.unexpected_count ?? counts.unexpected ?? 0
            const rev = corner.needs_review_count ?? counts.needs_review ?? 0
            const exp = corner.expected_count ?? counts.expected ?? 0
            return (
              <tr key={text(corner.corner_id) || text(corner.corner_label)}>
                <td><strong>{text(corner.corner_label)}</strong></td>
                <td className="numeric">{text(corner.logic_area_delta_pct) ? formatSignedPct(corner.logic_area_delta_pct) : '—'}</td>
                <td className="numeric">
                  {text(corner.power_current_mw) ? `${text(corner.power_current_mw)} mW` : '—'}
                  {text(corner.power_delta_pct) ? ` (${formatSignedPct(corner.power_delta_pct)})` : ''}
                </td>
                <td>이상 {format(unexp)} · 확인 {format(rev)} · 설명됨 {format(exp)}</td>
                <td>
                  {text(corner.comparison_id) ? (
                    <Button variant="outline" size="sm" onClick={() => onOpenComparison(text(corner.comparison_id))}>
                      비교 열기 →
                    </Button>
                  ) : <small className="text-muted">—</small>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
