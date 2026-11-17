import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, ChevronsDownUp, LoaderCircle, StepBack, StepForward } from 'lucide-react'
import { format, formatPercent, text, type TrackingRecord } from './api'
import { Button } from './components/workspace'

export type HierarchyItem = { path: string; parent_path: string | null; depth: number; area: unknown; power_mw: unknown }
export type ResultGroup = { id: string; baselinePaths: string[]; currentPaths: string[]; metrics: Record<string, TrackingRecord['payload']> }
export type BrowserRow = { key: string; baselinePaths: string[]; currentPaths: string[]; baseline?: HierarchyItem; current?: HierarchyItem; result?: ResultGroup; childCount: number }
export type ChildState = { rows: BrowserRow[]; total: number; loading: boolean; error: string }

type Metric = 'area' | 'power'
type Field = 'baseline' | 'current' | 'delta' | 'relative_change_pct'
type Sort = { key: 'name' | `${Metric}.${Field}`; descending: boolean }
type Line = { kind: 'row'; row: BrowserRow; depth: number; parent: string; parentRow?: BrowserRow } | { kind: 'status'; key: string; depth: number; parent: string; state: ChildState }
const metricKey: Record<Metric, string> = { area: 'total_area', power: 'pwr_all_total_mw' }
const fields: [Field, string][] = [['baseline', '기준'], ['current', '현재'], ['delta', 'Δ'], ['relative_change_pct', 'Δ%']]
const stateLabel: Record<string, string> = { comparable: '비교', contextual: '조건 차이', unavailable: '불가', pending: '실행 전' }

// A nested row shows only its path below the displayed parent on the same
// side (normally the next hierarchy level). Search results keep full paths.
function leaf(paths: string[], parentPaths: string[] | undefined, separator: string | undefined, full: boolean) {
  if (full) return paths.join(' + ')
  return paths.map(path => {
    const parent = parentPaths?.find(candidate => path.length > candidate.length && path.startsWith(candidate) && (!separator || path.startsWith(separator, candidate.length)))
    if (parent !== undefined) return path.slice(parent.length + (separator ? separator.length : 1))
    return separator && path.includes(separator) ? path.slice(path.lastIndexOf(separator) + separator.length) : path
  }).join(' + ')
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
    return reason || (!row.result ? hasResult ? '결과 없음' : row.baselinePaths.length && row.currentPaths.length ? '비교 실행 전' : '대응 미지정' : '')
  })
  return notes[0] === notes[1] ? notes[0] : notes.map((note, index) => note && `${index ? 'Power' : 'Area'} ${note}`).filter(Boolean).join(' · ')
}

function overall(row: BrowserRow) {
  const states = (['area', 'power'] as Metric[]).map(metric => text(row.result?.metrics[metricKey[metric]]?.comparability) || 'unavailable')
  return states.includes('unavailable') ? 'unavailable' : states.includes('contextual') ? 'contextual' : 'comparable'
}

// Reference results carry the separator in each metric's evidence.
const separatorOf = (row: BrowserRow, fallback?: string) => fallback || text((row.result?.metrics.total_area?.evidence as Record<string, unknown> | undefined)?.hierarchy_separator) || undefined

type Diff = 'added' | 'removed' | 'changed' | 'same'
// Meld-style row state: presence first, then a renamed counterpart or any
// non-zero/unavailable comparison. This is a difference marker, not a verdict.
function diffOf(row: BrowserRow, fallback?: string): Diff {
  const separator = separatorOf(row, fallback) || '/'
  if (!row.baselinePaths.length) return 'added'
  if (!row.currentPaths.length) return 'removed'
  const leafName = (paths: string[]) => paths.map(path => path.slice(path.lastIndexOf(separator) + separator.length)).join('+')
  if (leafName(row.baselinePaths) !== leafName(row.currentPaths)) return 'changed'
  // Previews have no calculated result yet: only presence and names differ.
  return !row.result || unchanged(row) ? 'same' : 'changed'
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
  const [mode, setMode] = useState<'split' | 'single'>('split')
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
    const walk = (rows: BrowserRow[], depth: number, parent: string, parentRow?: BrowserRow) => {
      for (const row of order(rows)) {
        result.push({ kind: 'row', row, depth, parent, parentRow })
        if (!expanded.has(row.key)) continue
        const state = childrenOf(row)
        if (state) walk(state.rows, depth + 1, row.key, row)
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
    if (event.altKey && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) { event.preventDefault(); jump(event.key === 'ArrowDown' ? 1 : -1); return }
    if (moves[event.key]) { event.preventDefault(); moves[event.key]() }
  }
  const differences = rowLines.filter(line => diffOf(line.row, separator) !== 'same').length
  function jump(step: 1 | -1) {
    const start = rowLines.findIndex(line => line.row.key === selected)
    for (let index = start + step; index >= 0 && index < rowLines.length; index += step) {
      if (diffOf(rowLines[index].row, separator) !== 'same') { focus(rowLines[index].row.key); return }
    }
  }
  function sortBy(key: Sort['key']) {
    // Numbers start with the largest value; a second click reverses.
    setSort(previous => previous.key === key ? { key, descending: !previous.descending } : { key, descending: key !== 'name' })
  }
  const sortMark = (key: Sort['key']) => sort.key === key ? (sort.descending ? <ArrowDown size={11} aria-hidden="true" /> : <ArrowUp size={11} aria-hidden="true" />) : null
  const ariaSort = (key: Sort['key']) => sort.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'
  const sortHead = (key: Sort['key'], label: string, extra = '') => <th key={key} className={`numeric${extra ? ` ${extra}` : ''}`} aria-sort={ariaSort(key)}><button type="button" onClick={() => sortBy(key)}>{label} {sortMark(key)}</button></th>

  return <div className="hierarchy-browser">
    <div className="browser-toolbar" role="toolbar" aria-label="Hierarchy 보기">
      <div className="view-switch" role="group" aria-label="보기 방식">{([['split', '좌우 비교'], ['single', '단일 표']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)}>{label}</button>)}</div>
      <Button variant="outline" size="xs" disabled={!differences} onClick={() => jump(-1)} title="이전 차이 (Alt+↑)"><StepBack size={13} />이전 차이</Button>
      <Button variant="outline" size="xs" disabled={!differences} onClick={() => jump(1)} title="다음 차이 (Alt+↓)"><StepForward size={13} />다음 차이</Button>
      <span className="diff-count">표시 중 차이 {differences.toLocaleString()}</span>
      {!flat && <Button variant="outline" size="xs" onClick={() => setExpanded(new Set())} disabled={!expanded.size}><ChevronsDownUp size={14} />모두 접기</Button>}
      <label className="browser-check"><input type="checkbox" checked={changedOnly} onChange={event => setChangedOnly(event.target.checked)} />변화·미대응만</label>
      {sort.key !== 'name' && <Button variant="ghost" size="xs" onClick={() => setSort({ key: 'name', descending: false })}>정렬 초기화</Button>}
      <small>{flat ? '검색 결과 · instance 전체 경로' : '↑↓ 이동 · → 펼치기 · ← 접기 · Alt+↑↓ 차이 이동'} · 부모 값에는 자식이 포함됩니다 · 수치 비교이며 정상/이상 판정이 아님</small>
    </div>
    <div className="browser-scroll">
      <table role={flat ? 'grid' : 'treegrid'} aria-label="Instance Area · Power hierarchy" className={mode === 'split' ? 'split-view' : undefined}>
        {mode === 'split'
          ? <colgroup><col className="col-pane" /><col className="col-number" /><col className="col-number" /><col className="col-gutter" /><col className="col-gutter" /><col className="col-pane" /><col className="col-number" /><col className="col-number" /><col className="col-state" /></colgroup>
          : <colgroup><col className="col-instance" />{Array.from({ length: 8 }, (_, index) => <col key={index} className="col-number" />)}<col className="col-state" /></colgroup>}
        <thead>
          {mode === 'split' ? <>
            <tr><th colSpan={3} className="pane-head">기준</th><th colSpan={2} className="group-head gutter-head">변화율</th><th colSpan={4} className="pane-head current-pane">현재</th></tr>
            <tr>
              <th className="instance-head" aria-sort={ariaSort('name')}><button type="button" onClick={() => sortBy('name')}>Instance {sortMark('name')}</button></th>
              {sortHead('area.baseline', 'Area')}{sortHead('power.baseline', 'Power (mW)')}
              {sortHead('area.relative_change_pct', 'Area Δ%', 'group-start')}{sortHead('power.relative_change_pct', 'Power Δ%')}
              <th className="instance-head group-start"><button type="button" onClick={() => sortBy('name')}>Instance</button></th>
              {sortHead('area.current', 'Area')}{sortHead('power.current', 'Power (mW)')}
              <th className="state-head">상태</th>
            </tr>
          </> : <>
            <tr><th rowSpan={2} className="instance-head" aria-sort={ariaSort('name')}><button type="button" onClick={() => sortBy('name')}>Instance {sortMark('name')}</button></th><th colSpan={4} className="group-head">Area</th><th colSpan={4} className="group-head">Power (mW)</th><th rowSpan={2} className="state-head">상태</th></tr>
            <tr>{(['area', 'power'] as Metric[]).flatMap(metric => fields.map(([field, label]) => sortHead(`${metric}.${field}`, label, field === 'baseline' ? 'group-start' : '')))}</tr>
          </>}
        </thead>
        <tbody ref={body} onKeyDown={onKey}>
          {lines.map(line => {
            const guides = Array.from({ length: Math.min(line.depth, 24) }, (_, index) => <span key={index} className="tree-guide" aria-hidden="true" />)
            if (line.kind === 'status') {
              const { state } = line
              const message = state.loading ? <span role="status"><LoaderCircle size={13} className="spin" />하위 instance 조회 중…</span>
                : state.error ? <span className="browser-error" role="alert">{state.error} <Button variant="ghost" size="xs" onClick={() => onRequestChildren?.(line.parent, state.rows.length)}>다시 조회</Button></span>
                : <Button variant="ghost" size="xs" onClick={() => onRequestChildren?.(line.parent, state.rows.length)}>{(state.total - state.rows.length).toLocaleString()}개 더 보기</Button>
              return <tr key={line.key} className="browser-status"><td colSpan={mode === 'split' ? 9 : 10}><span className="tree-cell">{guides}<span className="tree-spacer" />{message}</span></td></tr>
            }
            const { row } = line
            const open = expanded.has(row.key)
            const pathSeparator = separatorOf(row, separator)
            const name = leaf(row.currentPaths, line.parentRow?.currentPaths, pathSeparator, flat), baseName = leaf(row.baselinePaths, line.parentRow?.baselinePaths, pathSeparator, flat)
            const state = row.result ? overall(row) : hasResult ? 'unavailable' : 'pending'
            const reason = reasonOf(row, hasResult)
            const diff = diffOf(row, separator)
            const toggleButton = row.childCount ? <button type="button" tabIndex={-1} className="tree-toggle" aria-label={`${name || baseName} ${open ? '접기' : '펼치기'}`} onClick={event => { event.stopPropagation(); toggle(row) }}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button> : <span className="tree-spacer" />
            const number = (metric: Metric, field: Field, extra = '') => {
              const raw = value(row, metric, field)
              const direction = field === 'delta' || field === 'relative_change_pct' ? sign(raw) : 0
              const magnitude = field === 'relative_change_pct' && direction ? Math.min(100, Math.abs(Number(text(raw)))) : 0
              return <td key={`${metric}.${field}`} className={`numeric${extra ? ` ${extra}` : ''}${direction > 0 ? ' up' : direction < 0 ? ' down' : ''}`} title={raw == null || raw === '' ? undefined : text(raw)}>
                {field === 'relative_change_pct' ? <span className="pct-cell"><span className="pct-bar" aria-hidden="true"><span style={{ width: `${magnitude}%` }} /></span>{formatPercent(raw)}</span> : field === 'delta' && direction ? `${direction > 0 ? '▲' : '▼'} ${format(raw)}` : format(raw)}
              </td>
            }
            const stateCell = <td className="state" title={reason || undefined}><span className={`state-dot ${state}`} aria-hidden="true" />{stateLabel[state] || state}</td>
            const attributes = { 'data-key': row.key, tabIndex: selected === row.key || (!selected && line === lines[0]) ? 0 : -1, 'aria-level': line.depth + 1, 'aria-expanded': row.childCount ? open : undefined, 'aria-selected': selected === row.key, onClick: () => setSelected(row.key), onDoubleClick: () => toggle(row), onFocus: () => setSelected(row.key) }
            if (mode === 'split') {
              // Both panes share one row, so expanding either side expands both
              // and corresponding instances stay on the same line.
              const side = (present: boolean, label: string, paths: string[], kind: 'baseline' | 'current') => present
                ? <><td className={`instance pane-${kind}`} title={paths.join(' + ')}><span className="tree-cell">{guides}{toggleButton}<span className="instance-name">{label}</span>{row.childCount > 0 && <span className="child-count">{row.childCount.toLocaleString()}</span>}</span></td>{number('area', kind, `pane-${kind}`)}{number('power', kind, `pane-${kind}`)}</>
                : <><td className={`instance void pane-${kind}`}><span className="tree-cell">{guides}{toggleButton}<span className="void-label">{kind === 'baseline' ? '기준 없음' : '현재 없음'}</span></span></td><td className={`void pane-${kind}`} /><td className={`void pane-${kind}`} /></>
              return <tr key={row.key} {...attributes} className={`diff-${diff}${selected === row.key ? ' selected' : ''}`}>
                {side(row.baselinePaths.length > 0, baseName, row.baselinePaths, 'baseline')}
                {number('area', 'relative_change_pct', 'group-start gutter')}{number('power', 'relative_change_pct', 'gutter')}
                {side(row.currentPaths.length > 0, name, row.currentPaths, 'current')}
                {stateCell}
              </tr>
            }
            const tooltip = [`현재 ${row.currentPaths.join(' + ') || '없음'}`, `기준 ${row.baselinePaths.join(' + ') || '없음'}`].join('\n')
            return <tr key={row.key} {...attributes} className={selected === row.key ? 'selected' : undefined}>
              <td className="instance" title={tooltip}><span className="tree-cell">{guides}{toggleButton}
                <span className="instance-name">{name || baseName}</span>
                {name && baseName && name !== baseName && <span className="instance-alias">← {baseName}</span>}
                {!row.currentPaths.length && <span className="side-tag">현재 없음</span>}
                {!row.baselinePaths.length && <span className="side-tag">기준 없음</span>}
                {row.childCount > 0 && <span className="child-count">{row.childCount.toLocaleString()}</span>}
              </span></td>
              {(['area', 'power'] as Metric[]).flatMap(metric => fields.map(([field]) => number(metric, field, field === 'baseline' ? 'group-start' : '')))}
              {stateCell}
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
