import { useEffect, useMemo, useState } from 'react'
import { Copy, FileText, LoaderCircle, Mail } from 'lucide-react'
import { array, date, format, object, request, text, type Auth } from './api'
import { Badge, Button, Empty } from './components/workspace'
import { MAIL_STATE_LABEL, trackingReportsSchema, type TrackingReportsData } from './trackingApi'
import { formatSignedPct } from './tracking/trackingUtils'

type UnifiedReportItem = {
  key: string
  origin: 'sent' | 'live'
  kind: 'tracking_top_mail' | 'tracking_block_mail'
  netVer: string
  title: string
  subtitle: string
  timestamp: string
  badge: string
  hasNewer: boolean
  subject: string
  body: string
  snapshot: Record<string, unknown>
}

interface ReportsViewProps {
  auth: Auth
  areaProjectId: string
  refreshVersion: number
  onOpenComparison: (comparisonId: string) => void
}

export function ReportsView({ auth, areaProjectId, refreshVersion, onOpenComparison }: ReportsViewProps) {
  const [data, setData] = useState<TrackingReportsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [kindFilter, setKindFilter] = useState<'all' | 'tracking_top_mail' | 'tracking_block_mail'>('all')
  const [originFilter, setOriginFilter] = useState<'all' | 'sent' | 'live'>('all')
  const [netFilter, setNetFilter] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!areaProjectId) { setData(null); return }
    const controller = new AbortController()
    setLoading(true); setError('')
    request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/reports`, trackingReportsSchema, undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '리포트를 불러오지 못했습니다.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [auth, areaProjectId, refreshVersion])

  const allItems = useMemo<UnifiedReportItem[]>(() => {
    if (!data) return []
    const items: UnifiedReportItem[] = []
    for (const r of data.sent_reports) {
      const isTop = r.kind === 'tracking_top_mail'
      items.push({
        key: `sent:${r.id}`,
        origin: 'sent',
        kind: isTop ? 'tracking_top_mail' : 'tracking_block_mail',
        netVer: r.net_ver,
        title: isTop ? 'TOP 요약 리포트' : r.block_name,
        subtitle: [r.net_ver, r.revision_label].filter(Boolean).join(' · '),
        timestamp: r.frozen_at || r.created_at || '',
        badge: MAIL_STATE_LABEL[r.delivery.state] || '발송됨',
        hasNewer: r.has_newer_results,
        subject: r.subject,
        body: r.body,
        snapshot: r.snapshot,
      })
    }
    for (const round of data.live_reports) {
      items.push({
        key: `live:top:${round.round_id}`,
        origin: 'live',
        kind: 'tracking_top_mail',
        netVer: round.net_ver,
        title: 'TOP 요약 리포트 (현재 기준)',
        subtitle: `${round.net_ver}${round.deadline_date ? ` · 마감 ${round.deadline_date} ${round.deadline_time || ''}` : ''}`,
        timestamp: '',
        badge: round.top_mail_sent ? '최신 현황' : '발송 전 요약',
        hasNewer: false,
        subject: round.top_report.subject,
        body: round.top_report.body,
        snapshot: round.top_report.snapshot,
      })
      for (const blk of round.block_reports) {
        items.push({
          key: `live:block:${round.round_id}:${blk.block_id}`,
          origin: 'live',
          kind: 'tracking_block_mail',
          netVer: round.net_ver,
          title: `${blk.block_name}${blk.is_full_chip ? ' (Full-chip)' : ''}`,
          subtitle: [round.net_ver, blk.revision_label].filter(Boolean).join(' · '),
          timestamp: '',
          badge: blk.mail_sent ? '최신 현황' : '발송 전 요약',
          hasNewer: false,
          subject: blk.subject,
          body: blk.body,
          snapshot: blk.snapshot,
        })
      }
    }
    return items
  }, [data])

  const netVersions = useMemo(() => [...new Set(allItems.map(item => item.netVer).filter(Boolean))], [allItems])
  const filtered = useMemo(
    () => allItems.filter(item =>
      (kindFilter === 'all' || item.kind === kindFilter)
      && (originFilter === 'all' || item.origin === originFilter)
      && (!netFilter || item.netVer === netFilter),
    ),
    [allItems, kindFilter, originFilter, netFilter],
  )
  const selected = filtered.find(item => item.key === selectedKey) || filtered[0]

  useEffect(() => {
    if (selected && selected.key !== selectedKey) setSelectedKey(selected.key)
  }, [selected?.key])

  function copyBody() {
    if (!selected) return
    void navigator.clipboard?.writeText(`${selected.subject}\n\n${selected.body}`).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    }).catch(() => undefined)
  }

  if (loading && !data) {
    return <p className="muted" role="status"><LoaderCircle size={15} className="spin" /> 발송 리포트를 불러오는 중…</p>
  }
  if (error) return <div className="notice error" role="alert">{error}</div>
  if (!allItems.length) {
    return <Empty title="조회할 발송 리포트가 없습니다">버전 회차가 등록되거나 결과 메일이 발송되면 여기에서 TOP 요약과 블록별 리포트를 메일과 같은 내용으로 바로 확인할 수 있습니다.</Empty>
  }

  return (
    <div className="reports-view">
      <section className="panel history-filter-bar" aria-label="리포트 필터">
        <div className="view-switch" role="group" aria-label="리포트 구분">
          {([['all', '전체'], ['tracking_top_mail', 'TOP 요약'], ['tracking_block_mail', '블록 결과']] as const).map(([val, label]) => (
            <button key={val} type="button" aria-pressed={kindFilter === val} onClick={() => setKindFilter(val)}>{label}</button>
          ))}
        </div>
        <div className="view-switch" role="group" aria-label="발송 상태">
          {([['all', '전체'], ['sent', `발송 고정 (${data?.sent_reports.length ?? 0})`], ['live', '현재 기준 요약']] as const).map(([val, label]) => (
            <button key={val} type="button" aria-pressed={originFilter === val} onClick={() => setOriginFilter(val)}>{label}</button>
          ))}
        </div>
        {netVersions.length > 1 && (
          <select aria-label="버전 필터" value={netFilter} onChange={e => setNetFilter(e.target.value)}>
            <option value="">전체 NET_VER</option>
            {netVersions.map(nv => <option key={nv} value={nv}>{nv}</option>)}
          </select>
        )}
        <small className="history-count">총 {filtered.length}건</small>
      </section>

      {!filtered.length ? (
        <Empty title="조건에 맞는 리포트가 없습니다">필터를 변경해 다른 리포트를 확인하세요.</Empty>
      ) : (
        <div className="reports-layout">
          <aside className="panel report-list" aria-label="리포트 목록">
            {filtered.map(item => (
              <button
                key={item.key}
                type="button"
                className={`report-list-item${selected?.key === item.key ? ' is-selected' : ''}`}
                onClick={() => setSelectedKey(item.key)}
              >
                <div className="report-item-top">
                  <strong>
                    {item.kind === 'tracking_top_mail' ? <Mail size={14} aria-hidden="true" /> : <FileText size={14} aria-hidden="true" />}
                    <span>{item.title}</span>
                  </strong>
                  <Badge value={item.badge} />
                </div>
                <div className="report-item-meta">
                  <span>{item.subtitle || '—'}</span>
                  {item.timestamp && <small>{date(item.timestamp)}</small>}
                </div>
                {item.hasNewer && <small className="report-revised-hint">발송 이후 수정본 도착</small>}
              </button>
            ))}
          </aside>

          {selected && (
            <section className="panel report-detail" aria-label="리포트 상세">
              <div className="report-detail-head">
                <div>
                  <div className="tracking-tags">
                    <Badge value={selected.origin === 'sent' ? '발송 당시 고정본' : '현재 기준 요약'} />
                    <Badge value={selected.badge} />
                    {selected.hasNewer && <Badge value="발송 후 수정" />}
                  </div>
                  <h2>{selected.subject}</h2>
                  <small>
                    {selected.timestamp ? `기준 시각 ${date(selected.timestamp)}` : '아직 발송되지 않았거나 최신 기준 실시간 집계입니다.'}
                  </small>
                </div>
                <Button variant="outline" size="sm" onClick={copyBody}>
                  <Copy size={14} />{copied ? '복사됨' : '본문 복사'}
                </Button>
              </div>

              {selected.kind === 'tracking_top_mail' ? (
                <div className="table-scroll report-structured">
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
                      {array(selected.snapshot.blocks).map(object).map(row => {
                        const firstComp = array(row.corners).map(object).find(c => text(c.comparison_id))
                        return (
                          <tr key={text(row.block_id) || text(row.block_name)}>
                            <td>
                              <div className="tracking-cell-stack">
                                <strong>{row.is_full_chip ? `${text(selected.snapshot.project_name)} (Full-chip)` : text(row.block_name)}</strong>
                                {text(row.revision_label) && <small>{text(row.revision_label)}</small>}
                              </div>
                            </td>
                            <td>{format(row.arrived_corners)}/{format(row.required_corners)}</td>
                            <td>{format(row.completed_corners)}/{format(row.required_corners)}</td>
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
              ) : (
                <div className="table-scroll report-structured">
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
                      {array(selected.snapshot.corners).map(object).map(corner => {
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
              )}

              <div className="report-body-wrap">
                <h3>메일 본문</h3>
                <pre className="report-mail-body">{selected.body}</pre>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  )
}
