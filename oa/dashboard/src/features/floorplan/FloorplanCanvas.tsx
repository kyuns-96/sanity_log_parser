import { useMemo, useRef, useState, type MouseEvent, type WheelEvent } from 'react'
import { Download, FlipHorizontal2, FlipVertical2, Maximize2, MousePointer, Move, MapPin, RotateCcw, RotateCw, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  BIST_PALETTE,
  evaluateSandboxState,
  flipOrientX,
  flipOrientY,
  generateClientTcl,
  orientedSize,
  pin1Offset,
  rotateOrient90,
  type DatapathFlow,
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
  onSaveProposal,
}: FloorplanCanvasProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [layers, setLayers] = useState({
    density: true,
    regions: true,
    halo: true,
    notches: true,
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

  const dragRef = useRef<
    | { kind: 'pan'; startX: number; startY: number; origPan: { x: number; y: number } }
    | { kind: 'macro'; startUmX: number; startUmY: number; origins: Map<string, { llx: number; lly: number; orient: MacroBox['orient'] }> }
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

  function onMacroMouseDown(e: MouseEvent<SVGGElement>, macro: MacroBox) {
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
      const origins = new Map<string, { llx: number; lly: number; orient: MacroBox['orient'] }>()
      for (const name of nextSel) {
        const cur = sandbox.effectiveMacros.find(m => m.inst_name === name)
        if (cur) origins.set(name, { llx: cur.llx, lly: cur.lly, orient: cur.orient })
      }
      dragRef.current = { kind: 'macro', startUmX: ux, startUmY: uy, origins }
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
      next.set(name, { inst_name: name, llx: snap(orig.llx + dx), lly: snap(orig.lly + dy), orient: orig.orient })
    }
    onOverridesChange(next)
  }

  function stopDrag() {
    dragRef.current = null
  }

  function transformSelected(fn: (m: MacroBox) => MacroOverride) {
    const targets = selectedSet.size ? [...selectedSet] : (selectedInst ? [selectedInst] : [])
    if (!targets.length) return
    const next = new Map(overrides)
    for (const name of targets) {
      const cur = sandbox.effectiveMacros.find(m => m.inst_name === name)
      if (cur) next.set(name, fn(cur))
    }
    onOverridesChange(next)
  }

  function stackSelected(axis: 'x' | 'y') {
    const targets = (selectedSet.size ? [...selectedSet] : [])
      .map(n => sandbox.effectiveMacros.find(m => m.inst_name === n))
      .filter((m): m is MacroBox => Boolean(m))
    if (targets.length < 2) return
    const gap = Math.max(0, Number(channelGapUm) || 0)
    targets.sort((a, b) => axis === 'x' ? a.llx - b.llx : a.lly - b.lly)
    const anchor = targets[0]
    let cursor = axis === 'x' ? anchor.llx : anchor.lly
    const next = new Map(overrides)
    for (const m of targets) {
      const [ow, oh] = orientedSize(m.width, m.height, m.orient)
      if (axis === 'x') {
        next.set(m.inst_name, { inst_name: m.inst_name, llx: Number(cursor.toFixed(2)), lly: anchor.lly, orient: m.orient })
        cursor += ow + gap
      } else {
        next.set(m.inst_name, { inst_name: m.inst_name, llx: anchor.llx, lly: Number(cursor.toFixed(2)), orient: m.orient })
        cursor += oh + gap
      }
    }
    onOverridesChange(next)
  }

  function downloadClientExport(format: 'innovus' | 'icc2' | 'csv') {
    const content = generateClientTcl(scene, overrides, format, proposalTitle || 'Designer_Draft')
    const ext = format === 'csv' ? 'csv' : 'tcl'
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${scene.design_name}_${scene.fp_ver}_${format}.${ext}`
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
            <Move size={14} />Designer Memory 배치 툴
          </button>
        </div>

        <div className="fp-layer-chips">
          {(
            [
              ['bistColor', 'BIST 색상'],
              ['notches', 'Pin-1 방향'],
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
        <div className="fp-sandbox-bar" aria-label="Designer Memory 배치 툴바">
          <div className="fp-sandbox-ops">
            <span className="fp-sandbox-badge">
              실측 스케일 배치 ({selectedSet.size || (selectedInst ? 1 : 0)}개 선택 · 변경 {sandbox.changedCount}개)
            </span>
            <label className="fp-inline-field">
              <span>그리드</span>
              <select value={snapUm} onChange={e => setSnapUm(Number(e.target.value))}>
                <option value={0}>자유(0µm)</option>
                <option value={1}>1 µm</option>
                <option value={5}>5 µm</option>
                <option value={10}>10 µm</option>
              </select>
            </label>
            <Button variant="outline" size="sm" disabled={!canWrite} onClick={() => transformSelected(m => ({ inst_name: m.inst_name, llx: m.llx, lly: m.lly, orient: flipOrientX(m.orient) }))} title="좌우 미러링 (MY)">
              <FlipHorizontal2 size={13} />Flip X (MY)
            </Button>
            <Button variant="outline" size="sm" disabled={!canWrite} onClick={() => transformSelected(m => ({ inst_name: m.inst_name, llx: m.llx, lly: m.lly, orient: flipOrientY(m.orient) }))} title="상하 미러링 (MX)">
              <FlipVertical2 size={13} />Flip Y (MX)
            </Button>
            <Button variant="outline" size="sm" disabled={!canWrite} onClick={() => transformSelected(m => ({ inst_name: m.inst_name, llx: m.llx, lly: m.lly, orient: rotateOrient90(m.orient) }))}>
              <RotateCw size={13} />90° 회전
            </Button>
            <label className="fp-inline-field">
              <span>채널 간격(µm)</span>
              <input type="number" min={0} max={200} value={channelGapUm} onChange={e => setChannelGapUm(e.target.value)} />
            </label>
            <Button variant="outline" size="sm" disabled={!canWrite || selectedSet.size < 2} onClick={() => stackSelected('x')}>가로 정렬·배열</Button>
            <Button variant="outline" size="sm" disabled={!canWrite || selectedSet.size < 2} onClick={() => stackSelected('y')}>세로 정렬·배열</Button>
            <Button variant="ghost" size="sm" disabled={!overrides.size} onClick={() => onOverridesChange(new Map())}>
              <RotateCcw size={13} />초기화
            </Button>
          </div>

          <div className="fp-sandbox-export">
            <span className={sandbox.outOfBounds.size || sandbox.overlapping.size ? 'fp-collision-warn' : 'fp-collision-ok'}>
              {sandbox.outOfBounds.size || sandbox.overlapping.size
                ? `경계 이탈 ${sandbox.outOfBounds.size} · 충돌 ${sandbox.overlapping.size}`
                : '물리 경계·충돌 정상'}
            </span>
            {canWrite && (
              <>
                <input
                  type="text"
                  className="fp-proposal-input"
                  placeholder="제안안 이름 (예: Designer안 #1)"
                  value={proposalTitle}
                  onChange={e => setProposalTitle(e.target.value)}
                />
                <Button
                  size="sm"
                  disabled={savingProp || !overrides.size}
                  onClick={async () => {
                    setSavingProp(true)
                    try {
                      await onSaveProposal(proposalTitle.trim() || `Designer 배치안 (${new Date().toLocaleTimeString('ko-KR')})`, '')
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

      <div className="fp-svg-stage">
        <svg
          ref={svgRef}
          viewBox={`${vbX} ${vbY} ${vbW} ${vbH}`}
          className={`fp-svg mode-${tool}`}
          onWheel={onWheel}
          onMouseDown={onCanvasMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={stopDrag}
          onMouseLeave={stopDrag}
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

          {/* Macros / SRAMs / IPs / Subblocks */}
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
            const stroke = hasCollision ? '#dc2626' : (layers.bistColor && bCol ? bCol : style.stroke)
            const fill = hasCollision ? 'rgba(254,202,202,0.85)' : (layers.bistColor && bCol ? `${bCol}26` : style.fill)
            const [hl, hb, hr, ht] = m.halo_um
            const [nx, ny] = pin1Offset(m.width, m.height, m.orient)
            const shortName = m.inst_name.split('/').pop() || m.inst_name
            const fontSize = Math.max(Math.min(ow / Math.max(shortName.length * 0.68, 4), oh * 0.3), Math.max(dw, dh) * 0.009)

            return (
              <g
                key={m.inst_name}
                className="fp-macro-node"
                opacity={dimmed ? 0.28 : 1}
                onMouseDown={e => onMacroMouseDown(e, m)}
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
                  strokeWidth={strokeUnit * (isSelected || isBistFocused ? 2.6 : 1.3)}
                />
                {layers.notches && (
                  <circle
                    cx={m.llx + nx}
                    cy={-(m.lly + ny)}
                    r={Math.max(Math.min(ow, oh) * 0.08, strokeUnit * 2.2)}
                    fill={stroke}
                  />
                )}
                <text
                  x={rx + ow * 0.5}
                  y={ry + oh * 0.54}
                  textAnchor="middle"
                  fontSize={fontSize}
                  fill="#0f172a"
                  pointerEvents="none"
                >
                  {shortName}
                </text>
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
      </div>
    </div>
  )
}
