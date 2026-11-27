/**
 * Smooth placement contours for std-cell hierarchy regions.
 *
 * `ModuleRegionSummary.bins` is a sparse 32×32 tile histogram over the die bbox
 * (`[gx, gy, pct_of_module_max]`, produced by common/floorplan/def_lef_parser.py).
 * The histogram is padded, lightly blurred, bilinearly upsampled and traced with
 * marching squares; loops are drawn as closed Catmull-Rom curves so the region
 * reads like a placement-density outline instead of a percentile rectangle.
 */

export const MODULE_TILE_GRID = 32

/** Categorical palette, distinguishable on both light and dark canvases. */
export const MODULE_PALETTE = [
  '#2f7ed8', '#e0663a', '#2a9d6f', '#c2417a', '#8a5cc7',
  '#c99a1a', '#1f9bb0', '#d0453f', '#6f8f2a', '#8a6d52',
]

export interface ModuleContour {
  /** Outline of the placed region (svg coordinates, y flipped). */
  outer: string
  /** Dense core of the region. */
  core: string
}

const UPSAMPLE = 3

function blur(src: Float32Array, n: number): Float32Array {
  const out = new Float32Array(n * n)
  const k = [1, 2, 1]
  const tmp = new Float32Array(n * n)
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      let s = 0, w = 0
      for (let d = -1; d <= 1; d++) {
        const ii = i + d
        if (ii < 0 || ii >= n) continue
        s += src[j * n + ii] * k[d + 1]
        w += k[d + 1]
      }
      tmp[j * n + i] = s / w
    }
  }
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      let s = 0, w = 0
      for (let d = -1; d <= 1; d++) {
        const jj = j + d
        if (jj < 0 || jj >= n) continue
        s += tmp[jj * n + i] * k[d + 1]
        w += k[d + 1]
      }
      out[j * n + i] = s / w
    }
  }
  return out
}

function upsample(src: Float32Array, n: number, s: number): { data: Float32Array; m: number } {
  const m = (n - 1) * s + 1
  const out = new Float32Array(m * m)
  for (let j = 0; j < m; j++) {
    const fy = j / s
    const y0 = Math.min(Math.floor(fy), n - 2)
    const ty = fy - y0
    for (let i = 0; i < m; i++) {
      const fx = i / s
      const x0 = Math.min(Math.floor(fx), n - 2)
      const tx = fx - x0
      const a = src[y0 * n + x0], b = src[y0 * n + x0 + 1]
      const c = src[(y0 + 1) * n + x0 + 1], d = src[(y0 + 1) * n + x0]
      out[j * m + i] = (a * (1 - tx) + b * tx) * (1 - ty) + (d * (1 - tx) + c * tx) * ty
    }
  }
  return { data: out, m }
}

/** Marching squares → closed loops in field coordinates. */
function traceLoops(v: Float32Array, m: number, t: number): [number, number][][] {
  const pointCache = new Map<number, [number, number]>()
  const edgePoint = (key: number): [number, number] => {
    const hit = pointCache.get(key)
    if (hit) return hit
    const node = key >> 1
    const i = node % m
    const j = Math.floor(node / m)
    let pt: [number, number]
    if ((key & 1) === 0) {
      const a = v[j * m + i], b = v[j * m + i + 1]
      const f = Math.max(0, Math.min(1, (t - a) / ((b - a) || 1e-9)))
      pt = [i + f, j]
    } else {
      const a = v[j * m + i], d = v[(j + 1) * m + i]
      const f = Math.max(0, Math.min(1, (t - a) / ((d - a) || 1e-9)))
      pt = [i, j + f]
    }
    pointCache.set(key, pt)
    return pt
  }

  const segA: number[] = []
  const segB: number[] = []
  const byEdge = new Map<number, number[]>()
  const addSeg = (e1: number, e2: number) => {
    const idx = segA.length
    segA.push(e1)
    segB.push(e2)
    const l1 = byEdge.get(e1)
    if (l1) l1.push(idx); else byEdge.set(e1, [idx])
    const l2 = byEdge.get(e2)
    if (l2) l2.push(idx); else byEdge.set(e2, [idx])
  }

  for (let j = 0; j < m - 1; j++) {
    for (let i = 0; i < m - 1; i++) {
      const a = v[j * m + i], b = v[j * m + i + 1], c = v[(j + 1) * m + i + 1], d = v[(j + 1) * m + i]
      const code = (a >= t ? 1 : 0) | (b >= t ? 2 : 0) | (c >= t ? 4 : 0) | (d >= t ? 8 : 0)
      if (code === 0 || code === 15) continue
      const node = j * m + i
      const e0 = node * 2 // bottom
      const e1 = (node + 1) * 2 + 1 // right
      const e2 = (node + m) * 2 // top
      const e3 = node * 2 + 1 // left
      const center = (a + b + c + d) / 4 >= t
      switch (code) {
        case 1: case 14: addSeg(e3, e0); break
        case 2: case 13: addSeg(e0, e1); break
        case 3: case 12: addSeg(e3, e1); break
        case 4: case 11: addSeg(e1, e2); break
        case 6: case 9: addSeg(e0, e2); break
        case 7: case 8: addSeg(e3, e2); break
        case 5:
          if (center) { addSeg(e0, e1); addSeg(e2, e3) } else { addSeg(e3, e0); addSeg(e1, e2) }
          break
        case 10:
          if (center) { addSeg(e3, e0); addSeg(e1, e2) } else { addSeg(e0, e1); addSeg(e2, e3) }
          break
      }
    }
  }

  const used = new Uint8Array(segA.length)
  const loops: [number, number][][] = []
  for (let s = 0; s < segA.length; s++) {
    if (used[s]) continue
    used[s] = 1
    const startEdge = segA[s]
    let edge = segB[s]
    const loop: [number, number][] = [edgePoint(startEdge)]
    let guard = segA.length + 2
    while (edge !== startEdge && guard-- > 0) {
      loop.push(edgePoint(edge))
      const next = (byEdge.get(edge) || []).find(idx => !used[idx])
      if (next === undefined) break
      used[next] = 1
      edge = segA[next] === edge ? segB[next] : segA[next]
    }
    if (loop.length >= 3) loops.push(loop)
  }
  return loops
}

function simplify(loop: [number, number][], minDist: number): [number, number][] {
  const out: [number, number][] = []
  for (const p of loop) {
    const last = out[out.length - 1]
    if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) >= minDist) out.push(p)
  }
  return out.length >= 3 ? out : loop
}

function loopArea(loop: [number, number][]) {
  let a = 0
  for (let i = 0; i < loop.length; i++) {
    const [x1, y1] = loop[i]
    const [x2, y2] = loop[(i + 1) % loop.length]
    a += x1 * y2 - x2 * y1
  }
  return Math.abs(a / 2)
}

/** Closed Catmull-Rom spline through the points as cubic Béziers. */
function smoothPath(pts: [number, number][]) {
  const n = pts.length
  const f = (v: number) => Number(v.toFixed(2))
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n]
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6
    d += `C${f(c1x)} ${f(c1y)} ${f(c2x)} ${f(c2y)} ${f(p2[0])} ${f(p2[1])}`
  }
  return `${d}Z`
}

export function moduleContour(bins: [number, number, number][], dieBbox: [number, number, number, number]): ModuleContour {
  const g = MODULE_TILE_GRID
  // Two zero rings keep every iso-line closed after the 3×3 blur.
  const n = g + 4
  const field = new Float32Array(n * n)
  for (const bin of bins) {
    const gx = Number(bin[0]), gy = Number(bin[1]), val = Number(bin[2])
    if (gx >= 0 && gx < g && gy >= 0 && gy < g && Number.isFinite(val)) field[(gy + 2) * n + gx + 2] = Math.max(0, val)
  }
  const blurred = blur(field, n)
  let max = 0
  for (const val of blurred) if (val > max) max = val
  if (max <= 0) return { outer: '', core: '' }
  const { data, m } = upsample(blurred, n, UPSAMPLE)

  const [x0, y0, x1, y1] = dieBbox
  const tw = (x1 - x0) / g
  const th = (y1 - y0) / g
  // Upsampled node k sits at padded index p = k/UPSAMPLE; tile p-2 has its centre at (p - 1.5) tiles from the die origin.
  const toSvg = ([fx, fy]: [number, number]): [number, number] => [x0 + (fx / UPSAMPLE - 1.5) * tw, -(y0 + (fy / UPSAMPLE - 1.5) * th)]

  const build = (level: number) => traceLoops(data, m, level)
    .filter(loop => loopArea(loop) >= UPSAMPLE * UPSAMPLE * 0.35)
    .map(loop => smoothPath(simplify(loop, UPSAMPLE * 0.45).map(toSvg)))
    .join(' ')

  return { outer: build(max * 0.17), core: build(max * 0.58) }
}

/** A std-cell hierarchy region shown on the canvas with its own colour. */
export interface ModuleLayer {
  path: string
  color: string
}
