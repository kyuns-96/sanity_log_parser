import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, FolderInput, Lock, Pencil, Plus, RotateCcw, Save, Trash2, Unlock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { appUrl } from '@/lib/appUrl'
import {
  BIST_PALETTE,
  DEF_TO_ICC2,
  evaluateSandboxState,
  orientedSize,
  type ChecklistTemplateItem,
  type ChecklistVerdict,
  type DatapathFlow,
  type EffectiveMacroBox,
  type FloorplanMetricsData,
  type FloorplanScene,
  type MacroBox,
  type MacroOverride,
  type MetricSlice,
  type Orient,
  type PlacementProposal,
  type ReviewPin,
} from './floorplanApi'
import type { ModuleLayer } from './moduleContours'
import {
  initMemoryGrouping,
  isMemory,
  memoryGroupStats,
  memoryMoves,
  nextGroupColor,
  nextGroupName,
  UNGROUPED_LABEL,
  type ColorBy,
  type MemoryGrouping,
  type MemoryGroupStat,
} from './memoryGroups'

interface FloorplanInspectorProps {
  scene: FloorplanScene | null
  isTop: boolean
  selectedInst: string
  onSelectInst: (inst: string) => void
  selectedModule: string
  onSelectModule: (mod: string) => void
  moduleLayers: ModuleLayer[]
  onToggleModuleLayer: (path: string) => void
  onClearModuleLayers: () => void
  selectedBistGroup: string
  onSelectBistGroup: (grp: string) => void
  selectedSet: Set<string>
  onSelectedSetChange: (next: Set<string>) => void
  onColorByChange: (next: ColorBy) => void
  memoryGrouping: MemoryGrouping | null
  onMemoryGroupingChange: (next: MemoryGrouping) => void
  overrides: Map<string, MacroOverride>
  onOverridesChange: (next: Map<string, MacroOverride>) => void
  onEnsureSandboxMode: () => void
  onLoadProposalOverrides: (overrides: MacroOverride[]) => void
  onlyChangedTcl: boolean
  proposals: PlacementProposal[]
  metrics: FloorplanMetricsData | null
  availableUploads: { id: string; original_filename: string; process_label: string }[]
  selectedUploadId: string
  onSelectUploadId: (id: string) => void
  overallVerdict: 'PENDING' | 'PASS' | 'WARN' | 'ACTION'
  onOverallVerdictChange: (v: 'PENDING' | 'PASS' | 'WARN' | 'ACTION') => void
  summaryNote: string
  onSummaryNoteChange: (note: string) => void
  checklistTemplate: ChecklistTemplateItem[]
  onSaveChecklistTemplate: (items: ChecklistTemplateItem[]) => Promise<void>
  checklistVerdicts: ChecklistVerdict[]
  onChecklistVerdictsChange: (items: ChecklistVerdict[]) => void
  reviewPins: ReviewPin[]
  onReviewPinsChange: (pins: ReviewPin[]) => void
  datapaths: DatapathFlow[]
  onDatapathsChange: (dps: DatapathFlow[]) => void
  onSaveReview: () => Promise<void>
  canWrite: boolean
  demo: boolean
}

const ALL_ORIENTS: Orient[] = ['N', 'FN', 'S', 'FS', 'W', 'E', 'FW', 'FE']

const KIND_LABELS: Record<MacroBox['kind'], string> = {
  sram: 'SRAM',
  ip: 'Hard IP',
  macro: 'Macro',
  subblock: 'Sub-block',
}

function num(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function fmtMetric(v: unknown, suffix = ''): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return `${String(v)}${suffix}`
  return `${n.toLocaleString('en-US', { maximumFractionDigits: 2 })}${suffix}`
}

export function FloorplanInspector({
  scene,
  isTop,
  selectedInst,
  onSelectInst,
  selectedModule,
  onSelectModule,
  moduleLayers,
  onToggleModuleLayer,
  onClearModuleLayers,
  selectedBistGroup,
  onSelectBistGroup,
  selectedSet,
  onSelectedSetChange,
  onColorByChange,
  memoryGrouping,
  onMemoryGroupingChange,
  overrides,
  onOverridesChange,
  onEnsureSandboxMode,
  onLoadProposalOverrides,
  onlyChangedTcl,
  proposals,
  metrics,
  availableUploads,
  selectedUploadId,
  onSelectUploadId,
  overallVerdict,
  onOverallVerdictChange,
  summaryNote,
  onSummaryNoteChange,
  checklistTemplate,
  onSaveChecklistTemplate,
  checklistVerdicts,
  onChecklistVerdictsChange,
  reviewPins,
  onReviewPinsChange,
  datapaths,
  onDatapathsChange,
  onSaveReview,
  canWrite,
  demo,
}: FloorplanInspectorProps) {
  const [tab, setTab] = useState<'layout' | 'metrics' | 'review'>('layout')
  const [macroKindFilter, setMacroKindFilter] = useState<'all' | MacroBox['kind'] | 'changed'>('all')
  const [macroQuery, setMacroQuery] = useState('')
  const [editingTpl, setEditingTpl] = useState(false)
  const [newItemTitle, setNewItemTitle] = useState('')
  const [newItemDesc, setNewItemDesc] = useState('')
  const [dpName, setDpName] = useState('')
  const [dpSequence, setDpSequence] = useState<string[]>([])
  const [savingRev, setSavingRev] = useState(false)

  const liveSandbox = useMemo(() => scene ? evaluateSandboxState(scene, overrides) : null, [scene, overrides])
  const selectedMacro: EffectiveMacroBox | null = useMemo(
    () => liveSandbox?.effectiveMacros.find(m => m.inst_name === selectedInst) || null,
    [liveSandbox, selectedInst],
  )

  const presentKinds = useMemo(() => {
    const s = new Set<MacroBox['kind']>()
    for (const m of scene?.macros || []) s.add(m.kind)
    return (['subblock', 'sram', 'ip', 'macro'] as const).filter(k => s.has(k))
  }, [scene?.macros])

  const filteredMacros = useMemo(() => {
    const list = liveSandbox?.effectiveMacros || []
    const q = macroQuery.trim().toLowerCase()
    return list.filter(m => {
      if (macroKindFilter === 'changed' && !m.changed) return false
      if (macroKindFilter !== 'all' && macroKindFilter !== 'changed' && m.kind !== macroKindFilter) return false
      if (selectedBistGroup && m.bist_group !== selectedBistGroup) return false
      if (q && !m.inst_name.toLowerCase().includes(q) && !m.master.toLowerCase().includes(q)) return false
      return true
    })
  }, [liveSandbox?.effectiveMacros, macroKindFilter, macroQuery, selectedBistGroup])

  function updateSingleMacro(m: EffectiveMacroBox, patch: Partial<MacroOverride>) {
    onEnsureSandboxMode()
    const next = new Map(overrides)
    const prev = next.get(m.inst_name)
    next.set(m.inst_name, {
      inst_name: m.inst_name,
      llx: m.llx,
      lly: m.lly,
      orient: m.orient,
      halo_um: prev?.halo_um ?? m.halo_um,
      locked: m.locked,
      ...patch,
    })
    onOverridesChange(next)
  }

  function resetSingleMacro(instName: string) {
    const next = new Map(overrides)
    next.delete(instName)
    onOverridesChange(next)
  }

  const activeMetricSlice: { label: string; slice: MetricSlice | null } = useMemo(() => {
    if (!metrics) return { label: '연결된 CSV 없음', slice: null }
    if (selectedModule) {
      const hit = metrics.instances.find(i => i.path === selectedModule || i.name === selectedModule)
      if (hit) return { label: `Instance: ${hit.path}`, slice: hit }
    }
    return { label: '블록 전체 (Root)', slice: metrics.root }
  }, [metrics, selectedModule])

  const [editingGroup, setEditingGroup] = useState<{ id: string; name: string } | null>(null)
  const memoryStats = useMemo(
    () => (memoryGrouping && liveSandbox && scene ? memoryGroupStats(memoryGrouping, liveSandbox.effectiveMacros, scene.die_bbox) : []),
    [memoryGrouping, liveSandbox, scene],
  )
  const memoryChanges = useMemo(() => (memoryGrouping ? memoryMoves(memoryGrouping) : []), [memoryGrouping])
  const ungroupedMemories = useMemo(
    () => (memoryGrouping && liveSandbox ? liveSandbox.effectiveMacros.filter(m => isMemory(m) && !memoryGrouping.assign[m.inst_name]) : []),
    [memoryGrouping, liveSandbox],
  )
  const selectedMemoryNames = useMemo(() => {
    const names = selectedSet.size ? [...selectedSet] : selectedInst ? [selectedInst] : []
    return names.filter(n => memoryGrouping && n in memoryGrouping.assign)
  }, [selectedSet, selectedInst, memoryGrouping])

  function showMemoryMembers(names: string[]) {
    onSelectedSetChange(new Set(names))
    onSelectInst(names[0] || '')
    onColorByChange('memgroup')
  }

  function moveSelectedMemories(groupId: string | null) {
    if (!memoryGrouping || !selectedMemoryNames.length) return
    const assign = { ...memoryGrouping.assign }
    for (const name of selectedMemoryNames) assign[name] = groupId
    onMemoryGroupingChange({ ...memoryGrouping, assign })
    onColorByChange('memgroup')
  }

  function addMemoryGroup() {
    if (!memoryGrouping) return
    const id = `mg-u${Date.now().toString(36)}`
    const group = { id, name: nextGroupName(memoryGrouping.groups), color: nextGroupColor(memoryGrouping.groups), origin: 'user' as const }
    const assign = { ...memoryGrouping.assign }
    for (const name of selectedMemoryNames) assign[name] = id
    onMemoryGroupingChange({ ...memoryGrouping, groups: [...memoryGrouping.groups, group], assign })
    setEditingGroup({ id, name: group.name })
    onColorByChange('memgroup')
  }

  function commitGroupName() {
    if (!memoryGrouping || !editingGroup) return
    const name = editingGroup.name.trim()
    if (name) {
      onMemoryGroupingChange({ ...memoryGrouping, groups: memoryGrouping.groups.map(g => (g.id === editingGroup.id ? { ...g, name } : g)) })
    }
    setEditingGroup(null)
  }

  function deleteMemoryGroup(groupId: string) {
    if (!memoryGrouping) return
    const assign = { ...memoryGrouping.assign }
    for (const [name, gid] of Object.entries(assign)) if (gid === groupId) assign[name] = null
    onMemoryGroupingChange({ ...memoryGrouping, groups: memoryGrouping.groups.filter(g => g.id !== groupId), assign })
  }

  function memoryGroupMeta(st: MemoryGroupStat) {
    const origin = st.group.origin === 'bist' ? 'BIST' : st.group.origin === 'hier' ? '계층' : '사용자'
    return `${st.members.length}개 · 최대 이격 ${st.maxSpanUm} µm · ${origin}`
  }

  const verdictMap = useMemo(() => new Map(checklistVerdicts.map(v => [v.item_id, v])), [checklistVerdicts])

  function updateChecklistItem(item: ChecklistTemplateItem, patch: Partial<ChecklistVerdict>) {
    const cur = verdictMap.get(item.id) || { item_id: item.id, title: item.title, verdict: 'PENDING' as const, comment: '' }
    const updated = { ...cur, title: item.title, ...patch }
    const rest = checklistVerdicts.filter(v => v.item_id !== item.id)
    onChecklistVerdictsChange([...rest, updated])
  }

  function addDatapathFlow() {
    if (!scene || dpSequence.length < 2) return
    const waypoints = dpSequence.map(token => {
      const [kind, id] = token.split(':', 2)
      if (kind === 'pin') {
        const p = scene.pins.find(x => x.name === id)
        return p ? { label: p.name, x: p.x, y: p.y } : null
      }
      if (kind === 'mod') {
        const mr = scene.module_regions.find(x => x.hier_path === id)
        return mr ? { label: mr.hier_path, x: mr.cx, y: mr.cy } : null
      }
      if (kind === 'mac') {
        const mac = liveSandbox?.effectiveMacros.find(x => x.inst_name === id)
        if (!mac) return null
        const [ow, oh] = orientedSize(mac.width, mac.height, mac.orient)
        return { label: mac.inst_name.split('/').pop() || mac.inst_name, x: Number((mac.llx + ow * 0.5).toFixed(1)), y: Number((mac.lly + oh * 0.5).toFixed(1)) }
      }
      const b = liveSandbox?.liveBist.find(x => x.bist_group === id)
      return b ? { label: b.bist_group, x: Number(((b.bbox_um[0] + b.bbox_um[2]) / 2).toFixed(1)), y: Number(((b.bbox_um[1] + b.bbox_um[3]) / 2).toFixed(1)) } : null
    }).filter((w): w is { label: string; x: number; y: number } => Boolean(w))
    if (waypoints.length < 2) return
    onDatapathsChange([
      ...datapaths,
      { id: `dp-${Date.now()}`, name: dpName.trim() || `Path #${datapaths.length + 1}`, color: '#2563eb', waypoints, note: '' },
    ])
    setDpName('')
    setDpSequence([])
  }

  const activeItems = checklistTemplate.filter(i => i.active)
  const vth = activeMetricSlice.slice?.vth || {}
  const slvt = num(vth.vt_slvt_pct), lvt = num(vth.vt_lvt_pct), rvt = num(vth.vt_rvt_pct), hvt = num(vth.vt_hvt_pct)
  const drv = activeMetricSlice.slice?.drive || {}
  const drvBins = Object.entries(drv.bins || {})
  const onlyChangedQs = onlyChangedTcl ? '&only_changed=true' : ''

  return (
    <aside className="fp-inspector" aria-label="Floorplan 검토 패널">
      <div className="fp-inspector-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'layout'} onClick={() => setTab('layout')}>
          {isTop ? 'Hard Macro · Sub-block' : 'Hard Macro · BIST · Path'}
        </button>
        <button type="button" role="tab" aria-selected={tab === 'metrics'} onClick={() => setTab('metrics')}>
          Area · LVT · Drive
        </button>
        <button type="button" role="tab" aria-selected={tab === 'review'} onClick={() => setTab('review')}>
          체크리스트 · 핀 ({reviewPins.length})
        </button>
      </div>

      {tab === 'layout' && (
        <div className="fp-tab-body">
          {selectedMacro && (
            <section className="fp-card fp-selected-macro" aria-label="선택 Hard Macro 배치 편집">
              <div className="fp-card-head">
                <strong>{selectedMacro.inst_name}</strong>
                <div className="fp-row-btns">
                  {selectedMacro.changed && <Badge variant="secondary">수정됨 Δ</Badge>}
                  <Badge variant="outline">{KIND_LABELS[selectedMacro.kind]} · {selectedMacro.orient} ({DEF_TO_ICC2[selectedMacro.orient]})</Badge>
                </div>
              </div>
              <small>
                Master: <code>{selectedMacro.master}</code> · 크기 <code>{orientedSize(selectedMacro.width, selectedMacro.height, selectedMacro.orient).join(' × ')} µm</code>
                {selectedMacro.bist_group && <> · BIST <strong>{selectedMacro.bist_group}</strong></>}
              </small>
              {selectedMacro.attributes && Object.keys(selectedMacro.attributes).length > 0 && (
                <div className="fp-layer-chips">
                  {Object.entries(selectedMacro.attributes).map(([k, v]) => (
                    <Badge key={k} variant="outline">{k}: {String(v)}</Badge>
                  ))}
                </div>
              )}

              {canWrite ? (
                <div className="fp-macro-editor">
                  <div className="fp-macro-edit-grid">
                    <label>
                      <span>LLX (µm)</span>
                      <input
                        type="number"
                        step="0.5"
                        disabled={selectedMacro.locked}
                        value={selectedMacro.llx}
                        onChange={e => updateSingleMacro(selectedMacro, { llx: Number(e.target.value) || 0 })}
                      />
                    </label>
                    <label>
                      <span>LLY (µm)</span>
                      <input
                        type="number"
                        step="0.5"
                        disabled={selectedMacro.locked}
                        value={selectedMacro.lly}
                        onChange={e => updateSingleMacro(selectedMacro, { lly: Number(e.target.value) || 0 })}
                      />
                    </label>
                    <label>
                      <span>방향 (Orient)</span>
                      <select
                        disabled={selectedMacro.locked}
                        value={selectedMacro.orient}
                        onChange={e => updateSingleMacro(selectedMacro, { orient: e.target.value as Orient })}
                      >
                        {ALL_ORIENTS.map(o => <option key={o} value={o}>{o} ({DEF_TO_ICC2[o]})</option>)}
                      </select>
                    </label>
                  </div>

                  <div className="fp-halo-edit-row">
                    <span>Halo L/B/R/T (µm)</span>
                    <div className="fp-halo-inputs">
                      {(['L', 'B', 'R', 'T'] as const).map((side, idx) => (
                        <input
                          key={side}
                          type="number"
                          min={0}
                          step="1"
                          disabled={selectedMacro.locked}
                          aria-label={`Halo ${side}`}
                          title={`Halo ${side} (µm)`}
                          value={selectedMacro.halo_um[idx]}
                          onChange={e => {
                            const nextHalo = [...selectedMacro.halo_um] as [number, number, number, number]
                            nextHalo[idx] = Math.max(0, Number(e.target.value) || 0)
                            updateSingleMacro(selectedMacro, { halo_um: nextHalo })
                          }}
                        />
                      ))}
                    </div>
                  </div>

                  <div className="fp-macro-edit-actions">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => updateSingleMacro(selectedMacro, { locked: !selectedMacro.locked })}
                    >
                      {selectedMacro.locked ? <><Unlock size={12} />잠금 해제</> : <><Lock size={12} />위치 잠금</>}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={!overrides.has(selectedMacro.inst_name)}
                      onClick={() => resetSingleMacro(selectedMacro.inst_name)}
                    >
                      <RotateCcw size={12} />원위치 복원
                    </Button>
                  </div>
                </div>
              ) : (
                <small>좌표: <code>({selectedMacro.llx}, {selectedMacro.lly}) µm</code> · Halo: <code>{selectedMacro.halo_um.join('/')} µm</code></small>
              )}
            </section>
          )}

          {/* Unified Hard Macro List (Available in BOTH TOP and Sub-block) */}
          <section className="fp-card">
            <div className="fp-card-head">
              <h4>전체 Hard Macro 목록 ({filteredMacros.length}/{liveSandbox?.effectiveMacros.length || 0})</h4>
              {liveSandbox && liveSandbox.changedCount > 0 && (
                <span className="fp-pill is-ok">변경 {liveSandbox.changedCount}개</span>
              )}
            </div>
            <div className="fp-macro-filter-bar">
              <div className="fp-layer-chips">
                <button
                  type="button"
                  className={`fp-chip-toggle${macroKindFilter === 'all' ? ' is-on' : ''}`}
                  onClick={() => setMacroKindFilter('all')}
                >
                  전체
                </button>
                {presentKinds.map(k => (
                  <button
                    key={k}
                    type="button"
                    className={`fp-chip-toggle${macroKindFilter === k ? ' is-on' : ''}`}
                    onClick={() => setMacroKindFilter(k)}
                  >
                    {KIND_LABELS[k]}
                  </button>
                ))}
                {(liveSandbox?.changedCount || 0) > 0 && (
                  <button
                    type="button"
                    className={`fp-chip-toggle${macroKindFilter === 'changed' ? ' is-on' : ''}`}
                    onClick={() => setMacroKindFilter('changed')}
                  >
                    변경됨 Δ
                  </button>
                )}
              </div>
              <input
                type="text"
                className="fp-macro-search"
                placeholder="Hard Macro 이름 / 마스터 검색…"
                value={macroQuery}
                onChange={e => setMacroQuery(e.target.value)}
              />
            </div>

            <div className="fp-compact-list">
              {filteredMacros.map(m => (
                <button
                  key={m.inst_name}
                  type="button"
                  className={`fp-list-row${selectedInst === m.inst_name ? ' is-selected' : ''}`}
                  onClick={() => onSelectInst(m.inst_name)}
                >
                  <span className="fp-macro-row-title">
                    <span className={`fp-kind-tag kind-${m.kind}`}>{KIND_LABELS[m.kind]}</span>
                    <strong>{m.inst_name}</strong>
                    <small>({m.master})</small>
                    {m.locked && <small title="잠금">🔒</small>}
                    {m.changed && <span className="fp-delta-mark">Δ</span>}
                  </span>
                  <code>({m.llx}, {m.lly}) · {m.orient}</code>
                </button>
              ))}
              {!filteredMacros.length && <small className="text-muted">조건에 맞는 Hard Macro가 없습니다.</small>}
            </div>
          </section>

          {!isTop && liveSandbox && liveSandbox.liveBist.length > 0 && (
            <section className="fp-card">
              <div className="fp-card-head">
                <h4>Memory BIST Group 검토</h4>
                {selectedBistGroup && (
                  <button type="button" className="fp-link-btn" onClick={() => onSelectBistGroup('')}>필터 해제</button>
                )}
              </div>
              <div className="fp-bist-list">
                {liveSandbox.liveBist.map((b, idx) => {
                  const active = selectedBistGroup === b.bist_group
                  return (
                    <button
                      key={b.bist_group}
                      type="button"
                      className={`fp-bist-item${active ? ' is-selected' : ''}`}
                      onClick={() => onSelectBistGroup(active ? '' : b.bist_group)}
                    >
                      <span className="fp-color-dot" style={{ background: BIST_PALETTE[idx % BIST_PALETTE.length] }} />
                      <div className="fp-bist-meta">
                        <strong>{b.bist_group}</strong>
                        <small>{b.member_count}개 SRAM · 최대 이격 {b.max_manhattan_span_um} µm</small>
                      </div>
                      {b.dispersed_warning ? (
                        <span className="fp-pill is-warn"><AlertTriangle size={12} />분산 주의</span>
                      ) : (
                        <span className="fp-pill is-ok"><CheckCircle2 size={12} />응집 양호</span>
                      )}
                    </button>
                  )
                })}
              </div>
            </section>
          )}

          {!isTop && memoryGrouping && Object.keys(memoryGrouping.assign).length > 0 && (
            <section className="fp-card">
              <div className="fp-card-head">
                <h4>Data Memory 그룹 ({memoryGrouping.groups.length})</h4>
                <div className="fp-row-btns">
                  {canWrite && <button type="button" className="fp-link-btn" onClick={addMemoryGroup}><Plus size={12} />새 그룹</button>}
                  {memoryChanges.length > 0 && scene && (
                    <button type="button" className="fp-link-btn" onClick={() => onMemoryGroupingChange(initMemoryGrouping(scene))}>원래대로</button>
                  )}
                </div>
              </div>
              {canWrite && (
                <p className="fp-mg-hint">
                  {selectedMemoryNames.length
                    ? <>선택한 메모리 <b>{selectedMemoryNames.length}</b>개 — 그룹의 <FolderInput size={12} aria-hidden="true" /> 버튼으로 옮기거나 새 그룹을 만드세요.</>
                    : '도면에서 메모리를 선택(Shift+클릭·영역 드래그)한 뒤 그룹으로 옮길 수 있습니다.'}
                </p>
              )}
              <div className="fp-bist-list fp-mg-list">
                {memoryStats.map(st => {
                  const editing = editingGroup?.id === st.group.id
                  const canMoveHere = canWrite && selectedMemoryNames.some(n => memoryGrouping.assign[n] !== st.group.id)
                  return (
                    <div key={st.group.id} className="fp-mg-row">
                      {editing ? (
                        <div className="fp-mg-main">
                          <span className="fp-color-dot" style={{ background: st.group.color }} />
                          <input
                            className="fp-mg-name-input"
                            autoFocus
                            value={editingGroup.name}
                            maxLength={60}
                            aria-label="그룹 이름"
                            onChange={e => setEditingGroup({ id: st.group.id, name: e.target.value })}
                            onBlur={commitGroupName}
                            onKeyDown={e => {
                              if (e.key === 'Enter') commitGroupName()
                              if (e.key === 'Escape') setEditingGroup(null)
                            }}
                          />
                        </div>
                      ) : (
                        <button type="button" className="fp-mg-main" title="멤버를 도면에서 선택" onClick={() => showMemoryMembers(st.members.map(m => m.inst_name))}>
                          <span className="fp-color-dot" style={{ background: st.group.color }} />
                          <span className="fp-bist-meta">
                            <strong>{st.group.name}</strong>
                            <small>{memoryGroupMeta(st)}</small>
                          </span>
                          {(st.movedIn > 0 || st.movedOut > 0) && (
                            <span className="fp-mg-delta" title="처음 그룹 대비 들어온/나간 메모리">{st.movedIn > 0 && `+${st.movedIn}`}{st.movedIn > 0 && st.movedOut > 0 && ' '}{st.movedOut > 0 && `−${st.movedOut}`}</span>
                          )}
                          {st.dispersed && <span className="fp-pill is-warn"><AlertTriangle size={12} />분산</span>}
                        </button>
                      )}
                      {canWrite && !editing && (
                        <div className="fp-mg-actions">
                          <button type="button" className="fp-icon-btn" disabled={!canMoveHere} title="선택한 메모리를 이 그룹으로" aria-label={`선택한 메모리를 ${st.group.name}(으)로`} onClick={() => moveSelectedMemories(st.group.id)}><FolderInput size={14} /></button>
                          <button type="button" className="fp-icon-btn" title="이름 변경" aria-label={`${st.group.name} 이름 변경`} onClick={() => setEditingGroup({ id: st.group.id, name: st.group.name })}><Pencil size={13} /></button>
                          <button type="button" className="fp-icon-btn" title="그룹 삭제 (멤버는 미분류로)" aria-label={`${st.group.name} 삭제`} onClick={() => deleteMemoryGroup(st.group.id)}><Trash2 size={13} /></button>
                        </div>
                      )}
                    </div>
                  )
                })}
                {(ungroupedMemories.length > 0 || (canWrite && selectedMemoryNames.some(n => memoryGrouping.assign[n]))) && (
                  <div className="fp-mg-row is-ungrouped">
                    <button type="button" className="fp-mg-main" disabled={!ungroupedMemories.length} onClick={() => showMemoryMembers(ungroupedMemories.map(m => m.inst_name))}>
                      <span className="fp-color-dot fp-dot-empty" />
                      <span className="fp-bist-meta"><strong>{UNGROUPED_LABEL}</strong><small>{ungroupedMemories.length}개</small></span>
                    </button>
                    {canWrite && (
                      <div className="fp-mg-actions">
                        <button type="button" className="fp-icon-btn" disabled={!selectedMemoryNames.some(n => memoryGrouping.assign[n])} title="선택한 메모리를 그룹에서 빼기" aria-label="선택한 메모리를 미분류로" onClick={() => moveSelectedMemories(null)}><FolderInput size={14} /></button>
                      </div>
                    )}
                  </div>
                )}
              </div>
              {memoryChanges.length > 0 && (
                <details className="fp-mg-moves">
                  <summary>그룹 변경 {memoryChanges.length}건</summary>
                  <ul>
                    {memoryChanges.map(mv => (
                      <li key={mv.inst_name}>
                        <code title={mv.inst_name}>{mv.inst_name.split('/').pop()}</code>
                        <span>{mv.from}</span><span aria-hidden="true">→</span><strong>{mv.to}</strong>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          )}

          {!isTop && (
            <section className="fp-card">
              <div className="fp-card-head">
                <h4>Instance별 물리 위치 확인 (Std-cell 계층){moduleLayers.length > 0 && <span className="fp-mod-count">{moduleLayers.length}개 표시</span>}</h4>
                {moduleLayers.length > 0 && <button type="button" className="fp-link-btn" onClick={onClearModuleLayers}>모두 해제</button>}
              </div>
              <div className="fp-compact-list">
                {(scene?.module_regions || []).map(mr => {
                  const layer = moduleLayers.find(l => l.path === mr.hier_path)
                  const focused = Boolean(layer && selectedModule === mr.hier_path)
                  return (
                    <button
                      key={mr.hier_path}
                      type="button"
                      aria-pressed={Boolean(layer)}
                      className={`fp-list-row fp-mod-row${layer ? ' is-on' : ''}${focused ? ' is-selected' : ''}`}
                      style={layer ? { borderColor: layer.color } : undefined}
                      onClick={() => onToggleModuleLayer(mr.hier_path)}
                      title={layer ? '도면에서 숨기기' : '도면에 표시 (여러 개 동시 표시 가능)'}
                    >
                      <span className="fp-mod-name">
                        <i className="fp-mod-swatch" style={layer ? { background: layer.color, borderColor: layer.color } : undefined} aria-hidden="true" />
                        <strong>{mr.hier_path}</strong> <small>{mr.cell_count.toLocaleString()} cells</small>
                      </span>
                      <code>중심 ({mr.cx}, {mr.cy})</code>
                    </button>
                  )
                })}
                {!(scene?.module_regions.length) && <small className="text-muted">모듈 영역 요약이 없습니다.</small>}
              </div>
            </section>
          )}

          {scene && (
            <section className="fp-card">
              <div className="fp-card-head">
                <h4>Data-path 경로선 검토 ({datapaths.length})</h4>
              </div>
              {datapaths.map(dp => (
                <div key={dp.id} className="fp-dp-row">
                  <span><strong>{dp.name}</strong>: {dp.waypoints.map(w => w.label).join(' → ')}</span>
                  {canWrite && (
                    <button type="button" className="fp-icon-btn" onClick={() => onDatapathsChange(datapaths.filter(x => x.id !== dp.id))} aria-label="경로 삭제">
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
              ))}
              {canWrite && (
                <div className="fp-dp-builder">
                  <div className="fp-dp-row">
                    <input type="text" placeholder="경로 이름 (예: Main Pipe)" value={dpName} onChange={e => setDpName(e.target.value)} />
                    <select
                      value=""
                      onChange={e => { if (e.target.value) setDpSequence(prev => [...prev, e.target.value]) }}
                    >
                      <option value="">+ 경유 지점 추가</option>
                      {scene.pins.slice(0, 30).map(p => <option key={`pin:${p.name}`} value={`pin:${p.name}`}>Pin: {p.name}</option>)}
                      {(liveSandbox?.effectiveMacros || []).map(m => <option key={`mac:${m.inst_name}`} value={`mac:${m.inst_name}`}>Macro: {m.inst_name}</option>)}
                      {scene.module_regions.map(m => <option key={`mod:${m.hier_path}`} value={`mod:${m.hier_path}`}>Inst: {m.hier_path}</option>)}
                      {scene.bist_summary.map(b => <option key={`bist:${b.bist_group}`} value={`bist:${b.bist_group}`}>BIST: {b.bist_group}</option>)}
                    </select>
                    <Button variant="outline" size="sm" disabled={dpSequence.length < 2} onClick={addDatapathFlow}>추가</Button>
                  </div>
                  {dpSequence.length > 0 && (
                    <small className="fp-dp-draft">
                      선택 순서: {dpSequence.map(s => s.split(':')[1]).join(' → ')}{' '}
                      <button type="button" className="fp-link-btn" onClick={() => setDpSequence([])}>비우기</button>
                    </small>
                  )}
                </div>
              )}
            </section>
          )}

          {proposals.length > 0 && (
            <section className="fp-card">
              <div className="fp-card-head">
                <h4>저장된 Hard Macro 배치 제안안 ({proposals.length})</h4>
              </div>
              <div className="fp-compact-list">
                {proposals.map(prop => (
                  <div key={prop.id} className="fp-proposal-row">
                    <div>
                      <strong>{prop.title}</strong>
                      <small>변경 {prop.changed_count}개 · 충돌 {prop.overlap_count}건</small>
                    </div>
                    <div className="fp-row-btns">
                      <Button variant="outline" size="sm" onClick={() => onLoadProposalOverrides(prop.overrides)}>캔버스 적용</Button>
                      {!demo && (
                        <>
                          <a className="fp-dl-link" href={appUrl(`/api/floorplan/proposals/${prop.id}/export?fmt=innovus${onlyChangedQs}`)}>Innovus</a>
                          <a className="fp-dl-link" href={appUrl(`/api/floorplan/proposals/${prop.id}/export?fmt=icc2${onlyChangedQs}`)}>ICC2</a>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {tab === 'metrics' && (
        <div className="fp-tab-body">
          <section className="fp-card">
            <div className="fp-card-head">
              <strong>{activeMetricSlice.label}</strong>
              {availableUploads.length > 0 && (
                <select className="fp-upload-select" value={selectedUploadId} onChange={e => onSelectUploadId(e.target.value)}>
                  {availableUploads.map(u => <option key={u.id} value={u.id}>{u.process_label} · {u.original_filename}</option>)}
                </select>
              )}
            </div>
            {metrics && metrics.instances.length > 0 && (
              <select className="fp-inst-select" value={selectedModule} onChange={e => onSelectModule(e.target.value)}>
                <option value="">블록 전체 (Root 요약)</option>
                {metrics.instances.map(inst => (
                  <option key={inst.path} value={inst.path}>{'  '.repeat(Math.max(0, inst.depth - 1))}{inst.path}</option>
                ))}
              </select>
            )}
          </section>

          {activeMetricSlice.slice ? (
            <>
              <section className="fp-card">
                <h4>Area · Gate Count</h4>
                <div className="fp-metric-grid">
                  <div><span>Total Area</span><strong>{fmtMetric(activeMetricSlice.slice.area.total_area, ' µm²')}</strong></div>
                  <div><span>Logic Area</span><strong>{fmtMetric(activeMetricSlice.slice.area.logic_area, ' µm²')}</strong></div>
                  <div><span>SRAM Area</span><strong>{fmtMetric(activeMetricSlice.slice.area.sram_area, ' µm²')}</strong></div>
                  <div><span>CTS 비중</span><strong>{fmtMetric(activeMetricSlice.slice.area.cts_area_pct, '%')}</strong></div>
                  {activeMetricSlice.slice.area.gate_count != null && (
                    <div className="col-span-2"><span>환산 Gate Count</span><strong>{fmtMetric(activeMetricSlice.slice.area.gate_count, ' G')}</strong></div>
                  )}
                </div>
              </section>

              <section className="fp-card">
                <div className="fp-card-head">
                  <h4>Vth (LVT Ratio) 분포</h4>
                  <strong>SLVT+LVT {(slvt + lvt).toFixed(1)}%</strong>
                </div>
                <div className="fp-vth-bar" aria-label="Vth 비율 바">
                  {slvt > 0 && <span style={{ width: `${slvt}%`, background: '#dc2626' }} title={`SLVT ${slvt}%`} />}
                  {lvt > 0 && <span style={{ width: `${lvt}%`, background: '#f59e0b' }} title={`LVT ${lvt}%`} />}
                  {rvt > 0 && <span style={{ width: `${rvt}%`, background: '#2563eb' }} title={`RVT ${rvt}%`} />}
                  {hvt > 0 && <span style={{ width: `${hvt}%`, background: '#16a34a' }} title={`HVT ${hvt}%`} />}
                </div>
                <div className="fp-vth-legend">
                  <span><i style={{ background: '#dc2626' }} />SLVT {fmtMetric(vth.vt_slvt_pct, '%')}</span>
                  <span><i style={{ background: '#f59e0b' }} />LVT {fmtMetric(vth.vt_lvt_pct, '%')}</span>
                  <span><i style={{ background: '#2563eb' }} />RVT {fmtMetric(vth.vt_rvt_pct, '%')}</span>
                  <span><i style={{ background: '#16a34a' }} />HVT {fmtMetric(vth.vt_hvt_pct, '%')}</span>
                </div>
              </section>

              <section className="fp-card">
                <h4>Drive Strength 분포</h4>
                <div className="fp-metric-grid cols-3">
                  <div><span>sub-1x (&lt;D1)</span><strong>{fmtMetric(drv.drv_sub1x_pct, '%')}</strong></div>
                  <div><span>D1 – D3</span><strong>{fmtMetric(drv.drv_d1_d3_pct, '%')}</strong></div>
                  <div><span>D4+ 고구동</span><strong>{fmtMetric(drv.drv_d4_plus_pct, '%')}</strong></div>
                </div>
                {drvBins.length > 0 && (
                  <div className="fp-drive-bars">
                    {drvBins.map(([bin, pct]) => {
                      const val = num(pct)
                      return (
                        <div key={bin} className="fp-drive-row">
                          <code>{bin}</code>
                          <div className="fp-drive-track"><span style={{ width: `${Math.min(100, val * 2)}%` }} /></div>
                          <span>{fmtMetric(pct, '%')}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </section>
            </>
          ) : (
            <p className="text-muted">이 블록에 연결된 Area/Power CSV가 있으면 Area · LVT · Drive 분포가 자동 표시됩니다.</p>
          )}
        </div>
      )}

      {tab === 'review' && (
        <div className="fp-tab-body">
          <section className="fp-card">
            <div className="fp-card-head">
              <h4>종합 판정 · 코멘트</h4>
              {canWrite && (
                <Button
                  size="sm"
                  disabled={savingRev}
                  onClick={async () => {
                    setSavingRev(true)
                    try { await onSaveReview() } finally { setSavingRev(false) }
                  }}
                >
                  <Save size={13} />리뷰 저장
                </Button>
              )}
            </div>
            <div className="fp-verdict-pills">
              {(['PENDING', 'PASS', 'WARN', 'ACTION'] as const).map(v => (
                <button
                  key={v}
                  type="button"
                  disabled={!canWrite}
                  className={`fp-verdict-btn is-${v.toLowerCase()}${overallVerdict === v ? ' is-active' : ''}`}
                  onClick={() => onOverallVerdictChange(v)}
                >
                  {v}
                </button>
              ))}
            </div>
            <textarea
              rows={2}
              disabled={!canWrite}
              placeholder="종합 리뷰 요약 및 후속 조치 메모"
              value={summaryNote}
              onChange={e => onSummaryNoteChange(e.target.value)}
            />
          </section>

          <section className="fp-card">
            <div className="fp-card-head">
              <h4>검토 체크리스트 ({activeItems.length})</h4>
              {canWrite && (
                <button type="button" className="fp-link-btn" onClick={() => setEditingTpl(v => !v)}>
                  {editingTpl ? '항목 편집 닫기' : '+ 항목 추가·관리'}
                </button>
              )}
            </div>

            {editingTpl && canWrite && (
              <div className="fp-tpl-editor">
                <div className="fp-dp-row">
                  <input type="text" placeholder="새 체크리스트 항목명" value={newItemTitle} onChange={e => setNewItemTitle(e.target.value)} />
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!newItemTitle.trim()}
                    onClick={() => {
                      const next: ChecklistTemplateItem[] = [
                        ...checklistTemplate,
                        { id: `custom_${Date.now()}`, title: newItemTitle.trim(), description: newItemDesc.trim(), active: true },
                      ]
                      setNewItemTitle(''); setNewItemDesc('')
                      void onSaveChecklistTemplate(next)
                    }}
                  >
                    <Plus size={13} />추가
                  </Button>
                </div>
                <input type="text" placeholder="항목 설명 (선택)" value={newItemDesc} onChange={e => setNewItemDesc(e.target.value)} />
                <div className="fp-tpl-toggle-list">
                  {checklistTemplate.map(item => (
                    <label key={item.id} className="fp-tpl-check">
                      <input
                        type="checkbox"
                        checked={item.active}
                        onChange={e => {
                          const next = checklistTemplate.map(x => x.id === item.id ? { ...x, active: e.target.checked } : x)
                          void onSaveChecklistTemplate(next)
                        }}
                      />
                      <span>{item.title}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div className="fp-checklist-rows">
              {activeItems.map(item => {
                const cur = verdictMap.get(item.id)
                const v = cur?.verdict || 'PENDING'
                return (
                  <div key={item.id} className="fp-check-item">
                    <div className="fp-check-top">
                      <div>
                        <strong>{item.title}</strong>
                        {item.description && <small>{item.description}</small>}
                      </div>
                      <select
                        value={v}
                        disabled={!canWrite}
                        onChange={e => updateChecklistItem(item, { verdict: e.target.value as ChecklistVerdict['verdict'] })}
                      >
                        <option value="PENDING">미검토</option>
                        <option value="PASS">PASS</option>
                        <option value="WARN">WARN</option>
                        <option value="ACTION">ACTION</option>
                        <option value="N/A">N/A</option>
                      </select>
                    </div>
                    <input
                      type="text"
                      disabled={!canWrite}
                      placeholder="검토 근거 / 코멘트"
                      value={cur?.comment || ''}
                      onChange={e => updateChecklistItem(item, { comment: e.target.value })}
                    />
                  </div>
                )
              })}
            </div>
          </section>

          <section className="fp-card">
            <h4>도면 핀 코멘트 ({reviewPins.length})</h4>
            {reviewPins.length === 0 ? (
              <small className="text-muted">상단 ‘핀 코멘트 찍기’ 모드로 도면 위 위치를 클릭해 추가하세요.</small>
            ) : (
              <div className="fp-pin-list">
                {reviewPins.map(pin => (
                  <div key={pin.id} className={`fp-pin-item${pin.resolved ? ' is-resolved' : ''}`}>
                    <div className="fp-check-top">
                      <span>
                        <strong>#{pin.number}</strong>{' '}
                        <Badge variant="outline">{pin.severity}</Badge>{' '}
                        <code>({pin.x}, {pin.y})</code>
                        {pin.carried_from_fp_ver && <small> · {pin.carried_from_fp_ver} 이월</small>}
                      </span>
                      {canWrite && (
                        <div className="fp-row-btns">
                          <label className="fp-tpl-check">
                            <input
                              type="checkbox"
                              checked={pin.resolved}
                              onChange={e => onReviewPinsChange(reviewPins.map(p => p.id === pin.id ? { ...p, resolved: e.target.checked } : p))}
                            />
                            <span>해결</span>
                          </label>
                          <button
                            type="button"
                            className="fp-icon-btn"
                            onClick={() => onReviewPinsChange(reviewPins.filter(p => p.id !== pin.id).map((p, idx) => ({ ...p, number: idx + 1 })))}
                            aria-label="핀 삭제"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      )}
                    </div>
                    <input
                      type="text"
                      disabled={!canWrite}
                      value={pin.title}
                      onChange={e => onReviewPinsChange(reviewPins.map(p => p.id === pin.id ? { ...p, title: e.target.value } : p))}
                      placeholder="이슈 제목"
                    />
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </aside>
  )
}
