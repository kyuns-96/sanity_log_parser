import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, ChevronRight, Plus, Sparkles, Trash2 } from 'lucide-react'
import { array, format, object, request, text, unknownSchema, uploadName, type Auth, type Upload } from '../api'
import { Badge, Button, Field } from '../components/workspace'
import type { AtlasBlock, AtlasProject } from '@/features/atlas/atlasContext'
import { cornerSpecId, type CornerSpec, type TrackingOverviewData } from '../trackingApi'
import { formatBlockBaselineVersion, uploadMatchesCorner } from './trackingUtils'

interface BlockTrackingConfigCardProps {
  auth: Auth
  project: AtlasProject
  baselineProject?: AtlasProject
  areaProjectId: string
  baselineAreaProjectId?: string | null
  areaBlocks: AtlasBlock[]
  uploads: Upload[]
  data: TrackingOverviewData | null
  defaultCorners: CornerSpec[]
  initialBlockId?: string
  busy: boolean
  runAction: (fn: () => Promise<void>, success?: string) => Promise<void>
  onReload: () => Promise<void>
}

export function BlockTrackingConfigCard({
  auth,
  project,
  baselineProject,
  areaProjectId,
  baselineAreaProjectId,
  areaBlocks,
  uploads,
  data,
  defaultCorners,
  initialBlockId,
  busy,
  runAction,
  onReload,
}: BlockTrackingConfigCardProps) {
  const [selectedBlockId, setSelectedBlockId] = useState(initialBlockId || '')
  useEffect(() => {
    if (initialBlockId) setSelectedBlockId(initialBlockId)
    else if (!selectedBlockId && areaBlocks.length) setSelectedBlockId(areaBlocks[0].area_id!)
  }, [initialBlockId, areaBlocks])

  const selectedBlock = areaBlocks.find(b => b.area_id === selectedBlockId)
  const blockCfgMap = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const row of data?.block_configs || []) {
      const bid = text(row.block_id)
      if (bid) map.set(bid, row)
    }
    return map
  }, [data?.block_configs])
  const blockCfg = blockCfgMap.get(selectedBlockId)

  const [blockEnabled, setBlockEnabled] = useState(true)
  const [blockKind, setBlockKind] = useState<'previous_project' | 'previous_version'>('previous_project')
  const [baselineBlockId, setBaselineBlockId] = useState('')
  const [baselineNetVer, setBaselineNetVer] = useState('')
  const [baselineVersionId, setBaselineVersionId] = useState('')
  const [blockRecipients, setBlockRecipients] = useState('')
  const [blockCorners, setBlockCorners] = useState<CornerSpec[]>([])
  const [blockCornerName, setBlockCornerName] = useState('')
  const [approvedBaselines, setApprovedBaselines] = useState<Record<string, string>>({})

  const baselineSideProject = blockKind === 'previous_version' ? project : baselineProject
  const effectiveBaselineBlockId = blockKind === 'previous_version' ? selectedBlockId : baselineBlockId
  const selectedBaselineBlock = useMemo(
    () => baselineSideProject?.blocks.find(b => b.area_id === effectiveBaselineBlockId),
    [baselineSideProject, effectiveBaselineBlockId],
  )
  const baselineNetVerOptions = useMemo(
    () => [...new Set((selectedBaselineBlock?.versions || []).filter(v => Boolean(v.area_id)).map(v => v.net_ver))],
    [selectedBaselineBlock],
  )
  const baselineRevisionOptions = useMemo(
    () => (selectedBaselineBlock?.versions || []).filter(v => Boolean(v.area_id) && v.net_ver === baselineNetVer),
    [selectedBaselineBlock, baselineNetVer],
  )

  useEffect(() => {
    const cfg = object(blockCfg)
    const kind = text(cfg.comparison_kind) === 'previous_version' ? 'previous_version' : 'previous_project'
    setBlockEnabled(cfg.enabled !== false)
    setBlockKind(kind)
    const savedBaseBlkId = text(cfg.baseline_block_id)
    const sameNameBase = kind === 'previous_project' && !savedBaseBlkId && selectedBlock
      ? baselineProject?.blocks.find(b => b.area_id && b.name.trim() === selectedBlock.name.trim())?.area_id || ''
      : ''
    const resolvedBlkId = kind === 'previous_version' ? selectedBlockId : (savedBaseBlkId || sameNameBase)
    setBaselineBlockId(kind === 'previous_version' ? '' : resolvedBlkId)

    const sideProj = kind === 'previous_version' ? project : baselineProject
    const bBlk = sideProj?.blocks.find(b => b.area_id === resolvedBlkId)
    const savedVerId = text(cfg.baseline_version_id) || text(object(array(cfg.approved_baselines)[0]).version_id)
    const savedVer = bBlk?.versions.find(v => v.area_id === savedVerId)
    const porVer = !savedVerId && bBlk?.por?.version_id
      ? bBlk.versions.find(v => v.id === bBlk.por?.version_id && Boolean(v.area_id))
      : undefined
    setBaselineNetVer(savedVer?.net_ver || text(object(cfg.baseline_version_info).net_ver) || porVer?.net_ver || '')
    setBaselineVersionId(savedVerId || porVer?.area_id || '')

    setBlockRecipients(array(cfg.recipients).map(text).join(', '))
    setBlockCorners(array(cfg.required_corners).map(item => {
      const o = object(item)
      return {
        label: text(o.label) || text(o.process_label),
        process_label: text(o.process_label) || text(o.label),
        voltage_v: text(o.voltage_v) || null,
        temp_c: text(o.temp_c) || null,
        pex: text(o.pex) || null,
      }
    }))
    const baseMap: Record<string, string> = {}
    for (const item of array(cfg.approved_baselines).map(object)) {
      if (text(item.corner_id) && text(item.upload_id)) baseMap[text(item.corner_id)] = text(item.upload_id)
    }
    setApprovedBaselines(baseMap)
  }, [selectedBlockId, blockCfg?.id, blockCfg?.row_version, baselineProject, project, selectedBlock])

  const effectiveBlockCorners = blockCorners.length ? blockCorners : defaultCorners
  const candidateBaselineUploads = useMemo(() => {
    const targetProjId = blockKind === 'previous_version' ? areaProjectId : baselineAreaProjectId
    const targetBlkId = blockKind === 'previous_version' ? selectedBlockId : baselineBlockId
    return uploads.filter(u =>
      u.state === 'ready'
      && (!targetProjId || u.project_id === targetProjId)
      && (!targetBlkId || u.block_id === targetBlkId)
      && (!baselineVersionId || u.version_id === baselineVersionId),
    )
  }, [uploads, blockKind, areaProjectId, baselineAreaProjectId, selectedBlockId, baselineBlockId, baselineVersionId])

  const [expText, setExpText] = useState('')
  const [expNet, setExpNet] = useState('')
  const [expPath, setExpPath] = useState('')
  const [expMetric, setExpMetric] = useState<'logic_area' | 'pwr_all_total_mw'>('logic_area')
  const [expCond, setExpCond] = useState<'any' | 'typical' | 'same_condition'>('any')
  const [expDir, setExpDir] = useState<'increase' | 'decrease'>('increase')
  const [expMin, setExpMin] = useState('')
  const [expMax, setExpMax] = useState('')
  const [expQuote, setExpQuote] = useState('')

  const blockExpectations = useMemo(
    () => (data?.expectations || []).filter(item => text(item.block_id) === selectedBlockId),
    [data?.expectations, selectedBlockId],
  )

  function addBlockCorner() {
    const name = blockCornerName.trim()
    if (!name) return
    setBlockCorners(rows => [...rows, { label: name, process_label: name, voltage_v: null, temp_c: null, pex: null }])
    setBlockCornerName('')
  }

  async function saveBlockSettings() {
    if (!selectedBlockId) throw new Error('설정할 블록을 선택하세요.')
    const resolvedMap: Record<string, string> = {}
    for (const c of effectiveBlockCorners) {
      const cid = cornerSpecId(c)
      const explicit = approvedBaselines[cid]
      const validExplicit = explicit && candidateBaselineUploads.some(u => u.id === explicit) ? explicit : ''
      if (validExplicit) {
        resolvedMap[cid] = validExplicit
      } else if (baselineVersionId) {
        const autoMatch = candidateBaselineUploads.find(u => uploadMatchesCorner(u, c))
        if (autoMatch) resolvedMap[cid] = autoMatch.id
      }
    }
    const baselines = Object.entries(resolvedMap).map(([corner_id, upload_id]) => ({ corner_id, upload_id }))
    const existingCfg = object(blockCfg)
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/config`, unknownSchema, {
      enabled: blockEnabled,
      comparison_kind: blockKind,
      baseline_project_id: blockKind === 'previous_version' ? areaProjectId : (baselineAreaProjectId || null),
      baseline_block_id: blockKind === 'previous_version' ? selectedBlockId : (baselineBlockId || null),
      baseline_version_id: baselineVersionId || null,
      design_stage: text(existingCfg.design_stage) || null,
      required_corners: blockCorners,
      recipients: blockRecipients.split(/[,;\s]+/).filter(Boolean),
      approved_baselines: baselines,
      approved_mapping_id: text(existingCfg.approved_mapping_id) || null,
      no_change_net_vers: array(existingCfg.no_change_net_vers).map(text).filter(Boolean),
      ...(blockCfg?.row_version ? { expected_row_version: Number(blockCfg.row_version) } : {}),
    })
    await onReload()
  }

  async function saveExpectation() {
    if (!selectedBlockId) throw new Error('블록을 먼저 선택하세요.')
    if (!expPath.trim()) throw new Error('대상 instance 경로를 입력하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/expectations`, unknownSchema, {
      effective_net_ver: expNet.trim() || null,
      target_paths: [expPath.trim()],
      scope: 'subtree',
      metric: expMetric,
      comparison_condition: expCond,
      direction: expDir,
      min_pct: expMin.trim() ? expMin.trim() : null,
      max_pct: expMax.trim() ? expMax.trim() : null,
      quote: expQuote.trim(),
    })
    setExpPath(''); setExpMin(''); setExpMax(''); setExpQuote('')
    await onReload()
  }

  async function proposeExpectation() {
    if (!selectedBlockId) throw new Error('블록을 먼저 선택하세요.')
    if (!expText.trim()) throw new Error('설계 변경 원문을 입력하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/expectations/propose`, unknownSchema, {
      request_id: crypto.randomUUID(),
      effective_net_ver: expNet.trim() || null,
      source_text: expText.trim(),
    })
    await onReload()
  }

  return (
    <section className="panel" aria-label="블록별 비교 기준 및 담당자">
      <div className="section-heading">
        <div>
          <h2>블록별 비교 기준 (NET_VER · REVISION) · 담당자</h2>
          <p>블록 행을 선택해 기준 블록, 기준 NET_VER·REVISION, 결과 메일 담당자를 설정하세요. 선택한 기준 버전의 코너 CSV가 자동으로 연결됩니다.</p>
        </div>
      </div>

      <div className="table-scroll settings-block-table">
        <table>
          <thead>
            <tr>
              <th>블록</th>
              <th>기준 블록</th>
              <th>기준 NET_VER · REVISION</th>
              <th>담당자 메일</th>
              <th>자동 분석</th>
            </tr>
          </thead>
          <tbody>
            {areaBlocks.map(block => {
              const bid = block.area_id!
              const cfg = object(blockCfgMap.get(bid))
              const isSelected = bid === selectedBlockId
              const isFc = block.name.trim() === project.name.trim()
              const kind = text(cfg.comparison_kind)
              const baseBlk = baselineProject?.blocks.find(b => b.area_id === text(cfg.baseline_block_id))
              const baseLabel = kind === 'previous_version'
                ? '같은 블록 이전 버전'
                : baseBlk ? `${baselineProject?.name} · ${baseBlk.name}` : '미지정'
              const baseVerLabel = formatBlockBaselineVersion(cfg, bid, project, baselineProject)
              const recips = array(cfg.recipients).map(text).filter(Boolean)
              const isEnabled = cfg.enabled !== false
              return (
                <tr key={bid} className={isSelected ? 'is-selected' : ''} onClick={() => setSelectedBlockId(bid)}>
                  <td>
                    <strong>{block.name}</strong>
                    {isFc && <span className="side-tag">Full-chip</span>}
                  </td>
                  <td className={baseLabel === '미지정' ? 'text-muted' : ''}>{baseLabel}</td>
                  <td className={baseVerLabel === '미지정' ? 'text-muted' : 'tracking-version-tag'}>{baseVerLabel}</td>
                  <td className={!recips.length ? 'text-muted' : ''}>{recips.length ? recips.join(', ') : '미지정'}</td>
                  <td><Badge value={isEnabled ? '사용' : '제외'} /></td>
                </tr>
              )
            })}
            {!areaBlocks.length && (
              <tr><td colSpan={5} className="text-muted">연결된 블록이 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {selectedBlock && (
        <div className="settings-block-editor">
          <div className="section-heading">
            <h3>
              {selectedBlock.name}
              {selectedBlock.name.trim() === project.name.trim() ? ' (Full-chip)' : ''} 기준 설정
            </h3>
            <label className="checkbox">
              <input type="checkbox" checked={blockEnabled} disabled={!project.can_write} onChange={e => setBlockEnabled(e.target.checked)} />
              <span>이 블록 자동 분석 사용</span>
            </label>
          </div>

          <div className="tracking-settings-grid">
            <Field label="비교 방식">
              <select
                value={blockKind}
                disabled={!project.can_write}
                onChange={e => {
                  const nextKind = e.target.value as 'previous_project' | 'previous_version'
                  setBlockKind(nextKind); setBaselineNetVer(''); setBaselineVersionId(''); setApprovedBaselines({})
                }}
              >
                <option value="previous_project">기준 프로젝트 ({baselineProject?.name || '미설정'})</option>
                <option value="previous_version">같은 프로젝트의 이전 버전</option>
              </select>
            </Field>
            {blockKind === 'previous_project' && (
              <Field label="기준 블록">
                <select
                  value={baselineBlockId}
                  disabled={!project.can_write || !baselineProject}
                  onChange={e => {
                    const nextBlkId = e.target.value
                    setBaselineBlockId(nextBlkId)
                    const nextBlk = baselineProject?.blocks.find(b => b.area_id === nextBlkId)
                    const porVer = nextBlk?.por?.version_id
                      ? nextBlk.versions.find(v => v.id === nextBlk.por?.version_id && Boolean(v.area_id))
                      : undefined
                    setBaselineNetVer(porVer?.net_ver || '')
                    setBaselineVersionId(porVer?.area_id || '')
                    setApprovedBaselines({})
                  }}
                >
                  <option value="">기준 블록 선택</option>
                  {(baselineProject?.blocks || []).filter(b => Boolean(b.area_id)).map(b => (
                    <option key={b.area_id!} value={b.area_id!}>{b.name}</option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="기준 NET_VER">
              <select
                value={baselineNetVer}
                disabled={!project.can_write || !selectedBaselineBlock}
                onChange={e => {
                  const nextNet = e.target.value
                  setBaselineNetVer(nextNet)
                  const matching = (selectedBaselineBlock?.versions || []).filter(v => Boolean(v.area_id) && v.net_ver === nextNet)
                  const porInNet = matching.find(v => v.id === selectedBaselineBlock?.por?.version_id)
                  setBaselineVersionId(porInNet?.area_id || (matching.length === 1 ? matching[0].area_id! : ''))
                  setApprovedBaselines({})
                }}
              >
                <option value="">기준 NET_VER 선택</option>
                {baselineNetVerOptions.map(nv => <option key={nv} value={nv}>{nv}</option>)}
              </select>
            </Field>
            <Field label="기준 REVISION · ECO">
              <select
                value={baselineVersionId}
                disabled={!project.can_write || !baselineNetVer}
                onChange={e => { setBaselineVersionId(e.target.value); setApprovedBaselines({}) }}
              >
                <option value="">기준 REVISION 선택</option>
                {baselineRevisionOptions.map(v => (
                  <option key={v.area_id!} value={v.area_id!}>{[v.revision, v.layout, v.eco_num].filter(Boolean).join(' · ')}{v.id === selectedBaselineBlock?.por?.version_id ? ' (PoR)' : ''}</option>
                ))}
              </select>
            </Field>
            <Field label="블록 담당자 메일 (쉼표 구분)">
              <input type="text" value={blockRecipients} disabled={!project.can_write} onChange={e => setBlockRecipients(e.target.value)} placeholder="owner@company.com" />
            </Field>
          </div>

          {effectiveBlockCorners.length > 0 && (
            <div className="tracking-subblock">
              <h3>기준 버전의 코너 매칭 상태</h3>
              <div className="tracking-corner-chips">
                {effectiveBlockCorners.map(c => {
                  const cid = cornerSpecId(c)
                  const pinned = candidateBaselineUploads.find(u => u.id === approvedBaselines[cid])
                  const matched = pinned || (baselineVersionId ? candidateBaselineUploads.find(u => uploadMatchesCorner(u, c)) : undefined)
                  return (
                    <span key={cid} className={`tracking-chip ${matched ? 'status-completed' : 'status-waiting_upload'}`}>
                      {matched && <CheckCircle2 size={12} aria-hidden="true" />}
                      {c.label}: {matched ? uploadName(matched) : baselineVersionId ? '해당 버전에 CSV 없음 (도착 시 자동 매칭)' : '기준 NET_VER·REVISION을 선택하세요'}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          {effectiveBlockCorners.length > 0 && candidateBaselineUploads.length > 1 && (
            <details className="settings-advanced">
              <summary>
                <ChevronRight size={15} className="chevron" aria-hidden="true" />
                <span>코너별 기준 파일 직접 지정 (같은 코너에 파일이 여러 개일 때)</span>
                <small>{candidateBaselineUploads.length}개 후보</small>
              </summary>
              <div className="settings-advanced-body">
                <div className="tracking-settings-grid">
                  {effectiveBlockCorners.map(c => {
                    const cid = cornerSpecId(c)
                    return (
                      <Field key={cid} label={`${c.label} 기준 파일`}>
                        <select
                          value={approvedBaselines[cid] || ''}
                          disabled={!project.can_write}
                          onChange={e => setApprovedBaselines(prev => ({ ...prev, [cid]: e.target.value }))}
                        >
                          <option value="">선택한 기준 버전에서 자동 매칭</option>
                          {candidateBaselineUploads.map(u => (
                            <option key={u.id} value={u.id}>{uploadName(u)}</option>
                          ))}
                        </select>
                      </Field>
                    )
                  })}
                </div>
              </div>
            </details>
          )}

          <details className="settings-advanced">
            <summary>
              <ChevronRight size={15} className="chevron" aria-hidden="true" />
              <span>이 블록 전용 필수 코너</span>
              <small>{blockCorners.length ? `전용 코너 ${blockCorners.length}개` : `프로젝트 기본 코너 상속 (${defaultCorners.map(c => c.label).join(', ') || '없음'})`}</small>
            </summary>
            <div className="settings-advanced-body">
              <div className="tracking-chip-list">
                {blockCorners.map((c, idx) => (
                  <span key={cornerSpecId(c)} className="tracking-chip">
                    {c.label}
                    {project.can_write && (
                      <button type="button" onClick={() => setBlockCorners(rows => rows.filter((_, i) => i !== idx))}>
                        <Trash2 size={12} />
                      </button>
                    )}
                  </span>
                ))}
                {!blockCorners.length && <small className="text-muted">비워 두면 프로젝트 기본 필수 코너를 그대로 사용합니다.</small>}
              </div>
              {project.can_write && (
                <div className="settings-corner-add">
                  <input
                    placeholder="블록 전용 코너명 (예: SS)"
                    value={blockCornerName}
                    onChange={e => setBlockCornerName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addBlockCorner() } }}
                  />
                  <Button variant="outline" size="sm" onClick={addBlockCorner}>
                    <Plus size={14} />추가
                  </Button>
                </div>
              )}
            </div>
          </details>

          <details className="settings-advanced">
            <summary>
              <ChevronRight size={15} className="chevron" aria-hidden="true" />
              <span>설계 변경 메모 · 예상 증감</span>
              <small>{blockExpectations.length}건 등록됨</small>
            </summary>
            <div className="settings-advanced-body">
              <p className="muted">미리 등록해 둔 예상 증감 범위 안의 변화는 분석 시 자동으로 ‘설명됨’으로 분류합니다.</p>
              <ul className="tracking-mini-list">
                {blockExpectations.map(exp => (
                  <li key={text(exp.id)}>
                    <span>
                      <strong>{array(exp.target_paths).map(text).join(', ')}</strong> · {exp.metric === 'logic_area' ? 'Logic Area' : 'Total Power'} · {exp.direction === 'increase' ? '증가' : '감소'}{' '}
                      {exp.min_pct != null || exp.max_pct != null ? `${format(exp.min_pct ?? 0)}~${format(exp.max_pct ?? '?')}%` : '(방향만)'}
                      {text(exp.effective_net_ver) ? ` · ${text(exp.effective_net_ver)}부터` : ''}
                      {text(exp.quote) ? ` · "${text(exp.quote)}"` : ''}
                    </span>
                  </li>
                ))}
                {!blockExpectations.length && <li className="muted">등록된 예상 변화가 없습니다.</li>}
              </ul>

              {project.can_write && (
                <div className="tracking-expectation-editor">
                  <div className="tracking-inline-add">
                    <input placeholder="적용 시작 버전 (선택)" value={expNet} onChange={e => setExpNet(e.target.value)} />
                    <input placeholder="instance 경로 (예: GPU/u_cache)" value={expPath} onChange={e => setExpPath(e.target.value)} />
                    <select value={expMetric} onChange={e => setExpMetric(e.target.value as 'logic_area' | 'pwr_all_total_mw')}>
                      <option value="logic_area">Logic Area</option>
                      <option value="pwr_all_total_mw">Total Power</option>
                    </select>
                    <select value={expCond} onChange={e => setExpCond(e.target.value as 'any' | 'typical' | 'same_condition')}>
                      <option value="any">모든 조건</option>
                      <option value="typical">대표 조건</option>
                      <option value="same_condition">동일 조건</option>
                    </select>
                    <select value={expDir} onChange={e => setExpDir(e.target.value as 'increase' | 'decrease')}>
                      <option value="increase">증가</option>
                      <option value="decrease">감소</option>
                    </select>
                    <input placeholder="최소 %" value={expMin} onChange={e => setExpMin(e.target.value)} />
                    <input placeholder="최대 %" value={expMax} onChange={e => setExpMax(e.target.value)} />
                    <input placeholder="사유 메모" value={expQuote} onChange={e => setExpQuote(e.target.value)} />
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => runAction(saveExpectation, '예상 변화를 등록했습니다.')}>
                      <Plus size={14} />등록
                    </Button>
                  </div>
                  <div className="tracking-ai-propose">
                    <textarea rows={2} value={expText} onChange={e => setExpText(e.target.value)} placeholder="Design info 변경 메모를 붙여넣으면 중앙 VWP AI가 예상 증감 범위를 추출해 제안합니다." />
                    <Button variant="outline" size="sm" disabled={busy || !expText.trim()} onClick={() => runAction(proposeExpectation, 'AI 예상 범위 제안을 요청했습니다.')}>
                      <Sparkles size={14} />AI 추출 요청
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </details>

          <div className="form-actions">
            <Button size="sm" disabled={busy || !project.can_write} onClick={() => runAction(saveBlockSettings, '블록 기준 설정을 저장했습니다.')}>
              {selectedBlock.name} 설정 저장
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}
