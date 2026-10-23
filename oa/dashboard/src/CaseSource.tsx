import type { CaseSourceMetadata, WorkspaceReport, WorkspaceVersion } from './workspaceApi'

export default function CaseSource({ value, report, version }: {
  value: CaseSourceMetadata; report?: WorkspaceReport; version?: WorkspaceVersion
}) {
  const source = value.source_version
  const scope = value.scope
  const project = scope?.project_id ?? value.project_id ?? source?.project_id
  const block = scope?.block_id ?? value.block_id ?? source?.block_id
  const eco = Object.prototype.hasOwnProperty.call(value, 'eco') ? value.eco : scope?.eco
  const differences: string[] = []
  const compare = (label: string, previous: unknown, current: unknown) => {
    if (previous !== undefined && current !== undefined && previous !== current) differences.push(label)
  }
  if (report) {
    compare('프로젝트', project, report.project_id)
    compare('블록', block, report.block_id)
    compare('버전', value.version_id ?? source?.id, report.version_id)
    compare('ECO', eco, report.eco)
    compare('sanity item', scope?.item_id, report.item_id)
    compare('도구', scope?.family, report.family)
    compare('파서', scope?.parser, report.parser)
  }
  if (version) {
    compare('revision', source?.revision, version.revision)
    compare('layout', source?.layout ?? scope?.layout, version.layout)
  }
  const comparisonLabels: Record<string, string> = { project_id: '프로젝트', block_id: '블록', net_ver: '버전', revision: 'revision', layout: 'layout', eco: 'ECO', item_id: 'sanity item', family: '도구', parser: '파서', rule_version: '규칙 버전' }
  for (const key of value.retrieval_comparison?.different_attributes || []) {
    const label = comparisonLabels[key]
    if (label && !differences.includes(label)) differences.push(label)
  }
  const differenceOrder = ['프로젝트', '블록', '버전', 'revision', 'layout', 'ECO', 'sanity item', '도구', '파서', '규칙 버전']
  differences.sort((left, right) => differenceOrder.indexOf(left) - differenceOrder.indexOf(right))
  const versionParts = [source?.net_ver && `버전 ${source.net_ver}`, source?.revision && `revision ${source.revision}`,
    source?.layout ?? scope?.layout, eco === undefined ? null : eco === null ? 'ECO 없음' : `ECO ${eco}`].filter(Boolean)
  return <span className="ws-case-source">
    {(project || block) && <small>{[project && `프로젝트 ${project}`, block && `블록 ${block}`].filter(Boolean).join(' · ')}</small>}
    {!!versionParts.length && <small>{versionParts.join(' · ')}</small>}
    {!!differences.length && <small>이번 보고서와 다른 조건: {differences.join(', ')}</small>}
  </span>
}
