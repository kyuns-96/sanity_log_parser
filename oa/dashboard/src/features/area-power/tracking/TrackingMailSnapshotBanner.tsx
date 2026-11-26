import { Mail } from 'lucide-react'
import { date, text } from '../api'
import { Button } from '../components/workspace'
import { MAIL_STATE_LABEL, type TrackingMailSnapshot } from '../trackingApi'
import { MailSnapshotBody } from './MailSnapshotBody'

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
      <MailSnapshotBody
        kind={mailSnapshot.kind}
        snapshot={mailSnapshot.snapshot}
        onOpenComparison={onOpenComparison}
      />
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
