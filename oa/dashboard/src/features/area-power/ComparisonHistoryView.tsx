import { useMemo, useState } from 'react'
import { History, Search } from 'lucide-react'
import { date, formatCornerLabel, object, text, uploadName, type TrackingRecord, type Upload } from './api'
import { Badge, Button, Empty } from './components/workspace'
import type { AtlasProject } from '@/features/atlas/atlasContext'
import { formatRevisionLabel } from '@/lib/versionUtils'
import type { TrackingOverviewData } from './trackingApi'

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
      revision: ver ? formatRevisionLabel(ver, ' ') : '',
      corner: formatCornerLabel(u),
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
