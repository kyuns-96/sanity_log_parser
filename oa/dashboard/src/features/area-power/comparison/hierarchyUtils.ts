import { useEffect, useState } from 'react'
import { z } from 'zod'
import { object, recordSchema, request, text, type Auth, type ComparisonDetail, type TrackingRecord } from '../api'
import type { BrowserRow, ResultGroup } from '../HierarchyBrowser'

export const baselineSchema = z.object({
  project_id: z.string(),
  row_version: z.string(),
  baseline_project_id: z.string().nullable(),
  baseline_project: recordSchema.nullable(),
  status: z.enum(['unset', 'available', 'unavailable']),
  process: z.string().nullish(),
  baseline_process: z.string().nullish(),
})

export const hierarchySchema = z.object({
  upload_id: z.string(),
  snapshot_id: z.string().nullable(),
  hierarchy_separator: z.string(),
  items: z.array(z.object({
    path: z.string(),
    parent_path: z.string().nullable(),
    depth: z.string().transform(Number).refine(value => Number.isSafeInteger(value) && value >= 0),
    area: z.unknown().nullable(),
    logic_area: z.unknown().optional(),
    power_mw: z.unknown().nullable(),
  })),
})

export type Hierarchy = z.infer<typeof hierarchySchema>
export type Selection = { block: string; net: string; version: string; corner: string; upload: string }
export type TreeRow = {
  key: string
  parent: string
  baselinePaths: string[]
  currentPaths: string[]
  baseline?: Hierarchy['items'][number]
  current?: Hierarchy['items'][number]
  result?: ResultGroup
}

export const emptySelection: Selection = { block: '', net: '', version: '', corner: '', upload: '' }

export function useHierarchy(auth: Auth, id: string, refreshVersion: number) {
  const [state, setState] = useState<{ id: string; data: Hierarchy | null; error: string; loading: boolean }>({
    id: '', data: null, error: '', loading: false,
  })
  useEffect(() => {
    if (!id) { setState({ id, data: null, error: '', loading: false }); return }
    const controller = new AbortController()
    setState({ id, data: null, error: '', loading: true })
    request(auth, `/uploads/${encodeURIComponent(id)}/hierarchy`, hierarchySchema, undefined, controller.signal)
      .then(data => { if (!controller.signal.aborted) setState({ id, data, error: '', loading: false }) })
      .catch(cause => {
        if (!controller.signal.aborted) {
          setState({ id, data: null, error: cause instanceof Error ? cause.message : 'hierarchy를 조회하지 못했습니다.', loading: false })
        }
      })
    return () => controller.abort()
  }, [auth, id, refreshVersion])
  return state.id === id ? state : { id, data: null, error: '', loading: !!id }
}

export function groupedResults(detail: ComparisonDetail | null) {
  const groups = new Map<string, ResultGroup>()
  const input = object(detail?.comparison.payload.request)
  function include(value: TrackingRecord['payload'], uploadId?: string | null, baselineUploadId?: string | null) {
    const pair = object(value.power_pair)
    if (
      text(value.metric).startsWith('pwr_')
      && (text(pair.current_upload_id || uploadId) !== text(input.current_upload_id)
        || text(pair.baseline_upload_id || baselineUploadId) !== text(input.baseline_upload_id))
    ) return
    const baselinePaths = Array.isArray(value.baseline_paths) ? value.baseline_paths.map(text) : []
    const currentPaths = Array.isArray(value.current_paths) ? value.current_paths.map(text) : []
    const id = text(value.group_id) || JSON.stringify([baselinePaths, currentPaths])
    const group: ResultGroup = groups.get(id) || { id, baselinePaths, currentPaths, metrics: {} }
    group.metrics[text(value.metric)] = value
    groups.set(id, group)
  }
  for (const row of detail?.results || []) include(row.payload, row.upload_id, row.baseline_upload_id)
  for (const observation of detail?.observations || []) include(observation)
  return [...groups.values()]
}

export function mergeHierarchy(current: Hierarchy['items'], baseline: Hierarchy['items'], results: ResultGroup[]) {
  const byCurrent = new Map(current.map(row => [row.path, row]))
  const byBaseline = new Map(baseline.map(row => [row.path, row]))
  const currentKeys = new Map<string, string>()
  const baselineKeys = new Map<string, string>()
  const rows: TreeRow[] = results.map(result => {
    const row: TreeRow = {
      key: `g:${result.id}`,
      parent: '',
      baselinePaths: result.baselinePaths,
      currentPaths: result.currentPaths,
      result,
      baseline: result.baselinePaths.length === 1 ? byBaseline.get(result.baselinePaths[0]) : undefined,
      current: result.currentPaths.length === 1 ? byCurrent.get(result.currentPaths[0]) : undefined,
    }
    if (result.currentPaths.length === 1 && result.baselinePaths.length <= 1) currentKeys.set(result.currentPaths[0], row.key)
    if (result.baselinePaths.length === 1 && result.currentPaths.length <= 1) baselineKeys.set(result.baselinePaths[0], row.key)
    return row
  })
  const currentRoot = current.find(item => item.parent_path === null)?.path
  const baselineRoot = baseline.find(item => item.parent_path === null)?.path
  const counterpart = (path: string) =>
    !results.length && currentRoot !== undefined && baselineRoot !== undefined ? baselineRoot + path.slice(currentRoot.length) : undefined
  for (const item of current) {
    if (currentKeys.has(item.path)) continue
    const pair = counterpart(item.path)
    const match = pair !== undefined && !baselineKeys.has(pair) ? byBaseline.get(pair) : undefined
    const key = match ? `p:${item.path}` : `c:${item.path}`
    currentKeys.set(item.path, key)
    if (match) baselineKeys.set(match.path, key)
    rows.push({ key, parent: '', currentPaths: [item.path], baselinePaths: match ? [match.path] : [], current: item, baseline: match })
  }
  for (const item of baseline) {
    if (baselineKeys.has(item.path)) continue
    const key = `b:${item.path}`
    baselineKeys.set(item.path, key)
    rows.push({ key, parent: '', currentPaths: [], baselinePaths: [item.path], baseline: item })
  }
  const byKey = new Map(rows.map(row => [row.key, row]))
  for (const row of rows) {
    const parent = row.current?.parent_path ? currentKeys.get(row.current.parent_path) : row.baseline?.parent_path ? baselineKeys.get(row.baseline.parent_path) : undefined
    if (parent && parent !== row.key) row.parent = parent
  }
  const settled = new Set<string>()
  for (const row of rows) {
    const trail = new Set<string>()
    let node: TreeRow | undefined = row
    while (node && !settled.has(node.key)) {
      if (trail.has(node.key)) { node.parent = ''; break }
      trail.add(node.key)
      node = byKey.get(node.parent)
    }
    for (const key of trail) settled.add(key)
  }
  return rows
}

export function referenceRows(value: ComparisonDetail): BrowserRow[] {
  const nodes = new Map((value.hierarchy_nodes || []).map(node => [node.group_id, node]))
  return groupedResults(value).map(result => ({
    key: result.id,
    baselinePaths: result.baselinePaths,
    currentPaths: result.currentPaths,
    result,
    childCount: nodes.get(result.id)?.child_count || 0,
    hiddenChildren: nodes.get(result.id)?.hidden_child_count || 0,
  }))
}
