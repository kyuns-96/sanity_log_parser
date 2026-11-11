// Product demo catalog over the native workspace's exact objects; page memory only.
import { WorkspaceError } from './workspaceApi'
import type { WorkspaceEvent, WorkspacePor, WorkspaceReport, WorkspaceUser, WorkspaceVersion } from './workspaceApi'

export type DemoVersion = WorkspaceVersion & { source_namespace: string; previous_version_id: string | null; area_id: string | null; declared_ecos?: (string | null)[] }
export type DemoProject = { id: string; name: string; description: string; tops: WorkspaceUser[]; area_id: string | null }
export type DemoBlock = { id: string; project_id: string; name: string; owner: WorkspaceUser; versions: DemoVersion[]; por: WorkspacePor; events: WorkspaceEvent[]; area_id: string | null }
type Input = Record<string, unknown>
type VersionIdentity = { project_id: string; block_id: string; source_namespace: string; net_ver: string; revision: string; layout: 'PRE' | 'POST' }
const fail = (code = 'INVALID_REQUEST'): never => { throw new WorkspaceError(code) }
const idPattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const controls = /[\x00-\x1f\x7f]/
function text(value: unknown, maximum = 100, trim = true, empty = false): string {
  if (typeof value !== 'string') return fail()
  const result = trim ? value.trim() : value
  if ((!empty && !result.trim()) || result.length > maximum || controls.test(result)) return fail()
  return result
}
function id(value: unknown): string { return typeof value === 'string' && idPattern.test(value) ? value : fail() }
function optionalId(value: unknown): string | null { return value == null ? null : id(value) }
function input(body: unknown, fields: string[]): Input {
  if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).some(key => !fields.includes(key))) return fail()
  return body as Input
}
const scopeFields = ['project_id', 'block_id', 'version_id', 'previous_version_id']
const identityFields = ['project_id', 'block_id', 'source_namespace', 'net_ver', 'revision', 'layout']
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`
  const serialized = JSON.stringify(value)
  if (serialized === undefined) return fail()
  return serialized
}

export function makeWorkspaceDemoCatalog(state: {
  projects: DemoProject[]; blocks: DemoBlock[]; user: () => WorkspaceUser;
  reports: (block: DemoBlock, version: DemoVersion) => WorkspaceReport[];
}) {
  const { projects, blocks } = state
  const receipts = new Map<string, { actor: string; operation: string; data: string; result: Input }>()
  // Owner/TOP associations grant the whole project, not individual blocks.
  const canAccess = (projectId: string) => projects.some(project => project.id === projectId &&
    (project.tops.some(top => top.id === state.user().id) || blocks.some(block => block.project_id === projectId && block.owner.id === state.user().id)))
  function scope(projectId: string, blockId: string | null = null, versionId: string | null = null) {
    if (versionId && !blockId) return fail()
    const project = projects.find(project => project.id === projectId)
    if (!project || !canAccess(projectId)) return fail('RESOURCE_NOT_FOUND')
    const block = blockId ? blocks.find(block => block.id === blockId && block.project_id === projectId) : null
    if (blockId && !block) return fail('RESOURCE_NOT_FOUND')
    const version = versionId ? block!.versions.find(version => version.id === versionId) : null
    if (versionId && !version) return fail('RESOURCE_NOT_FOUND')
    return { project, block: block || null, version: version || null }
  }
  function command(body: Input, operation: string, data: Input, action: () => Input) {
    if (typeof body.request_id !== 'string' || !uuidPattern.test(body.request_id)) return fail()
    const key = body.request_id.toLowerCase(), serialized = canonical(data), actor = state.user().id, previous = receipts.get(key)
    if (previous) {
      if (previous.actor !== actor || previous.operation !== operation || previous.data !== serialized) return fail('REQUEST_CONFLICT')
      return { ...previous.result, status: 'ALREADY_CREATED' }
    }
    const result = action()
    receipts.set(key, { actor, operation, data: serialized, result })
    return result
  }
  function identity(body: Input): VersionIdentity {
    const fields = { project_id: id(body.project_id), block_id: id(body.block_id), source_namespace: text(body.source_namespace, 1000, false),
      net_ver: text(body.net_ver, 1000, false), revision: text(body.revision, 1000, false), layout: body.layout }
    if (fields.layout !== 'PRE' && fields.layout !== 'POST') return fail()
    scope(fields.project_id, fields.block_id)
    return { ...fields, layout: fields.layout as 'PRE' | 'POST' }
  }
  async function versionIdentity(fields: VersionIdentity) {
    // common.sanity_submission.source_version: UTF-8 canonical JSON array, SHA-256.
    const bytes = new TextEncoder().encode(JSON.stringify([fields.source_namespace, fields.project_id, fields.block_id, fields.net_ver, fields.revision, fields.layout]))
    const digest = await crypto.subtle.digest('SHA-256', bytes)
    return 'sv-' + Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  }
  async function request(url: URL, body?: unknown): Promise<unknown> {
    const path = url.pathname
    if (path === '/atlas/session' && body === undefined) return { user_id: state.user().id, reviewer: state.user().name,
      authorization_ready: true, access_mode: 'memory_demo', writable: true,
      notice: '합성 데이터 · 브라우저 메모리 데모입니다. 변경은 새로고침하면 초기화됩니다.', can_read_business: true, can_receive: false, is_admin: false }
    if (path === '/atlas/catalog' && body === undefined) return { product: 'Design Atlas', local_mode: false, can_manage_catalog: true,
      area_enabled: true, area_status: 'ready', data_origin: 'SYNTHETIC_MEMORY_DEMO', projects: projects.filter(project => canAccess(project.id)).map(project => ({
        id: project.id, name: project.name, description: project.description, can_write: true, area_id: project.area_id,
        blocks: blocks.filter(block => block.project_id === project.id).map(block => ({ id: block.id, name: block.name, area_id: block.area_id, versions: block.versions })),
      })) }
    if (path === '/atlas/reports' && body === undefined) {
      const params = url.searchParams
      const { block, version } = scope(id(params.get('project_id')), id(params.get('block_id')), id(params.get('version_id')))
      const integer = (key: string, fallback: number, minimum: number, maximum: number) => {
        if (!params.has(key)) return fallback
        const value = Number(params.get(key))
        if (!params.get(key) || !Number.isSafeInteger(value) || value < minimum || value > maximum) return fail()
        return value
      }
      const offset = integer('offset', 0, 0, Number.MAX_SAFE_INTEGER), limit = integer('limit', 100, 1, 100)
      const rows = state.reports(block!, version!).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
      return { items: rows.slice(offset, offset + limit), total: rows.length, offset, limit }
    }
    if (path === '/atlas/projects') {
      const bodyFields = input(body, ['request_id', 'project_id', 'name', 'description'])
      const data = { project_id: id(bodyFields.project_id), name: text(bodyFields.name), description: text(bodyFields.description ?? '', 1000, true, true) }
      return command(bodyFields, 'PROJECT', data, () => {
        if (projects.some(project => project.id === data.project_id)) return fail('REQUEST_CONFLICT')
        projects.push({ id: data.project_id, name: data.name, description: data.description, tops: [state.user()], area_id: null })
        return { status: 'CREATED', project_id: data.project_id }
      })
    }
    if (path === '/atlas/blocks') {
      const bodyFields = input(body, ['request_id', 'project_id', 'block_id', 'name'])
      const data = { project_id: id(bodyFields.project_id), block_id: id(bodyFields.block_id), name: text(bodyFields.name) }
      scope(data.project_id)
      return command(bodyFields, 'BLOCK', data, () => {
        if (blocks.some(block => block.id === data.block_id)) return fail('REQUEST_CONFLICT')
        blocks.push({ id: data.block_id, project_id: data.project_id, name: data.name, owner: state.user(), versions: [],
          por: { revision: 0, version: null, assigned_by: null, assigned_at: null }, events: [], area_id: null })
        return { status: 'CREATED', project_id: data.project_id, block_id: data.block_id }
      })
    }
    if (path === '/atlas/version-identity') {
      const fields = identity(input(body, identityFields))
      return { version_id: await versionIdentity(fields) }
    }
    if (path === '/atlas/versions') {
      const bodyFields = input(body, [...identityFields, 'request_id', 'version_id', 'ecos', 'source_pre_id', 'previous_version_id'])
      const fields = identity(bodyFields), versionId = id(bodyFields.version_id)
      if (versionId !== await versionIdentity(fields)) return fail('INVALID_VERSION_IDENTITY')
      const { block } = scope(fields.project_id, fields.block_id)
      if (bodyFields.ecos !== undefined && !Array.isArray(bodyFields.ecos)) return fail()
      const ecos = ((bodyFields.ecos || []) as unknown[]).map(eco => eco === null ? null : text(eco))
      const sourcePreId = optionalId(bodyFields.source_pre_id), previousVersionId = optionalId(bodyFields.previous_version_id)
      if (new Set(ecos).size !== ecos.length || fields.layout === 'PRE' && (ecos.some(eco => eco !== null) || sourcePreId !== null) || fields.layout === 'POST' && ecos.includes(null)) return fail()
      if (sourcePreId) {
        const pre = scope(fields.project_id, fields.block_id, sourcePreId).version!
        if (pre.layout !== 'PRE' || pre.net_ver !== fields.net_ver || pre.source_namespace !== fields.source_namespace) return fail()
      }
      if (previousVersionId === versionId) return fail()
      if (previousVersionId) scope(fields.project_id, fields.block_id, previousVersionId)
      const data = { ...fields, version_id: versionId, ecos, source_pre_id: sourcePreId, previous_version_id: previousVersionId }
      return command(bodyFields, 'VERSION', data, () => {
        const existing = blocks.flatMap(block => block.versions).find(version => version.id === versionId)
        const version: DemoVersion = { ...fields, id: versionId, ecos: fields.layout === 'PRE' ? [null] : [],
          declared_ecos: fields.layout === 'PRE' ? [null] : ecos,
          source_pre_id: sourcePreId, previous_version_id: previousVersionId, area_id: null }
        if (existing) {
          const { area_id: _area, ...native } = existing
          const { area_id: _newArea, ...expected } = version
          if (canonical(native) !== canonical(expected)) return fail('REQUEST_CONFLICT')
          return { status: 'ALREADY_CREATED', version_id: versionId }
        }
        block!.versions.unshift(version)
        return { status: 'CREATED', project_id: fields.project_id, block_id: fields.block_id, version_id: versionId }
      })
    }
    if (path === '/atlas/area-scope') {
      const bodyFields = input(body, scopeFields), projectId = id(bodyFields.project_id), blockId = optionalId(bodyFields.block_id), versionId = optionalId(bodyFields.version_id)
      const { project, block, version } = scope(projectId, blockId, versionId)
      if (Object.hasOwn(bodyFields, 'previous_version_id')) {
        if (!version) return fail()
        if (optionalId(bodyFields.previous_version_id) !== version.previous_version_id) return fail('AREA_BINDING_CONFLICT')
      }
      // Check ancestry before allocating any UUID, so failed preparation has no partial mutation.
      if (version?.previous_version_id && !scope(projectId, blockId, version.previous_version_id).version!.area_id) return fail('AREA_PREVIOUS_NOT_PREPARED')
      project.area_id ||= crypto.randomUUID()
      if (block) block.area_id ||= crypto.randomUUID()
      if (version) version.area_id ||= crypto.randomUUID()
      return { status: 'PREPARED', project_id: projectId, block_id: blockId, version_id: versionId,
        area_project_id: project.area_id, area_block_id: block?.area_id || null, area_version_id: version?.area_id || null }
    }
    return fail('NOT_FOUND')
  }
  return { canAccess, request }
}
