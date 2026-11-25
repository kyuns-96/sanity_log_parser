import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, ChevronsDownUp, Columns3, LoaderCircle, Maximize2, Minimize2, StepBack, StepForward } from 'lucide-react'
import { compareNumber, isNumber } from 'lossless-json'
import { add, compare as compareFraction, divide, fraction, magnitude as absFraction, multiply, parseDecimal, subtract, type Fraction } from './fraction'
import { gateCountRow, type GateBases } from './gateCount'
import { format, formatPercent, text, type TrackingRecord } from './api'
import { Button } from './components/workspace'
import { AREA_THRESHOLD, DEFAULT_COLUMNS, DEFAULT_MIN_AREA, MAX_COLUMNS, METRIC_CATALOG, METRIC_GROUPS, atLeast, metricDef, type MetricDef } from './metricCatalog'

export type HierarchyItem = { path: string; parent_path: string | null; depth: number; area: unknown; logic_area?: unknown; power_mw: unknown }
export type ResultGroup = { id: string; baselinePaths: string[]; currentPaths: string[]; metrics: Record<string, TrackingRecord['payload']> }
export type BrowserRow = { key: string; baselinePaths: string[]; currentPaths: string[]; baseline?: HierarchyItem; current?: HierarchyItem; result?: ResultGroup; childCount: number; hiddenChildren?: number }
export type ChildState = { rows: BrowserRow[]; total: number; loading: boolean; error: string; hidden?: number }

type Field = 'baseline' | 'current' | 'delta' | 'relative_change_pct' | 'pct_point_change'
type Sort = { key: string; descending: boolean }
const defaultSort: Sort = { key: 'size', descending: true }
type Line = { kind: 'row'; row: BrowserRow; depth: number; parent: string; parentRow?: BrowserRow } | { kind: 'status'; key: string; depth: number; parent: string; state: ChildState; missing: boolean }
// Amounts show Δ and Δ%; shares (Vth, drive, CTS) show the percentage-point change.
const fieldsOf = (metric: MetricDef): [Field, string][] => metric.ratio ? [['baseline', '기준'], ['current', '현재'], ['pct_point_change', 'Δ%p']] : [['baseline', '기준'], ['current', '현재'], ['delta', 'Δ'], ['relative_change_pct', 'Δ%']]
const changeOf = (metric: MetricDef): [Field, string] => metric.ratio ? ['pct_point_change', 'Δ%p'] : ['relative_change_pct', 'Δ%']
const unitOf = (metric: MetricDef) => metric.unit && metric.unit !== 'GE' ? ` ${metric.unit}` : ''
const ABS_PERCENT_FIELDS = new Set<Field>(['relative_change_pct', 'pct_point_change'])
const MARK_LABEL: Record<string, string> = { unexpected: '이상', needs_review: '확인', expected: '설명됨', minor: '미미' }
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

function value(row: BrowserRow, metric: string, field: Field): unknown {
  const result = row.result?.metrics[metric]
  if (result) return result[field]
  const preview = metric === 'logic_area' ? 'logic_area' : metric === 'total_area' ? 'area' : metric === 'pwr_all_total_mw' ? 'power_mw' : null
  if (preview && field === 'baseline') return row.baseline?.[preview]
  if (preview && field === 'current') return row.current?.[preview]
  return null
}

const percent = (input: unknown) => { const shown = formatPercent(input); return shown === '—' ? shown : `${shown}%` }

function sign(input: unknown) {
  const source = text(input)
  if (!source || !/\d/.test(source) || /^[+-]?0*(\.0*)?([eE][+-]?\d+)?$/.test(source)) return 0
  return source.startsWith('-') ? -1 : 1
}

// The text a number cell shows. Column widths are measured from the same text.
function amount(input: unknown, metric: MetricDef) {
  return metric.unit === 'GE' ? formatPercent(input).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1') : format(input)
}

function shown(row: BrowserRow, metric: MetricDef, field: Field) {
  const raw = value(row, metric.id, field)
  if (ABS_PERCENT_FIELDS.has(field)) return formatPercent(raw)
  const direction = field === 'delta' ? sign(raw) : 0
  if (direction) return `${direction > 0 ? '▲' : '▼'} ${amount(raw, metric)}`
  return metric.ratio ? formatPercent(raw) : amount(raw, metric)
}

// Number columns are as wide as their content (the header label or the longest
// loaded value) instead of a fixed 92px, so the default Logic area · Power set
// fits a 1920px screen side by side. Instance columns share the width that is
// left and never go below their minimum; past that the table scrolls sideways.
// DIGIT, CELL_SLACK and HEAD_FONT mirror the cell and header styles in styles.css.
const CELL_SLACK = 16, DIGIT = 7.8, SORT_MARK = 14, COLUMN_MIN = 48, COLUMN_MAX = 150
const PANE_MIN = 150, INSTANCE_MIN = 240
const HEAD_FONT = '600 12px "Pretendard Variable", Pretendard, sans-serif'
// Mono digits are DIGIT wide; the ▲▼ markers come from a wider fallback glyph.
const valueWidth = (shownText: string) => Math.ceil(shownText.length * DIGIT) + (/[▲▼]/.test(shownText) ? 5 : 0)
// State labels are short Korean words (about 12px a glyph) after a 13px dot.
const stateWidth = (label: string) => 13 + [...label].reduce((sum, glyph) => sum + (glyph === ' ' ? 4 : 12), 0) + CELL_SLACK
const measuredHeads = new Map<string, number>()
let ruler: CanvasRenderingContext2D | null | undefined
function headWidth(label: string) {
  const cached = measuredHeads.get(label)
  if (cached !== undefined) return cached
  if (ruler === undefined) ruler = typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d')
  if (!ruler) return label.length * 7
  ruler.font = HEAD_FONT
  const width = Math.ceil(ruler.measureText(label).width)
  // Before the web font is ready the measurement uses a fallback face; do not keep it.
  if (document.fonts?.status === 'loaded') measuredHeads.set(label, width)
  return width
}
const columnWidth = (label: string, longest: number, sorted: boolean) => Math.min(COLUMN_MAX, Math.max(COLUMN_MIN, headWidth(label) + CELL_SLACK + (sorted ? SORT_MARK : 0), longest + CELL_SLACK))

function reasonOf(row: BrowserRow, hasResult: boolean) {
  const notes = ['total_area', 'pwr_all_total_mw'].map(metric => {
    const reason = text(row.result?.metrics[metric]?.reason).replace(/^policy_unconfigured( · 정상 판정 아님)?$/, '')
    return reason || (!row.result ? hasResult ? '결과 없음' : row.baselinePaths.length && row.currentPaths.length ? '비교 실행 전' : '대응 미지정' : '')
  })
  return notes[0] === notes[1] ? notes[0] : notes.map((note, index) => note && `${index ? 'Power' : 'Area'} ${note}`).filter(Boolean).join(' · ')
}

function overall(row: BrowserRow) {
  const states = ['total_area', 'pwr_all_total_mw'].map(metric => text(row.result?.metrics[metric]?.comparability) || 'unavailable')
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
  return !!row.result && overall(row) === 'comparable' && ['total_area', 'pwr_all_total_mw'].every(metric => sign(value(row, metric, 'delta')) === 0)
}

export function HierarchyBrowser({ roots, childrenOf, onRequestChildren, flat, hasResult, separator, columns, onColumns, minArea, onMinArea, labels, gateBases, unitSwitch, marks, locate }: {
  roots: BrowserRow[]
  childrenOf: (row: BrowserRow) => ChildState | undefined
  onRequestChildren?: (key: string, offset: number) => void
  flat: boolean
  hasResult: boolean
  separator?: string
  columns: string[]
  onColumns: (columns: string[]) => void
  minArea: string
  onMinArea: (value: string) => void
  labels: { baseline: string; current: string }
  gateBases: GateBases | null
  unitSwitch: ReactNode
  /** AI analysis verdict per result group id. */
  marks?: Map<string, string>
  /** Open these ancestor rows, then select the row `id`. */
  locate?: { ancestors: string[]; id: string; nonce: number } | null
}) {
  const sideName = (side: 'baseline' | 'current') => `${side === 'baseline' ? '기준' : '현재'}${labels[side] ? ` · ${labels[side]}` : ''}`
  const displayMetric = (metric: MetricDef) => gateBases && metric.id.endsWith('_area')
    ? { ...metric, label: metric.label.replace(/area$/i, 'Gate Count'), unit: 'GE' } : metric
  const metrics = columns.map(id => metricDef.get(id)).filter((metric): metric is MetricDef => !!metric).map(displayMetric)
  const [choosing, setChoosing] = useState(false)
  const [areaDraft, setAreaDraft] = useState(minArea)
  useEffect(() => setAreaDraft(minArea), [minArea])
  const commitArea = () => { const next = areaDraft.trim(); if (next === '' || AREA_THRESHOLD.test(next)) { if (next !== minArea) onMinArea(next) } else setAreaDraft(minArea) }
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [selected, setSelected] = useState('')
  const [sort, setSort] = useState<Sort>(defaultSort)
  const [changedOnly, setChangedOnly] = useState(false)
  const [mode, setMode] = useState<'split' | 'single'>('split')
  const [changeMode, setChangeMode] = useState<'pct' | 'abs'>('pct')
  const [maximized, setMaximized] = useState(false)
  const activeChangeOf = (metric: MetricDef): [Field, string] => metric.ratio ? ['pct_point_change', 'Δ%p'] : changeMode === 'abs' ? ['delta', 'Δ'] : ['relative_change_pct', 'Δ%']
  function switchChangeMode(val: 'pct' | 'abs') {
    setChangeMode(val)
    setSort(prev => {
      if (val === 'abs' && prev.key.endsWith('.relative_change_pct')) return { ...prev, key: prev.key.replace(/\.relative_change_pct$/, '.delta') }
      if (val === 'pct' && prev.key.endsWith('.delta')) return { ...prev, key: prev.key.replace(/\.delta$/, '.relative_change_pct') }
      return prev
    })
  }
  useEffect(() => {
    if (!maximized) return
    const close = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setMaximized(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [maximized])
  const body = useRef<HTMLTableSectionElement>(null)
  const rootKeys = roots.map(row => row.key).join('\n')
  useEffect(() => {
    setSelected('')
    // A single design root opens on its first level, like a hierarchy browser.
    if (!flat && roots.length === 1 && roots[0].childCount) setExpanded(new Set([roots[0].key]))
    else setExpanded(new Set())
  }, [rootKeys, flat])

  const projectRow = useMemo(() => {
    if (!gateBases) return null
    const cache = new WeakMap<BrowserRow, BrowserRow>()
    return (row: BrowserRow) => {
      const existing = cache.get(row)
      if (existing) return existing
      const converted = gateCountRow(row, gateBases)
      cache.set(row, converted)
      return converted
    }
  }, [gateBases])

  // Symmetric positive root reference per metric group (s = 0.10%, p = 10% => eps = 1.0% of Root).
  const rootRefs = useMemo(() => {
    const ZERO = fraction(0n)
    const TWO = fraction(2n)
    const mappedRoots = projectRow ? roots.map(projectRow) : roots
    const sideSum = (key: string, side: 'baseline' | 'current'): Fraction | null => {
      let acc = ZERO, found = false
      for (const r of mappedRoots) {
        const f = parseDecimal(value(r, key, side))
        if (f && f.n > 0n) { acc = add(acc, f); found = true }
      }
      return found ? acc : null
    }
    const symRef = (key: string): Fraction => {
      const b = sideSum(key, 'baseline'), c = sideSum(key, 'current')
      if (b && c) return divide(add(b, c), TWO)
      return b || c || ZERO
    }
    const totalArea = symRef('total_area')
    const logicRaw = symRef('logic_area')
    const logicArea = logicRaw.n > 0n ? logicRaw : totalArea
    const powerTotal = symRef('pwr_all_total_mw')
    const totalCells = symRef('total_cells')
    return { logicArea, totalArea, powerTotal, totalCells }
  }, [roots, projectRow])

  function rootForMetric(metricId: string): Fraction {
    if (metricId === 'logic_area' || metricId === 'cts_area' || metricId === 'others_area') return rootRefs.logicArea
    if (metricId.endsWith('_area')) return rootRefs.totalArea
    if (metricId.startsWith('pwr_')) return rootRefs.powerTotal
    return rootRefs.totalCells
  }

  function scaleMetrics(row: BrowserRow, metric: MetricDef): { dampedPct: Fraction | null; rootSharePct: Fraction | null; isNoise: boolean } {
    if (metric.ratio) return { dampedPct: null, rootSharePct: null, isNoise: false }
    const HUNDRED = fraction(100n)
    const EPS_FACTOR = fraction(1n, 100n) // s=0.10% / p=10% = 1.0% of Root
    const TEN = fraction(10n)
    const HALF = fraction(1n, 2n)
    const cFrac = parseDecimal(value(row, metric.id, 'current'))
    let bFrac = parseDecimal(value(row, metric.id, 'baseline'))
    if (!bFrac && !row.baselinePaths.length && row.currentPaths.length && cFrac) bFrac = fraction(0n)
    const deltaFrac = parseDecimal(value(row, metric.id, 'delta')) ?? (bFrac && cFrac ? subtract(cFrac, bFrac) : null)
    if (!deltaFrac || deltaFrac.n === 0n) return { dampedPct: fraction(0n), rootSharePct: fraction(0n), isNoise: false }
    const rootRef = rootForMetric(metric.id)
    const eps = rootRef.n > 0n ? multiply(rootRef, EPS_FACTOR) : fraction(0n)
    const denom = bFrac ? add(bFrac, eps) : eps
    const dampedPct = denom.n > 0n ? multiply(divide(deltaFrac, denom), HUNDRED) : null
    const rootSharePct = rootRef.n > 0n ? multiply(divide(absFraction(deltaFrac), rootRef), HUNDRED) : null
    const belowDamped = dampedPct ? compareFraction(absFraction(dampedPct), TEN) < 0 : false
    const belowShare = rootSharePct ? compareFraction(rootSharePct, HALF) < 0 : true
    return { dampedPct, rootSharePct, isNoise: belowDamped && belowShare }
  }

  function order(rows: BrowserRow[]) {
    const kept = changedOnly ? rows.filter(row => row.childCount > 0 || !unchanged(row)) : rows
    if (sort.key === 'name') {
      const label = (row: BrowserRow) => row.currentPaths[0] || row.baselinePaths[0] || ''
      return [...kept].sort((a, b) => (sort.descending ? -1 : 1) * label(a).localeCompare(label(b), undefined, { numeric: true }))
    }
    const read = (row: BrowserRow, metric: string, field: Field) => {
      const number = text(value(row, metric, field))
      return isNumber(number) ? number : null
    }
    const [sortMetric, sortField] = sort.key.split('.') as [string, Field]
    if (sortField === 'relative_change_pct') {
      const mDef = metricDef.get(sortMetric)
      if (mDef && !mDef.ratio) {
        return [...kept].sort((a, b) => {
          const left = scaleMetrics(a, mDef).dampedPct, right = scaleMetrics(b, mDef).dampedPct
          if (left === null || right === null) return left === right ? 0 : left === null ? 1 : -1
          return sort.descending ? compareFraction(right, left) : compareFraction(left, right)
        })
      }
    }
    // Default: largest instance first (current area, else baseline area), the
    // same order the server pages children in.
    const numeric = sort.key === 'size' ? (row: BrowserRow) => read(row, 'total_area', 'current') ?? read(row, 'total_area', 'baseline') : (row: BrowserRow) => read(row, sortMetric, sortField)
    return [...kept].sort((a, b) => {
      const left = numeric(a), right = numeric(b)
      if (left === null || right === null) return left === right ? 0 : left === null ? 1 : -1
      return sort.descending ? compareNumber(right, left) : compareNumber(left, right)
    })
  }
  const lines = useMemo(() => {
    const result: Line[] = []
    const walk = (rows: BrowserRow[], depth: number, parent: string, parentRow?: BrowserRow) => {
      for (const row of order(projectRow ? rows.map(projectRow) : rows)) {
        result.push({ kind: 'row', row, depth, parent, parentRow })
        if (!expanded.has(row.key)) continue
        const state = childrenOf(row)
        if (state) walk(state.rows, depth + 1, row.key, row)
        if (!state || state.loading || state.error || state.rows.length < state.total || state.hidden) result.push({ kind: 'status', key: `status:${row.key}`, depth: depth + 1, parent: row.key, state: state || { rows: [], total: row.childCount, loading: true, error: '' }, missing: !state })
      }
    }
    walk(roots, 0, '')
    return result
  }, [roots, expanded, childrenOf, sort, changedOnly, projectRow])
  const rowLines = lines.filter((line): line is Extract<Line, { kind: 'row' }> => line.kind === 'row')
  // Jump to an analysed instance: expand its ancestors (children load one level
  // at a time) and select it once its row exists.
  const [pending, setPending] = useState('')
  useEffect(() => {
    if (!locate) return
    setExpanded(previous => new Set([...previous, ...locate.ancestors]))
    setPending(locate.id)
  }, [locate?.nonce])
  const pendingVisible = !!pending && rowLines.some(line => line.row.key === pending)
  useEffect(() => { if (pendingVisible) { focus(pending); setPending('') } }, [pendingVisible, pending])
  // Expanded nodes without loaded children (first open, or after the columns
  // or threshold changed) request one level.
  const missing = lines.filter(line => line.kind === 'status' && line.missing).map(line => line.parent).join('\n')
  useEffect(() => { if (missing) for (const key of missing.split('\n')) onRequestChildren?.(key, 0) }, [missing])
  const current = rowLines.find(line => line.row.key === selected)

  function toggle(row: BrowserRow, open = !expanded.has(row.key)) {
    if (!row.childCount || open === expanded.has(row.key)) return
    const next = new Set(expanded)
    if (open) next.add(row.key); else next.delete(row.key)
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
  function sortBy(key: string) {
    // Numbers start with the largest value; a second click reverses.
    setSort(previous => previous.key === key ? { key, descending: !previous.descending } : { key, descending: key !== 'name' })
  }
  const sortMark = (key: string) => sort.key === key ? (sort.descending ? <ArrowDown size={11} aria-hidden="true" /> : <ArrowUp size={11} aria-hidden="true" />) : null
  const ariaSort = (key: string) => sort.key === key ? (sort.descending ? 'descending' : 'ascending') : 'none'
  // `short` is what the header shows when the zone header above already names the unit.
  const sortHead = (key: string, label: string, extra = '', short = label) => <th key={key} className={`numeric${extra ? ` ${extra}` : ''}`} aria-sort={ariaSort(key)} title={label}><button type="button" aria-label={label} onClick={() => sortBy(key)}><span className="head-label">{short}</span>{sortMark(key)}</button></th>

  const fracToApproxPct = (f: Fraction | null): string => {
    if (!f) return '—'
    const signStr = f.n < 0n ? '-' : f.n > 0n ? '+' : ''
    const scaled100 = (absFraction(f).n * 200n + f.d) / (2n * f.d)
    return `${signStr}${scaled100 / 100n}.${(scaled100 % 100n).toString().padStart(2, '0')}%`
  }

  const toggleColumn = (id: string) => onColumns(columns.includes(id) ? columns.filter(item => item !== id) : METRIC_CATALOG.map(metric => metric.id).filter(item => item === id || columns.includes(item)))
  const numberCell = (row: BrowserRow, metric: MetricDef, field: Field, extra = '') => {
    const raw = value(row, metric.id, field)
    const isChangeField = field === 'delta' || ABS_PERCENT_FIELDS.has(field)
    const direction = isChangeField ? sign(raw) : 0
    const scale = isChangeField && direction && !metric.ratio ? scaleMetrics(row, metric) : null
    const isNoise = Boolean(scale?.isNoise)
    let barMagnitude = 0
    if (field === 'pct_point_change' && direction) {
      barMagnitude = Math.min(100, Math.abs(Number(text(raw))) * 5)
    } else if (isChangeField && direction && !metric.ratio && !isNoise && scale?.rootSharePct) {
      const absShare = absFraction(scale.rootSharePct)
      if (compareFraction(absShare, fraction(40n)) >= 0) {
        barMagnitude = 100
      } else {
        const bps = Number((absShare.n * 10000n) / absShare.d)
        const scaledBar = Math.min(100, (bps / 10000) * 2.5)
        barMagnitude = scaledBar >= 1 ? scaledBar : 0
      }
    }
    const label = shown(row, metric, field)
    let tooltip: string | undefined = raw == null || raw === '' ? undefined : text(raw)
    if (scale && direction) {
      const rawDelta = amount(value(row, metric.id, 'delta'), metric)
      const u = unitOf(metric)
      tooltip = `Δ ${direction > 0 ? '+' : ''}${rawDelta}${u} · 체급 연동 ${fracToApproxPct(scale.dampedPct)} · 루트 비중 ${fracToApproxPct(scale.rootSharePct).replace(/^\+/, '')}${isNoise ? ' (미소 변화 완충)' : ''}`
    }
    const toneClass = isNoise ? ' noise-muted' : direction > 0 ? ' up' : direction < 0 ? ' down' : ''
    // The size of a change is a bar along the cell proportional to share of root total.
    return <td key={`${metric.id}.${field}`} className={`numeric${extra ? ` ${extra}` : ''}${toneClass}${barMagnitude ? ' change-bar' : ''}`} style={barMagnitude ? { '--magnitude': barMagnitude / 100 } as CSSProperties : undefined} title={tooltip}>{label}</td>
  }
  const splitColumns = 2 + metrics.length * 3 + 1
  const singleColumns = 1 + metrics.reduce((sum, metric) => sum + fieldsOf(metric).length, 0) + 1
  const stateOf = (row: BrowserRow) => row.result ? overall(row) : hasResult ? 'unavailable' : 'pending'
  // Widest shown value per column (px) among the loaded rows.
  const longest = useMemo(() => {
    const result = new Map<string, number>()
    const note = (key: string, width: number) => { if (width > (result.get(key) || 0)) result.set(key, width) }
    for (const line of lines) {
      if (line.kind !== 'row') continue
      note('state', stateWidth(stateLabel[stateOf(line.row)] || stateOf(line.row)))
      for (const metric of metrics) for (const field of mode === 'split' ? ['baseline', 'current', activeChangeOf(metric)[0]] as Field[] : fieldsOf(metric).map(([field]) => field)) note(`${metric.id}.${field}`, valueWidth(shown(line.row, metric, field)))
    }
    return result
  }, [lines, columns.join('\n'), mode, changeMode, hasResult])
  const widthOf = (metric: MetricDef, field: Field, label: string) => columnWidth(label, longest.get(`${metric.id}.${field}`) || 0, sort.key === `${metric.id}.${field}`)
  // Both panes use one width per metric so the two sides line up.
  const paneWidth = (metric: MetricDef) => Math.max(widthOf(metric, 'baseline', `${metric.label}${unitOf(metric)}`), widthOf(metric, 'current', `${metric.label}${unitOf(metric)}`))
  const gutterWidth = (metric: MetricDef) => widthOf(metric, activeChangeOf(metric)[0], metric.label)
  const singleWidths = metrics.flatMap(metric => fieldsOf(metric).map(([field, label]) => widthOf(metric, field, label)))
  const stateColumn = Math.max(52, longest.get('state') || 0)
  const minWidth = mode === 'split'
    ? 2 * PANE_MIN + metrics.reduce((sum, metric) => sum + 2 * paneWidth(metric) + gutterWidth(metric), 0) + stateColumn
    : INSTANCE_MIN + singleWidths.reduce((sum, width) => sum + width, 0) + stateColumn

  return <div className={`hierarchy-browser${maximized ? ' maximized' : ''}`} role={maximized ? 'dialog' : undefined} aria-modal={maximized || undefined} aria-label={maximized ? 'Hierarchy 크게 보기' : undefined}>
    <div className="browser-toolbar" role="toolbar" aria-label="Hierarchy 보기">
      {maximized && unitSwitch}
      <div className="view-switch" role="group" aria-label="보기 방식">{([['split', '좌우 비교'], ['single', '단일 표']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)}>{label}</button>)}</div>
      {mode === 'split' && <div className="view-switch" role="group" aria-label="변화 표시">{([['pct', '변화 Δ%'], ['abs', '변화 Δ']] as const).map(([val, lbl]) => <button key={val} type="button" aria-pressed={changeMode === val} onClick={() => switchChangeMode(val)} title={val === 'pct' ? '상대 변화율 표시 (정렬 시 미소 노이즈를 완충한 체급 연동 순)' : '절대 변화량(Δ) 표시 및 크기순 정렬'}>{lbl}</button>)}</div>}
      <Button variant="outline" size="xs" aria-expanded={choosing} onClick={() => setChoosing(value => !value)}><Columns3 size={13} />열 선택 · {metrics.length}</Button>
      <label className="browser-threshold" title="현재·기준 중 큰 total area가 이 값보다 작은 하위 instance를 숨깁니다. 비우면 모두 표시합니다. 루트는 항상 표시합니다.">{gateBases ? '원본 Area' : 'Area'} ≥<input type="text" inputMode="decimal" value={areaDraft} placeholder="전체" onChange={event => setAreaDraft(event.target.value)} onBlur={commitArea} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); commitArea() } if (event.key === 'Escape') setAreaDraft(minArea) }} aria-invalid={areaDraft.trim() !== '' && !AREA_THRESHOLD.test(areaDraft.trim())} /></label>
      <Button variant="outline" size="xs" disabled={!differences} onClick={() => jump(-1)} title="이전 차이 (Alt+↑)"><StepBack size={13} />이전 차이</Button>
      <Button variant="outline" size="xs" disabled={!differences} onClick={() => jump(1)} title="다음 차이 (Alt+↓)"><StepForward size={13} />다음 차이</Button>
      <span className="diff-count">표시 중 차이 {differences.toLocaleString()}</span>
      <Button variant="outline" size="xs" aria-pressed={maximized} onClick={() => setMaximized(value => !value)} title={maximized ? '원래 크기 (Esc)' : '표만 화면 전체로 보기'}>{maximized ? <><Minimize2 size={13} />원래 크기</> : <><Maximize2 size={13} />크게 보기</>}</Button>
      {!flat && <Button variant="outline" size="xs" onClick={() => setExpanded(new Set())} disabled={!expanded.size}><ChevronsDownUp size={14} />모두 접기</Button>}
      <label className="browser-check"><input type="checkbox" checked={changedOnly} onChange={event => setChangedOnly(event.target.checked)} />변화·미대응만</label>
      {sort.key === 'size' ? <span className="diff-count">정렬 · {gateBases ? 'Gate Count' : 'Area'} 큰 순</span> : <Button variant="ghost" size="xs" onClick={() => setSort(defaultSort)}>정렬 초기화 ({gateBases ? 'Gate Count' : 'Area'} 큰 순)</Button>}
      {mode === 'single' && <span className="side-legend">{sideName('baseline')} <span aria-hidden="true">↔</span> {sideName('current')}</span>}
      <small>{flat ? '검색 결과 · instance 전체 경로 · ' : ''}부모 값에는 자식이 포함됩니다 · 수치 비교이며 정상/이상 판정이 아님</small>
    </div>
    {gateBases && <p className="gate-count-basis" role="status"><span>기준 · {gateBases.baseline.note}</span><span>현재 · {gateBases.current.note}</span></p>}
    {choosing && <div className="column-chooser" role="group" aria-label="표시할 지표">
      {METRIC_GROUPS.map(group => <fieldset key={group}><legend>{group}</legend>{METRIC_CATALOG.filter(metric => metric.group === group).map(metric => {
        const checked = columns.includes(metric.id)
        return <label key={metric.id}><input type="checkbox" checked={checked} disabled={!checked && columns.length >= MAX_COLUMNS} onChange={() => toggleColumn(metric.id)} />{displayMetric(metric).label}{metric.ratio ? ' %' : metric.unit ? ` (${metric.unit})` : ''}</label>
      })}</fieldset>)}
      <div className="chooser-actions"><small>최대 {MAX_COLUMNS}개 · 선택은 이 브라우저에만 저장됩니다. 비율(%)은 Δ%p로 비교합니다.</small><Button variant="ghost" size="xs" onClick={() => onColumns(DEFAULT_COLUMNS)}>기본값 ({gateBases ? 'Logic Gate Count' : 'Logic area'} · Power)</Button><Button variant="ghost" size="xs" onClick={() => onMinArea(DEFAULT_MIN_AREA)}>Area 임계값 기본 {DEFAULT_MIN_AREA}</Button><Button variant="outline" size="xs" onClick={() => setChoosing(false)}>닫기</Button></div>
    </div>}
    <div className="browser-scroll">
      <table role={flat ? 'grid' : 'treegrid'} aria-label="Instance hierarchy 비교" className={mode === 'split' ? 'split-view' : undefined} style={{ minWidth }}>
        {mode === 'split'
          ? <colgroup><col className="col-pane" />{metrics.map(metric => <col key={`b${metric.id}`} style={{ width: paneWidth(metric) }} />)}{metrics.map(metric => <col key={`g${metric.id}`} style={{ width: gutterWidth(metric) }} />)}<col className="col-pane" />{metrics.map(metric => <col key={`c${metric.id}`} style={{ width: paneWidth(metric) }} />)}<col className="col-state" style={{ width: stateColumn }} /></colgroup>
          : <colgroup><col className="col-instance" />{singleWidths.map((width, index) => <col key={index} style={{ width }} />)}<col className="col-state" style={{ width: stateColumn }} /></colgroup>}
        <thead>
          {mode === 'split' ? <>
            <tr><th colSpan={1 + metrics.length} className="pane-head" title={sideName('baseline')}><span className="zone-label">{sideName('baseline')}</span></th><th colSpan={metrics.length} className="group-head gutter-head" title="현재가 기준보다 얼마나 달라졌는지입니다. 막대 길이는 루트 전체 대비 절대 기여도이며, Δ% 정렬은 미소 노이즈를 완충한 체급 연동 순입니다."><span className="zone-label">변화 <small>{changeMode === 'abs' ? 'Δ · 비율은 Δ%p' : 'Δ% · 비율은 Δ%p'}</small></span></th><th colSpan={2 + metrics.length} className="pane-head current-pane" title={sideName('current')}><span className="zone-label">{sideName('current')}</span></th></tr>
            <tr>
              <th className="instance-head" aria-sort={ariaSort('name')}><button type="button" onClick={() => sortBy('name')}>Instance {sortMark('name')}</button></th>
              {metrics.map(metric => sortHead(`${metric.id}.baseline`, `${metric.label}${unitOf(metric)}`))}
              {metrics.map((metric, index) => sortHead(`${metric.id}.${activeChangeOf(metric)[0]}`, `${metric.label} ${activeChangeOf(metric)[1]}`, index ? '' : 'group-start', metric.label))}
              <th className="instance-head group-start"><button type="button" onClick={() => sortBy('name')}>Instance</button></th>
              {metrics.map(metric => sortHead(`${metric.id}.current`, `${metric.label}${unitOf(metric)}`))}
              <th className="state-head">상태</th>
            </tr>
          </> : <>
            <tr><th rowSpan={2} className="instance-head" aria-sort={ariaSort('name')}><button type="button" onClick={() => sortBy('name')}>Instance {sortMark('name')}</button></th>{metrics.map(metric => <th key={metric.id} colSpan={fieldsOf(metric).length} className="group-head">{metric.label}{unitOf(metric)}</th>)}<th rowSpan={2} className="state-head">상태</th></tr>
            <tr>{metrics.flatMap(metric => fieldsOf(metric).map(([field, label]) => sortHead(`${metric.id}.${field}`, label, field === 'baseline' ? 'group-start' : '')))}</tr>
          </>}
        </thead>
        <tbody ref={body} onKeyDown={onKey}>
          {lines.map(line => {
            const guides = Array.from({ length: Math.min(line.depth, 24) }, (_, index) => <span key={index} className="tree-guide" aria-hidden="true" />)
            if (line.kind === 'status') {
              const { state } = line
              const hidden = state.hidden ? <span className="hidden-note">Area {minArea ? `< ${minArea}` : '임계값 미만'}인 하위 {state.hidden.toLocaleString()}개 숨김</span> : null
              const message = state.loading ? <span role="status"><LoaderCircle size={13} className="spin" />하위 instance 조회 중…</span>
                : state.error ? <span className="browser-error" role="alert">{state.error} <Button variant="ghost" size="xs" onClick={() => onRequestChildren?.(line.parent, state.rows.length)}>다시 조회</Button></span>
                : state.rows.length < state.total ? <Button variant="ghost" size="xs" onClick={() => onRequestChildren?.(line.parent, state.rows.length)}>{(state.total - state.rows.length).toLocaleString()}개 더 보기</Button> : null
              return <tr key={line.key} className="browser-status"><td colSpan={mode === 'split' ? splitColumns : singleColumns}><span className="tree-cell">{guides}<span className="tree-spacer" />{message}{hidden}</span></td></tr>
            }
            const { row } = line
            const open = expanded.has(row.key)
            const pathSeparator = separatorOf(row, separator)
            const name = leaf(row.currentPaths, line.parentRow?.currentPaths, pathSeparator, flat), baseName = leaf(row.baselinePaths, line.parentRow?.baselinePaths, pathSeparator, flat)
            const state = stateOf(row)
            const reason = reasonOf(row, hasResult)
            const diff = diffOf(row, separator)
            const toggleButton = row.childCount ? <button type="button" tabIndex={-1} className="tree-toggle" aria-label={`${name || baseName} ${open ? '접기' : '펼치기'}`} onClick={event => { event.stopPropagation(); toggle(row) }}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button> : <span className="tree-spacer" />
            const verdict = marks?.get(row.result?.id || row.key)
            const mark = verdict && <span className={`verdict-tag verdict-${verdict}`} title="AI 분석 판정">{MARK_LABEL[verdict] || verdict}</span>
            const count = row.childCount > 0 && <span className="child-count" title={row.hiddenChildren ? `표시 ${row.childCount.toLocaleString()} · 임계값 미만 ${row.hiddenChildren.toLocaleString()} 숨김` : undefined}>{row.childCount.toLocaleString()}</span>
            const stateCell = <td className="state" title={reason || undefined}><span className={`state-dot ${state}`} aria-hidden="true" />{stateLabel[state] || state}</td>
            const attributes = { 'data-key': row.key, tabIndex: selected === row.key || (!selected && line === lines[0]) ? 0 : -1, 'aria-level': line.depth + 1, 'aria-expanded': row.childCount ? open : undefined, 'aria-selected': selected === row.key, onClick: () => setSelected(row.key), onDoubleClick: () => toggle(row), onFocus: () => setSelected(row.key) }
            if (mode === 'split') {
              // Both panes share one row, so expanding either side expands both
              // and corresponding instances stay on the same line.
              const side = (present: boolean, label: string, paths: string[], kind: 'baseline' | 'current') => present
                ? <><td className={`instance pane-${kind}`} title={paths.join(' + ')}><span className="tree-cell">{guides}{toggleButton}<span className="instance-name">{label}</span>{count}{kind === 'current' && mark}</span></td>{metrics.map(metric => numberCell(row, metric, kind, `pane-${kind}`))}</>
                : <><td className={`instance void pane-${kind}`}><span className="tree-cell">{guides}{toggleButton}<span className="void-label">{kind === 'baseline' ? '기준 없음' : '현재 없음'}</span>{kind === 'current' && mark}</span></td>{metrics.map(metric => <td key={metric.id} className={`void pane-${kind}`} />)}</>
              return <tr key={row.key} {...attributes} className={`diff-${diff}${selected === row.key ? ' selected' : ''}`}>
                {side(row.baselinePaths.length > 0, baseName, row.baselinePaths, 'baseline')}
                {metrics.map((metric, index) => numberCell(row, metric, activeChangeOf(metric)[0], `${index ? '' : 'group-start '}gutter`))}
                {side(row.currentPaths.length > 0, name, row.currentPaths, 'current')}
                {stateCell}
              </tr>
            }
            const tooltip = [`${sideName('current')}: ${row.currentPaths.join(' + ') || '없음'}`, `${sideName('baseline')}: ${row.baselinePaths.join(' + ') || '없음'}`].join('\n')
            return <tr key={row.key} {...attributes} className={selected === row.key ? 'selected' : undefined}>
              <td className="instance" title={tooltip}><span className="tree-cell">{guides}{toggleButton}
                <span className="instance-name">{name || baseName}</span>
                {name && baseName && name !== baseName && <span className="instance-alias">← {baseName}</span>}
                {!row.currentPaths.length && <span className="side-tag">현재 없음</span>}
                {!row.baselinePaths.length && <span className="side-tag">기준 없음</span>}
                {count}{mark}
              </span></td>
              {metrics.flatMap(metric => fieldsOf(metric).map(([field]) => numberCell(row, metric, field, field === 'baseline' ? 'group-start' : '')))}
              {stateCell}
            </tr>
          })}
        </tbody>
      </table>
    </div>
    {!flat && <p className="browser-keys">↑↓ 이동 · → 펼치기 · ← 접기 · Alt+↑↓ 차이 이동</p>}
    {current && <dl className="browser-detail" aria-live="polite">
      <div><dt title={sideName('current')}>{sideName('current')}</dt><dd>{current.row.currentPaths.join(' + ') || '대응 없음'}</dd></div>
      <div><dt title={sideName('baseline')}>{sideName('baseline')}</dt><dd>{current.row.baselinePaths.join(' + ') || '대응 없음'}</dd></div>
      {metrics.map(metric => {
        const sc = !metric.ratio ? scaleMetrics(current.row, metric) : null
        return <div key={metric.id}><dt>{metric.label}{unitOf(metric)}</dt><dd>{metric.ratio
          ? <>{percent(value(current.row, metric.id, 'baseline'))} → {percent(value(current.row, metric.id, 'current'))} · Δ {formatPercent(value(current.row, metric.id, 'pct_point_change'))}%p</>
          : <>{amount(value(current.row, metric.id, 'baseline'), metric)} → {amount(value(current.row, metric.id, 'current'), metric)} · Δ {amount(value(current.row, metric.id, 'delta'), metric)} · {percent(value(current.row, metric.id, 'relative_change_pct'))}{sc?.dampedPct ? ` (체급 연동 ${fracToApproxPct(sc.dampedPct)} · 비중 ${fracToApproxPct(sc.rootSharePct).replace(/^\+/, '')})` : ''}</>}</dd></div>
      })}
      {reasonOf(current.row, hasResult) && <div><dt>사유</dt><dd>{reasonOf(current.row, hasResult)}</dd></div>}
    </dl>}
  </div>
}
