import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { DropdownMenu } from 'radix-ui'
import {
  AlignEndHorizontal,
  AlignHorizontalDistributeStart,
  AlignStartVertical,
  AlignVerticalDistributeStart,
  Check,
  ChevronDown,
  Download,
  FlipHorizontal2,
  FlipVertical2,
  Layers,
  Lock,
  MapPin,
  Minus,
  MousePointer2,
  Move,
  Plus,
  RotateCcw,
  RotateCw,
  Save,
  Scan,
  Unlock,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  BIST_PALETTE,
  DEF_TO_ICC2,
  evaluateSandboxState,
  flipOrientX,
  flipOrientY,
  generateClientTcl,
  orientedSize,
  pin1Offset,
  rotateOrient90,
  type DatapathFlow,
  type EffectiveMacroBox,
  type FloorplanScene,
  type MacroBox,
  type MacroOverride,
  type ReviewPin,
} from './floorplanApi'

export type CanvasTool = 'inspect' | 'pin' | 'sandbox'

interface FloorplanCanvasProps {
  scene: FloorplanScene
  compareScene: FloorplanScene | null
  tool: CanvasTool
  onToolChange: (tool: CanvasTool) => void
  selectedInst: string
  onSelectInst: (inst: string) => void
  selectedModule: string
  selectedBistGroup: string
  overrides: Map<string, MacroOverride>
  onOverridesChange: (next: Map<string, MacroOverride>) => void
  datapaths: DatapathFlow[]
  reviewPins: ReviewPin[]
  onAddPinAt: (xUm: number, yUm: number) => void
  canWrite: boolean
  onlyChangedTcl: boolean
  onOnlyChangedTclChange: (val: boolean) => void
  onSaveProposal: (title: string, note: string) => Promise<void>
}

type MacroKind = MacroBox['kind']
type LayerKey = 'density' | 'halo' | 'notches' | 'labels' | 'pins' | 'bistColor' | 'datapaths' | 'reviewPins' | 'diffOverlay'

const KIND_ORDER: MacroKind[] = ['sram', 'ip', 'macro', 'subblock']
const KIND_LABEL: Record<MacroKind, string> = { sram: 'SRAM', ip: 'Hard IP', macro: 'Macro', subblock: 'Sub-block' }

const LAYER_ITEMS: { key: LayerKey; label: string }[] = [
  { key: 'labels', label: '이름 라벨' },
  { key: 'notches', label: 'Pin-1 방향' },
  { key: 'halo', label: 'Halo · Blockage' },
  { key: 'density', label: '셀 밀도' },
  { key: 'bistColor', label: 'BIST 그룹 색상' },
  { key: 'pins', label: 'IO Pin' },
  { key: 'datapaths', label: 'Data-path' },
  { key: 'reviewPins', label: '리뷰 핀' },
]

const TOOL_HINT: Record<CanvasTool, string> = {
  inspect: '드래그 이동 · 휠 확대/축소 · 클릭 선택',
  pin: '리뷰 핀을 놓을 위치를 클릭하세요',
  sandbox: 'Shift+클릭 다중 선택 · 드래그로 이동',
}

const MIN_ZOOM = 0.5
const MAX_ZOOM = 64

/** 1·2·5 × 10ⁿ step closest to the given raw length. */
function niceStep(raw: number) {
  if (!(raw > 0)) return 1
  const p = 10 ** Math.floor(Math.log10(raw))
  const n = raw / p
  return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * p
}

function formatLength(um: number) {
  if (um >= 1000) return `${Number((um / 1000).toPrecision(3))} mm`
  return `${Number(um.toPrecision(3))} µm`
}

function formatUm(v: number) {
  return Number.isInteger(v) ? String(v) : v.toFixed(2)
}

function truncate(text: string, maxChars: number) {
  if (maxChars <= 1) return ''
  return text.length > maxChars ? `${text.slice(0, Math.max(1, maxChars - 1))}…` : text
}

function scrollOffset(el: HTMLElement) {
  let total = window.scrollY
  for (let node = el.parentElement; node; node = node.parentElement) total += node.scrollTop
  return total
}

function LayerSwatch({ layer }: { layer: LayerKey }) {
  return (
    <svg className="fp-layer-swatch" viewBox="0 0 16 16" aria-hidden="true">
      {layer === 'labels' && <path d="M3 4h10M3 8h7M3 12h9" />}
      {layer === 'notches' && <><rect x="2.5" y="2.5" width="11" height="11" className="sw-box" /><path d="M2.5 13.5v-5l5 5z" className="sw-fill" /></>}
      {layer === 'halo' && <><rect x="1.5" y="1.5" width="13" height="13" className="sw-dash" /><rect x="4.5" y="4.5" width="7" height="7" className="sw-box" /></>}
      {layer === 'density' && <rect x="1.5" y="1.5" width="13" height="13" rx="2" className="sw-density" />}
      {layer === 'bistColor' && <><rect x="1.5" y="1.5" width="6" height="6" className="sw-b1" /><rect x="8.5" y="8.5" width="6" height="6" className="sw-b2" /></>}
      {layer === 'pins' && <path d="M2 4.5 8 8l-6 3.5z" className="sw-fill" />}
      {layer === 'datapaths' && <path d="M2 12 7 6l3 3 4-5" className="sw-line" />}
      {layer === 'reviewPins' && <circle cx="8" cy="8" r="5" className="sw-fill" />}
      {layer === 'diffOverlay' && <><rect x="1.5" y="3.5" width="9" height="9" className="sw-dash" /><rect x="5.5" y="1.5" width="9" height="9" className="sw-box" /></>}
    </svg>
  )
}

function IconButton({ label, onClick, disabled, pressed, children }: { label: string; onClick: () => void; disabled?: boolean; pressed?: boolean; children: ReactNode }) {
  return (
    <button type="button" className="fp-ibtn" title={label} aria-label={label} aria-pressed={pressed} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  )
}

export function FloorplanCanvas({
  scene,
  compareScene,
  tool,
  onToolChange,
  selectedInst,
  onSelectInst,
  selectedModule,
  selectedBistGroup,
  overrides,
  onOverridesChange,
  datapaths,
  reviewPins,
  onAddPinAt,
  canWrite,
  onlyChangedTcl,
  onOnlyChangedTclChange,
  onSaveProposal,
}: FloorplanCanvasProps) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const svgRef = useRef<SVGSVGElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const readoutRef = useRef<HTMLSpanElement>(null)
  const pointerRef = useRef({ x: 0, y: 0 })
  const [view, setView] = useState({ zoom: 1, panX: 0, panY: 0 })
  const [stageSize, setStageSize] = useState({ w: 1000, h: 640 })
  const [stageHeight, setStageHeight] = useState(560)
  const [hoveredName, setHoveredName] = useState('')
  const [layers, setLayers] = useState<Record<LayerKey, boolean>>({
    density: true,
    halo: true,
    notches: true,
    labels: false,
    pins: true,
    bistColor: scene.bist_summary.length > 0,
    datapaths: true,
    reviewPins: true,
    diffOverlay: true,
  })
  const [snapUm, setSnapUm] = useState(5)
  const [channelGapUm, setChannelGapUm] = useState('15')
  const [selectedSet, setSelectedSet] = useState<Set<string>>(new Set())
  const [proposalTitle, setProposalTitle] = useState('')
  const [savingProp, setSavingProp] = useState(false)

  useEffect(() => {
    setView({ zoom: 1, panX: 0, panY: 0 })
    setHoveredName('')
    setSelectedSet(new Set())
    setLayers(prev => ({ ...prev, bistColor: scene.bist_summary.length > 0 }))
  }, [scene.design_name, scene.fp_ver, scene.bist_summary.length])

  // The stage fills the remaining viewport height; the viewBox always matches the stage aspect ratio
  // so screen pixels map linearly to µm (no letterboxing) and overlays can be sized in screen pixels.
  useLayoutEffect(() => {
    function measure() {
      const el = stageRef.current
      if (!el) return
      const top = el.getBoundingClientRect().top + scrollOffset(el)
      setStageHeight(Math.max(480, Math.round(window.innerHeight - top - 58)))
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [tool])

  useEffect(() => {
    const el = stageRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      const r = entries[0]?.contentRect
      if (r && r.width > 0 && r.height > 0) setStageSize({ w: r.width, h: r.height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const dragRef = useRef<
    | { kind: 'pan'; startX: number; startY: number; umPerPx: number; origPan: { x: number; y: number } }
    | { kind: 'macro'; startUmX: number; startUmY: number; origins: Map<string, { llx: number; lly: number; orient: MacroBox['orient']; halo_um: MacroBox['halo_um'] }> }
    | null
  >(null)

  const [x0, y0, x1, y1] = scene.die_bbox
  const dw = Math.max(x1 - x0, 10)
  const dh = Math.max(y1 - y0, 10)
  const pad = Math.max(dw, dh) * 0.07
  const fitW = dw + pad * 2
  const fitH = dh + pad * 2
  const aspect = stageSize.w / Math.max(stageSize.h, 1)
  const baseW = fitW / fitH > aspect ? fitW : fitH * aspect
  const baseH = baseW / aspect
  const centerX = (x0 + x1) / 2
  const centerY = -(y0 + y1) / 2
  const vbW = baseW / view.zoom
  const vbH = baseH / view.zoom
  const vbX = centerX - vbW / 2 + view.panX
  const vbY = centerY - vbH / 2 + view.panY
  /** µm per screen pixel at the current zoom. */
  const k = vbW / Math.max(stageSize.w, 1)
  const px = (n: number) => n * k
  const screen = (x: number, y: number) => `translate(${x} ${y}) scale(${k})`

  const sandbox = useMemo(() => evaluateSandboxState(scene, overrides), [scene, overrides])
  const bistColorMap = useMemo(() => {
    const m = new Map<string, string>()
    sandbox.liveBist.forEach((b, idx) => m.set(b.bist_group, BIST_PALETTE[idx % BIST_PALETTE.length]))
    return m
  }, [sandbox.liveBist])

  const activeModuleSummary = useMemo(
    () => scene.module_regions.find(mr => mr.hier_path === selectedModule || selectedModule.startsWith(mr.hier_path + '/')),
    [scene.module_regions, selectedModule],
  )

  const activeTargets = useMemo(() => {
    const names = selectedSet.size ? [...selectedSet] : (selectedInst ? [selectedInst] : [])
    return names
      .map(n => sandbox.effectiveMacros.find(m => m.inst_name === n))
      .filter((m): m is EffectiveMacroBox => Boolean(m))
  }, [selectedSet, selectedInst, sandbox.effectiveMacros])

  const kindsPresent = useMemo(() => KIND_ORDER.filter(kind => scene.macros.some(m => m.kind === kind)), [scene.macros])
  const hasSram = kindsPresent.includes('sram')
  const hasNonSram = scene.macros.some(m => m.kind !== 'sram')

  const densityHref = useMemo(() => {
    const g = scene.density_grid_size
    if (!g || scene.density_grid.length !== g * g || typeof document === 'undefined') return ''
    const canvas = document.createElement('canvas')
    canvas.width = g
    canvas.height = g
    const ctx = canvas.getContext('2d')
    if (!ctx) return ''
    const img = ctx.createImageData(g, g)
    scene.density_grid.forEach((val, idx) => {
      const gx = idx % g
      const row = g - 1 - Math.floor(idx / g)
      const lum = Math.round(Math.max(0, Math.min(1, (Number(val) - 10) / 90)) * 255)
      const o = (row * g + gx) * 4
      img.data[o] = lum
      img.data[o + 1] = lum
      img.data[o + 2] = lum
      img.data[o + 3] = 255
    })
    ctx.putImageData(img, 0, 0)
    return canvas.toDataURL()
  }, [scene.density_grid, scene.density_grid_size])

  const anySelectedUnlocked = activeTargets.some(m => !m.locked)
  const allSelectedLocked = activeTargets.length > 0 && activeTargets.every(m => m.locked)

  function clientToUm(clientX: number, clientY: number): [number, number] {
    const svg = svgRef.current
    if (!svg) return [0, 0]
    const rect = svg.getBoundingClientRect()
    const rx = (clientX - rect.left) / Math.max(rect.width, 1)
    const ry = (clientY - rect.top) / Math.max(rect.height, 1)
    return [vbX + rx * vbW, -(vbY + ry * vbH)]
  }

  function snap(val: number) {
    return snapUm > 0 ? Math.round(val / snapUm) * snapUm : Number(val.toFixed(2))
  }

  function zoomAt(factor: number, rx = 0.5, ry = 0.5) {
    setView(prev => {
      const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number((prev.zoom * factor).toFixed(4))))
      if (zoom === prev.zoom) return prev
      const w0 = baseW / prev.zoom, h0 = baseH / prev.zoom
      const ux = centerX - w0 / 2 + prev.panX + rx * w0
      const uy = centerY - h0 / 2 + prev.panY + ry * h0
      const w1 = baseW / zoom, h1 = baseH / zoom
      return { zoom, panX: ux - rx * w1 - centerX + w1 / 2, panY: uy - ry * h1 - centerY + h1 / 2 }
    })
  }

  // Native non-passive listener so wheel zoom never scrolls the page; zoom is anchored at the cursor.
  const wheelRef = useRef<(e: globalThis.WheelEvent) => void>(() => {})
  wheelRef.current = e => {
    e.preventDefault()
    const svg = svgRef.current
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, (e.clientX - rect.left) / Math.max(rect.width, 1), (e.clientY - rect.top) / Math.max(rect.height, 1))
  }
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const handler = (e: globalThis.WheelEvent) => wheelRef.current(e)
    svg.addEventListener('wheel', handler, { passive: false })
    return () => svg.removeEventListener('wheel', handler)
  }, [])

  function onCanvasMouseDown(e: MouseEvent<SVGSVGElement>) {
    if (e.button !== 0) return
    if (tool === 'pin') {
      const [ux, uy] = clientToUm(e.clientX, e.clientY)
      onAddPinAt(Number(ux.toFixed(1)), Number(uy.toFixed(1)))
      return
    }
    dragRef.current = { kind: 'pan', startX: e.clientX, startY: e.clientY, umPerPx: k, origPan: { x: view.panX, y: view.panY } }
  }

  function onMacroMouseDown(e: MouseEvent<SVGGElement>, macro: EffectiveMacroBox) {
    if (tool === 'pin') return
    e.stopPropagation()
    onSelectInst(macro.inst_name)
    const nextSel = new Set(selectedSet)
    if (e.shiftKey) {
      if (nextSel.has(macro.inst_name)) nextSel.delete(macro.inst_name)
      else nextSel.add(macro.inst_name)
    } else if (!nextSel.has(macro.inst_name)) {
      nextSel.clear()
      nextSel.add(macro.inst_name)
    }
    setSelectedSet(nextSel)

    if (tool === 'sandbox' && canWrite) {
      const [ux, uy] = clientToUm(e.clientX, e.clientY)
      const origins = new Map<string, { llx: number; lly: number; orient: MacroBox['orient']; halo_um: MacroBox['halo_um'] }>()
      for (const name of nextSel) {
        const cur = sandbox.effectiveMacros.find(m => m.inst_name === name)
        if (cur && !cur.locked) {
          origins.set(name, { llx: cur.llx, lly: cur.lly, orient: cur.orient, halo_um: cur.halo_um })
        }
      }
      if (origins.size > 0) {
        dragRef.current = { kind: 'macro', startUmX: ux, startUmY: uy, origins }
      }
    }
  }

  function onMouseMove(e: MouseEvent<SVGSVGElement>) {
    const [ux, uy] = clientToUm(e.clientX, e.clientY)
    if (readoutRef.current) readoutRef.current.textContent = `${ux.toFixed(1)}, ${uy.toFixed(1)}`
    const drag = dragRef.current
    if (!drag) return
    if (drag.kind === 'pan') {
      setView(prev => ({
        ...prev,
        panX: drag.origPan.x - (e.clientX - drag.startX) * drag.umPerPx,
        panY: drag.origPan.y - (e.clientY - drag.startY) * drag.umPerPx,
      }))
      return
    }
    const dx = ux - drag.startUmX
    const dy = uy - drag.startUmY
    const next = new Map(overrides)
    for (const [name, orig] of drag.origins.entries()) {
      const prevOv = next.get(name)
      next.set(name, {
        inst_name: name,
        llx: snap(orig.llx + dx),
        lly: snap(orig.lly + dy),
        orient: orig.orient,
        halo_um: prevOv?.halo_um ?? orig.halo_um,
        locked: false,
      })
    }
    onOverridesChange(next)
  }

  function stopDrag() {
    dragRef.current = null
  }

  function transformSelected(fn: (m: EffectiveMacroBox) => Partial<MacroOverride>) {
    if (!activeTargets.length) return
    const next = new Map(overrides)
    for (const cur of activeTargets) {
      if (cur.locked) continue
      const prevOv = next.get(cur.inst_name)
      next.set(cur.inst_name, {
        inst_name: cur.inst_name,
        llx: cur.llx,
        lly: cur.lly,
        orient: cur.orient,
        halo_um: prevOv?.halo_um ?? cur.halo_um,
        locked: cur.locked,
        ...fn(cur),
      })
    }
    onOverridesChange(next)
  }

  function toggleLockSelected() {
    if (!activeTargets.length) return
    const targetLocked = !allSelectedLocked
    const next = new Map(overrides)
    for (const cur of activeTargets) {
      const prevOv = next.get(cur.inst_name)
      next.set(cur.inst_name, {
        inst_name: cur.inst_name,
        llx: cur.llx,
        lly: cur.lly,
        orient: cur.orient,
        halo_um: prevOv?.halo_um ?? cur.halo_um,
        locked: targetLocked,
      })
    }
    onOverridesChange(next)
  }

  function alignEdges(edge: 'left' | 'bottom') {
    if (activeTargets.length < 2) return
    const lockedAnchor = activeTargets.find(m => m.locked)
    const refVal = lockedAnchor
      ? (edge === 'left' ? lockedAnchor.llx : lockedAnchor.lly)
      : Math.min(...activeTargets.map(m => (edge === 'left' ? m.llx : m.lly)))
    const next = new Map(overrides)
    for (const m of activeTargets) {
      if (m.locked) continue
      const prevOv = next.get(m.inst_name)
      next.set(m.inst_name, {
        inst_name: m.inst_name,
        llx: edge === 'left' ? Number(refVal.toFixed(2)) : m.llx,
        lly: edge === 'bottom' ? Number(refVal.toFixed(2)) : m.lly,
        orient: m.orient,
        halo_um: prevOv?.halo_um ?? m.halo_um,
        locked: false,
      })
    }
    onOverridesChange(next)
  }

  function stackSelected(axis: 'x' | 'y') {
    if (activeTargets.length < 2) return
    const gap = Math.max(0, Number(channelGapUm) || 0)
    const sorted = [...activeTargets].sort((a, b) => axis === 'x' ? a.llx - b.llx : a.lly - b.lly)
    const anchor = sorted.find(m => m.locked) || sorted[0]
    let cursor = axis === 'x' ? anchor.llx : anchor.lly
    const next = new Map(overrides)
    for (const m of sorted) {
      const [ow, oh] = orientedSize(m.width, m.height, m.orient)
      const prevOv = next.get(m.inst_name)
      if (!m.locked) {
        if (axis === 'x') {
          next.set(m.inst_name, { inst_name: m.inst_name, llx: Number(cursor.toFixed(2)), lly: anchor.lly, orient: m.orient, halo_um: prevOv?.halo_um ?? m.halo_um, locked: false })
        } else {
          next.set(m.inst_name, { inst_name: m.inst_name, llx: anchor.llx, lly: Number(cursor.toFixed(2)), orient: m.orient, halo_um: prevOv?.halo_um ?? m.halo_um, locked: false })
        }
      } else {
        cursor = axis === 'x' ? m.llx : m.lly
      }
      cursor += (axis === 'x' ? ow : oh) + gap
    }
    onOverridesChange(next)
  }

  function selectByFilter(filter: 'all' | 'sram' | 'non_sram') {
    const matched = sandbox.effectiveMacros.filter(m => {
      if (filter === 'sram') return m.kind === 'sram'
      if (filter === 'non_sram') return m.kind !== 'sram'
      return true
    })
    const next = new Set(matched.map(m => m.inst_name))
    setSelectedSet(next)
    if (matched[0]) onSelectInst(matched[0].inst_name)
  }

  function placePopover() {
    const stage = stageRef.current
    const pop = popoverRef.current
    if (!stage || !pop) return
    const r = stage.getBoundingClientRect()
    const x = pointerRef.current.x - r.left
    const y = pointerRef.current.y - r.top
    let left = x + 16
    let top = y + 18
    if (left + pop.offsetWidth > r.width - 8) left = x - 16 - pop.offsetWidth
    if (top + pop.offsetHeight > r.height - 8) top = y - 14 - pop.offsetHeight
    pop.style.transform = `translate(${Math.round(Math.max(8, left))}px, ${Math.round(Math.max(8, top))}px)`
  }

  function onMacroHover(e: MouseEvent<SVGGElement>, macro: EffectiveMacroBox) {
    pointerRef.current = { x: e.clientX, y: e.clientY }
    if (dragRef.current) {
      if (hoveredName) setHoveredName('')
      return
    }
    if (hoveredName !== macro.inst_name) setHoveredName(macro.inst_name)
    else placePopover()
  }

  useLayoutEffect(() => {
    if (hoveredName) placePopover()
  }, [hoveredName])

  function downloadClientExport(format: 'innovus' | 'icc2' | 'csv') {
    const content = generateClientTcl(scene, overrides, format, proposalTitle || 'Designer_Draft', onlyChangedTcl)
    const ext = format === 'csv' ? 'csv' : 'tcl'
    const suffix = onlyChangedTcl ? '_delta' : ''
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${scene.design_name}_${scene.fp_ver}_${format}${suffix}.${ext}`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const hovered = hoveredName ? sandbox.effectiveMacros.find(m => m.inst_name === hoveredName) : undefined
  const layerItems = compareScene ? [...LAYER_ITEMS, { key: 'diffOverlay' as const, label: `이전 버전 윤곽 (${compareScene.fp_ver})` }] : LAYER_ITEMS
  const layersOn = layerItems.filter(item => layers[item.key]).length
  const hasWarnings = sandbox.outOfBounds.size > 0 || sandbox.overlapping.size > 0

  const gridMinor = niceStep(k * 22)
  const gridMajor = gridMinor * 5
  const scaleUm = niceStep(k * 110)
  const diePoints = scene.die_polygon.map(([pxu, pyu]) => `${pxu},${-pyu}`).join(' ')

  return (
    <div className="fp-canvas-panel">
      <div className="fp-canvas-toolbar" role="toolbar" aria-label="도면 도구">
        <div className="fp-seg" role="group" aria-label="도구">
          <button type="button" aria-pressed={tool === 'inspect'} onClick={() => onToolChange('inspect')}>
            <MousePointer2 size={14} aria-hidden="true" />선택
          </button>
          {canWrite && (
            <button type="button" aria-pressed={tool === 'pin'} onClick={() => onToolChange('pin')}>
              <MapPin size={14} aria-hidden="true" />리뷰 핀
            </button>
          )}
          <button type="button" aria-pressed={tool === 'sandbox'} onClick={() => onToolChange('sandbox')}>
            <Move size={14} aria-hidden="true" />Macro 배치
          </button>
        </div>

        <DropdownMenu.Root modal={false}>
          <DropdownMenu.Trigger asChild>
            <button type="button" className="fp-menu-trigger">
              <Layers size={14} aria-hidden="true" />레이어<span className="fp-menu-count">{layersOn}/{layerItems.length}</span>
              <ChevronDown size={13} aria-hidden="true" />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content className="fp-layer-menu" align="start" sideOffset={6} collisionPadding={12}>
              <DropdownMenu.Label className="fp-layer-menu-label">표시 레이어</DropdownMenu.Label>
              {layerItems.map(item => (
                <DropdownMenu.CheckboxItem
                  key={item.key}
                  className="fp-layer-option"
                  checked={layers[item.key]}
                  onCheckedChange={checked => setLayers(prev => ({ ...prev, [item.key]: checked === true }))}
                  onSelect={e => e.preventDefault()}
                >
                  <LayerSwatch layer={item.key} />
                  <span>{item.label}</span>
                  <DropdownMenu.ItemIndicator className="fp-layer-check"><Check size={14} aria-hidden="true" /></DropdownMenu.ItemIndicator>
                </DropdownMenu.CheckboxItem>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>

        {compareScene && layers.diffOverlay && (
          <span className="fp-compare-note"><i aria-hidden="true" />{compareScene.fp_ver} 윤곽 비교 중</span>
        )}
      </div>

      {tool === 'sandbox' && (
        <div className="fp-sandbox" aria-label="Hard Macro 배치 툴바">
          <div className="fp-sandbox-row">
            <span className="fp-sb-count"><b>{activeTargets.length}</b>개 선택</span>
            <div className="fp-seg is-small" role="group" aria-label="빠른 선택">
              <button type="button" onClick={() => selectByFilter('all')}>전체 <span className="fp-num">{scene.macros.length}</span></button>
              {hasSram && <button type="button" onClick={() => selectByFilter('sram')}>SRAM</button>}
              {hasNonSram && <button type="button" onClick={() => selectByFilter('non_sram')}>SRAM 외</button>}
            </div>
            <i className="fp-divider" aria-hidden="true" />
            <span className="fp-sb-label">방향</span>
            <IconButton label="좌우 미러링 (MY)" disabled={!canWrite || !anySelectedUnlocked} onClick={() => transformSelected(m => ({ orient: flipOrientX(m.orient) }))}><FlipHorizontal2 size={15} /></IconButton>
            <IconButton label="상하 미러링 (MX)" disabled={!canWrite || !anySelectedUnlocked} onClick={() => transformSelected(m => ({ orient: flipOrientY(m.orient) }))}><FlipVertical2 size={15} /></IconButton>
            <IconButton label="90° 회전" disabled={!canWrite || !anySelectedUnlocked} onClick={() => transformSelected(m => ({ orient: rotateOrient90(m.orient) }))}><RotateCw size={15} /></IconButton>
            <IconButton label={allSelectedLocked ? '잠금 해제' : '위치 잠금 (드래그·정렬 기준점)'} pressed={allSelectedLocked} disabled={!canWrite || !activeTargets.length} onClick={toggleLockSelected}>
              {allSelectedLocked ? <Unlock size={15} /> : <Lock size={15} />}
            </IconButton>
            <i className="fp-divider" aria-hidden="true" />
            <span className="fp-sb-label">정렬</span>
            <IconButton label="좌측 정렬" disabled={!canWrite || activeTargets.length < 2} onClick={() => alignEdges('left')}><AlignStartVertical size={15} /></IconButton>
            <IconButton label="하단 정렬" disabled={!canWrite || activeTargets.length < 2} onClick={() => alignEdges('bottom')}><AlignEndHorizontal size={15} /></IconButton>
            <IconButton label="가로 배열 (채널 간격)" disabled={!canWrite || activeTargets.length < 2} onClick={() => stackSelected('x')}><AlignHorizontalDistributeStart size={15} /></IconButton>
            <IconButton label="세로 배열 (채널 간격)" disabled={!canWrite || activeTargets.length < 2} onClick={() => stackSelected('y')}><AlignVerticalDistributeStart size={15} /></IconButton>
            <label className="fp-field" title="배열 시 Hard Macro 사이 채널 간격">
              <span>채널</span>
              <input type="number" min={0} max={500} value={channelGapUm} onChange={e => setChannelGapUm(e.target.value)} />
              <em>µm</em>
            </label>
            <i className="fp-divider" aria-hidden="true" />
            <label className="fp-field">
              <span>그리드</span>
              <select value={snapUm} onChange={e => setSnapUm(Number(e.target.value))}>
                <option value={0}>자유</option>
                <option value={1}>1 µm</option>
                <option value={5}>5 µm</option>
                <option value={10}>10 µm</option>
              </select>
            </label>
            <button type="button" className="fp-text-btn fp-push-right" disabled={!overrides.size} onClick={() => onOverridesChange(new Map())}>
              <RotateCcw size={13} aria-hidden="true" />전체 초기화
            </button>
          </div>

          <div className="fp-sandbox-row is-foot">
            <span className={`fp-sb-status${hasWarnings ? ' is-warn' : ''}`}>
              <i aria-hidden="true" />
              {hasWarnings ? `경계 이탈 ${sandbox.outOfBounds.size} · 충돌 ${sandbox.overlapping.size}` : '경계 · Halo 충돌 없음'}
            </span>
            <span className="fp-sb-meta">변경 <b>{sandbox.changedCount}</b> · 잠금 <b>{sandbox.lockedCount}</b></span>
            <span className="fp-push-right" />
            {canWrite && (
              <>
                <input
                  type="text"
                  className="fp-proposal-input"
                  placeholder="제안안 이름 (예: Hard Macro안 #1)"
                  value={proposalTitle}
                  onChange={e => setProposalTitle(e.target.value)}
                />
                <Button
                  size="sm"
                  disabled={savingProp || !sandbox.changedCount}
                  onClick={async () => {
                    setSavingProp(true)
                    try {
                      await onSaveProposal(proposalTitle.trim() || `Hard Macro 배치안 (${new Date().toLocaleTimeString('ko-KR')})`, '')
                      setProposalTitle('')
                    } finally {
                      setSavingProp(false)
                    }
                  }}
                >
                  <Save size={13} />제안안 저장
                </Button>
                <i className="fp-divider" aria-hidden="true" />
              </>
            )}
            <span className="fp-sb-label"><Download size={13} aria-hidden="true" />내보내기</span>
            <div className="fp-seg is-small" role="group" aria-label="내보내기 형식">
              <button type="button" onClick={() => downloadClientExport('innovus')}>Innovus Tcl</button>
              <button type="button" onClick={() => downloadClientExport('icc2')}>ICC2 Tcl</button>
              <button type="button" onClick={() => downloadClientExport('csv')}>CSV</button>
            </div>
            <label className="fp-check" title="위치·방향·Halo가 수정된 Hard Macro만 Tcl/CSV로 내보냅니다">
              <input type="checkbox" checked={onlyChangedTcl} onChange={e => onOnlyChangedTclChange(e.target.checked)} />
              <span>변경분만</span>
            </label>
          </div>
        </div>
      )}

      <div ref={stageRef} className="fp-svg-stage" style={{ height: stageHeight }}>
        <svg
          ref={svgRef}
          viewBox={`${vbX} ${vbY} ${vbW} ${vbH}`}
          preserveAspectRatio="none"
          className={`fp-svg mode-${tool}`}
          onMouseDown={onCanvasMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={stopDrag}
          onMouseLeave={() => {
            stopDrag()
            setHoveredName('')
            if (readoutRef.current) readoutRef.current.textContent = '—'
          }}
        >
          <defs>
            <pattern id={`${uid}-grid-minor`} width={gridMinor} height={gridMinor} patternUnits="userSpaceOnUse">
              <path d={`M ${gridMinor} 0 L 0 0 0 ${gridMinor}`} className="fp-grid-minor" strokeWidth={px(2)} />
            </pattern>
            <pattern id={`${uid}-grid-major`} width={gridMajor} height={gridMajor} patternUnits="userSpaceOnUse">
              <path d={`M ${gridMajor} 0 L 0 0 0 ${gridMajor}`} className="fp-grid-major" strokeWidth={px(2)} />
            </pattern>
            <pattern id={`${uid}-hatch-danger`} width={px(6)} height={px(6)} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1={0} y1={0} x2={0} y2={px(6)} className="fp-hatch-danger" strokeWidth={px(1.25)} />
            </pattern>
            <pattern id={`${uid}-hatch-warn`} width={px(7)} height={px(7)} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1={0} y1={0} x2={0} y2={px(7)} className="fp-hatch-warn" strokeWidth={px(1)} />
            </pattern>
            <clipPath id={`${uid}-die`}>
              <polygon points={diePoints} />
            </clipPath>
            {densityHref && (
              <mask id={`${uid}-density`} maskUnits="userSpaceOnUse" x={x0} y={-(y0 + dh)} width={dw} height={dh}>
                <image href={densityHref} x={x0} y={-(y0 + dh)} width={dw} height={dh} preserveAspectRatio="none" />
              </mask>
            )}
          </defs>

          {/* Die, density and µm grid */}
          <polygon points={diePoints} className="fp-die-fill" />
          {layers.density && densityHref && (
            <rect x={x0} y={-(y0 + dh)} width={dw} height={dh} className="fp-density" mask={`url(#${uid}-density)`} clipPath={`url(#${uid}-die)`} />
          )}
          <rect x={vbX} y={vbY} width={vbW} height={vbH} fill={`url(#${uid}-grid-minor)`} pointerEvents="none" />
          <rect x={vbX} y={vbY} width={vbW} height={vbH} fill={`url(#${uid}-grid-major)`} pointerEvents="none" />
          <polygon points={diePoints} className="fp-die-line" strokeWidth={px(1.25)} />

          <g pointerEvents="none" className="fp-die-annot">
            <g transform={screen(x0, -y1)}>
              <text x={0} y={-10} className="fp-die-title">
                {scene.design_name}<tspan dx={8} className="fp-die-sub">{scene.fp_ver}</tspan>
              </text>
            </g>
            <g transform={screen(x1, -y1)}>
              <text x={0} y={-10} textAnchor="end" className="fp-coord">{formatUm(x1)}, {formatUm(y1)}</text>
            </g>
            <g transform={screen(x0, -y0)}>
              <text x={0} y={18} className="fp-coord">{formatUm(x0)}, {formatUm(y0)}</text>
            </g>
          </g>

          {/* Selected hierarchy region */}
          {activeModuleSummary && (
            <g pointerEvents="none">
              <rect
                x={activeModuleSummary.p10_x}
                y={-activeModuleSummary.p90_y}
                width={Math.max(activeModuleSummary.p90_x - activeModuleSummary.p10_x, 12)}
                height={Math.max(activeModuleSummary.p90_y - activeModuleSummary.p10_y, 12)}
                className="fp-module-box"
                strokeDasharray={`${px(5)} ${px(3)}`}
                strokeWidth={px(1.5)}
              />
              <g transform={screen(activeModuleSummary.p10_x, -activeModuleSummary.p90_y)}>
                <text x={0} y={-6} className="fp-module-label">{activeModuleSummary.hier_path}</text>
              </g>
              <g transform={screen(activeModuleSummary.cx, -activeModuleSummary.cy)} className="fp-module-center">
                <path d="M-7 0H7M0 -7V7" />
                <circle r={2.5} />
              </g>
            </g>
          )}

          {/* Regions & blockages */}
          {layers.halo && scene.regions.map((reg, idx) => {
            const isHard = reg.kind === 'blockage'
            const isSoft = reg.kind === 'soft_blockage'
            const minX = Math.min(...reg.points.map(p => p[0]))
            const maxX = Math.max(...reg.points.map(p => p[0]))
            const maxY = Math.max(...reg.points.map(p => p[1]))
            return (
              <g key={`${reg.name}-${idx}`} pointerEvents="none">
                <polygon
                  points={reg.points.map(([pxu, pyu]) => `${pxu},${-pyu}`).join(' ')}
                  className={isHard ? 'fp-blockage' : isSoft ? 'fp-soft-blockage' : 'fp-region'}
                  fill={isHard ? `url(#${uid}-hatch-danger)` : isSoft ? `url(#${uid}-hatch-warn)` : undefined}
                  strokeDasharray={isHard ? undefined : `${px(4)} ${px(3)}`}
                  strokeWidth={px(1)}
                />
                {(maxX - minX) / k > 70 && (
                  <g transform={screen(minX, -maxY)}>
                    <text x={4} y={13} className={`fp-region-label${isHard ? ' is-danger' : isSoft ? ' is-warn' : ''}`}>{reg.name}</text>
                  </g>
                )}
              </g>
            )
          })}

          {/* Previous FP_VER outlines */}
          {compareScene && layers.diffOverlay && compareScene.macros.map(pm => {
            const [ow, oh] = orientedSize(pm.width, pm.height, pm.orient)
            return (
              <rect
                key={`prev-${pm.inst_name}`}
                x={pm.llx}
                y={-(pm.lly + oh)}
                width={ow}
                height={oh}
                className="fp-ghost"
                strokeDasharray={`${px(3)} ${px(3)}`}
                strokeWidth={px(1)}
                pointerEvents="none"
              />
            )
          })}

          {/* Live BIST group bounding boxes */}
          {layers.bistColor && sandbox.liveBist.map(b => {
            if (selectedBistGroup && selectedBistGroup !== b.bist_group) return null
            const [bx0, by0, bx1, by1] = b.bbox_um
            const col = bistColorMap.get(b.bist_group) || BIST_PALETTE[0]
            const focused = selectedBistGroup === b.bist_group
            return (
              <g key={`bist-${b.bist_group}`} pointerEvents="none">
                <rect
                  x={bx0 - px(6)}
                  y={-by1 - px(6)}
                  width={Math.max(bx1 - bx0, 1) + px(12)}
                  height={Math.max(by1 - by0, 1) + px(12)}
                  fill="none"
                  stroke={col}
                  strokeDasharray={`${px(6)} ${px(3)}`}
                  strokeWidth={px(focused ? 2 : 1.25)}
                />
                <g transform={screen(bx0 - px(6), -by1 - px(6))}>
                  <text x={0} y={-5} className="fp-bist-label" style={{ fill: col }}>{b.bist_group}</text>
                </g>
              </g>
            )
          })}

          {/* Hard macros: SRAM / Hard IP / custom macro / sub-block */}
          {sandbox.effectiveMacros.map(m => {
            const kind: MacroKind = KIND_LABEL[m.kind] ? m.kind : 'macro'
            const [ow, oh] = orientedSize(m.width, m.height, m.orient)
            const rx = m.llx
            const ry = -(m.lly + oh)
            const wPx = ow / k
            const hPx = oh / k
            const isSelected = m.inst_name === selectedInst || selectedSet.has(m.inst_name)
            const isHovered = m.inst_name === hoveredName
            const isBistFocused = Boolean(selectedBistGroup && m.bist_group === selectedBistGroup)
            const dimmed = Boolean(selectedBistGroup && m.bist_group !== selectedBistGroup)
            const hasCollision = sandbox.outOfBounds.has(m.inst_name) || sandbox.overlapping.has(m.inst_name)
            const bCol = layers.bistColor && m.bist_group ? bistColorMap.get(m.bist_group) : undefined
            const line = hasCollision ? 'var(--fp-danger)' : m.locked ? 'var(--fp-locked)' : bCol || (m.changed ? 'var(--fp-changed)' : `var(--fp-${kind}-line)`)
            const fill = bCol ? `${bCol}1f` : `var(--fp-${kind}-fill)`
            const [hl, hb, hr, ht] = m.halo_um
            const [nx, ny] = pin1Offset(m.width, m.height, m.orient)
            const notch = Math.min(px(10), Math.min(ow, oh) * 0.3)
            const ncx = m.llx + nx
            const ncy = -(m.lly + ny)
            const ix = nx === 0 ? 1 : -1
            const iy = ny === 0 ? -1 : 1
            const shortName = m.inst_name.split('/').pop() || m.inst_name
            const showName = layers.labels && wPx > 44 && hPx > 20
            const showMaster = showName && hPx > 40
            const strokePx = isSelected || isBistFocused ? 2 : isHovered ? 1.75 : m.changed ? 1.5 : 1

            return (
              <g
                key={m.inst_name}
                className={`fp-macro-node${m.locked ? ' is-locked' : ''}`}
                opacity={dimmed ? 0.28 : 1}
                onMouseDown={e => { setHoveredName(''); onMacroMouseDown(e, m) }}
                onMouseEnter={e => onMacroHover(e, m)}
                onMouseMove={e => onMacroHover(e, m)}
                onMouseLeave={() => setHoveredName(prev => (prev === m.inst_name ? '' : prev))}
              >
                {layers.halo && (hl > 0 || hb > 0 || hr > 0 || ht > 0) && (
                  <rect
                    x={rx - hl}
                    y={ry - ht}
                    width={ow + hl + hr}
                    height={oh + hb + ht}
                    className="fp-halo"
                    strokeDasharray={`${px(2)} ${px(2)}`}
                    strokeWidth={px(1)}
                  />
                )}
                <rect
                  x={rx}
                  y={ry}
                  width={ow}
                  height={oh}
                  style={{ fill, stroke: line }}
                  strokeDasharray={m.locked ? `${px(5)} ${px(2.5)}` : undefined}
                  strokeWidth={px(strokePx)}
                />
                {hasCollision && <rect x={rx} y={ry} width={ow} height={oh} fill={`url(#${uid}-hatch-danger)`} pointerEvents="none" />}
                {layers.notches && notch > 0 && (
                  <polygon
                    points={`${ncx},${ncy} ${ncx + ix * notch},${ncy} ${ncx},${ncy + iy * notch}`}
                    style={{ fill: line }}
                    pointerEvents="none"
                  />
                )}
                {showName && (
                  <g transform={screen(rx, ry)} pointerEvents="none">
                    <text x={7} y={17} className="fp-macro-name">
                      {truncate(`${shortName}${m.changed ? ' Δ' : ''}`, Math.floor((wPx - 14 - (m.locked ? 18 : 0)) / 7.3))}
                    </text>
                    {showMaster && (
                      <text x={7} y={32} className="fp-macro-master">{truncate(m.master, Math.floor((wPx - 14) / 6.7))}</text>
                    )}
                  </g>
                )}
                {m.locked && wPx > 28 && hPx > 24 && (
                  <g transform={screen(rx + ow, ry)} pointerEvents="none" className="fp-lock-badge">
                    <rect x={-19} y={4} width={15} height={15} rx={3} />
                    <path d="M-14.5 10.5V8.8a2 2 0 0 1 4 0v1.7" />
                    <rect x={-15.75} y={10.5} width={6.5} height={5} rx={1} className="fp-lock-body" />
                  </g>
                )}
                {isSelected && (
                  <g pointerEvents="none" className="fp-selection">
                    <rect x={rx - px(3)} y={ry - px(3)} width={ow + px(6)} height={oh + px(6)} strokeWidth={px(1.25)} />
                    {[[rx - px(3), ry - px(3)], [rx + ow + px(3), ry - px(3)], [rx - px(3), ry + oh + px(3)], [rx + ow + px(3), ry + oh + px(3)]].map(([hx, hy], hi) => (
                      <g key={hi} transform={screen(hx, hy)}>
                        <rect x={-3.5} y={-3.5} width={7} height={7} className="fp-handle" />
                      </g>
                    ))}
                  </g>
                )}
              </g>
            )
          })}

          {/* IO pins */}
          {layers.pins && scene.pins.map((p, idx) => {
            const tri = p.side === 'R' ? '0,-4.5 -7.5,0 0,4.5' : p.side === 'B' ? '-4.5,0 0,-7.5 4.5,0' : p.side === 'T' ? '-4.5,0 0,7.5 4.5,0' : '0,-4.5 7.5,0 0,4.5'
            const label = p.side === 'R' ? { x: -11, y: 4, a: 'end' } : p.side === 'B' ? { x: 0, y: -12, a: 'middle' } : p.side === 'T' ? { x: 0, y: 19, a: 'middle' } : { x: 11, y: 4, a: 'start' }
            return (
              <g key={`${p.name}-${idx}`} transform={screen(p.x, -p.y)} className={`fp-io-pin dir-${String(p.direction || '').toLowerCase()}`}>
                <polygon points={tri} />
                {layers.labels && scene.pins.length <= 48 && (
                  <text x={label.x} y={label.y} textAnchor={label.a as 'start' | 'middle' | 'end'} className="fp-pin-label">{p.name}</text>
                )}
                <title>{`${p.name} (${p.direction}) @ (${p.x}, ${p.y})`}</title>
              </g>
            )
          })}

          {/* Data-path flows */}
          {layers.datapaths && datapaths.map(dp => {
            const color = dp.color || 'var(--fp-changed)'
            const pts = dp.waypoints
            const last = pts[pts.length - 1]
            const prev = pts[pts.length - 2]
            const angle = last && prev ? (Math.atan2(-(last.y - prev.y), last.x - prev.x) * 180) / Math.PI : 0
            return (
              <g key={dp.id} pointerEvents="none">
                <polyline
                  points={pts.map(wp => `${wp.x},${-wp.y}`).join(' ')}
                  fill="none"
                  stroke={color}
                  strokeWidth={px(2.25)}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {pts.map((wp, wi) => (
                  <g key={wi} transform={screen(wp.x, -wp.y)}>
                    {wi < pts.length - 1 || pts.length < 2 ? <circle r={3.5} className="fp-waypoint" style={{ stroke: color }} /> : null}
                    {layers.labels && wp.label && <text x={7} y={-7} className="fp-waypoint-label">{wp.label}</text>}
                  </g>
                ))}
                {last && prev && (
                  <g transform={`${screen(last.x, -last.y)} rotate(${angle})`}>
                    <path d="M-10 -5 L0 0 L-10 5 Z" style={{ fill: color }} />
                  </g>
                )}
              </g>
            )
          })}

          {/* Review pins */}
          {layers.reviewPins && reviewPins.map(pin => (
            <g key={pin.id} pointerEvents="none" transform={screen(pin.x, -pin.y)} className={`fp-review-pin sev-${pin.resolved ? 'resolved' : pin.severity.toLowerCase()}`}>
              <path d="M0 0C-1.6-4.2-9-8.6-9-16.5A9 9 0 1 1 9-16.5C9-8.6 1.6-4.2 0 0Z" />
              <text x={0} y={-12.8} textAnchor="middle">{pin.number}</text>
            </g>
          ))}
        </svg>

        <div className="fp-scalebar" aria-hidden="true">
          <span style={{ width: scaleUm / k }} />
          <em>{formatLength(scaleUm)}</em>
        </div>

        <div className="fp-zoom-stack" role="group" aria-label="확대/축소">
          <button type="button" title="확대" aria-label="확대" onClick={() => zoomAt(1.4)}><Plus size={15} /></button>
          <button type="button" title="축소" aria-label="축소" onClick={() => zoomAt(1 / 1.4)}><Minus size={15} /></button>
          <button type="button" title="화면 맞춤" aria-label="화면 맞춤" onClick={() => setView({ zoom: 1, panX: 0, panY: 0 })}><Scan size={14} /></button>
        </div>

        {hovered && (
          <div ref={popoverRef} className="fp-macro-popover" role="tooltip">
            <div className="fp-pop-head">
              <span className={`fp-kind-tag kind-${hovered.kind}`}>{KIND_LABEL[hovered.kind] || hovered.kind}</span>
              {hovered.locked && <span className="fp-pop-flag is-locked"><Lock size={11} aria-hidden="true" />잠금</span>}
              {hovered.changed && <span className="fp-pop-flag is-changed">Δ 변경</span>}
            </div>
            <div className="fp-pop-name">{hovered.inst_name}</div>
            <dl className="fp-pop-grid">
              <dt>Master</dt><dd>{hovered.master}</dd>
              <dt>원점</dt><dd>{formatUm(hovered.llx)}, {formatUm(hovered.lly)} µm</dd>
              <dt>크기</dt><dd>{orientedSize(hovered.width, hovered.height, hovered.orient).map(formatUm).join(' × ')} µm</dd>
              <dt>방향</dt><dd>{hovered.orient} <span className="fp-pop-dim">/ {DEF_TO_ICC2[hovered.orient]}</span></dd>
              <dt>상태</dt><dd>{hovered.status}</dd>
              {hovered.bist_group && (<><dt>BIST</dt><dd>{hovered.bist_group}{hovered.bist_controller ? <span className="fp-pop-dim"> · {hovered.bist_controller}</span> : null}</dd></>)}
            </dl>
            {hovered.attributes && Object.keys(hovered.attributes).length > 0 && (
              <div className="fp-pop-attrs">
                {Object.entries(hovered.attributes).map(([key, value]) => (
                  <span key={key}>{key}=<b>{String(value)}</b></span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="fp-statusbar">
        <div className="fp-legend">
          {kindsPresent.map(kind => (
            <span key={kind}><i className={`fp-swatch k-${kind}`} aria-hidden="true" />{KIND_LABEL[kind]}</span>
          ))}
        </div>
        <span className="fp-status-hint">{TOOL_HINT[tool]}</span>
        <div className="fp-readout">
          <span>X, Y <b ref={readoutRef}>—</b> µm</span>
          <span>Die <b>{formatUm(dw)} × {formatUm(dh)}</b> µm</span>
          <span><b>{Math.round(view.zoom * 100)}</b>%</span>
        </div>
      </div>
    </div>
  )
}
