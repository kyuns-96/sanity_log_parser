// Synthetic, browser-memory data for the workflow demo. This is not an auth service.
export type DemoUser = { id: string; name: string; team: string; employee: string; admin?: boolean }
export type CheckResult = 'PASS' | 'FAIL' | 'MISSING'
export type DemoCheck = { id: string; name: string; result: CheckResult }
export type DemoBlock = { id: string; name: string; checks: DemoCheck[] }
export type DemoProject = { id: string; key: string; name: string; description: string; blocks: DemoBlock[] }
export type ScopeGrant = { userId: string; projectId: string; scope: 'project' | 'block'; blockId?: string; role: 'top' | 'reviewer' | 'manager' }
export type AccessEvent = { id: string; projectId: string; actor: string; text: string; at: string }
export type AccessState = { projects: DemoProject[]; grants: ScopeGrant[]; events: AccessEvent[] }
export type BlockDraft = { id: string; name: string; owners: string[]; checks: string[] }
export type ProjectDraft = { key: string; name: string; description: string; tops: string[]; blocks: BlockDraft[] }

export const demoUsers: DemoUser[] = [
  { id: 'u-admin', name: '정민수', team: '운영팀', employee: 'DEMO-001', admin: true },
  { id: 'u-lee', name: '이영희', team: 'SoC 설계팀', employee: 'DEMO-102' },
  { id: 'u-kim', name: '김철수', team: 'CPU 설계팀', employee: 'DEMO-203' },
  { id: 'u-park', name: '박민지', team: 'GPU 설계팀', employee: 'DEMO-204' },
  { id: 'u-choi', name: '최지우', team: 'SoC 설계팀', employee: 'DEMO-305' },
]
export const checkTemplates = ['GCA', 'Timing', 'Design checks']
export const person = (id: string) => demoUsers.find(user => user.id === id)!
export const checkLabels: Record<CheckResult, string> = { PASS: 'PASS', FAIL: 'FAIL', MISSING: '미수신' }

function block(id: string, name: string, results: CheckResult[]): DemoBlock {
  return { id, name, checks: checkTemplates.map((label, i) => ({ id: `${id}-${i}`, name: label, result: results[i] })) }
}
export function initialAccessState(): AccessState {
  return {
    projects: [
      { id: 'p-atlas', key: 'atlas', name: 'Atlas', description: '차세대 SoC · 사전 검증', blocks: [block('atlas-cpu', 'CPU', ['FAIL', 'PASS', 'PASS']), block('atlas-gpu', 'GPU', ['PASS', 'FAIL', 'PASS']), block('atlas-ddr', 'DDR', ['MISSING', 'PASS', 'PASS'])] },
      { id: 'p-boreal', key: 'boreal', name: 'Boreal', description: '모바일 플랫폼 · 블록 검증', blocks: [block('boreal-cpu', 'CPU', ['PASS', 'PASS', 'PASS']), block('boreal-ddr', 'DDR', ['FAIL', 'PASS', 'MISSING'])] },
    ],
    grants: [
      { userId: 'u-lee', projectId: 'p-atlas', scope: 'project', role: 'top' },
      { userId: 'u-kim', projectId: 'p-atlas', scope: 'block', blockId: 'atlas-cpu', role: 'reviewer' },
      { userId: 'u-park', projectId: 'p-atlas', scope: 'block', blockId: 'atlas-gpu', role: 'reviewer' },
      { userId: 'u-park', projectId: 'p-atlas', scope: 'block', blockId: 'atlas-ddr', role: 'reviewer' },
      { userId: 'u-park', projectId: 'p-boreal', scope: 'project', role: 'top' },
      { userId: 'u-kim', projectId: 'p-boreal', scope: 'block', blockId: 'boreal-ddr', role: 'reviewer' },
    ], events: [],
  }
}
export function roleLabel(state: AccessState, user: DemoUser) {
  if (user.admin) return '관리자'
  const grants = state.grants.filter(grant => grant.userId === user.id)
  if (grants.some(grant => grant.role === 'manager')) return '프로젝트 관리자'
  if (grants.some(grant => grant.role === 'top')) return 'TOP 담당자'
  return grants.length ? '블록 담당자' : '미배정 사용자'
}
export function canManageProject(state: AccessState, user: DemoUser, projectId: string) {
  return !!user.admin || state.grants.some(grant => grant.userId === user.id && grant.projectId === projectId && grant.role === 'manager' && grant.scope === 'project')
}
export function visibleBlocks(state: AccessState, user: DemoUser, project: DemoProject) {
  const grants = state.grants.filter(grant => grant.userId === user.id && grant.projectId === project.id)
  if (user.admin || grants.some(grant => grant.scope === 'project')) return project.blocks
  return project.blocks.filter(block => grants.some(grant => grant.scope === 'block' && grant.blockId === block.id))
}
export function visibleProjects(state: AccessState, user: DemoUser) {
  return state.projects.filter(project => user.admin || state.grants.some(grant => grant.userId === user.id && grant.projectId === project.id))
}
export function newProjectDraft(): ProjectDraft {
  return { key: 'orion', name: 'Orion', description: '신규 SoC 프로젝트', tops: ['u-lee'], blocks: [
    { id: crypto.randomUUID(), name: 'CPU', owners: ['u-kim'], checks: [...checkTemplates] },
    { id: crypto.randomUUID(), name: 'GPU', owners: ['u-park'], checks: [...checkTemplates] },
  ] }
}
export function validateProject(state: AccessState, draft: ProjectDraft, includeBlocks = true): string {
  if (!draft.name.trim() || draft.name.trim().length > 60) return '프로젝트 이름을 1~60자로 입력해 주세요.'
  if (!/^[a-z][a-z0-9_-]{1,39}$/.test(draft.key)) return '프로젝트 키는 영문 소문자로 시작하는 2~40자의 영문·숫자·하이픈·밑줄이어야 합니다.'
  if (state.projects.some(project => project.key === draft.key)) return '이미 등록된 프로젝트 키입니다. 다른 키를 입력해 주세요.'
  if (!draft.tops.length || draft.tops.some(id => !demoUsers.some(user => user.id === id))) return 'TOP 담당자를 한 명 이상 선택해 주세요.'
  if (!includeBlocks) return ''
  if (!draft.blocks.length) return '블록을 한 개 이상 등록해 주세요.'
  const names = draft.blocks.map(block => block.name.trim().toLowerCase())
  if (names.some(name => !name || name.length > 40) || new Set(names).size !== names.length) return '블록 이름은 1~40자로 입력하고, 프로젝트 안에서 중복되지 않게 해 주세요.'
  if (draft.blocks.some(block => !block.checks.length || block.checks.some(check => !checkTemplates.includes(check)))) return '블록마다 sanity item을 한 개 이상 선택해 주세요.'
  if (draft.blocks.some(block => block.owners.some(id => !demoUsers.some(user => user.id === id)))) return '등록된 사용자 계정을 선택해 주세요.'
  return ''
}
function event(user: DemoUser, projectId: string, text: string): AccessEvent {
  return { id: crypto.randomUUID(), projectId, actor: user.name, text, at: new Date().toISOString() }
}
export function createDemoProject(state: AccessState, user: DemoUser, draft: ProjectDraft): AccessState {
  if (!user.admin) throw new Error('프로젝트 생성 권한이 없습니다.')
  const error = validateProject(state, draft)
  if (error) throw new Error(error)
  const projectId = `p-${crypto.randomUUID()}`
  const project: DemoProject = { id: projectId, key: draft.key, name: draft.name.trim(), description: draft.description.trim(), blocks: draft.blocks.map(block => ({ id: block.id, name: block.name.trim(), checks: block.checks.map(name => ({ id: crypto.randomUUID(), name, result: 'MISSING' })) })) }
  const grants: ScopeGrant[] = [
    ...draft.tops.map(userId => ({ userId, projectId, scope: 'project' as const, role: 'top' as const })),
    ...draft.blocks.flatMap(block => block.owners.map(userId => ({ userId, projectId, scope: 'block' as const, blockId: block.id, role: 'reviewer' as const }))),
  ]
  return { projects: [...state.projects, project], grants: [...state.grants, ...grants], events: [event(user, projectId, `${project.name} 생성 · TOP ${draft.tops.map(id => person(id).name).join(', ')} · ${project.blocks.length}개 블록 배정`), ...state.events] }
}
export function saveDemoGrants(state: AccessState, user: DemoUser, projectId: string, grants: ScopeGrant[]): AccessState {
  const project = state.projects.find(project => project.id === projectId)
  if (!project || !canManageProject(state, user, projectId)) throw new Error('이 프로젝트의 담당자 관리 권한이 없습니다.')
  if (!grants.some(grant => grant.role === 'top')) throw new Error('TOP 담당자를 한 명 이상 유지해 주세요.')
  if (grants.some(grant => grant.projectId !== projectId || !demoUsers.some(user => user.id === grant.userId) ||
    (grant.role === 'reviewer' ? grant.scope !== 'block' || !project.blocks.some(block => block.id === grant.blockId) :
      !['top', 'manager'].includes(grant.role) || grant.scope !== 'project' || grant.blockId !== undefined))) throw new Error('담당 범위가 올바르지 않습니다.')
  const identity = (grant: ScopeGrant) => `${grant.userId}:${grant.role}:${grant.scope}:${grant.blockId || ''}`
  const old = state.grants.filter(grant => grant.projectId === projectId)
  const unique = [...new Map(grants.map(grant => [identity(grant), grant])).values()]
  const added = unique.filter(grant => !old.some(before => identity(before) === identity(grant)))
  const removed = old.filter(grant => !unique.some(after => identity(after) === identity(grant)))
  if (!added.length && !removed.length) return state
  const describe = (grant: ScopeGrant) => `${person(grant.userId).name} / ${grant.scope === 'block' ? project.blocks.find(block => block.id === grant.blockId)!.name : grant.role === 'top' ? 'TOP' : '프로젝트 관리'}`
  const changes = [...added.map(grant => `추가: ${describe(grant)}`), ...removed.map(grant => `해제: ${describe(grant)}`)].join(' · ')
  return { ...state, grants: [...state.grants.filter(grant => grant.projectId !== projectId), ...unique], events: [event(user, projectId, `${project.name} · ${changes}`), ...state.events] }
}
