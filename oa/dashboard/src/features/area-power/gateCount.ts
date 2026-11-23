import type { ProcessDefinition } from '@/features/access/accessApi'
import { object, type Upload } from './api'
import type { BrowserRow, HierarchyItem } from './HierarchyBrowser'
import { display, divide, fraction, multiply, parseDecimal, subtract, type Fraction } from './fraction'

type Basis = { factor: Fraction | null; note: string }
export type GateBases = { baseline: Basis; current: Basis }
const squareMicrometres = new Map([['nm2', fraction(1n, 1000000n)], ['um2', fraction(1n)], ['mm2', fraction(1000000n)]])

export function gateBasis(upload: Upload | undefined, process: string | null | undefined, definitions: ProcessDefinition[]): Basis {
  if (!upload) return { factor: null, note: '실행 미선택' }
  const definition = definitions.find(item => item.name === process)
  const unitArea = parseDecimal(definition?.unit_area)
  const reported = String(object(object(upload.input_contract).metadata).area_unit ?? '').trim()
  const unit = (reported || 'um2').normalize('NFKC').toLowerCase().replace(/μ/g, 'u').replace(/[\s^]/g, '')
  const scale = squareMicrometres.get(unit)
  const notes = [process || '프로젝트 공정 미지정']
  if (definition && unitArea && unitArea.n > 0n) notes.push(`NAND2_D1 ${definition.unit_area} µm²`)
  else if (process) notes.push('Unit area 미등록')
  if (!reported) notes.push('Area 단위 미입력 → µm² 적용')
  else if (!scale) notes.push(`Area 단위 ${reported} 환산 불가`)
  else if (unit !== 'um2') notes.push(`원본 Area 단위 ${reported}`)
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
