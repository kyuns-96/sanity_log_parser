import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, ChevronRight } from 'lucide-react'
import { array, object, request, text, unknownSchema, uploadName, type Auth, type Upload } from '../api'
import { Badge, Button, Field } from '../components/workspace'
import type { AtlasBlock, AtlasProject } from '@/features/atlas/atlasContext'
import {
  formatEcoOptionLabel,
  groupVersionsByRevision,
  pickPreferredVersion,
  revisionGroupKey,
} from '@/lib/versionUtils'
import { cornerSpecId, type CornerSpec, type TrackingOverviewData } from '../trackingApi'
import { BlockExpectationsSection } from './BlockExpectationsSection'
import { CornerChipListEditor } from './CornerChipListEditor'
import { formatBlockBaselineVersion, parseCornerSpecList, uploadMatchesCorner, versionEcoNumbers } from './trackingUtils'

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
  const [baselineRevKey, setBaselineRevKey] = useState('')
  const [baselineVersionId, setBaselineVersionId] = useState('')
  const [blockRecipients, setBlockRecipients] = useState('')
  const [blockCorners, setBlockCorners] = useState<CornerSpec[]>([])
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
  const baselineRevisionGroups = useMemo(
    () => groupVersionsByRevision((selectedBaselineBlock?.versions || []).filter(ver => Boolean(ver.area_id) && ver.net_ver === baselineNetVer)),
    [selectedBaselineBlock, baselineNetVer],
  )
  const selectedBaselineVersion = useMemo(
    () => (selectedBaselineBlock?.versions || []).find(v => v.area_id === baselineVersionId),
    [selectedBaselineBlock, baselineVersionId],
  )
  const activeBaselineRevKey = selectedBaselineVersion
    ? revisionGroupKey(selectedBaselineVersion)
    : (baselineRevisionGroups.some(g => g.key === baselineRevKey) ? baselineRevKey : '')
  const baselineEcoOptions = useMemo(
    () => baselineRevisionGroups.find(g => g.key === activeBaselineRevKey)?.versions || [],
    [baselineRevisionGroups, activeBaselineRevKey],
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
    const targetVer = savedVer || porVer
    setBaselineNetVer(targetVer?.net_ver || text(object(cfg.baseline_version_info).net_ver) || '')
    setBaselineRevKey(targetVer ? revisionGroupKey(targetVer) : '')
    setBaselineVersionId(savedVerId || porVer?.area_id || '')

    setBlockRecipients(array(cfg.recipients).map(text).join(', '))
    setBlockCorners(parseCornerSpecList(cfg.required_corners))
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
                  setBlockKind(nextKind); setBaselineNetVer(''); setBaselineRevKey(''); setBaselineVersionId(''); setApprovedBaselines({})
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
                    setBaselineRevKey(porVer ? revisionGroupKey(porVer) : '')
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
                  const autoVer = pickPreferredVersion(matching, selectedBaselineBlock?.por?.version_id)
                  setBaselineRevKey(autoVer ? revisionGroupKey(autoVer) : '')
                  setBaselineVersionId(autoVer?.area_id || '')
                  setApprovedBaselines({})
                }}
              >
                <option value="">기준 NET_VER 선택</option>
                {baselineNetVerOptions.map(nv => <option key={nv} value={nv}>{nv}</option>)}
              </select>
            </Field>
            <Field label="기준 REVISION">
              <select
                value={activeBaselineRevKey}
                disabled={!project.can_write || !baselineNetVer}
                onChange={e => {
                  const nextKey = e.target.value
                  setBaselineRevKey(nextKey)
                  const group = baselineRevisionGroups.find(g => g.key === nextKey)
                  const autoVer = pickPreferredVersion(group?.versions || [], selectedBaselineBlock?.por?.version_id)
                  setBaselineVersionId(autoVer?.area_id || '')
                  setApprovedBaselines({})
                }}
              >
                <option value="">기준 REVISION 선택</option>
                {baselineRevisionGroups.map(group => (
                  <option key={group.key} value={group.key}>{group.label}</option>
                ))}
              </select>
            </Field>
            <Field label="기준 ECO_NUM">
              <select
                value={baselineVersionId}
                disabled={!project.can_write || !baselineNetVer || !activeBaselineRevKey}
                onChange={e => { setBaselineVersionId(e.target.value); setApprovedBaselines({}) }}
              >
                <option value="">기준 ECO_NUM 선택</option>
                {baselineEcoOptions.map(v => (
                  <option key={v.area_id!} value={v.area_id!}>
                    {formatEcoOptionLabel(versionEcoNumbers(v), v.id === selectedBaselineBlock?.por?.version_id)}
                  </option>
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
              <CornerChipListEditor
                corners={blockCorners}
                onChange={setBlockCorners}
                canWrite={project.can_write}
                emptyText="비워 두면 프로젝트 기본 필수 코너를 그대로 사용합니다."
                placeholder="블록 전용 코너명 (예: SS)"
              />
            </div>
          </details>

          <BlockExpectationsSection
            auth={auth}
            areaProjectId={areaProjectId}
            selectedBlockId={selectedBlockId}
            canWrite={project.can_write}
            expectations={data?.expectations || []}
            busy={busy}
            runAction={runAction}
            onReload={onReload}
          />

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
