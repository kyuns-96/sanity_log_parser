import { useEffect, useMemo, useState } from 'react'
import { Copy, FileText, History, LoaderCircle, Mail, Search } from 'lucide-react'
import { array, cornerKey, date, format, object, request, text, uploadName, type Auth, type TrackingRecord, type Upload } from './api'
import { Badge, Button, Empty } from './components/workspace'
import type { AtlasProject } from '../atlasContext'
import {
  MAIL_STATE_LABEL,
  trackingReportsSchema,
  type TrackingOverviewData,
  type TrackingReportsData,
} from './trackingApi'

function formatSignedPct(raw: unknown): string {
  const s = text(raw)
  if (!s) return ''
  return `${s.startsWith('-') || s.startsWith('+') ? '' : '+'}${s}%`
}

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

interface ComparisonHistoryViewProps {
  project: AtlasProject
  candidates: AtlasProject[]
  projectRecords: Map<string, TrackingRecord>
  uploads: Upload[]
  comparisons: TrackingRecord[]
  trackingData: TrackingOverviewData | null
  onOpenComparison: (comparisonId: string) => void
}

export function ComparisonHistoryView({
  project,
  candidates,
  projectRecords,
  uploads,
  comparisons,
  trackingData,
  onOpenComparison,
}: ComparisonHistoryViewProps) {
  const [sourceFilter, setSourceFilter] = useState<'all' | 'manual' | 'auto'>('all')
  const [kindFilter, setKindFilter] = useState<'all' | 'previous_project' | 'previous_version'>('all')
  const [blockFilter, setBlockFilter] = useState('')
  const [query, setQuery] = useState('')

  const uploadById = useMemo(() => new Map(uploads.map(u => [u.id, u])), [uploads])
  const projectByAreaId = useMemo(() => {
    const map = new Map<string, AtlasProject>()
    for (const p of candidates) {
      const aid = p.area_id || projectRecords.get(p.id)?.id
      if (aid) map.set(aid, p)
    }
    return map
  }, [candidates, projectRecords])

  const autoComparisonIds = useMemo(() => {
    const set = new Set<string>()
    for (const round of trackingData?.rounds || []) {
      for (const blk of round.blocks) {
        for (const c of blk.corners) {
          if (c.comparison_id) set.add(c.comparison_id)
        }
      }
    }
    return set
  }, [trackingData])

  function describeUpload(uploadId: string) {
    const u = uploadById.get(uploadId)
    if (!u) return { projectName: '', blockName: '', blockId: '', netVer: '', revision: '', corner: '', fileName: uploadId ? uploadId.slice(0, 8) : '' }
    const owner = projectByAreaId.get(u.project_id)
    const blk = owner?.blocks.find(b => b.area_id === u.block_id)
    const ver = blk?.versions.find(v => v.area_id === u.version_id)
    return {
      projectName: owner?.name || '',
      blockName: blk?.name || '',
      blockId: blk?.id || '',
      netVer: ver?.net_ver || '',
      revision: ver ? [ver.revision, ver.layout].filter(Boolean).join(' ') : '',
      corner: [u.process_label, u.voltage_v && `${u.voltage_v}V`, u.temp_c && `${u.temp_c}°C`, u.pex].filter(Boolean).join(' / ') || cornerKey(u),
      fileName: uploadName(u),
    }
  }

  const enriched = useMemo(() => {
    return [...comparisons]
      .sort((a, b) => text(b.created_at).localeCompare(text(a.created_at)))
      .map(row => {
        const req = object(row.payload.request)
        const currentId = text(req.current_upload_id || row.upload_id)
        const baselineId = text(req.baseline_upload_id || row.baseline_upload_id)
        const kind = text(req.comparison_kind) === 'previous_version' ? 'previous_version' : 'previous_project'
        const isAuto = autoComparisonIds.has(row.id)
        const cur = describeUpload(currentId)
        const base = baselineId ? describeUpload(baselineId) : null
        const searchText = [
          cur.blockName, cur.netVer, cur.revision, cur.corner, cur.fileName,
          base?.projectName, base?.blockName, base?.netVer, base?.revision, base?.corner, base?.fileName,
        ].filter(Boolean).join(' ').toLowerCase()
        return { row, kind, isAuto, cur, base, searchText }
      })
  }, [comparisons, uploadById, projectByAreaId, autoComparisonIds])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return enriched.filter(item =>
      (sourceFilter === 'all' || (sourceFilter === 'auto' ? item.isAuto : !item.isAuto))
      && (kindFilter === 'all' || item.kind === kindFilter)
      && (!blockFilter || item.cur.blockId === blockFilter)
      && (!q || item.searchText.includes(q)),
    )
  }, [enriched, sourceFilter, kindFilter, blockFilter, query])

  if (!comparisons.length) {
    return <Empty title="저장된 비교 이력이 없습니다">‘직접 비교’에서 비교를 실행하면 여기에 블록·버전·코너별 이력이 쌓여 언제든 다시 열 수 있습니다.</Empty>
  }

  return (
    <div className="history-view">
      <section className="panel history-filter-bar" aria-label="비교 이력 필터">
        <div className="view-switch" role="group" aria-label="실행 출처">
          {([['all', `전체 (${enriched.length})`], ['manual', '수동 비교'], ['auto', '자동 분석']] as const).map(([val, label]) => (
            <button key={val} type="button" aria-pressed={sourceFilter === val} onClick={() => setSourceFilter(val)}>{label}</button>
          ))}
        </div>
        <div className="view-switch" role="group" aria-label="비교 방식">
          {([['all', '전체 방식'], ['previous_project', '기준 프로젝트'], ['previous_version', '이전 버전']] as const).map(([val, label]) => (
            <button key={val} type="button" aria-pressed={kindFilter === val} onClick={() => setKindFilter(val)}>{label}</button>
          ))}
        </div>
        <select aria-label="블록 필터" value={blockFilter} onChange={e => setBlockFilter(e.target.value)}>
          <option value="">전체 블록</option>
          {project.blocks.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <label className="history-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="NET_VER, REVISION, 코너, 파일명 검색"
          />
        </label>
        <small className="history-count">{filtered.length}건 표시</small>
      </section>

      <section className="panel tracking-table-panel" aria-label="저장된 비교 이력 목록">
        {!filtered.length ? (
          <p className="muted">검색 조건에 맞는 비교 이력이 없습니다.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>실행 시각</th>
                  <th>구분</th>
                  <th>현재 실행 (블록 · NET_VER · REV · 코너)</th>
                  <th>비교 기준</th>
                  <th>상태</th>
                  <th>열기</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(({ row, kind, isAuto, cur, base }) => (
                  <tr key={row.id} className="history-clickable-row" onClick={() => onOpenComparison(row.id)}>
                    <td className="history-time">{date(row.created_at)}</td>
                    <td>
                      <div className="tracking-tags">
                        <Badge value={isAuto ? '자동 분석' : '수동 비교'} />
                        <span className="side-tag">{kind === 'previous_version' ? '이전 버전' : '기준 프로젝트'}</span>
                      </div>
                    </td>
                    <td>
                      <div className="tracking-cell-stack">
                        <strong>
                          {cur.blockName || '블록 미확인'}
                          {cur.netVer ? ` · ${cur.netVer}` : ''}
                        </strong>
                        <small>
                          {[cur.revision, cur.corner].filter(Boolean).join(' · ') || cur.fileName}
                        </small>
                      </div>
                    </td>
                    <td>
                      {base ? (
                        <div className="tracking-cell-stack">
                          <span>
                            {[base.projectName, base.blockName].filter(Boolean).join(' · ') || '기준 실행'}
                            {base.netVer ? ` · ${base.netVer}` : ''}
                          </span>
                          <small>{[base.revision, base.corner].filter(Boolean).join(' · ') || base.fileName}</small>
                        </div>
                      ) : (
                        <small className="text-muted">기준 실행 없음 (단독 관측)</small>
                      )}
                    </td>
                    <td>
                      <Badge value={row.payload.result_format === 'hierarchy-reference-v1' && row.state === 'completed' ? '조회 준비 완료' : row.state} />
                    </td>
                    <td>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={e => { e.stopPropagation(); onOpenComparison(row.id) }}
                      >
                        <History size={13} />결과 열기 →
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
