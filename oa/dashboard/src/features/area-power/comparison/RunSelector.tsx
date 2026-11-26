import { useEffect, useMemo, useState } from 'react'
import { cornerKey, formatCornerLabel, isAreaOnlyUpload, uploadName, type Upload } from '../api'
import { Button, Field } from '../components/workspace'
import type { AtlasProject } from '@/features/atlas/atlasContext'
import {
  formatEcoOptionLabel,
  formatVersionLabel,
  groupVersionsByRevision,
  pickPreferredVersion,
  revisionGroupKey,
} from '@/lib/versionUtils'
import { versionEcoNumbers } from '../tracking/trackingUtils'
import { emptySelection, type Selection } from './hierarchyUtils'

interface RunSelectorProps {
  label: string
  project: AtlasProject
  selection: Selection
  onChange: (value: Selection) => void
  uploads: Upload[]
  busy: boolean
  onPrepare: (selection: Selection, previous?: string | null) => void
  lockedBlock?: boolean
  hint?: string
}

export function RunSelector({ label, project, selection, onChange, uploads, busy, onPrepare, lockedBlock = false, hint }: RunSelectorProps) {
  const block = project.blocks.find(row => row.id === selection.block)
  const version = block?.versions.find(row => row.id === selection.version && row.net_ver === selection.net)
  const [selectedRevKey, setSelectedRevKey] = useState('')
  const [previous, setPrevious] = useState('')

  useEffect(() => setPrevious(''), [project.id, selection.block, selection.version])
  useEffect(() => {
    if (version) setSelectedRevKey(revisionGroupKey(version))
    else if (!selection.net) setSelectedRevKey('')
  }, [selection.block, selection.net, version?.id])

  const netVersions = useMemo(
    () => (block?.versions || []).filter(row => row.net_ver === selection.net),
    [block, selection.net],
  )
  const revisionGroups = useMemo(() => groupVersionsByRevision(netVersions), [netVersions])

  const activeRevKey = version ? revisionGroupKey(version) : (revisionGroups.some(g => g.key === selectedRevKey) ? selectedRevKey : '')
  const ecoOptions = useMemo(
    () => revisionGroups.find(g => g.key === activeRevKey)?.versions || [],
    [revisionGroups, activeRevKey],
  )

  const needsLineage = version && !Object.hasOwn(version, 'previous_version_id')
  const corners = new Map<string, string>()
  for (const upload of uploads) if (!corners.has(cornerKey(upload))) corners.set(cornerKey(upload), formatCornerLabel(upload))
  const areaOnlySingleCorner = corners.size === 1 && uploads.length > 0 && uploads.every(isAreaOnlyUpload)
    ? [...corners.keys()][0]
    : ''
  useEffect(() => {
    if (areaOnlySingleCorner && !selection.corner) {
      onChange({ ...selection, corner: areaOnlySingleCorner, upload: '' })
    }
  }, [areaOnlySingleCorner, selection.block, selection.net, selection.version, selection.corner])
  const effectiveCorner = selection.corner || areaOnlySingleCorner
  const runs = effectiveCorner ? uploads.filter(row => cornerKey(row) === effectiveCorner) : []

  return <fieldset className="run-selector"><legend>{label} · {project.name}</legend><div className="run-fields">
    <Field label={`${label} Block`}><select value={selection.block} disabled={lockedBlock} onChange={event => { setSelectedRevKey(''); onChange({ ...emptySelection, block: event.target.value }); setPrevious('') }}><option value="">Block 선택</option>{project.blocks.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
    <Field label={`${label} NET_VER`}><select value={selection.net} disabled={!block} onChange={event => { setSelectedRevKey(''); onChange({ ...selection, net: event.target.value, version: '', corner: '', upload: '' }); setPrevious('') }}><option value="">NET_VER 선택</option>{[...new Set(block?.versions.map(row => row.net_ver) || [])].map(value => <option key={value}>{value}</option>)}</select></Field>
    <Field label={`${label} REVISION`}><select value={activeRevKey} disabled={!block || !selection.net} onChange={event => {
      const nextKey = event.target.value
      setSelectedRevKey(nextKey)
      setPrevious('')
      const group = revisionGroups.find(g => g.key === nextKey)
      const autoVer = pickPreferredVersion(group?.versions || [], block?.por?.version_id)?.id || ''
      onChange({ ...selection, version: autoVer, corner: '', upload: '' })
    }}><option value="">REVISION 선택</option>{revisionGroups.map(group => <option key={group.key} value={group.key}>{group.label}</option>)}</select></Field>
    <Field label={`${label} ECO_NUM`}><select value={selection.version} disabled={!block || !selection.net || !activeRevKey} onChange={event => { onChange({ ...selection, version: event.target.value, corner: '', upload: '' }); setPrevious('') }}><option value="">ECO_NUM 선택</option>{ecoOptions.map(row => <option key={row.id} value={row.id}>{formatEcoOptionLabel(versionEcoNumbers(row), row.id === block?.por?.version_id)}</option>)}</select></Field>
    <Field label={`${label} corner`}><select value={effectiveCorner} disabled={!version?.area_id} onChange={event => onChange({ ...selection, corner: event.target.value, upload: '' })}><option value="">corner 선택</option>{[...corners].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label={`${label} 실행 파일`} className="run-file"><select value={selection.upload} disabled={!effectiveCorner} onChange={event => onChange({ ...selection, corner: effectiveCorner, upload: event.target.value })}><option value="">실행 파일 선택</option>{runs.map(row => <option key={row.id} value={row.id}>{uploadName(row)}</option>)}</select></Field>
  </div>
    {block && (!block.area_id || version && !version.area_id) && <div className="scope-preparation"><small>선택한 정확한 범위의 수치 연결이 필요합니다. 다른 범위 파일로 대체하지 않습니다.</small>{needsLineage && <Field label={`${label} 명시적 이전 버전`}><select value={previous} onChange={event => setPrevious(event.target.value)}><option value="">계보를 선택하세요</option><option value="none">이 버전에서 계보 시작</option>{block.versions.filter(row => row.id !== version.id).map(row => <option key={row.id} value={row.id}>{formatVersionLabel(row)}</option>)}</select></Field>}<Button variant="outline" disabled={busy || !project.can_write || !!needsLineage && !previous} onClick={() => onPrepare(selection, needsLineage ? previous === 'none' ? null : previous : undefined)}>선택 범위 연결</Button></div>}
    {version?.area_id && !uploads.length && <small>선택한 버전에 준비된 CSV가 없습니다. 아래 업로드 이력의 메타데이터로 PC CLI 업로드를 준비하세요.</small>}
    {hint && <small>{hint}</small>}
  </fieldset>
}
