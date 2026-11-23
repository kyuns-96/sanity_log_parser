import { Mail } from 'lucide-react'
import { array, date, format, object, text } from '../api'
import { Button } from '../components/workspace'
import { MAIL_STATE_LABEL, type TrackingMailSnapshot } from '../trackingApi'
import { formatSignedPct } from './trackingUtils'

interface TrackingMailSnapshotBannerProps {
  mailSnapshot: TrackingMailSnapshot
  onClose: () => void
  onOpenComparison: (comparisonId: string) => void
}

export function TrackingMailSnapshotBanner({
  mailSnapshot,
  onClose,
  onOpenComparison,
}: TrackingMailSnapshotBannerProps) {
  return (
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
        <Button variant="outline" size="sm" onClick={onClose}>
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
                  <Button variant="ghost" size="sm" onClick={() => onOpenComparison(text(corner.comparison_id))}>
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
  )
}
