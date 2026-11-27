import { useEffect, useRef } from 'react'
import './login-reel.css'

/*
 * Decorative login reel: a synthetic chip layout filmed as five edited shots
 * (floorplan → placement scan → power density → routing → ECO). Everything is
 * generated from a fixed seed in the browser; no media files or network calls.
 */

type Rect = { x: number; y: number; w: number; h: number }
type Cell = Rect & { k: number; id: number }
type Macro = Rect & { name: string }
type Net = { pts: number[] }
type World = {
  core: Rect; macros: Macro[]; cells: Cell[]; pads: Rect[]; straps: Rect[]
  m2: Float32Array; m3: Float32Array; m4: Float32Array; m5: Float32Array
  topNets: Net[]; hot: { x: number; y: number; r: number; a: number }[]
}
type Prepared = { canvas: HTMLCanvasElement; u: Rect; s: number; cells: Cell[]; nets: Net[] }
type Frame = { ctx: CanvasRenderingContext2D; t: number; view: Rect; s: number; dpr: number; cw: number; ch: number; padX: number; padY: number; p: Prepared; w: World; mono: string }
type Shot = { name: string; dur: number; from: [number, number, number]; to: [number, number, number]; overlay?: (f: Frame) => void; nets?: (u: Rect, w: World) => Net[] }

const S = 1400
const ROW = 8
const UM = 0.25
const WIDTHS = [3, 4, 4, 5, 5, 6, 6, 7, 8, 8, 10, 12, 14]
const C = {
  bg: '#090f19', die: '#0d1624', core: '#0f1a2a', seal: '#2a3d57', pad: '#16253a', padLine: '#35506f',
  cells: ['#16243a', '#1a2a41', '#1e3048', '#253a57'], cellLine: '#0a121d', pin: '#5f84b8',
  macro: '#111d30', macroGrid: '#192a43', macroLine: '#4d6f9b', macroText: '#86a3c9',
  m2: 'rgba(96,140,205,.28)', m3: 'rgba(92,170,158,.2)', m4: 'rgba(190,160,98,.14)', m5: 'rgba(150,130,190,.12)',
  vdd: 'rgba(80,120,178,.24)', vss: 'rgba(110,128,152,.16)', rail: 'rgba(98,128,170,.3)',
  accent: '141,187,255', amber: '235,194,123', coral: '243,154,147', hud: 'rgba(214,226,243,.66)',
}

const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v))
const ease = (t: number) => 1 - Math.pow(1 - clamp(t), 3)
const hits = (r: Rect, v: Rect) => r.x < v.x + v.w && r.x + r.w > v.x && r.y < v.y + v.h && r.y + r.h > v.y
const hash = (n: number) => { let x = Math.imul(n ^ 0x9e3779b9, 0x85ebca6b); x ^= x >>> 13; x = Math.imul(x, 0xc2b2ae35); return ((x ^ x >>> 16) >>> 0) / 4294967296 }

function rng(seed: number) {
  return () => {
    seed = seed + 0x6d2b79f5 | 0
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed)
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t
    return ((t ^ t >>> 14) >>> 0) / 4294967296
  }
}

function buildWorld(): World {
  const r = rng(20261127)
  const core = { x: 110, y: 110, w: 1180, h: 1180 }
  const m = (x: number, y: number, w: number, h: number, name: string): Macro => ({ x: core.x + x, y: core.y + y, w, h, name })
  const macros = [
    m(10, 10, 240, 300, 'SRAM 4096x64'), m(10, 330, 240, 300, 'SRAM 4096x64'),
    m(290, 10, 360, 170, 'ROM 8Kx32'), m(670, 10, 500, 170, 'SRAM 2P 1024x128'),
    m(840, 860, 330, 310, 'PLL / CLKGEN'), m(10, 980, 420, 190, 'SRAM 2048x72'),
  ]
  const hubs = [[560, 470], [760, 640], [470, 820], [1040, 420], [900, 300]]
  const halo = 8
  const cells: Cell[] = []
  for (let y = core.y; y + ROW <= core.y + core.h; y += ROW) {
    const blocked = macros.filter(mc => y + ROW > mc.y - halo && y < mc.y + mc.h + halo).map(mc => [mc.x - halo, mc.x + mc.w + halo]).sort((a, b) => a[0] - b[0])
    const end = core.x + core.w
    let x = core.x
    while (x < end) {
      const hit = blocked.find(([a, b]) => x >= a && x < b)
      if (hit) { x = hit[1]; continue }
      const limit = blocked.find(([a]) => a > x)?.[0] ?? end
      const density = 0.8 + 0.13 * Math.sin(x * 0.011 + Math.sin(y * 0.007) * 2) * Math.cos(y * 0.013) + 0.05 * Math.sin(x * 0.05 + y * 0.03)
      if (r() > density) { x += 1 + Math.floor(r() * 4); continue }
      const ffBias = hubs.reduce((sum, [hx, hy]) => sum + Math.exp(-((x - hx) ** 2 + (y - hy) ** 2) / 7200), 0)
      const ff = r() < 0.025 + 0.4 * ffBias
      const w = ff ? 17 + Math.floor(r() * 4) : WIDTHS[Math.floor(r() * WIDTHS.length)]
      if (x + w > limit) { x = limit; continue }
      cells.push({ x, y, w, h: ROW, k: ff ? 3 : Math.floor(r() * 3), id: cells.length })
      x += w
    }
  }
  const inMacro = (x: number, y: number) => macros.some(mc => x > mc.x && x < mc.x + mc.w && y > mc.y && y < mc.y + mc.h)
  const segs = (count: number, horizontal: boolean, pitch: number, min: number, span: number, avoid: boolean) => {
    const out: number[] = []
    while (out.length < count * 4) {
      const track = (horizontal ? core.y : core.x) + pitch / 2 + pitch * Math.floor(r() * (core.h / pitch))
      const start = (horizontal ? core.x : core.y) + r() * core.w
      const len = min + r() * r() * span
      const mid = start + len / 2
      if (avoid && (horizontal ? inMacro(mid, track) : inMacro(track, mid))) continue
      if (horizontal) out.push(start, track, Math.min(start + len, core.x + core.w), track)
      else out.push(track, start, track, Math.min(start + len, core.y + core.h))
    }
    return new Float32Array(out)
  }
  const pads: Rect[] = []
  for (let p = 96; p + 34 <= S - 96; p += 54) {
    pads.push({ x: p, y: 24, w: 34, h: 52 }, { x: p, y: S - 76, w: 34, h: 52 }, { x: 24, y: p, w: 52, h: 34 }, { x: S - 76, y: p, w: 52, h: 34 })
  }
  const straps: Rect[] = []
  for (let x = core.x + 26; x < core.x + core.w - 10; x += 72) straps.push({ x, y: core.y, w: 3.5, h: core.h }, { x: x + 10, y: core.y, w: 3.5, h: core.h })
  for (let y = core.y + 60; y < core.y + core.h - 10; y += 150) straps.push({ x: core.x, y, w: core.w, h: 4 }, { x: core.x, y: y + 12, w: core.w, h: 4 })

  const topNets: Net[] = []
  const pin = (mc: Macro) => {
    const cx = core.x + core.w / 2, cy = core.y + core.h / 2
    const midX = mc.x + mc.w / 2, midY = mc.y + mc.h / 2
    if (Math.abs(cx - midX) / mc.w > Math.abs(cy - midY) / mc.h) return [cx > midX ? mc.x + mc.w : mc.x, mc.y + mc.h * (0.15 + r() * 0.7)]
    return [mc.x + mc.w * (0.15 + r() * 0.7), cy > midY ? mc.y + mc.h : mc.y]
  }
  for (let i = 0; i < 18; i++) {
    const [x1, y1] = pin(macros[i % macros.length])
    const [x2, y2] = r() < 0.45 ? pin(macros[(i + 2 + Math.floor(r() * 3)) % macros.length]) : [core.x + 300 + r() * 520, core.y + 220 + r() * 600]
    const xm = x1 + (x2 - x1) * (0.3 + r() * 0.4)
    topNets.push({ pts: r() < 0.5 ? [x1, y1, xm, y1, xm, y2, x2, y2] : [x1, y1, x1, y1 + (y2 - y1) * 0.5, x2, y1 + (y2 - y1) * 0.5, x2, y2] })
  }
  return {
    core, macros, cells, pads, straps, topNets,
    m2: segs(9000, true, 2, 4, 70, true), m3: segs(7000, false, 2, 4, 60, true),
    m4: segs(900, true, 4, 30, 240, false), m5: segs(520, false, 4, 30, 260, false),
    hot: [{ x: 620, y: 500, r: 95, a: 1 }, { x: 830, y: 690, r: 70, a: 0.8 }, { x: 330, y: 400, r: 90, a: 0.55 }, { x: 1060, y: 330, r: 110, a: 0.7 }, { x: 520, y: 860, r: 60, a: 0.45 }],
  }
}

function paintStatic(ctx: CanvasRenderingContext2D, w: World, v: Rect, s: number, dpr: number, mono: string) {
  const px = 1 / s
  ctx.fillStyle = C.bg; ctx.fillRect(v.x, v.y, v.w, v.h)
  ctx.fillStyle = C.die; ctx.fillRect(0, 0, S, S)
  ctx.strokeStyle = C.seal; ctx.lineWidth = Math.max(px, 1.6)
  ctx.strokeRect(6, 6, S - 12, S - 12); ctx.strokeRect(13, 13, S - 26, S - 26)
  ctx.beginPath(); w.pads.filter(p => hits(p, v)).forEach(p => ctx.rect(p.x, p.y, p.w, p.h))
  ctx.fillStyle = C.pad; ctx.fill(); ctx.strokeStyle = C.padLine; ctx.lineWidth = Math.max(px, 1); ctx.stroke()
  ctx.fillStyle = C.core; ctx.fillRect(w.core.x, w.core.y, w.core.w, w.core.h)

  const cells = w.cells.filter(c => hits(c, v))
  const detail = s * ROW / dpr
  for (let k = 0; k < 4; k++) {
    ctx.beginPath()
    for (const c of cells) if (c.k === k) ctx.rect(c.x + 0.2, c.y + 0.45, c.w - 0.4, ROW - 0.9)
    ctx.fillStyle = C.cells[k]; ctx.fill()
    if (detail > 22) { ctx.strokeStyle = C.cellLine; ctx.lineWidth = px * 1.2; ctx.stroke() }
  }
  if (detail > 10) {
    ctx.beginPath()
    for (let y = w.core.y; y <= w.core.y + w.core.h; y += ROW) if (y >= v.y - 1 && y <= v.y + v.h + 1) { ctx.moveTo(Math.max(v.x, w.core.x), y); ctx.lineTo(Math.min(v.x + v.w, w.core.x + w.core.w), y) }
    ctx.strokeStyle = C.rail; ctx.lineWidth = 0.7; ctx.stroke()
  }
  if (detail > 34) {
    ctx.beginPath()
    for (const c of cells) {
      const n = 1 + Math.floor(hash(c.id) * 3)
      for (let i = 0; i < n; i++) ctx.rect(c.x + c.w * (i + 1) / (n + 1) - 0.45, c.y + ROW * (hash(c.id * 7 + i) > 0.5 ? 0.36 : 0.62) - 0.45, 0.9, 0.9)
    }
    ctx.fillStyle = C.pin; ctx.fill()
  }

  const wires = (data: Float32Array, color: string, width: number) => {
    ctx.beginPath()
    for (let i = 0; i < data.length; i += 4) {
      const x1 = data[i], y1 = data[i + 1], x2 = data[i + 2], y2 = data[i + 3]
      if (Math.max(x1, x2) < v.x || Math.min(x1, x2) > v.x + v.w || Math.max(y1, y2) < v.y || Math.min(y1, y2) > v.y + v.h) continue
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2)
    }
    ctx.strokeStyle = color; ctx.lineWidth = Math.max(width, px * 0.8); ctx.stroke()
  }
  wires(w.m2, C.m2, 0.6); wires(w.m3, C.m3, 0.6)

  for (const mc of w.macros.filter(mc => hits(mc, v))) {
    ctx.fillStyle = C.macro; ctx.fillRect(mc.x, mc.y, mc.w, mc.h)
    const step = s / dpr > 1.6 ? 4 : 16
    ctx.beginPath()
    for (let x = mc.x + step; x < mc.x + mc.w; x += step) { ctx.moveTo(x, mc.y + 6); ctx.lineTo(x, mc.y + mc.h - 6) }
    for (let y = mc.y + step; y < mc.y + mc.h; y += step) { ctx.moveTo(mc.x + 6, y); ctx.lineTo(mc.x + mc.w - 6, y) }
    ctx.strokeStyle = C.macroGrid; ctx.lineWidth = Math.max(px, step === 4 ? 0.35 : 0.8); ctx.stroke()
    ctx.strokeStyle = C.macroLine; ctx.lineWidth = Math.max(px * 1.2, 0.8); ctx.strokeRect(mc.x, mc.y, mc.w, mc.h)
    const font = 11 * dpr / s
    if (mc.w * s / dpr > 140) {
      ctx.fillStyle = C.macroText; ctx.font = `${font}px ${mono}`; ctx.textBaseline = 'top'
      ctx.fillText(mc.name, mc.x + 10 * dpr / s, mc.y + 9 * dpr / s)
    }
  }
  wires(w.m4, C.m4, 1.4); wires(w.m5, C.m5, 1.4)
  w.straps.filter(st => hits(st, v)).forEach((st, i) => { ctx.fillStyle = i % 2 ? C.vss : C.vdd; ctx.fillRect(st.x, st.y, st.w, st.h) })
}

/** Strokes the first `f` of a Manhattan polyline and returns its current end point. */
function partial(ctx: CanvasRenderingContext2D, pts: number[], f: number): [number, number] {
  let total = 0
  for (let i = 2; i < pts.length; i += 2) total += Math.abs(pts[i] - pts[i - 2]) + Math.abs(pts[i + 1] - pts[i - 1])
  let remain = total * clamp(f)
  ctx.moveTo(pts[0], pts[1])
  for (let i = 2; i < pts.length; i += 2) {
    const len = Math.abs(pts[i] - pts[i - 2]) + Math.abs(pts[i + 1] - pts[i - 1])
    if (remain >= len) { ctx.lineTo(pts[i], pts[i + 1]); remain -= len; continue }
    const k = len ? remain / len : 0
    const x = pts[i - 2] + (pts[i] - pts[i - 2]) * k, y = pts[i - 1] + (pts[i + 1] - pts[i - 1]) * k
    ctx.lineTo(x, y)
    return [x, y]
  }
  return [pts[pts.length - 2], pts[pts.length - 1]]
}

function screenText(f: Frame, draw: () => void) {
  f.ctx.save(); f.ctx.setTransform(1, 0, 0, 1, 0, 0); draw(); f.ctx.restore()
}

function makeShots(w: World): Shot[] {
  const eco = w.cells.filter(c => c.k < 3 && c.w >= 4 && c.w <= 6).reduce((best, c) => Math.hypot(c.x - 650, c.y - 640) < Math.hypot(best.x - 650, best.y - 640) ? c : best)
  const flagged = (c: Cell) => hash(c.id * 31 + 5) < 0.004
  return [
    { name: 'FLOORPLAN', dur: 7, from: [700, 704, 0.9], to: [700, 694, 0.98], overlay: f => {
      const { ctx, t, s, dpr } = f
      ctx.lineWidth = 1.3 * dpr / s; ctx.strokeStyle = `rgba(${C.accent},.82)`; ctx.fillStyle = `rgb(${C.accent})`
      w.topNets.forEach((net, i) => {
        const p = (t - 0.08 - i * 0.028) / 0.34
        if (p <= 0) return
        ctx.beginPath(); const [ex, ey] = partial(ctx, net.pts, p); ctx.stroke()
        const d = 3 * dpr / s
        ctx.fillRect(net.pts[0] - d / 2, net.pts[1] - d / 2, d, d)
        if (p >= 1) ctx.fillRect(ex - d / 2, ey - d / 2, d, d)
      })
    } },
    { name: 'PLACEMENT', dur: 6.5, from: [560, 520, 6], to: [622, 527, 6.4], overlay: f => {
      const { ctx, t, view, s, dpr, p } = f
      const band = 26, lead = view.x - band + (view.w + band * 2) * t
      ctx.beginPath()
      for (const c of p.cells) if (c.x + c.w <= lead - band) ctx.rect(c.x + 0.2, c.y + 0.45, c.w - 0.4, ROW - 0.9)
      ctx.fillStyle = `rgba(${C.accent},.07)`; ctx.fill()
      ctx.beginPath()
      for (const c of p.cells) if (c.x < lead && c.x + c.w > lead - band) ctx.rect(c.x + 0.2, c.y + 0.45, c.w - 0.4, ROW - 0.9)
      ctx.fillStyle = `rgba(${C.accent},.32)`; ctx.fill()
      ctx.beginPath()
      for (const c of p.cells) if (flagged(c) && c.x < lead) ctx.rect(c.x - 0.6, c.y - 0.2, c.w + 1.2, ROW + 0.4)
      ctx.strokeStyle = `rgba(${C.amber},.95)`; ctx.lineWidth = 1.4 * dpr / s; ctx.stroke()
      ctx.fillStyle = `rgba(${C.accent},.9)`; ctx.fillRect(lead, view.y, 1.1 * dpr / s, view.h)
    } },
    { name: 'POWER DENSITY', dur: 7, from: [760, 572, 1.7], to: [742, 560, 1.95], overlay: f => {
      const { ctx, t, view, cw, ch, dpr, mono } = f
      const tile = 20, origin = w.hot[0]
      for (let x = Math.floor(view.x / tile) * tile; x < view.x + view.w; x += tile) {
        for (let y = Math.floor(view.y / tile) * tile; y < view.y + view.h; y += tile) {
          if (x < w.core.x || y < w.core.y || x >= w.core.x + w.core.w || y >= w.core.y + w.core.h) continue
          const cx = x + tile / 2, cy = y + tile / 2
          const value = w.hot.reduce((sum, h) => sum + h.a * Math.exp(-((cx - h.x) ** 2 + (cy - h.y) ** 2) / (h.r * h.r)), 0) + (hash(x * 977 + y) - 0.5) * 0.12
          if (value < 0.14) continue
          const appear = clamp((t * 1.5 - Math.hypot(cx - origin.x, cy - origin.y) / 900) * 2.2)
          if (appear <= 0) continue
          const v = clamp(value)
          const rgb = v > 0.62 ? C.coral : C.amber
          ctx.fillStyle = `rgba(${rgb},${(0.1 + v * 0.42) * appear})`
          ctx.fillRect(x + 1, y + 1, tile - 2, tile - 2)
        }
      }
      screenText(f, () => {
        const a = clamp((t - 0.25) * 4), x = cw - f.padX * dpr - 140 * dpr, y = ch - f.padY * dpr - 72 * dpr
        const g = ctx.createLinearGradient(x, 0, x + 140 * dpr, 0)
        g.addColorStop(0, `rgba(${C.amber},${0.12 * a})`); g.addColorStop(0.6, `rgba(${C.amber},${0.5 * a})`); g.addColorStop(1, `rgba(${C.coral},${0.6 * a})`)
        ctx.fillStyle = g; ctx.fillRect(x, y, 140 * dpr, 5 * dpr)
        ctx.fillStyle = `rgba(214,226,243,${0.6 * a})`; ctx.font = `${10 * dpr}px ${mono}`; ctx.textBaseline = 'top'
        ctx.textAlign = 'left'; ctx.fillText('0', x, y + 10 * dpr)
        ctx.textAlign = 'right'; ctx.fillText('1.8 mW/µm²', x + 140 * dpr, y + 10 * dpr)
      })
    } },
    { name: 'ROUTING', dur: 6.5, from: [700, 760, 10], to: [713, 751, 11], nets: (u, world) => {
      const r = rng(77)
      const pool = world.cells.filter(c => hits(c, u))
      return Array.from({ length: 26 }, () => {
        const c = pool[Math.floor(r() * pool.length)]
        let x = c.x + c.w / 2, y = c.y + ROW * 0.5
        const pts = [x, y]
        const turns = 2 + Math.floor(r() * 3)
        for (let i = 0; i < turns; i++) {
          if (i % 2 === 0) x = Math.round(x + (r() - 0.5) * 70); else y = Math.round((y + (r() - 0.5) * 60) / 2) * 2 + 1
          pts.push(x, y)
        }
        return { pts }
      })
    }, overlay: f => {
      const { ctx, t, s, dpr, p } = f
      p.nets.forEach((net, i) => {
        const prog = (t - 0.04 - i * 0.022) / 0.28
        if (prog <= 0) return
        ctx.lineCap = 'square'
        for (let j = 2; j < net.pts.length; j += 2) {
          const before = (j - 2) / (net.pts.length - 2), after = j / (net.pts.length - 2)
          if (prog <= before) break
          const k = clamp((prog - before) / (after - before))
          const x0 = net.pts[j - 2], y0 = net.pts[j - 1], x1 = x0 + (net.pts[j] - x0) * k, y1 = y0 + (net.pts[j + 1] - y0) * k
          ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1)
          ctx.strokeStyle = y0 === net.pts[j + 1] ? 'rgba(127,176,255,.92)' : 'rgba(127,214,196,.88)'
          ctx.lineWidth = 0.7; ctx.stroke()
          if (k === 1 && j < net.pts.length - 2) {
            ctx.fillStyle = 'rgba(232,240,252,.9)'; ctx.fillRect(x1 - 0.55, y1 - 0.55, 1.1, 1.1)
          }
        }
        ctx.lineCap = 'butt'
        ctx.strokeStyle = `rgba(${C.accent},.95)`; ctx.lineWidth = 1 * dpr / s
        ctx.strokeRect(net.pts[0] - 0.8, net.pts[1] - 0.8, 1.6, 1.6)
      })
    } },
    { name: 'ECO', dur: 7.5, from: [eco.x + eco.w / 2 + 34, eco.y + 4 - 22, 6], to: [eco.x + eco.w / 2 + 6, eco.y + 2, 9], overlay: f => {
      const { ctx, t, s, dpr, view, mono } = f
      const grow = ease((t - 0.55) / 0.25) * 6
      const pad = 1.4, spread = 1 + (1 - ease((t - 0.12) / 0.22)) * 5
      const bx = eco.x - pad, by = eco.y - pad, bw = eco.w + grow + pad * 2, bh = ROW + pad * 2
      const cx = bx + bw / 2, cy = by + bh / 2, hw = bw / 2 * spread, hh = bh / 2 * spread
      if (t > 0.12) {
        const arm = Math.min(hw, hh) * 0.45
        ctx.strokeStyle = `rgba(${C.amber},${clamp((t - 0.12) * 6)})`; ctx.lineWidth = 1.5 * dpr / s
        ctx.beginPath()
        for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
          const x = cx + sx * hw, y = cy + sy * hh
          ctx.moveTo(x - sx * arm, y); ctx.lineTo(x, y); ctx.lineTo(x, y - sy * arm)
        }
        ctx.stroke()
      }
      if (t > 0.5) {
        ctx.setLineDash([1.2, 1]); ctx.strokeStyle = `rgba(${C.amber},${0.7 * (1 - ease((t - 0.6) / 0.2))})`; ctx.lineWidth = 0.4
        ctx.strokeRect(eco.x, eco.y + 0.45, eco.w, ROW - 0.9); ctx.setLineDash([])
        ctx.fillStyle = `rgba(${C.amber},${0.28 * ease((t - 0.55) / 0.2)})`; ctx.fillRect(eco.x, eco.y + 0.45, eco.w + grow, ROW - 0.9)
        ctx.strokeStyle = `rgba(${C.amber},${ease((t - 0.55) / 0.2)})`; ctx.lineWidth = 1.2 * dpr / s; ctx.strokeRect(eco.x, eco.y + 0.45, eco.w + grow, ROW - 0.9)
      }
      const a = clamp((t - 0.34) * 5)
      if (a > 0) screenText(f, () => {
        const ax = (bx + bw - view.x) * s, ay = (by - view.y) * s
        const lx = ax + 46 * dpr, ly = ay - 54 * dpr
        ctx.fillStyle = `rgba(7,12,20,${0.82 * a})`
        ctx.fillRect(lx - 18 * dpr, ly - 18 * dpr, 220 * dpr, (t > 0.8 ? 66 : 50) * dpr)
        ctx.strokeStyle = `rgba(${C.amber},${0.8 * a})`; ctx.lineWidth = dpr
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(lx - 10 * dpr, ly + 6 * dpr); ctx.lineTo(lx + 150 * dpr, ly + 6 * dpr); ctx.stroke()
        ctx.textAlign = 'left'; ctx.textBaseline = 'bottom'
        ctx.font = `${12 * dpr}px ${mono}`; ctx.fillStyle = `rgba(236,242,250,${a})`
        ctx.fillText('u_core/u_alu/U18432', lx - 8 * dpr, ly + 2 * dpr - (1 - a) * 4 * dpr)
        ctx.textBaseline = 'top'; ctx.font = `${11 * dpr}px ${mono}`; ctx.fillStyle = `rgba(${C.amber},${a})`
        ctx.fillText(t > 0.55 ? 'NAND2_X1 → NAND2_X4' : 'NAND2_X1  slack -0.042', lx - 8 * dpr, ly + 12 * dpr)
        if (t > 0.8) { ctx.fillStyle = `rgba(214,226,243,${0.6 * clamp((t - 0.8) * 6)})`; ctx.fillText('ECO 0142 · pending review', lx - 8 * dpr, ly + 28 * dpr) }
      })
    } },
  ]
}

export function LoginReel() {
  const wrapper = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const host = wrapper.current!, canvas = canvasRef.current!
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const world = buildWorld()
    const shots = makeShots(world)
    const total = shots.reduce((sum, shot) => sum + shot.dur, 0)
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
    const mono = getComputedStyle(document.documentElement).getPropertyValue('--font-mono').trim() || 'monospace'
    let dpr = 1, pw = 0, ph = 0, base = 1, padX = 48, padY = 48
    let cache = new Map<number, Prepared>()
    let frame = 0, idle = 0
    const start = performance.now()

    const viewAt = (shot: Shot, t: number) => {
      const z = shot.from[2] * Math.pow(shot.to[2] / shot.from[2], t)
      const cx = shot.from[0] + (shot.to[0] - shot.from[0]) * t, cy = shot.from[1] + (shot.to[1] - shot.from[1]) * t
      const scale = base * z
      return { view: { x: cx - pw / scale / 2, y: cy - ph / scale / 2, w: pw / scale, h: ph / scale }, z, cx, cy }
    }
    const prepare = (index: number) => {
      const hit = cache.get(index)
      if (hit) return hit
      const shot = shots[index]
      const a = viewAt(shot, 0).view, b = viewAt(shot, 1).view
      const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y)
      const u = { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y }
      let s = base * Math.max(shot.from[2], shot.to[2]) * dpr
      if (Math.max(u.w, u.h) * s > 4096) s = 4096 / Math.max(u.w, u.h)
      const off = document.createElement('canvas')
      off.width = Math.ceil(u.w * s); off.height = Math.ceil(u.h * s)
      const octx = off.getContext('2d')!
      octx.setTransform(s, 0, 0, s, -u.x * s, -u.y * s)
      paintStatic(octx, world, u, s, dpr, mono)
      const prepared = { canvas: off, u, s, cells: world.cells.filter(c => hits(c, u)), nets: shot.nets?.(u, world) ?? [] }
      cache.set(index, prepared)
      return prepared
    }
    const release = (keep: number[]) => {
      for (const [index, value] of cache) if (!keep.includes(index)) { value.canvas.width = 0; cache.delete(index) }
    }

    const draw = (elapsed: number) => {
      if (!pw || !ph) return
      let local = elapsed % total, index = 0
      while (local >= shots[index].dur) { local -= shots[index].dur; index++ }
      const shot = shots[index], next = (index + 1) % shots.length
      const t = local / shot.dur
      const p = prepare(index)
      release([index, next])
      if (!reduced && !cache.has(next) && !idle) idle = window.setTimeout(() => { idle = 0; if (pw) prepare(next) }, 120)
      const { view, z, cx, cy } = viewAt(shot, t)
      const s = base * z * dpr
      const cw = canvas.width, ch = canvas.height
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.imageSmoothingQuality = 'high'
      ctx.drawImage(p.canvas, (view.x - p.u.x) * p.s, (view.y - p.u.y) * p.s, view.w * p.s, view.h * p.s, 0, 0, cw, ch)
      ctx.setTransform(s, 0, 0, s, -view.x * s, -view.y * s)
      shot.overlay?.({ ctx, t, view, s, dpr, cw, ch, padX, padY, p, w: world, mono })
      ctx.setTransform(1, 0, 0, 1, 0, 0)

      // HUD: shot index, cursor position, zoom and a 1-2-5 scale bar.
      ctx.save(); ctx.shadowColor = 'rgba(4,8,14,.95)'; ctx.shadowBlur = 6 * dpr
      ctx.font = `${11 * dpr}px ${mono}`; ctx.fillStyle = C.hud; ctx.textAlign = 'right'; ctx.textBaseline = 'top'
      const right = cw - padX * dpr, top = padY * dpr + 2 * dpr
      ctx.fillText(`${String(index + 1).padStart(2, '0')} / ${String(shots.length).padStart(2, '0')}   ${shot.name}`, right, top)
      ctx.fillStyle = 'rgba(214,226,243,.42)'
      ctx.fillText(`X ${(cx * UM).toFixed(2)}  Y ${(cy * UM).toFixed(2)}  ×${z.toFixed(1)}`, right, top + 18 * dpr)
      const perUm = base * z / UM
      const target = 110 / perUm, mag = Math.pow(10, Math.floor(Math.log10(target)))
      const nice = [5, 2, 1].map(n => n * mag).find(n => n <= target) ?? mag
      const len = nice * perUm * dpr, y = ch - padY * dpr - 10 * dpr
      ctx.fillStyle = 'rgba(214,226,243,.55)'
      ctx.fillRect(right - len, y, len, dpr); ctx.fillRect(right - len, y - 4 * dpr, dpr, 5 * dpr); ctx.fillRect(right - dpr, y - 4 * dpr, dpr, 5 * dpr)
      ctx.textBaseline = 'bottom'; ctx.fillText(`${nice >= 1 ? nice : nice.toFixed(1)} µm`, right, y - 7 * dpr)
      ctx.restore()

      if (!reduced && local < 0.24) { ctx.fillStyle = `rgba(4,8,14,${0.6 * (1 - local / 0.24)})`; ctx.fillRect(0, 0, cw, ch) }
    }

    const resize = () => {
      const box = host.getBoundingClientRect()
      const style = getComputedStyle(host.parentElement ?? host)
      dpr = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(4.2e6 / Math.max(1, box.width * box.height)))
      pw = box.width; ph = box.height
      padX = parseFloat(style.paddingRight) || 48; padY = parseFloat(style.paddingTop) || 48
      base = Math.min(pw, ph) / S
      canvas.width = Math.max(1, Math.round(pw * dpr)); canvas.height = Math.max(1, Math.round(ph * dpr))
      release([]); cache = new Map()
      if (reduced) draw(shots[0].dur * 0.92)
    }
    const observer = new ResizeObserver(resize)
    observer.observe(host)
    resize()
    // 30 fps keeps the footage cadence and halves the cost on large desktop panels.
    let last = -1
    const loop = (now: number) => {
      frame = requestAnimationFrame(loop)
      if (last >= 0 && now - last < 1000 / 30 - 2) return
      last = now; draw((now - start) / 1000)
    }
    if (!reduced) frame = requestAnimationFrame(loop)
    return () => { cancelAnimationFrame(frame); clearTimeout(idle); observer.disconnect(); release([]) }
  }, [])

  return <div ref={wrapper} className="login-reel" aria-hidden="true"><canvas ref={canvasRef} /></div>
}
