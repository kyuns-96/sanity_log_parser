import { useEffect, useMemo, useRef, useState, type MouseEvent, type WheelEvent } from 'react'
import { Download, FlipHorizontal2, FlipVertical2, Lock, Maximize2, MousePointer, Move, MapPin, RotateCcw, RotateCw, Save, Unlock } from 'lucide-react'
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

const KIND_STYLE: Record<string, { fill: string; stroke: string }> = {
  sram: { fill: '#dbeafe', stroke: '#1d4ed8' },
  ip: { fill: '#fef3c7', stroke: '#b45309' },
  subblock: { fill: '#e0e7ff', stroke: '#4338ca' },
  macro: { fill: '#f1f5f9', stroke: '#475569' },
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
  const svgRef = useRef<SVGSVGElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [hoveredMacro, setHoveredMacro] = useState<{ macro: EffectiveMacroBox; x: number; y: number; flipLeft: boolean; flipUp: boolean } | null>(null)
  const [layers, setLayers] = useState({
    density: true,
    regions: true,
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
    setZoom(1)
    setPan({ x: 0, y: 0 })
    setHoveredMacro(null)
    setSelectedSet(new Set())
  }, [scene.design_name, scene.fp_ver])

  const dragRef = useRef<
    | { kind: 'pan'; startX: number; startY: number; origPan: { x: number; y: number } }
    | { kind: 'macro'; startUmX: number; startUmY: number; origins: Map<string, { llx: number; lly: number; orient: MacroBox['orient']; halo_um: MacroBox['halo_um'] }> }
    | null
  >(null)

  const [x0, y0, x1, y1] = scene.die_bbox
  const dw = Math.max(x1 - x0, 10)
  const dh = Math.max(y1 - y0, 10)
  const pad = Math.max(dw, dh) * 0.05
  const vbW = (dw + pad * 2) / zoom
  const vbH = (dh + pad * 2) / zoom
  const vbX = (x0 - pad) + pan.x
  const vbY = (-y1 - pad) + pan.y

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

  const anySelectedUnlocked = activeTargets.some(m => !m.locked)
  const allSelectedLocked = activeTargets.length > 0 && activeTargets.every(m => m.locked)

  function clientToUm(clientX: number, clientY: number): [number, number] {
    const svg = svgRef.current
    if (!svg) return [0, 0]
    const rect = svg.getBoundingClientRect()
    const rx = (clientX - rect.left) / Math.max(rect.width, 1)
    const ry = (clientY - rect.top) / Math.max(rect.height, 1)
    const ux = vbX + rx * vbW
    const uy = -(vbY + ry * vbH)
    return [ux, uy]
  }

  function snap(val: number) {
    return snapUm > 0 ? Math.round(val / snapUm) * snapUm : Number(val.toFixed(2))
  }

  function onWheel(e: WheelEvent<SVGSVGElement>) {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.18 : 1 / 1.18
    setZoom(prev => Math.min(12, Math.max(0.5, Number((prev * factor).toFixed(3)))))
  }

  function onCanvasMouseDown(e: MouseEvent<SVGSVGElement>) {
    if (e.button !== 0) return
    if (tool === 'pin') {
      const [ux, uy] = clientToUm(e.clientX, e.clientY)
      onAddPinAt(Number(ux.toFixed(1)), Number(uy.toFixed(1)))
      return
    }
    dragRef.current = { kind: 'pan', startX: e.clientX, startY: e.clientY, origPan: { ...pan } }
  }

  function onMacroMouseDown(e: MouseEvent<SVGGElement>, macro: EffectiveMacroBox) {
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
    const drag = dragRef.current
    if (!drag) return
    if (drag.kind === 'pan') {
      const svg = svgRef.current
      if (!svg) return
      const rect = svg.getBoundingClientRect()
      const dxUm = ((e.clientX - drag.startX) / Math.max(rect.width, 1)) * vbW
      const dyUm = ((e.clientY - drag.startY) / Math.max(rect.height, 1)) * vbH
      setPan({ x: drag.origPan.x - dxUm, y: drag.origPan.y - dyUm })
      return
    }
    const [ux, uy] = clientToUm(e.clientX, e.clientY)
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

  function updateHoverPopover(e: MouseEvent<SVGGElement>, macro: EffectiveMacroBox) {
    if (dragRef.current) {
      setHoveredMacro(null)
      return
    }
    const stage = stageRef.current
    if (!stage) return
    const rect = stage.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    setHoveredMacro({
      macro,
      x,
      y,
      flipLeft: x > rect.width - 320,
      flipUp: y > rect.height - 160,
    })
  }

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

  const strokeUnit = Math.max(dw, dh) * 0.0018

  return (
    <div className="fp-canvas-panel">
      <div className="fp-canvas-toolbar" role="toolbar" aria-label="도면 도구">
        <div className="fp-tool-group">
          <button type="button" className={tool === 'inspect' ? 'is-active' : ''} onClick={() => onToolChange('inspect')}>
            <MousePointer size={14} />탐색 · 선택
          </button>
          {canWrite && (
            <button type="button" className={tool === 'pin' ? 'is-active' : ''} onClick={() => onToolChange('pin')}>
              <MapPin size={14} />핀 코멘트 찍기
            </button>
          )}
          <button type="button" className={tool === 'sandbox' ? 'is-active' : ''} onClick={() => onToolChange('sandbox')}>
            <Move size={14} />Hard Macro 배치 툴
          </button>
        </div>

        <div className="fp-layer-chips">
          {(
            [
              ['bistColor', 'BIST 색상'],
              ['notches', 'Pin-1 방향'],
              ['labels', '고정 라벨'],
              ['halo', 'Halo/Blockage'],
              ['density', '셀 밀도'],
              ['pins', 'IO Pin'],
              ['datapaths', 'Data-path'],
              ['reviewPins', '리뷰 핀'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`fp-chip-toggle${layers[key] ? ' is-on' : ''}`}
              onClick={() => setLayers(prev => ({ ...prev, [key]: !prev[key] }))}
            >
              {label}
            </button>
          ))}
          {compareScene && (
            <button
              type="button"
              className={`fp-chip-toggle${layers.diffOverlay ? ' is-on' : ''}`}
              onClick={() => setLayers(prev => ({ ...prev, diffOverlay: !prev.diffOverlay }))}
            >
              이전 버전 윤곽 겹침
            </button>
          )}
        </div>

        <div className="fp-zoom-controls">
          <span>{Math.round(zoom * 100)}%</span>
          <Button variant="outline" size="sm" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }}>
            <Maximize2 size={13} />화면 맞춤
          </Button>
        </div>
      </div>

      {tool === 'sandbox' && (
        <div className="fp-sandbox-bar" aria-label="Hard Macro 배치 툴바">
          <div className="fp-sandbox-ops">
            <span className="fp-sandbox-badge">
              Hard Macro 배치 ({activeTargets.length}개 선택 · 변경 {sandbox.changedCount}개{sandbox.lockedCount ? ` · 잠금 ${sandbox.lockedCount}개` : ''})
            </span>
            <div className="fp-quick-sel">
              <button type="button" className="fp-chip-toggle" onClick={() => selectByFilter('all')}>전체({scene.macros.length})</button>
              {scene.macros.some(m => m.kind === 'sram') && (
                <button type="button" className="fp-chip-toggle" onClick={() => selectByFilter('sram')}>SRAM</button>
              )}
              {scene.macros.some(m => m.kind !== 'sram') && (
                <button type="button" className="fp-chip-toggle" onClick={() => selectByFilter('non_sram')}>IP·Macro·Sub</button>
              )}
            </div>
            <label className="fp-inline-field">
              <span>그리드</span>
              <select value={snapUm} onChange={e => setSnapUm(Number(e.target.value))}>
                <option value={0}>자유(0µm)</option>
                <option value={1}>1 µm</option>
                <option value={5}>5 µm</option>
                <option value={10}>10 µm</option>
              </select>
            </label>
            <Button variant="outline" size="sm" disabled={!canWrite || !anySelectedUnlocked} onClick={() => transformSelected(m => ({ orient: flipOrientX(m.orient) }))} title="좌우 미러링 (MY)">
              <FlipHorizontal2 size={13} />Flip X (MY)
            </Button>
            <Button variant="outline" size="sm" disabled={!canWrite || !anySelectedUnlocked} onClick={() => transformSelected(m => ({ orient: flipOrientY(m.orient) }))} title="상하 미러링 (MX)">
              <FlipVertical2 size={13} />Flip Y (MX)
            </Button>
            <Button variant="outline" size="sm" disabled={!canWrite || !anySelectedUnlocked} onClick={() => transformSelected(m => ({ orient: rotateOrient90(m.orient) }))}>
              <RotateCw size={13} />90° 회전
            </Button>
            <Button variant="outline" size="sm" disabled={!canWrite || !activeTargets.length} onClick={toggleLockSelected} title="잠긴 Hard Macro는 드래그·정렬 시 고정 기준점이 됩니다">
              {allSelectedLocked ? <><Unlock size={13} />잠금 해제</> : <><Lock size={13} />위치 잠금</>}
            </Button>
            <label className="fp-inline-field">
              <span>채널(µm)</span>
              <input type="number" min={0} max={500} value={channelGapUm} onChange={e => setChannelGapUm(e.target.value)} />
            </label>
            <Button variant="outline" size="sm" disabled={!canWrite || activeTargets.length < 2} onClick={() => alignEdges('left')}>좌측 정렬</Button>
            <Button variant="outline" size="sm" disabled={!canWrite || activeTargets.length < 2} onClick={() => alignEdges('bottom')}>하단 정렬</Button>
            <Button variant="outline" size="sm" disabled={!canWrite || activeTargets.length < 2} onClick={() => stackSelected('x')}>가로 배열</Button>
            <Button variant="outline" size="sm" disabled={!canWrite || activeTargets.length < 2} onClick={() => stackSelected('y')}>세로 배열</Button>
            <Button variant="ghost" size="sm" disabled={!overrides.size} onClick={() => onOverridesChange(new Map())}>
              <RotateCcw size={13} />전체 초기화
            </Button>
          </div>

          <div className="fp-sandbox-export">
            <span className={sandbox.outOfBounds.size || sandbox.overlapping.size ? 'fp-collision-warn' : 'fp-collision-ok'}>
              {sandbox.outOfBounds.size || sandbox.overlapping.size
                ? `경계 이탈 ${sandbox.outOfBounds.size} · 충돌 ${sandbox.overlapping.size}`
                : '물리 경계·Halo 충돌 정상'}
            </span>
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
              </>
            )}
            <label className="fp-tpl-check" title="체크 시 위치·방향·Halo가 수정된 Hard Macro만 Tcl/CSV로 내보냅니다">
              <input type="checkbox" checked={onlyChangedTcl} onChange={e => onOnlyChangedTclChange(e.target.checked)} />
              <span>변경분만 Tcl</span>
            </label>
            <Button variant="outline" size="sm" onClick={() => downloadClientExport('innovus')}>
              <Download size={13} />Innovus Tcl
            </Button>
            <Button variant="outline" size="sm" onClick={() => downloadClientExport('icc2')}>
              <Download size={13} />ICC2 Tcl
            </Button>
            <Button variant="ghost" size="sm" onClick={() => downloadClientExport('csv')}>CSV</Button>
          </div>
        </div>
      )}

      <div ref={stageRef} className="fp-svg-stage">
        <svg
          ref={svgRef}
          viewBox={`${vbX} ${vbY} ${vbW} ${vbH}`}
          className={`fp-svg mode-${tool}`}
          onWheel={onWheel}
          onMouseDown={onCanvasMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={stopDrag}
          onMouseLeave={() => { stopDrag(); setHoveredMacro(null) }}
        >
          {/* Die Polygon */}
          <polygon
            points={scene.die_polygon.map(([px, py]) => `${px},${-py}`).join(' ')}
            fill="#ffffff"
            stroke="#0f172a"
            strokeWidth={strokeUnit * 1.6}
          />

          {/* Std-cell Density */}
          {layers.density && scene.density_grid.length === scene.density_grid_size * scene.density_grid_size && (
            <g>
              {scene.density_grid.map((val, idx) => {
                if (val < 12) return null
                const g = scene.density_grid_size
                const gx = idx % g, gy = Math.floor(idx / g)
                const tw = dw / g, th = dh / g
                return (
                  <rect
                    key={idx}
                    x={x0 + gx * tw}
                    y={-(y0 + (gy + 1) * th)}
                    width={tw}
                    height={th}
                    fill={`rgba(14, 116, 144, ${Math.min(0.28, val / 280)})`}
                  />
                )
              })}
            </g>
          )}

          {/* Selected Hierarchy Sub-instance Region Highlight */}
          {activeModuleSummary && (
            <g>
              <rect
                x={activeModuleSummary.p10_x}
                y={-activeModuleSummary.p90_y}
                width={Math.max(activeModuleSummary.p90_x - activeModuleSummary.p10_x, 12)}
                height={Math.max(activeModuleSummary.p90_y - activeModuleSummary.p10_y, 12)}
                fill="rgba(16, 185, 129, 0.18)"
                stroke="#059669"
                strokeDasharray={`${strokeUnit * 5} ${strokeUnit * 3}`}
                strokeWidth={strokeUnit * 1.8}
              />
              <circle cx={activeModuleSummary.cx} cy={-activeModuleSummary.cy} r={strokeUnit * 5} fill="#059669" />
            </g>
          )}

          {/* Regions & Blockages */}
          {layers.halo && scene.regions.map((reg, idx) => (
            <polygon
              key={idx}
              points={reg.points.map(([px, py]) => `${px},${-py}`).join(' ')}
              fill={reg.kind.includes('blockage') ? 'rgba(239,68,68,0.10)' : 'rgba(99,102,241,0.08)'}
              stroke={reg.kind.includes('blockage') ? '#ef4444' : '#6366f1'}
              strokeDasharray={reg.kind.includes('blockage') ? `${strokeUnit * 4} ${strokeUnit * 3}` : undefined}
              strokeWidth={strokeUnit}
            />
          ))}

          {/* Previous FP_VER ghost diff boxes */}
          {compareScene && layers.diffOverlay && compareScene.macros.map(pm => {
            const [ow, oh] = orientedSize(pm.width, pm.height, pm.orient)
            return (
              <rect
                key={`prev-${pm.inst_name}`}
                x={pm.llx}
                y={-(pm.lly + oh)}
                width={ow}
                height={oh}
                fill="none"
                stroke="#94a3b8"
                strokeDasharray={`${strokeUnit * 3} ${strokeUnit * 3}`}
                strokeWidth={strokeUnit * 1.1}
              />
            )
          })}

          {/* Live BIST Group Bounding Boxes */}
          {layers.bistColor && sandbox.liveBist.map(b => {
            if (selectedBistGroup && selectedBistGroup !== b.bist_group) return null
            const [bx0, by0, bx1, by1] = b.bbox_um
            const col = bistColorMap.get(b.bist_group) || '#2563eb'
            return (
              <rect
                key={`bist-${b.bist_group}`}
                x={bx0}
                y={-by1}
                width={Math.max(bx1 - bx0, 1)}
                height={Math.max(by1 - by0, 1)}
                fill="none"
                stroke={col}
                strokeDasharray={`${strokeUnit * 6} ${strokeUnit * 3}`}
                strokeWidth={strokeUnit * (selectedBistGroup === b.bist_group ? 2.4 : 1.3)}
              />
            )
          })}

          {/* All Hard Macros: SRAMs / Hard IPs / Custom Macros / Sub-blocks */}
          {sandbox.effectiveMacros.map(m => {
            const [ow, oh] = orientedSize(m.width, m.height, m.orient)
            const rx = m.llx
            const ry = -(m.lly + oh)
            const isSelected = m.inst_name === selectedInst || selectedSet.has(m.inst_name)
            const isBistFocused = Boolean(selectedBistGroup && m.bist_group === selectedBistGroup)
            const dimmed = Boolean(selectedBistGroup && m.bist_group !== selectedBistGroup)
            const hasCollision = sandbox.outOfBounds.has(m.inst_name) || sandbox.overlapping.has(m.inst_name)
            const bCol = m.bist_group ? bistColorMap.get(m.bist_group) : undefined
            const style = KIND_STYLE[m.kind] || KIND_STYLE.macro
            const stroke = hasCollision ? '#dc2626' : (m.locked ? '#d97706' : (layers.bistColor && bCol ? bCol : style.stroke))
            const fill = hasCollision ? 'rgba(254,202,202,0.85)' : (layers.bistColor && bCol ? `${bCol}26` : style.fill)
            const [hl, hb, hr, ht] = m.halo_um
            const [nx, ny] = pin1Offset(m.width, m.height, m.orient)
            const shortName = m.inst_name.split('/').pop() || m.inst_name
            const labelText = `${m.locked ? '🔒 ' : ''}${shortName}${m.changed ? ' Δ' : ''}`
            const fontSize = Math.max(Math.min(ow / Math.max(labelText.length * 0.68, 4), oh * 0.3), Math.max(dw, dh) * 0.009)

            return (
              <g
                key={m.inst_name}
                className={`fp-macro-node${m.locked ? ' is-locked' : ''}`}
                opacity={dimmed ? 0.28 : 1}
                onMouseDown={e => { setHoveredMacro(null); onMacroMouseDown(e, m) }}
                onMouseEnter={e => updateHoverPopover(e, m)}
                onMouseMove={e => updateHoverPopover(e, m)}
                onMouseLeave={() => setHoveredMacro(prev => (prev?.macro.inst_name === m.inst_name ? null : prev))}
              >
                {layers.halo && (hl > 0 || hb > 0 || hr > 0 || ht > 0) && (
                  <rect
                    x={rx - hl}
                    y={ry - ht}
                    width={ow + hl + hr}
                    height={oh + hb + ht}
                    fill="none"
                    stroke="#cbd5e1"
                    strokeDasharray={`${strokeUnit * 2} ${strokeUnit * 2}`}
                    strokeWidth={strokeUnit * 0.7}
                  />
                )}
                <rect
                  x={rx}
                  y={ry}
                  width={ow}
                  height={oh}
                  fill={fill}
                  stroke={stroke}
                  strokeDasharray={m.locked ? `${strokeUnit * 4} ${strokeUnit * 2}` : undefined}
                  strokeWidth={strokeUnit * (isSelected || isBistFocused ? 2.8 : (m.changed ? 2.1 : 1.3))}
                />
                {layers.notches && (
                  <circle
                    cx={m.llx + nx}
                    cy={-(m.lly + ny)}
                    r={Math.max(Math.min(ow, oh) * 0.08, strokeUnit * 2.2)}
                    fill={stroke}
                  />
                )}
                {layers.labels && (
                  <text
                    x={rx + ow * 0.5}
                    y={ry + oh * 0.54}
                    textAnchor="middle"
                    fontSize={fontSize}
                    fill="#0f172a"
                    pointerEvents="none"
                  >
                    {labelText}
                  </text>
                )}
              </g>
            )
          })}

          {/* IO Pins */}
          {layers.pins && scene.pins.map((p, idx) => (
            <g key={`${p.name}-${idx}`}>
              <circle
                cx={p.x}
                cy={-p.y}
                r={strokeUnit * 3.2}
                fill={p.direction === 'INPUT' ? '#16a34a' : p.direction === 'OUTPUT' ? '#ea580c' : '#64748b'}
              />
              <title>{`${p.name} (${p.direction}) @ (${p.x}, ${p.y})`}</title>
            </g>
          ))}

          {/* Datapath Flows */}
          {layers.datapaths && datapaths.map(dp => (
            <g key={dp.id}>
              <polyline
                points={dp.waypoints.map(wp => `${wp.x},${-wp.y}`).join(' ')}
                fill="none"
                stroke={dp.color || '#2563eb'}
                strokeWidth={strokeUnit * 2.8}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {dp.waypoints.map((wp, wi) => (
                <circle key={wi} cx={wp.x} cy={-wp.y} r={strokeUnit * 3.8} fill={dp.color || '#2563eb'} stroke="#fff" strokeWidth={strokeUnit} />
              ))}
            </g>
          ))}

          {/* Review Pins */}
          {layers.reviewPins && reviewPins.map(pin => {
            const r = Math.max(dw, dh) * 0.015
            const col = pin.resolved ? '#64748b' : pin.severity === 'ACTION' ? '#dc2626' : pin.severity === 'WARN' ? '#d97706' : '#16a34a'
            return (
              <g key={pin.id} pointerEvents="none">
                <circle cx={pin.x} cy={-pin.y} r={r} fill={col} stroke="#ffffff" strokeWidth={r * 0.2} />
                <text x={pin.x} y={-pin.y + r * 0.36} textAnchor="middle" fontSize={r * 1.05} fontWeight={700} fill="#ffffff">
                  {pin.number}
                </text>
              </g>
            )
          })}
        </svg>

        {hoveredMacro && (
          <div
            className="fp-macro-popover"
            role="tooltip"
            style={{
              left: hoveredMacro.flipLeft ? Math.max(8, hoveredMacro.x - 14) : hoveredMacro.x + 14,
              top: hoveredMacro.flipUp ? Math.max(8, hoveredMacro.y - 12) : hoveredMacro.y + 14,
              transform: `${hoveredMacro.flipLeft ? 'translateX(-100%)' : ''} ${hoveredMacro.flipUp ? 'translateY(-100%)' : ''}`.trim() || undefined,
            }}
          >
            <div className="fp-popover-head">
              <span className={`fp-kind-tag kind-${hoveredMacro.macro.kind}`}>{hoveredMacro.macro.kind.toUpperCase()}</span>
              <strong>{hoveredMacro.macro.inst_name}</strong>
              {hoveredMacro.macro.locked && <span title="잠금">🔒</span>}
              {hoveredMacro.macro.changed && <span className="fp-delta-mark">Δ</span>}
            </div>
            <div className="fp-popover-meta">
              <div><span>Master</span><code>{hoveredMacro.macro.master}</code></div>
              <div>
                <span>좌표 · 크기</span>
                <code>
                  ({hoveredMacro.macro.llx}, {hoveredMacro.macro.lly}) µm · {orientedSize(hoveredMacro.macro.width, hoveredMacro.macro.height, hoveredMacro.macro.orient).join(' × ')} µm
                </code>
              </div>
              <div>
                <span>방향 · 상태</span>
                <code>{hoveredMacro.macro.orient} ({DEF_TO_ICC2[hoveredMacro.macro.orient]}) · {hoveredMacro.macro.status}</code>
              </div>
              {hoveredMacro.macro.bist_group && (
                <div><span>BIST</span><code>{hoveredMacro.macro.bist_group}{hoveredMacro.macro.bist_controller ? ` (${hoveredMacro.macro.bist_controller})` : ''}</code></div>
              )}
              {hoveredMacro.macro.attributes && Object.keys(hoveredMacro.macro.attributes).length > 0 && (
                <div className="fp-popover-attrs">
                  {Object.entries(hoveredMacro.macro.attributes).map(([k, v]) => (
                    <span key={k}>{k}: {String(v)}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
