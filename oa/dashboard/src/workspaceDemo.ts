// Synthetic, per-page memory adapter for the same UI used by /workspace.
// No fetch, browser storage, server credentials, or model calls belong here.
import { WorkspaceError } from './workspaceApi'
import { workspaceDemoUsers as users } from './workspaceDemoAccounts'
import type {
  ClusterBrief, ClusterEvent, GroupBrief, GroupRationale, Judgment, PipelineGroup, PipelineTask,
  RationaleContent, ReviewCase, ReviewProposal, WorkspaceEvent, WorkspacePor, WorkspaceRecord,
  WorkspaceReport, WorkspaceSession, WorkspaceUser, WorkspaceVersion, WorkbenchView,
} from './workspaceApi'

type DemoBlock = { id: string; project_id: string; name: string; owner: WorkspaceUser; versions: WorkspaceVersion[]; por: WorkspacePor; events: WorkspaceEvent[] }
type Snapshot = { event: ClusterEvent; rationales: Record<string, GroupRationale> }
type DemoReport = {
  report: WorkspaceReport; version: WorkspaceVersion; records: WorkspaceRecord[]; sha: string;
  snapshots: Snapshot[]; reviews: Record<string, ReviewCase[]>; proposals: Record<string, ReviewProposal>; task: PipelineTask | null;
}
type Command = {
  username?: string; version_id?: string; expected_revision?: number; expected_cluster_revision?: number;
  request_id?: string; action?: string; reason?: string; identity_fields?: string[]; reusable?: boolean;
  compatible_versions?: boolean; criteria?: string; content?: RationaleContent; reviewed?: boolean;
  conclusion?: Judgment; conditions?: Record<string, string>; proposal_id?: string;
  task_id?: string; expected_chunk?: number;
  operation?: { kind: string; destination_id?: string; new_group_id?: string; label?: string; record_ids?: string[]; group_ids?: string[] };
}
const copy = <T,>(value: T): T => structuredClone(value)
const now = () => new Date().toISOString()
function reject(code: string): never { throw new WorkspaceError(code) }
const emptyPor = (): WorkspacePor => ({ revision: 0, version: null, assigned_by: null, assigned_at: null })
const count = (items: string[]) => items.reduce<Record<string, number>>((result, value) => { result[value] = (result[value] || 0) + 1; return result }, {})
const rationaleCounts = (rows: Record<string, GroupRationale>) => ({ MISSING: 0, DRAFT: 0, CONFIRMED: 0, STALE: 0, ...count(Object.values(rows).map(row => row.status)) })
async function hash(value: string) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function makeWorkspaceDemo() {
  let user = users[3]
  const projects = ['Atlas', 'Boreal', 'Cygnus'].map((name, index) => ({ id: `demo-${name.toLowerCase()}`, name, description: `${name} 합성 검사 프로젝트`, tops: [users[index]] }))
  const owners = [[3, 4, 5], [6, 7, 8], [9, 3, 4]]
  const blocks: DemoBlock[] = projects.flatMap((project, projectIndex) => ['CPU', 'GPU', 'DDR'].map((name, index) => {
    const id = `${project.id}-${name.toLowerCase()}`
    const versions: WorkspaceVersion[] = [2, 1].flatMap(sequence => (['POST', 'PRE'] as const).map(layout => ({
      id: `${id}-net${sequence}-${layout.toLowerCase()}`, project_id: project.id, block_id: id,
      net_ver: `NET_00${sequence}`, revision: `REV00${sequence}-${name.toLowerCase()}${layout === 'POST' ? '-BE' : ''}`,
      layout, source_pre_id: layout === 'POST' ? `${id}-net${sequence}-pre` : null,
      ecos: layout === 'POST' ? ['chip_finish', 'eco01'] : [null],
    })))
    return { id, project_id: project.id, name, owner: users[owners[projectIndex][index]], versions, por: emptyPor(), events: [] }
  }))
  const reports = new Map<string, DemoReport>()
  const receipts = new Map<string, { command: string; value: unknown }>()
  const canWrite = (block: DemoBlock) => block.owner.id === user.id
  const canRead = (block: DemoBlock) => canWrite(block) || projects.find(project => project.id === block.project_id)!.tops.some(top => top.id === user.id)
  const session = (): WorkspaceSession => ({ user, csrf: 'memory-demo', expires_at: '2099-01-01T00:00:00Z', environment: 'MEMORY_DEMO' })

  function scopedBlock(id: string) {
    const block = blocks.find(block => block.id === id)
    return block && canRead(block) ? block : reject('NOT_FOUND')
  }
  function checkRevision(expected: number | undefined, actual: number) { if (expected !== actual) reject('REVISION_CONFLICT') }
  function page<T>(items: T[], params: URLSearchParams, defaultLimit = 25) {
    const offset = Math.max(0, Number(params.get('offset')) || 0)
    const limit = Math.min(50, Math.max(1, Number(params.get('limit')) || defaultLimit))
    return { items: items.slice(offset, offset + limit), total: items.length, offset, limit }
  }
  function current(state: DemoReport) { return state.snapshots.at(-1) }
  function selectedSnapshot(state: DemoReport, params: URLSearchParams) {
    if (!params.has('event_id')) return current(state)
    return state.snapshots.find(snapshot => snapshot.event.id === params.get('event_id')) || reject('NOT_FOUND')
  }
  function groupStatus(state: DemoReport, groupId: string, revision: number) {
    const review = state.reviews[groupId]?.[0]
    return !review ? 'PENDING' : review.cluster_revision !== revision ? 'STALE' : review.conclusion
  }
  function removedGroups(state: DemoReport) {
    const active = new Set(current(state)?.event.groups.map(group => group.id))
    return Object.entries(state.reviews).filter(([id, history]) => id !== 'report' && history.length && !active.has(id))
      .map(([id, history]) => ({ id, label: history[0].group_snapshot?.label || id, conclusion: history[0].conclusion }))
  }
  function summary(state: DemoReport) {
    const snapshot = current(state)
    const groups = snapshot?.event.groups || []
    const counts = { PASS: 0, FAIL: 0, DEFER: 0, STALE: 0, PENDING: 0, ...count(groups.map(group => groupStatus(state, group.id, snapshot!.event.revision))) }
    const reviewed = snapshot?.event.confirmed ? counts.PASS + counts.FAIL : 0
    return { confirmed: !!snapshot?.event.confirmed, total: groups.length, reviewed, pending: groups.length - reviewed,
      complete: !!snapshot?.event.confirmed && !!groups.length && reviewed === groups.length,
      rationale_counts: snapshot ? rationaleCounts(snapshot.rationales) : rationaleCounts({}), counts }
  }
  function brief(snapshot: Snapshot): ClusterBrief {
    const { groups, ...event } = snapshot.event
    return { ...event, group_count: groups.length, record_count: groups.reduce((total, group) => total + group.record_ids.length, 0), rationale_counts: rationaleCounts(snapshot.rationales) }
  }
  function reportView(state: DemoReport, detail = false): WorkspaceReport {
    const snapshot = current(state), progress = summary(state), review = state.reviews.report?.[0]
    return { ...state.report, ...(detail ? { raw_report: state.records.length ? state.records.map(record => record.raw).join('\n') : null, records: state.records } : {}),
      human_review: review ? { conclusion: review.conclusion, actor: review.actor, at: review.at, revision: review.revision, reusable: review.reusable } : null,
      group_review_summary: snapshot ? { cluster_revision: snapshot.event.revision, confirmed: progress.confirmed, total: progress.total, reviewed: progress.reviewed, stale: progress.counts.STALE,
        groups: Object.fromEntries(snapshot.event.groups.map(group => [group.id, { conclusion: state.reviews[group.id]?.[0]?.conclusion || null, stale: groupStatus(state, group.id, snapshot.event.revision) === 'STALE', revision: state.reviews[group.id]?.length || 0 }])), removed: removedGroups(state) } : null }
  }
  function workbench(state: DemoReport, block: DemoBlock): WorkbenchView {
    const snapshot = current(state)
    return { report: reportView(state), version: state.version, current: snapshot ? brief(snapshot) : null,
      revision: snapshot?.event.revision || 0, ready: state.report.analysis_ready, can_edit: canWrite(block), stale: false,
      task: state.task, record_count: state.records.length, raw_line_count: state.records.length, source_sha256: state.sha,
      case_count: 0, reusable_count: 0, removed_count: removedGroups(state).length, corporate_ai_connected: false, summary: summary(state) }
  }
  async function commit(state: DemoReport, action: ClusterEvent['action'], groups: PipelineGroup[], rationales: Record<string, GroupRationale>, command: Command = {}) {
    const previous = current(state)
    const event: ClusterEvent = { id: `demo-cluster-${crypto.randomUUID()}`, report_id: state.report.id, revision: (previous?.event.revision || 0) + 1,
      action, confirmed: action === 'CONFIRM', reusable: command.reusable ?? previous?.event.reusable ?? false,
      compatible_versions: command.compatible_versions ?? previous?.event.compatible_versions ?? false,
      criteria: command.criteria ?? previous?.event.criteria ?? '', reason: command.reason || '합성 로그를 사용하는 메모리 데모입니다.',
      identity_fields: command.identity_fields || previous?.event.identity_fields || ['object', 'check_point', 'mode'], groups: copy(groups), at: now(), actor: copy(user),
      references: [], advisor: 'MEMORY_DEMO', model_called: false }
    const bound = copy(rationales)
    for (const group of groups) {
      const row = bound[group.id]
      if (!row) continue
      if (action === 'CONFIRM') { row.status = 'CONFIRMED'; row.reviewed_by = copy(user); row.reviewed_at = event.at }
      row.binding = { source_sha256: state.sha, cluster_event_id: event.id, cluster_revision: event.revision, group_id: group.id, label: group.label, membership_sha256: await hash([...group.record_ids].sort().join('\n')) }
    }
    state.snapshots.push({ event, rationales: bound })
    return brief(current(state)!)
  }
  async function draft(state: DemoReport, fields = ['object', 'check_point', 'mode']) {
    const buckets = new Map<string, WorkspaceRecord[]>()
    for (const record of state.records) {
      const key = JSON.stringify(fields.map(field => record.fields[field] ?? null))
      buckets.set(key, [...(buckets.get(key) || []), record])
    }
    const groups: PipelineGroup[] = [], rationales: Record<string, GroupRationale> = {}
    for (const records of buckets.values()) {
      const group = { id: `demo-group-${groups.length + 1}`, label: `${records[0].fields.object} · ${records[0].fields.mode}`, record_ids: records.map(record => record.id) }
      groups.push(group)
      rationales[group.id] = { content: { reason: `${fields.join(', ')} 값이 같은 합성 로그를 묶었습니다. 원문과 그룹 구성을 확인한 뒤 분류를 확정하세요.`, evidence_ids: group.record_ids.slice(0, 2), reference_groups: [] },
        source: 'MEMORY_DEMO', status: 'DRAFT', binding: { source_sha256: state.sha, cluster_event_id: '', cluster_revision: 0, group_id: group.id, label: group.label, membership_sha256: '' },
        reviewed_by: null, reviewed_at: null, change_reason: '데모 분류 초안', previous_event_id: null }
    }
    await commit(state, 'PROPOSE', groups, rationales, { identity_fields: fields })
  }
  function versionReports(block: DemoBlock, version: WorkspaceVersion, eco: string | null) {
    if (version.layout === 'PRE' || eco !== 'chip_finish') return []
    const families = [
      { family: 'GCA', item: 'gca_constraints', count: version.net_ver === 'NET_002' ? 28 : 4, rules: ['CLK_0003', 'CAS_0003', 'CLK_0026'], points: ['clock_in', 'async_path', 'test_enable'] },
      { family: 'VCLP', item: 'vclp_low_power', count: 5, rules: ['ISO_CONTROL', 'LS_MISSING', 'RET_CONTROL'], points: ['isolation_en', 'level_shift', 'retention_en'] },
      { family: 'SPYGLASS', item: 'spyglass_lint', count: 5, rules: ['W164', 'W287a', 'W415'], points: ['reset_n', 'bus_width', 'driver'] },
    ]
    return families.flatMap(config => Array.from({ length: config.count }, (_, index) => {
      const id = `${version.id}-${config.family.toLowerCase()}-${String(index + 1).padStart(3, '0')}`
      if (!reports.has(id)) {
        const missing = index === config.count - 1, partial = index === 2
        const status = missing || partial ? 'UNKNOWN' : index % 4 === 3 ? 'PASS' : 'FAIL'
        const rule = config.rules[index % config.rules.length]
        const records: WorkspaceRecord[] = missing ? [] : Array.from({ length: 18 + index % 5 * 3 }, (_, number) => {
          const group = number % 3, object = `top/${block.name.toLowerCase()}/${['core', 'io', 'test'][group]}`, mode = group === 2 ? 'TEST' : 'FUNCTIONAL'
          const raw = `[SYNTHETIC DEMO] ${rule} object=${object} check_point=${config.points[group]} mode=${mode} instance=u_${String(number + 1).padStart(3, '0')} result=${status}`
          return { id: `${id}-log-${number + 1}`, check_status: status, fields: { rule, code: rule, object, check_point: config.points[group], mode, message: `${rule} · ${object}의 ${config.points[group]} 조건을 확인하세요.`, severity: status === 'PASS' ? 'INFO' : 'WARNING' },
            raw, line_start: number + 1, line_end: number + 1, reasons: partial ? ['SYNTHETIC_PARTIAL_INPUT'] : [] }
        })
        reports.set(id, { report: { id, project_id: block.project_id, block_id: block.id, version_id: version.id, eco, item_id: config.item, family: config.family,
          rule, check_status: status, parse_status: missing ? 'MISSING' : partial ? 'PARTIAL' : 'COMPLETE', parser: 'synthetic_workspace_demo_v1', record_count: records.length,
          blockers: missing ? ['REPORT_NOT_RECEIVED'] : partial ? ['SYNTHETIC_PARTIAL_INPUT'] : [], analysis_ready: !missing && !partial },
          records, version, sha: '', snapshots: [], reviews: {}, proposals: {}, task: null })
      }
      return reports.get(id)!
    }))
  }

  async function dispatch(path: string, body?: unknown): Promise<unknown> {
    const url = new URL(path, 'http://memory.demo'), params = url.searchParams, command = (body || {}) as Command
    if (url.pathname === '/session') return session()
    if (url.pathname === '/demo/account' && body) { user = users.find(user => user.username === command.username) || reject('NOT_FOUND'); return session() }
    if (url.pathname === '/overview') return { user, data_origin: 'SYNTHETIC_MEMORY_DEMO', projects: projects.map(project => ({ ...project, blocks: blocks.filter(block => block.project_id === project.id && canRead(block)).map(block => {
      const version = block.por.version || block.versions[0], rows = versionReports(block, version, version.ecos[0])
      return { id: block.id, project_id: block.project_id, name: block.name, owners: [block.owner], can_assign_por: canWrite(block), por: block.por, report_version: version, total: rows.length, counts: count(rows.map(row => row.report.check_status)) }
    }) })).filter(project => project.blocks.length) }
    const parts = url.pathname.split('/').filter(Boolean)
    if (parts[0] !== 'blocks') reject('NOT_FOUND')
    const block = scopedBlock(parts[1])
    if (body && !canWrite(block)) reject('FORBIDDEN')
    if (parts[2] === 'history') return { items: block.events }
    if (parts[2] === 'por' && body) {
      checkRevision(command.expected_revision, block.por.revision)
      const version = block.versions.find(version => version.id === command.version_id) || reject('NOT_FOUND')
      if (!command.reason?.trim()) reject('INVALID_REQUEST')
      if (block.por.version?.id === version.id) reject('POR_UNCHANGED')
      const event = { id: `demo-por-${crypto.randomUUID()}`, previous_version_id: block.por.version?.id || null, version, actor: copy(user), reason: command.reason, at: now(), revision: block.por.revision + 1 }
      block.events.unshift(event); block.por = { revision: event.revision, version, assigned_by: copy(user), assigned_at: event.at }
      return block.por
    }
    if (parts[2] !== 'versions') reject('NOT_FOUND')
    if (parts.length === 3) return { items: block.versions, por: block.por, can_assign_por: canWrite(block) }
    const version = block.versions.find(version => version.id === parts[3]) || reject('NOT_FOUND')
    if (parts[4] !== 'reports') reject('NOT_FOUND')
    const rows = versionReports(block, version, params.get('eco') || version.ecos[0])
    if (parts.length === 5) {
      const groups = [...new Set(rows.map(row => row.report.item_id!))].map(item_id => {
        const items = rows.filter(row => row.report.item_id === item_id)
        return { item_id, total: items.length, counts: count(items.map(row => row.report.check_status)), parse_counts: count(items.map(row => row.report.parse_status)), families: [...new Set(items.map(row => row.report.family))] }
      })
      const items = rows.filter(({ report }) => (!params.has('item_id') || report.item_id === params.get('item_id')) && (!params.get('family') || report.family === params.get('family'))
        && (!params.get('status') || (params.get('status') === 'MISSING' ? report.parse_status === 'MISSING' : report.check_status === params.get('status')))
        && `${report.id} ${report.rule}`.toLowerCase().includes((params.get('search') || '').toLowerCase())).map(row => reportView(row))
      return { ...page(items, params), counts: count(rows.map(row => row.report.check_status)), groups, version }
    }
    const state = rows.find(row => row.report.id === parts[5]) || reject('NOT_FOUND')
    if (!state.sha) state.sha = await hash(state.records.map(record => record.raw).join('\n'))
    if (parts.length === 6) return reportView(state, true)
    const snapshot = current(state), revision = snapshot?.event.revision || 0
    if (parts[6] === 'workbench') return workbench(state, block)
    if (parts[6] === 'review' || parts[6] === 'review-proposals' || parts[6] === 'groups' && ['review', 'review-proposals'].includes(parts[8])) {
      const activeGroup = parts[6] === 'groups' ? snapshot?.event.groups.find(group => group.id === parts[7]) : null
      const group = parts[6] === 'groups' ? activeGroup || state.reviews[parts[7]]?.[0]?.group_snapshot || reject('NOT_FOUND') : null
      const key = group?.id || 'report', history = state.reviews[key] || [], latest = history[0] || null
      const stale = !!group && !!latest && latest.cluster_revision !== revision
      const canReview = canWrite(block) && (!group || !!snapshot?.event.confirmed && !!activeGroup)
      const facts = Object.fromEntries(Object.entries((group ? state.records.find(record => record.id === group.record_ids[0]) : state.records[0])?.fields || {}).filter(([key]) => ['rule', 'mode', 'object', 'check_point', 'severity', 'code'].includes(key)).map(([key, value]) => [key, String(value)]))
      if (!body) return { current: latest, current_stale: stale, revision: history.length, history, facts, ready: state.report.analysis_ready, can_review: canReview,
        cases: [], case_count: 0, reusable_count: 0, cases_displayed: 0, proposal: state.proposals[key] || null, corporate_ai_connected: false,
        blocked_reason: group && !snapshot?.event.confirmed ? 'CLUSTER_NOT_CONFIRMED' : null }
      if (!canReview) reject(group ? 'CLUSTER_NOT_CONFIRMED' : 'FORBIDDEN')
      checkRevision(command.expected_revision, history.length)
      if (group) checkRevision(command.expected_cluster_revision, revision)
      if (parts.at(-1) === 'review-proposals') {
        state.proposals[key] = { id: `demo-proposal-${crypto.randomUUID()}`, conclusion: 'DEFER', reason: '데모에서는 AI를 호출하지 않습니다. 원문을 확인하고 직접 판정해 주세요.', references: [], advisor: 'MEMORY_DEMO', model_called: false, at: now() }
        return state.proposals[key]
      }
      if (!command.reason?.trim() || !['PASS', 'FAIL', 'DEFER'].includes(command.conclusion || '')) reject('INVALID_REQUEST')
      if (!state.report.analysis_ready && command.conclusion !== 'DEFER') reject('REVIEW_BLOCKED')
      if (command.reusable && (!command.criteria?.trim() || command.conclusion === 'DEFER')) reject('INVALID_CRITERIA')
      const event: ReviewCase = { id: `demo-review-${crypto.randomUUID()}`, report_id: state.report.id, version_id: version.id, conclusion: command.conclusion!, reason: command.reason,
        criteria: command.criteria || '', conditions: command.conditions || {}, reusable: !!command.reusable, compatible_versions: !!command.compatible_versions,
        revision: history.length + 1, actor: copy(user), at: now(), source_sha256: state.sha, source_version: version,
        proposal_id: command.proposal_id || null, proposal_conclusion: command.proposal_id ? state.proposals[key]?.conclusion || null : null, proposal_agreed: command.proposal_id ? state.proposals[key]?.conclusion === command.conclusion : null,
        ...(group ? { group_id: group.id, cluster_revision: revision, group_snapshot: copy(group) } : {}) }
      state.reviews[key] = [event, ...history]
      return { event }
    }
    if (parts[6] === 'groups' && parts[8] === 'rationale') {
      const selected = selectedSnapshot(state, params), group = selected?.event.groups.find(group => group.id === parts[7]) || reject('NOT_FOUND')
      const row = selected!.rationales[group.id]
      if (!body) return { group_id: group.id, cluster_revision: selected!.event.revision, event_id: selected!.event.id, status: row?.status || 'MISSING', rationale: row?.status === 'MISSING' ? null : row || null,
        samples: state.records.filter(record => row?.content.evidence_ids.includes(record.id)), references: [],
        history: page([...state.snapshots].reverse().filter(entry => entry.rationales[group.id]).map(entry => ({ id: entry.event.id, revision: entry.event.revision, at: entry.event.at, actor: entry.event.actor, rationale: entry.rationales[group.id] })), params, 5) }
      checkRevision(command.expected_revision, revision)
      const next = copy(snapshot!.rationales), content = command.action === 'ACCEPT' ? next[group.id]?.content : command.content
      if (!content?.reason.trim() || !content.evidence_ids.length || content.evidence_ids.length > 20 || content.evidence_ids.some(id => !group.record_ids.includes(id)) || !command.reason?.trim()) reject('INVALID_RATIONALE')
      next[group.id] = { ...next[group.id], content, source: command.action === 'ACCEPT' ? next[group.id].source : 'HUMAN', status: command.action === 'ACCEPT' || command.reviewed ? 'CONFIRMED' : 'DRAFT',
        reviewed_by: command.action === 'ACCEPT' || command.reviewed ? copy(user) : null, reviewed_at: command.action === 'ACCEPT' || command.reviewed ? now() : null,
        change_reason: command.reason, previous_event_id: snapshot!.event.id }
      return commit(state, 'EDIT', snapshot!.event.groups, next, command)
    }
    if (parts[6] !== 'clustering') reject('NOT_FOUND')
    if (parts[7] === 'tasks') {
      if (!body || !state.report.analysis_ready) reject('CLUSTER_BLOCKED')
      if (parts[8] === 'step') {
        const task = state.task || reject('NOT_FOUND')
        if (command.task_id !== task.id || command.expected_chunk !== task.next_chunk) reject('REVISION_CONFLICT')
        if (command.action === 'CANCEL') { task.status = 'CANCELLED'; return task }
        await draft(state, taskFields.get(task.id)); task.status = 'COMPLETED'; task.processed = state.records.length; task.next_chunk = 1; task.chunks[0].status = 'COMPLETED'
        return task
      }
      checkRevision(command.expected_revision, revision)
      const task: PipelineTask = { id: `demo-task-${crypto.randomUUID()}`, status: 'PENDING', record_count: state.records.length, processed: 0, next_chunk: 0, error: null,
        chunks: [{ index: 0, start: 0, stop: state.records.length, record_count: state.records.length, input_sha256: state.sha, status: 'PENDING' }] }
      state.task = task; taskFields.set(task.id, command.identity_fields || ['object', 'check_point', 'mode'])
      return task
    }
    if (parts[7] === 'history') return params.get('removed') === 'true' ? page(removedGroups(state), params) : page([...state.snapshots].reverse().map(brief), params)
    const selected = selectedSnapshot(state, params)
    if (parts[7] === 'groups') {
      const items: GroupBrief[] = (selected?.event.groups || []).map(group => {
        const row = selected!.rationales[group.id]
        return { id: group.id, label: group.label, record_count: group.record_ids.length, status: groupStatus(state, group.id, selected!.event.revision), revision: state.reviews[group.id]?.length || 0,
          example: state.records.find(record => record.id === group.record_ids[0])?.fields || {}, rationale: { status: row?.status || 'MISSING', reason: row?.content.reason || '', reason_truncated: false, reason_omitted_chars: 0, source: row?.source || null, evidence_count: row?.content.evidence_ids.length || 0 } }
      }).filter(group => group.label.toLowerCase().includes((params.get('search') || '').toLowerCase())
        && (!params.get('status') || (params.get('status') === 'UNREVIEWED' ? !['PASS', 'FAIL'].includes(group.status) : group.status === params.get('status')))
        && (!params.get('rationale_status') || (params.get('rationale_status') === 'UNRESOLVED' ? group.rationale.status !== 'CONFIRMED' : group.rationale.status === params.get('rationale_status'))))
      return page(items, params)
    }
    if (parts[7] === 'records') {
      const group = params.get('group_id') ? selected?.event.groups.find(group => group.id === params.get('group_id')) || reject('NOT_FOUND') : null
      const records = state.records.filter(record => (!group || group.record_ids.includes(record.id)) && record.raw.toLowerCase().includes((params.get('search') || '').toLowerCase()))
      const statuses = group ? state.records.filter(record => group.record_ids.includes(record.id)).map(record => record.check_status) : []
      return { ...page(records.map(record => ({ ...record, raw_display_truncated: false })), params, 50), cluster_revision: selected?.event.revision || 0,
        group: group ? { id: group.id, label: group.label, record_count: group.record_ids.length, check_status: statuses.includes('FAIL') ? 'FAIL' : statuses.includes('UNKNOWN') ? 'UNKNOWN' : 'PASS' } : null }
    }
    if (!body || !snapshot) reject('CLUSTER_NOT_READY')
    checkRevision(command.expected_revision, revision)
    if (parts[7] === 'operations') {
      const operation = command.operation || reject('INVALID_REQUEST'), groups = copy(snapshot.event.groups), rationales = copy(snapshot.rationales)
      const destination = groups.find(group => group.id === operation.destination_id)
      if (operation.kind === 'RENAME') {
        if (!destination || !operation.label?.trim()) reject('INVALID_REQUEST')
        destination.label = operation.label.trim()
      } else if (operation.kind === 'MOVE') {
        const ids = operation.record_ids || []
        if (!ids.length || new Set(ids).size !== ids.length || ids.some(id => !state.records.some(record => record.id === id))) reject('INVALID_PARTITION')
        if (!destination && (!operation.new_group_id || groups.some(group => group.id === operation.new_group_id) || !operation.label?.trim())) reject('INVALID_REQUEST')
        groups.forEach(group => { group.record_ids = group.record_ids.filter(id => !ids.includes(id)) })
        if (destination) destination.record_ids.push(...ids)
        else groups.push({ id: operation.new_group_id!, label: operation.label!.trim(), record_ids: ids })
      } else if (operation.kind === 'MERGE') {
        const ids = operation.group_ids || []
        if (!destination || ids.length < 2 || !ids.includes(destination.id) || ids.some(id => !groups.some(group => group.id === id))) reject('INVALID_REQUEST')
        destination.record_ids = groups.filter(group => ids.includes(group.id)).flatMap(group => group.record_ids)
        groups.forEach(group => { if (group.id !== destination.id && ids.includes(group.id)) group.record_ids = [] })
      } else reject('INVALID_REQUEST')
      const remaining = groups.filter(group => group.record_ids.length)
      for (const group of remaining) {
        const before = snapshot.event.groups.find(previous => previous.id === group.id)
        if (rationales[group.id] && JSON.stringify(before) !== JSON.stringify(group)) rationales[group.id].status = 'STALE'
        if (!rationales[group.id]) rationales[group.id] = { content: { reason: '', evidence_ids: [], reference_groups: [] }, source: 'HUMAN', status: 'MISSING', binding: { source_sha256: state.sha, cluster_event_id: '', cluster_revision: revision, group_id: group.id, label: group.label, membership_sha256: '' }, reviewed_by: null, reviewed_at: null, change_reason: command.reason || '', previous_event_id: snapshot.event.id }
      }
      const kept = Object.fromEntries(remaining.map(group => [group.id, rationales[group.id]]))
      return commit(state, 'EDIT', remaining, kept, command)
    }
    if (command.action === 'CONFIRM') {
      if (!state.report.analysis_ready) reject('CLUSTER_BLOCKED')
      if (Object.values(snapshot.rationales).some(row => row.status === 'MISSING' || row.status === 'STALE')) reject('RATIONALE_REQUIRED')
      return commit(state, 'CONFIRM', snapshot.event.groups, snapshot.rationales, command)
    }
    if (command.action === 'WITHDRAW') return commit(state, 'WITHDRAW', snapshot.event.groups, snapshot.rationales, command)
    return reject('INVALID_REQUEST')
  }
  const taskFields = new Map<string, string[]>()
  return async <T,>(path: string, options: { body?: unknown; signal?: AbortSignal } = {}): Promise<T> => {
    options.signal?.throwIfAborted()
    const command = options.body as Command | undefined
    const key = command?.request_id ? `${user.id}:${path}:${command.request_id}` : null
    const previous = key ? receipts.get(key) : null, serialized = JSON.stringify(options.body)
    if (previous) {
      if (previous.command !== serialized) reject('REQUEST_CONFLICT')
      return copy(previous.value) as T
    }
    const result = copy(await dispatch(path, options.body))
    if (key) receipts.set(key, { command: serialized, value: result })
    return result as T
  }
}

export const workspaceDemoRequest = makeWorkspaceDemo()
