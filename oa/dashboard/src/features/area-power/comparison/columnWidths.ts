// Number columns are as wide as their content (the header label or the longest
// loaded value) instead of a fixed 92px, so the default Logic area · Power set
// fits a 1920px screen side by side. Instance columns share the width that is
// left and never go below their minimum; past that the table scrolls sideways.
// DIGIT, CELL_SLACK and HEAD_FONT mirror the cell and header styles in styles.css.
export const CELL_SLACK = 16
const DIGIT = 7.8
const SORT_MARK = 14
const COLUMN_MIN = 48
const COLUMN_MAX = 150
export const PANE_MIN = 150
export const INSTANCE_MIN = 240
const HEAD_FONT = '600 12px "Pretendard Variable", Pretendard, sans-serif'

// Mono digits are DIGIT wide; the ▲▼ markers come from a wider fallback glyph.
export const valueWidth = (shownText: string) =>
  Math.ceil(shownText.length * DIGIT) + (/[▲▼]/.test(shownText) ? 5 : 0)

// State labels are short Korean words (about 12px a glyph) after a 13px dot.
export const stateWidth = (label: string) =>
  13 + [...label].reduce((sum, glyph) => sum + (glyph === ' ' ? 4 : 12), 0) + CELL_SLACK

const measuredHeads = new Map<string, number>()
let ruler: CanvasRenderingContext2D | null | undefined

export function headWidth(label: string) {
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

export const columnWidth = (label: string, longest: number, sorted: boolean) =>
  Math.min(COLUMN_MAX, Math.max(COLUMN_MIN, headWidth(label) + CELL_SLACK + (sorted ? SORT_MARK : 0), longest + CELL_SLACK))
