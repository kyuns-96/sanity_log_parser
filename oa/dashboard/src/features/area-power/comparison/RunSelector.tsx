import { useEffect, useState } from 'react'
import { cornerKey, uploadName, type Upload } from '../api'
import { Button, Field } from '../components/workspace'
import type { AtlasProject } from '@/features/atlas/atlasContext'
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
  const [previous, setPrevious] = useState('')
  useEffect(() => setPrevious(''), [project.id, selection.block, selection.version])
  const needsLineage = version && !Object.hasOwn(version, 'previous_version_id')
  const corners = new Map<string, string>()
  for (const upload of uploads) if (!corners.has(cornerKey(upload))) corners.set(cornerKey(upload), `${upload.process_label ?? '?'} · ${upload.voltage_v ?? '?'} V · ${upload.temp_c ?? '?'} °C · ${upload.pex ?? '?'}`)
  const runs = selection.corner ? uploads.filter(row => cornerKey(row) === selection.corner) : []
  return <fieldset className="run-selector"><legend>{label} · {project.name}</legend><div className="run-fields">
    <Field label={`${label} Block`}><select value={selection.block} disabled={lockedBlock} onChange={event => { onChange({ ...emptySelection, block: event.target.value }); setPrevious('') }}><option value="">Block 선택</option>{project.blocks.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
    <Field label={`${label} NET_VER`}><select value={selection.net} disabled={!block} onChange={event => { onChange({ ...selection, net: event.target.value, version: '', corner: '', upload: '' }); setPrevious('') }}><option value="">NET_VER 선택</option>{[...new Set(block?.versions.map(row => row.net_ver) || [])].map(value => <option key={value}>{value}</option>)}</select></Field>
    <Field label={`${label} REVISION`}><select value={selection.version} disabled={!block || !selection.net} onChange={event => { onChange({ ...selection, version: event.target.value, corner: '', upload: '' }); setPrevious('') }}><option value="">REVISION 선택</option>{block?.versions.filter(row => row.net_ver === selection.net).map(row => <option key={row.id} value={row.id}>{[row.revision, row.layout, row.eco_num].filter(Boolean).join(' · ')}{row.id === block.por?.version_id ? ' (PoR)' : ''}</option>)}</select></Field>
    <Field label={`${label} corner`}><select value={selection.corner} disabled={!version?.area_id} onChange={event => onChange({ ...selection, corner: event.target.value, upload: '' })}><option value="">corner 선택</option>{[...corners].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label={`${label} 실행 파일`} className="run-file"><select value={selection.upload} disabled={!selection.corner} onChange={event => onChange({ ...selection, upload: event.target.value })}><option value="">실행 파일 선택</option>{runs.map(row => <option key={row.id} value={row.id}>{uploadName(row)}</option>)}</select></Field>
  </div>
    {block && (!block.area_id || version && !version.area_id) && <div className="scope-preparation"><small>선택한 정확한 범위의 수치 연결이 필요합니다. 다른 범위 파일로 대체하지 않습니다.</small>{needsLineage && <Field label={`${label} 명시적 이전 버전`}><select value={previous} onChange={event => setPrevious(event.target.value)}><option value="">계보를 선택하세요</option><option value="none">이 버전에서 계보 시작</option>{block.versions.filter(row => row.id !== version.id).map(row => <option key={row.id} value={row.id}>{[row.net_ver, row.revision, row.layout, row.eco_num].filter(Boolean).join(' · ')}</option>)}</select></Field>}<Button variant="outline" disabled={busy || !project.can_write || !!needsLineage && !previous} onClick={() => onPrepare(selection, needsLineage ? previous === 'none' ? null : previous : undefined)}>선택 범위 연결</Button></div>}
    {version?.area_id && !uploads.length && <small>선택한 버전에 준비된 CSV가 없습니다. 아래 업로드 이력의 메타데이터로 PC CLI 업로드를 준비하세요.</small>}
    {hint && <small>{hint}</small>}
  </fieldset>
}
