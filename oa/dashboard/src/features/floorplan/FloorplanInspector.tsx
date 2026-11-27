import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, Plus, Save, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { appUrl } from '@/lib/appUrl'
import {
  BIST_PALETTE,
  evaluateSandboxState,
  orientedSize,
  type ChecklistTemplateItem,
  type ChecklistVerdict,
  type DatapathFlow,
  type FloorplanMetricsData,
  type FloorplanScene,
  type MacroOverride,
  type MetricSlice,
  type PlacementProposal,
  type ReviewPin,
} from './floorplanApi'

interface FloorplanInspectorProps {
  scene: FloorplanScene | null
  isTop: boolean
  selectedInst: string
  onSelectInst: (inst: string) => void
  selectedModule: string
  onSelectModule: (mod: string) => void
  selectedBistGroup: string
  onSelectBistGroup: (grp: string) => void
  overrides: Map<string, MacroOverride>
  onLoadProposalOverrides: (overrides: MacroOverride[]) => void
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
  selectedBistGroup,
  onSelectBistGroup,
  overrides,
  onLoadProposalOverrides,
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
  const [editingTpl, setEditingTpl] = useState(false)
  const [newItemTitle, setNewItemTitle] = useState('')
  const [newItemDesc, setNewItemDesc] = useState('')
  const [dpName, setDpName] = useState('')
  const [dpSequence, setDpSequence] = useState<string[]>([])
  const [savingRev, setSavingRev] = useState(false)

  const liveSandbox = useMemo(() => scene ? evaluateSandboxState(scene, overrides) : null, [scene, overrides])
  const selectedMacro = useMemo(
    () => liveSandbox?.effectiveMacros.find(m => m.inst_name === selectedInst) || null,
    [liveSandbox, selectedInst],
  )

  const activeMetricSlice: { label: string; slice: MetricSlice | null } = useMemo(() => {
    if (!metrics) return { label: '연결된 CSV 없음', slice: null }
    if (selectedModule) {
      const hit = metrics.instances.find(i => i.path === selectedModule || i.name === selectedModule)
      if (hit) return { label: `Instance: ${hit.path}`, slice: hit }
    }
    return { label: '블록 전체 (Root)', slice: metrics.root }
  }, [metrics, selectedModule])

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

  return (
    <aside className="fp-inspector" aria-label="Floorplan 검토 패널">
      <div className="fp-inspector-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'layout'} onClick={() => setTab('layout')}>
          {isTop ? 'Sub-block · IP' : 'BIST · Data-path · Inst'}
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
            <section className="fp-card fp-selected-macro">
              <div className="fp-card-head">
                <strong>{selectedMacro.inst_name}</strong>
                <Badge variant="outline">{selectedMacro.kind.toUpperCase()} · {selectedMacro.orient}</Badge>
              </div>
              <small>
                Master: <code>{selectedMacro.master}</code> · 위치 <code>({selectedMacro.llx}, {selectedMacro.lly}) µm</code> ·
                크기 <code>{orientedSize(selectedMacro.width, selectedMacro.height, selectedMacro.orient).join(' × ')} µm</code>
                {selectedMacro.bist_group && <> · BIST <strong>{selectedMacro.bist_group}</strong></>}
              </small>
            </section>
          )}

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

          <section className="fp-card">
            <div className="fp-card-head">
              <h4>{isTop ? 'Sub-block · Hard IP 목록' : 'Instance별 물리 위치 확인'}</h4>
              {selectedModule && <button type="button" className="fp-link-btn" onClick={() => onSelectModule('')}>선택 해제</button>}
            </div>
            {isTop ? (
              <div className="fp-compact-list">
                {(liveSandbox?.effectiveMacros || []).map(m => (
                  <button
                    key={m.inst_name}
                    type="button"
                    className={`fp-list-row${selectedInst === m.inst_name ? ' is-selected' : ''}`}
                    onClick={() => onSelectInst(m.inst_name)}
                  >
                    <span><strong>{m.inst_name}</strong> <small>({m.master})</small></span>
                    <code>{m.width}×{m.height} µm</code>
                  </button>
                ))}
              </div>
            ) : (
              <div className="fp-compact-list">
                {(scene?.module_regions || []).map(mr => {
                  const active = selectedModule === mr.hier_path
                  return (
                    <button
                      key={mr.hier_path}
                      type="button"
                      className={`fp-list-row${active ? ' is-selected' : ''}`}
                      onClick={() => onSelectModule(active ? '' : mr.hier_path)}
                    >
                      <span><strong>{mr.hier_path}</strong> <small>{mr.cell_count.toLocaleString()} cells</small></span>
                      <code>중심 ({mr.cx}, {mr.cy})</code>
                    </button>
                  )
                })}
                {!(scene?.module_regions.length) && <small className="text-muted">모듈 영역 요약이 없습니다.</small>}
              </div>
            )}
          </section>

          {!isTop && scene && (
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
                <h4>저장된 Designer 배치 제안안 ({proposals.length})</h4>
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
                          <a className="fp-dl-link" href={appUrl(`/api/floorplan/proposals/${prop.id}/export?fmt=innovus`)}>Innovus</a>
                          <a className="fp-dl-link" href={appUrl(`/api/floorplan/proposals/${prop.id}/export?fmt=icc2`)}>ICC2</a>
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
