// Columns the hierarchy browser can show. The ids match the server's
// observable comparison metrics (area_power/common/analysis.py OBSERVABLE).
export type MetricDef = { id: string; label: string; group: string; ratio: boolean; unit: string }

const capital = (value: string) => value.toUpperCase() === value ? value : value[0].toUpperCase() + value.slice(1)
const categories = ['total', 'logic', 'cts', 'sram', 'io', 'others']
const vth = ['slvt', 'lvt', 'rvt', 'hvt']
const drives = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D10', 'D12', 'D14', 'D16', 'other']
const powerGroups = ['all', 'logic', 'sram', 'io', 'others']
const powerParts = ['total', 'internal', 'switching', 'leakage']

export const METRIC_CATALOG: MetricDef[] = [
  ...categories.map(group => ({ id: `${group}_area`, label: group === 'total' ? 'Area' : `${group === 'cts' ? 'CTS' : group === 'io' ? 'IO' : group === 'sram' ? 'SRAM' : capital(group)} area`, group: 'Area', ratio: false, unit: '' })),
  { id: 'cts_area_pct', label: 'CTS area', group: 'Area', ratio: true, unit: '%' },
  ...vth.map(name => ({ id: `vt_${name}_pct`, label: name.toUpperCase(), group: 'Vth', ratio: true, unit: '%' })),
  ...vth.map(name => ({ id: `vt_${name}_llp_pct`, label: `${name.toUpperCase()} LLP`, group: 'Vth', ratio: true, unit: '%' })),
  ...drives.map(name => ({ id: `drv_${name}_pct`, label: name === 'other' ? 'Drive other' : name, group: 'Drive', ratio: true, unit: '%' })),
  ...powerGroups.flatMap(group => powerParts.map(part => ({ id: `pwr_${group}_${part}_mw`, label: `${group === 'all' ? 'Power' : `${group === 'io' ? 'IO' : group === 'sram' ? 'SRAM' : capital(group)} power`}${part === 'total' ? '' : ` ${part}`}`, group: 'Power', ratio: false, unit: 'mW' }))),
  ...categories.map(group => ({ id: `${group}_cells`, label: `${group === 'cts' ? 'CTS' : group === 'io' ? 'IO' : group === 'sram' ? 'SRAM' : capital(group)} cells`, group: 'Cells', ratio: false, unit: '' })),
]
export const METRIC_GROUPS = ['Area', 'Vth', 'Power', 'Drive', 'Cells']
export const metricDef = new Map(METRIC_CATALOG.map(metric => [metric.id, metric]))
// Area and total power are always fetched: they decide order, diff markers and status.
export const REQUIRED_METRICS = ['total_area', 'pwr_all_total_mw']
export const DEFAULT_COLUMNS = ['total_area', 'pwr_all_total_mw', 'vt_lvt_pct', 'vt_rvt_pct', 'vt_hvt_pct']
export const MAX_COLUMNS = 14
export const DEFAULT_MIN_AREA = '50'
export const requestedMetrics = (columns: string[]) => [...new Set([...REQUIRED_METRICS, ...columns])]

const COLUMNS_KEY = 'designAtlas.hierarchy.columns'
const MIN_AREA_KEY = 'designAtlas.hierarchy.minArea'
export const AREA_THRESHOLD = /^(?:\d+(?:\.\d*)?|\.\d+)$/

// Per-viewer display preferences only; the page works without storage.
export function loadColumns(): string[] {
  try {
    const stored = JSON.parse(window.localStorage.getItem(COLUMNS_KEY) || 'null')
    if (Array.isArray(stored)) {
      const valid = METRIC_CATALOG.map(metric => metric.id).filter(id => stored.includes(id)).slice(0, MAX_COLUMNS)
      if (valid.length) return valid
    }
  } catch { /* fall back to defaults */ }
  return DEFAULT_COLUMNS
}
export function saveColumns(columns: string[]) {
  try { window.localStorage.setItem(COLUMNS_KEY, JSON.stringify(columns)) } catch { /* display preference only */ }
}
export function loadMinArea(): string {
  try {
    const stored = window.localStorage.getItem(MIN_AREA_KEY)
    if (stored !== null && (stored === '' || AREA_THRESHOLD.test(stored))) return stored
  } catch { /* fall back to default */ }
  return DEFAULT_MIN_AREA
}
export function saveMinArea(value: string) {
  try { window.localStorage.setItem(MIN_AREA_KEY, value) } catch { /* display preference only */ }
}

// Exact comparison of a decimal value with the threshold (no float rounding).
function scaled(source: string): [bigint, number] | null {
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(source.trim())
  if (!match || !(match[2] || match[3])) return null
  const digits = `${match[2] || ''}${match[3] || ''}` || '0'
  const scale = (match[3] || '').length - Number(match[4] || 0)
  return [BigInt(`${match[1]}${digits}`), scale]
}
export function atLeast(value: unknown, threshold: string): boolean | null {
  const left = value == null ? null : scaled(String(value)), right = scaled(threshold)
  if (!left || !right) return null
  // value = digits × 10^-scale; bring both to the larger scale (never a negative power).
  const scale = Math.max(left[1], right[1])
  const lift = ([number, own]: [bigint, number]) => number * 10n ** BigInt(scale - own)
  return lift(left) >= lift(right)
}
