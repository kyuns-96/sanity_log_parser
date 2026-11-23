export type Fraction = { n: bigint; d: bigint }
const abs = (value: bigint) => value < 0n ? -value : value
export function fraction(n: bigint, d = 1n): Fraction {
  if (!d) throw new RangeError('0으로 나눌 수 없습니다.')
  if (d < 0n) { n = -n; d = -d }
  let a = abs(n), b = d
  while (b) { const next = a % b; a = b; b = next }
  return { n: n / a, d: d / a }
}
export function parseDecimal(value: unknown): Fraction | null {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(String(value))
  if (!match) return null
  const exponent = Number(match[4] || 0) - (match[3]?.length || 0)
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return null
  const n = BigInt(`${match[1]}${match[2]}${match[3] || ''}`)
  return exponent >= 0 ? fraction(n * 10n ** BigInt(exponent)) : fraction(n, 10n ** BigInt(-exponent))
}
export const add = (a: Fraction, b: Fraction) => fraction(a.n * b.d + b.n * a.d, a.d * b.d)
export const subtract = (a: Fraction, b: Fraction) => fraction(a.n * b.d - b.n * a.d, a.d * b.d)
export const multiply = (a: Fraction, b: Fraction) => fraction(a.n * b.n, a.d * b.d)
export const divide = (a: Fraction, b: Fraction) => fraction(a.n * b.d, a.d * b.n)
export const compare = (a: Fraction, b: Fraction) => a.n * b.d < b.n * a.d ? -1 : a.n * b.d === b.n * a.d ? 0 : 1
export const magnitude = (a: Fraction) => fraction(abs(a.n), a.d)
// All arithmetic is rational BigInt. Only repeating decimal DISPLAY is rounded,
// half-up at 30 fractional places; exact numerator/denominator remain in evidence.
export function display(value: Fraction | null): string | null {
  if (value === null) return null
  let denominator = value.d, twos = 0, fives = 0
  while (denominator % 2n === 0n) { denominator /= 2n; twos++ }
  while (denominator % 5n === 0n) { denominator /= 5n; fives++ }
  const places = denominator === 1n ? Math.max(twos, fives) : 30
  const scale = 10n ** BigInt(places)
  const scaled = (abs(value.n) * scale * 2n + value.d) / (2n * value.d)
  const digits = (scaled % scale).toString().padStart(places, '0').replace(/0+$/, '')
  return `${value.n < 0n && scaled ? '-' : ''}${scaled / scale}${digits ? `.${digits}` : ''}`
}
