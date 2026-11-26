import type { ProcessDefinition } from '@/features/access/accessApi'
import { object, type Upload } from './api'
import type { BrowserRow, HierarchyItem } from './HierarchyBrowser'
import { display, divide, fraction, multiply, parseDecimal, subtract, type Fraction } from './fraction'

type Basis = { factor: Fraction | null; note: string }
export type GateBases = { baseline: Basis; current: Basis }
const squareMicrometres = new Map<string, Fraction>([
  ['nm2', fraction(1n, 1000000n)],
  ['sqnm', fraction(1n, 1000000n)],
  ['squarenm', fraction(1n, 1000000n)],
  ['um2', fraction(1n)],
  ['squm', fraction(1n)],
  ['squareum', fraction(1n)],
  ['um*um', fraction(1n)],
  ['micron2', fraction(1n)],
  ['microns2', fraction(1n)],
  ['squaremicron', fraction(1n)],
  ['squaremicrons', fraction(1n)],
  ['squaremicrometer', fraction(1n)],
  ['squaremicrometers', fraction(1n)],
  ['squaremicrometre', fraction(1n)],
  ['squaremicrometres', fraction(1n)],
  ['mm2', fraction(1000000n)],
  ['sqmm', fraction(1000000n)],
  ['squaremm', fraction(1000000n)],
])
const UNKNOWN_UNITS = new Set(['', 'unknown', 'unconfirmed', 'none', 'null', 'na', 'n/a', '-'])

export function gateBasis(upload: Upload | undefined, process: string | null | undefined, definitions: ProcessDefinition[]): Basis {
  if (!upload) return { factor: null, note: '실행 미선택' }
  const procName = (process || '').trim()
  const definition = procName
    ? (definitions.find(item => item.name === procName)
      || definitions.find(item => item.name.trim().toLowerCase() === procName.toLowerCase()))
    : undefined
  const unitArea = parseDecimal(definition?.unit_area)
  const rawReported = String(object(object(upload.input_contract).metadata).area_unit ?? '').trim()
  const reported = UNKNOWN_UNITS.has(rawReported.toLowerCase()) ? '' : rawReported
  const unit = (reported || 'um2').normalize('NFKC').toLowerCase().replace(/μ/g, 'u').replace(/[\s^_-]/g, '')
  const scale = squareMicrometres.get(unit)
  const notes = [procName || '프로젝트 공정 미지정']
  if (definition && unitArea && unitArea.n > 0n) notes.push(`NAND2_D1 ${definition.unit_area} µm²`)
  else if (procName) notes.push('Unit area 미등록')
  if (!reported || unit === 'um2') notes.push('Area 단위 µm²')
  else if (!scale) notes.push(`Area 단위 ${reported} 환산 불가`)
  else notes.push(`원본 Area 단위 ${reported}`)
  const factor = scale && unitArea && unitArea.n > 0n ? divide(scale, unitArea) : null
  return { factor, note: notes.join(' · ') }
}

function count(area: unknown, { factor }: Basis) {
  if (!factor || area == null) return null
  const amount = parseDecimal(area)
  return amount && multiply(amount, factor)
}

function preview(item: HierarchyItem | undefined, basis: Basis) {
  return item && { ...item, area: display(count(item.area, basis)), logic_area: display(count(item.logic_area, basis)) }
}

export function gateCountRow(row: BrowserRow, bases: GateBases): BrowserRow {
  const baseline = preview(row.baseline, bases.baseline)
  const current = preview(row.current, bases.current)
  if (!row.result) return { ...row, baseline, current }

  const metrics = { ...row.result.metrics }
  for (const [key, value] of Object.entries(metrics)) {
    if (!key.endsWith('_area')) continue
    const before = count(value.baseline, bases.baseline), after = count(value.current, bases.current)
    // Keep the original comparison's exclusions. Subtract only after both sides use their own NAND2_D1.
    const delta = before && after && value.delta != null && value.comparability !== 'unavailable' ? subtract(after, before) : null
    const relative = delta && before?.n && value.relative_change_pct != null ? divide(multiply(delta, fraction(100n)), before) : null
    const missing = (value.baseline != null && !before) || (value.current != null && !after)
    metrics[key] = { ...value, baseline: display(before), current: display(after), delta: display(delta), relative_change_pct: display(relative),
      ...(missing && { comparability: 'unavailable', reason: 'Gate Count 환산 기준 없음' }) }
  }
  return { ...row, baseline, current, result: { ...row.result, metrics } }
}
