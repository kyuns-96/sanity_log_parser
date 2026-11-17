import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, ChevronsDownUp, LoaderCircle } from 'lucide-react'
import { format, formatPercent, text, type TrackingRecord } from './api'
import { Button } from './components/workspace'

export type HierarchyItem = { path: string; parent_path: string | null; depth: number; area: unknown; power_mw: unknown }
export type ResultGroup = { id: string; baselinePaths: string[]; currentPaths: string[]; metrics: Record<string, TrackingRecord['payload']> }
export type BrowserRow = { key: string; baselinePaths: string[]; currentPaths: string[]; baseline?: HierarchyItem; current?: HierarchyItem; result?: ResultGroup; childCount: number }
export type ChildState = { rows: BrowserRow[]; total: number; loading: boolean; error: string }

type Metric = 'area' | 'power'
type Field = 'baseline' | 'current' | 'delta' | 'relative_change_pct'
type Sort = { key: 'name' | `${Metric}.${Field}`; descending: boolean }
type Line = { kind: 'row'; row: BrowserRow; depth: number; parent: string } | { kind: 'status'; key: string; depth: number; parent: string; state: ChildState }
const metricKey: Record<Metric, string> = { area: 'total_area', power: 'pwr_all_total_mw' }
const fields: [Field, string][] = [['baseline', '기준'], ['current', '현재'], ['delta', 'Δ'], ['relative_change_pct', 'Δ%']]
const stateLabel: Record<string, string> = { comparable: '비교', contextual: '조건 차이', unavailable: '불가' }

function leaf(paths: string[], item: HierarchyItem | undefined, separator: string | undefined, full: boolean) {
  const joined = paths.join(' + ')
  if (full || paths.length !== 1) return joined
  if (item?.parent_path && joined.startsWith(item.parent_path)) return joined.slice(item.parent_path.length + 1)
  return separator && joined.includes(separator) ? joined.slice(joined.lastIndexOf(separator) + separator.length) : joined
}

function value(row: BrowserRow, metric: Metric, field: Field): unknown {
  const result = row.result?.metrics[metricKey[metric]]
  if (result) return result[field]
  if (field === 'baseline') return metric === 'area' ? row.baseline?.area : row.baseline?.power_mw
  if (field === 'current') return metric === 'area' ? row.current?.area : row.current?.power_mw
  return null
}

const percent = (input: unknown) => { const shown = formatPercent(input); return shown === '—' ? shown : `${shown}%` }

function sign(input: unknown) {
  const source = text(input)
  if (!source || !/\d/.test(source) || /^[+-]?0*(\.0*)?([eE][+-]?\d+)?$/.test(source)) return 0
  return source.startsWith('-') ? -1 : 1
}

function reasonOf(row: BrowserRow, hasResult: boolean) {
  const notes = (['area', 'power'] as Metric[]).map(metric => {
    const reason = text(row.result?.metrics[metricKey[metric]]?.reason).replace(/^policy_unconfigured( · 정상 판정 아님)?$/, '')
    return reason || (!row.result ? hasResult ? '결과 없음' : '대응 미지정' : '')
  })
  return notes[0] === notes[1] ? notes[0] : notes.map((note, index) => note && `${index ? 'Power' : 'Area'} ${note}`).filter(Boolean).join(' · ')
}

function overall(row: BrowserRow) {
  const states = (['area', 'power'] as Metric[]).map(metric => text(row.result?.metrics[metricKey[metric]]?.comparability) || 'unavailable')
  return states.includes('unavailable') ? 'unavailable' : states.includes('contextual') ? 'contextual' : 'comparable'
}

function unchanged(row: BrowserRow) {
  return !!row.result && overall(row) === 'comparable' && (['area', 'power'] as Metric[]).every(metric => sign(value(row, metric, 'delta')) === 0)
}

export function HierarchyBrowser({ roots, childrenOf, onRequestChildren, flat, hasResult, separator }: {
  roots: BrowserRow[]
  childrenOf: (row: BrowserRow) => ChildState | undefined
  onRequestChildren?: (key: string, offset: number) => void
  flat: boolean
  hasResult: boolean
  separator?: string
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [selected, setSelected] = useState('')
  const [sort, setSort] = useState<Sort>({ key: 'name', descending: false })
  const [changedOnly, setChangedOnly] = useState(false)
  const body = useRef<HTMLTableSectionElement>(null)
  const rootKeys = roots.map(row => row.key).join('\n')
  useEffect(() => {
    setSelected('')
    // A single design root opens on its first level, like a hierarchy browser.
    if (!flat && roots.length === 1 && roots[0].childCount) {
      setExpanded(new Set([roots[0].key]))
      if (!childrenOf(roots[0])) onRequestChildren?.(roots[0].key, 0)
    } else setExpanded(new Set())
  }, [rootKeys, flat])

  function order(rows: BrowserRow[]) {
    const kept = changedOnly ? rows.filter(row => row.childCount > 0 || !unchanged(row)) : rows
    if (sort.key === 'name') return sort.descending ? [...kept].reverse() : kept
    const [metric, field] = sort.key.split('.') as [Metric, Field]
    const numeric = (row: BrowserRow) => { const parsed = Number(text(value(row, metric, field))); return text(value(row, metric, field)) === '' || Number.isNaN(parsed) ? null : parsed }
    return [...kept].sort((a, b) => {
      const left = numeric(a), right = numeric(b)
      if (left === null || right === null) return left === right ? 0 : left === null ? 1 : -1
      return sort.descending ? right - left : left - right
    })
  }

  const lines = useMemo(() => {
    const result: Line[] = []
    const walk = (rows: BrowserRow[], depth: number, parent: string) => {
      for (const row of order(rows)) {
        result.push({ kind: 'row', row, depth, parent })
        if (!expanded.has(row.key)) continue
        const state = childrenOf(row)
        if (state) walk(state.rows, depth + 1, row.key)
        if (!state || state.loading || state.error || state.rows.length < state.total) result.push({ kind: 'status', key: `status:${row.key}`, depth: depth + 1, parent: row.key, state: state || { rows: [], total: row.childCount, loading: true, error: '' } })
      }
    }
    walk(roots, 0, '')
    return result
  }, [roots, expanded, childrenOf, sort, changedOnly])
  const rowLines = lines.filter((line): line is Extract<Line, { kind: 'row' }> => line.kind === 'row')
  const current = rowLines.find(line => line.row.key === selected)

  function toggle(row: BrowserRow, open = !expanded.has(row.key)) {
    if (!row.childCount || open === expanded.has(row.key)) return
    const next = new Set(expanded)
    if (open) { next.add(row.key); if (!childrenOf(row)) onRequestChildren?.(row.key, 0) } else next.delete(row.key)
    setExpanded(next)
  }
  function focus(key: string) {
    setSelected(key)
    window.requestAnimationFrame(() => body.current?.querySelector<HTMLElement>(`tr[data-key="${CSS.escape(key)}"]`)?.focus())
  }
  function onKey(event: KeyboardEvent<HTMLTableSectionElement>) {
    const index = rowLines.findIndex(line => line.row.key === selected)
    const line = rowLines[index]
    const moves: Record<string, () => void> = {
      ArrowDown: () => rowLines[index + 1] && focus(rowLines[index + 1].row.key),
      ArrowUp: () => index > 0 && focus(rowLines[index - 1].row.key),
      Home: () => rowLines[0] && focus(rowLines[0].row.key),
      End: () => rowLines.length && focus(rowLines[rowLines.length - 1].row.key),
      ArrowRight: () => { if (!line?.row.childCount) return; if (!expanded.has(line.row.key)) toggle(line.row, true); else if (rowLines[index + 1]?.parent === line.row.key) focus(rowLines[index + 1].row.key) },
      ArrowLeft: () => { if (!line) return; if (expanded.has(line.row.key)) toggle(line.row, false); else if (line.parent) focus(line.parent) },
      Enter: () => line && toggle(line.row),
    }
    if (moves[event.key]) { event.preventDefault(); moves[event.key]() }
  }
  function sortBy(key: Sort['key']) {
    // Numbers start with the largest value; a second click reverses.
    setSort(previous => previous.key === key ? { key, descending: !previous.descending } : { key, descending: key !== 'name' })
  }
  const sortMark = (key: Sort['key']) => sort.key === key ? (sort.descending ? <ArrowDown size={11} aria-hidden="true" /> : <ArrowUp size={11} aria-hidden="true" />) : null
  const ariaSort = (key: Sort['key']) => sort.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'

  return <div className="hierarchy-browser">
    <div className="browser-toolbar" role="toolbar" aria-label="Hierarchy 보기">
      {!flat && <Button variant="outline" size="xs" onClick={() => setExpanded(new Set())} disabled={!expanded.size}><ChevronsDownUp size={14} />모두 접기</Button>}
      <label className="browser-check"><input type="checkbox" checked={changedOnly} onChange={event => setChangedOnly(event.target.checked)} />변화·미대응만</label>
      {sort.key !== 'name' && <Button variant="ghost" size="xs" onClick={() => setSort({ key: 'name', descending: false })}>정렬 초기화</Button>}
      <small>{flat ? '검색 결과 · instance 전체 경로' : '↑↓ 이동 · → 펼치기 · ← 접기'} · 부모 값에는 자식이 포함됩니다 · 수치 비교이며 정상/이상 판정이 아님</small>
    </div>
    <div className="browser-scroll">
      <table role={flat ? 'grid' : 'treegrid'} aria-label="Instance Area · Power hierarchy">
        <colgroup><col className="col-instance" />{Array.from({ length: 8 }, (_, index) => <col key={index} className="col-number" />)}<col className="col-state" /></colgroup>
        <thead>
          <tr><th rowSpan={2} className="instance-head" aria-sort={ariaSort('name')}><button type="button" onClick={() => sortBy('name')}>Instance {sortMark('name')}</button></th><th colSpan={4} className="group-head">Area</th><th colSpan={4} className="group-head">Power (mW)</th><th rowSpan={2} className="state-head">상태</th></tr>
          <tr>{(['area', 'power'] as Metric[]).flatMap(metric => fields.map(([field, label]) => <th key={`${metric}.${field}`} className={field === 'baseline' ? 'numeric group-start' : 'numeric'} aria-sort={ariaSort(`${metric}.${field}`)}><button type="button" onClick={() => sortBy(`${metric}.${field}`)}>{label} {sortMark(`${metric}.${field}`)}</button></th>))}</tr>
        </thead>
        <tbody ref={body} onKeyDown={onKey}>
          {lines.map(line => {
            const guides = Array.from({ length: Math.min(line.depth, 24) }, (_, index) => <span key={index} className="tree-guide" aria-hidden="true" />)
            if (line.kind === 'status') {
              const { state } = line
              return <tr key={line.key} className="browser-status"><td colSpan={10}><span className="tree-cell">{guides}<span className="tree-spacer" />
                {state.loading ? <span role="status"><LoaderCircle size={13} className="spin" />하위 instance 조회 중…</span>
                  : state.error ? <span className="browser-error" role="alert">{state.error} <Button variant="ghost" size="xs" onClick={() => onRequestChildren?.(line.parent, state.rows.length)}>다시 조회</Button></span>
                  : <Button variant="ghost" size="xs" onClick={() => onRequestChildren?.(line.parent, state.rows.length)}>{(state.total - state.rows.length).toLocaleString()}개 더 보기</Button>}
              </span></td></tr>
            }
            const { row } = line
            const open = expanded.has(row.key)
            const name = leaf(row.currentPaths, row.current, separator, flat), baseName = leaf(row.baselinePaths, row.baseline, separator, flat)
            const state = row.result ? overall(row) : 'unavailable'
            const reason = reasonOf(row, hasResult)
            const tooltip = [`현재 ${row.currentPaths.join(' + ') || '없음'}`, `기준 ${row.baselinePaths.join(' + ') || '없음'}`].join('\n')
            return <tr key={row.key} data-key={row.key} tabIndex={selected === row.key || (!selected && line === lines[0]) ? 0 : -1} aria-level={line.depth + 1} aria-expanded={row.childCount ? open : undefined} aria-selected={selected === row.key} className={selected === row.key ? 'selected' : undefined} onClick={() => setSelected(row.key)} onDoubleClick={() => toggle(row)} onFocus={() => setSelected(row.key)}>
              <td className="instance" title={tooltip}><span className="tree-cell">{guides}
                {row.childCount ? <button type="button" tabIndex={-1} className="tree-toggle" aria-label={`${name || baseName} ${open ? '접기' : '펼치기'}`} onClick={event => { event.stopPropagation(); toggle(row) }}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button> : <span className="tree-spacer" />}
                <span className="instance-name">{name || baseName}</span>
                {name && baseName && name !== baseName && <span className="instance-alias">← {baseName}</span>}
                {!row.currentPaths.length && <span className="side-tag">현재 없음</span>}
                {!row.baselinePaths.length && <span className="side-tag">기준 없음</span>}
                {row.childCount > 0 && <span className="child-count">{row.childCount.toLocaleString()}</span>}
              </span></td>
              {(['area', 'power'] as Metric[]).flatMap(metric => fields.map(([field]) => {
                const raw = value(row, metric, field)
                const direction = field === 'delta' || field === 'relative_change_pct' ? sign(raw) : 0
                const magnitude = field === 'relative_change_pct' && direction ? Math.min(100, Math.abs(Number(text(raw)))) : 0
                return <td key={`${metric}.${field}`} className={`numeric${field === 'baseline' ? ' group-start' : ''}${direction > 0 ? ' up' : direction < 0 ? ' down' : ''}`} title={raw == null || raw === '' ? undefined : text(raw)}>
                  {field === 'relative_change_pct' ? <span className="pct-cell"><span className="pct-bar" aria-hidden="true"><span style={{ width: `${magnitude}%` }} /></span>{formatPercent(raw)}</span> : field === 'delta' && direction ? `${direction > 0 ? '▲' : '▼'} ${format(raw)}` : format(raw)}
                </td>
              }))}
              <td className="state" title={reason || undefined}><span className={`state-dot ${state}`} aria-hidden="true" />{stateLabel[state] || state}</td>
            </tr>
          })}
        </tbody>
      </table>
    </div>
    {current && <dl className="browser-detail" aria-live="polite">
      <div><dt>현재</dt><dd>{current.row.currentPaths.join(' + ') || '대응 없음'}</dd></div>
      <div><dt>기준</dt><dd>{current.row.baselinePaths.join(' + ') || '대응 없음'}</dd></div>
      <div><dt>Area 기준 → 현재</dt><dd>{format(value(current.row, 'area', 'baseline'))} → {format(value(current.row, 'area', 'current'))} · Δ {format(value(current.row, 'area', 'delta'))} · {percent(value(current.row, 'area', 'relative_change_pct'))}</dd></div>
      <div><dt>Power 기준 → 현재</dt><dd>{format(value(current.row, 'power', 'baseline'))} → {format(value(current.row, 'power', 'current'))} mW · Δ {format(value(current.row, 'power', 'delta'))} · {percent(value(current.row, 'power', 'relative_change_pct'))}</dd></div>
      {reasonOf(current.row, hasResult) && <div><dt>사유</dt><dd>{reasonOf(current.row, hasResult)}</dd></div>}
    </dl>}
  </div>
}
