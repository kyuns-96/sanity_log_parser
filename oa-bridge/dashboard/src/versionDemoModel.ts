// Browser-memory examples. Version identifiers are opaque and owned by a block.
import { demoUsers, initialAccessState, visibleBlocks } from './accessDemoModel'
export type LayoutStage = 'PRE' | 'POST'
export type DemoResult = 'PASS' | 'FAIL'
export const initialEcoVersion = 'chip_finish'
export const versionItems = ['GCA', 'Timing', 'Design checks'] as const
export type VersionItem = typeof versionItems[number]
export const versionBlocks = ['CPU', 'GPU', 'DDR'] as const
export type VersionBlock = typeof versionBlocks[number]
export const versionProjectId = 'p-atlas'

// Reuse the account assignments shown in the access-flow demo.
const access = initialAccessState()
export const versionUsers = demoUsers.filter(user => ['u-lee', 'u-kim', 'u-park'].includes(user.id))
export function visibleVersionBlocks(userId: string): VersionBlock[] {
  const user = demoUsers.find(user => user.id === userId)
  const project = access.projects.find(project => project.id === versionProjectId)!
  return user ? visibleBlocks(access, user, project).map(block => block.name as VersionBlock) : []
}
export function blockOwners(block: VersionBlock, projectId = versionProjectId) {
  const entry = access.projects.find(project => project.id === projectId)?.blocks.find(entry => entry.name === block)
  return demoUsers.filter(user => access.grants.some(grant => grant.userId === user.id && grant.projectId === projectId
    && grant.scope === 'block' && grant.role === 'reviewer' && grant.blockId === entry?.id))
}

export type RevisionSet = {
  id: string; projectId: string; block: VersionBlock; netVer: string; revision: string; stage: LayoutStage;
  sourcePreId?: string;
}
// Each block's owner designates its PoR pair; neither an ECO nor a run is selected by PoR.
export type DemoPor = { projectId: string; block: VersionBlock; revisionSetId: string; assignedBy: string }
export type DesignSnapshot = { id: string; revisionSetId: string; ecoVersion: string | null }
export type VersionRun = {
  id: string; snapshotId: string; item: VersionItem; attempt: number; result: DemoResult;
  reviews: { revision: number; reviewer: string }[];
}
export type VersionState = { revisionSets: RevisionSet[]; snapshots: DesignSnapshot[]; runs: VersionRun[]; pors: DemoPor[] }

export function revisionSetOf(state: VersionState, id: string) {
  const set = state.revisionSets.find(set => set.id === id)
  if (!set) throw new Error('NET_VER·REVISION 세트를 찾을 수 없습니다.')
  return set
}
export function snapshotOf(state: VersionState, id: string) {
  const snapshot = state.snapshots.find(snapshot => snapshot.id === id)
  if (!snapshot) throw new Error('검사 대상 버전을 찾을 수 없습니다.')
  return snapshot
}
export function porOf(state: VersionState, block: VersionBlock, projectId = versionProjectId) {
  const por = state.pors.find(por => por.projectId === projectId && por.block === block)
  if (!por) throw new Error('블록의 PoR을 찾을 수 없습니다.')
  return por
}
export function snapshotsFor(state: VersionState, revisionSetId: string) {
  revisionSetOf(state, revisionSetId)
  return state.snapshots.filter(snapshot => snapshot.revisionSetId === revisionSetId)
}
export function latestRun(state: VersionState, snapshotId: string, item: VersionItem) {
  return state.runs.filter(run => run.snapshotId === snapshotId && run.item === item).at(-1)
}
export function canAssignPor(userId: string, set: RevisionSet) {
  return blockOwners(set.block, set.projectId).some(user => user.id === userId)
}
export function assignPor(state: VersionState, userId: string, revisionSetId: string): VersionState {
  const set = revisionSetOf(state, revisionSetId)
  if (!canAssignPor(userId, set)) throw new Error('이 블록의 담당자만 PoR을 지정할 수 있습니다.')
  const previous = porOf(state, set.block, set.projectId)
  if (previous.revisionSetId === set.id) return state
  return { ...state, pors: state.pors.map(por => por === previous ? { ...por, revisionSetId: set.id, assignedBy: userId } : por) }
}

export function initialVersionState(): VersionState {
  const revisionSets: RevisionSet[] = []
  const snapshots: DesignSnapshot[] = []
  const pors: DemoPor[] = []
  const versions: Record<VersionBlock, { netVer: string; revision: string; postfix: string }[]> = {
    CPU: [
      { netVer: 'EVT0_ML3_DEV00', revision: 'M3D0S1-hakhyun-Wsaif-v00-Cktran', postfix: 'beaifs-m5ps-BE' },
      { netVer: 'EVT0_ML3_DEV01', revision: 'M3D1S1-hakhyun-Wsaif-v00-Cktran', postfix: 'beaifs-m5ps-BE' },
      { netVer: 'EVT0_ML4_DEV00', revision: 'M4D0S1-hakhyun-Wsaif-v00-Cktran', postfix: 'beaifs-m5ps-BE' },
    ],
    GPU: [
      { netVer: 'EVT0_ML2_DEV01', revision: 'M2D1S2-minji-Wsaif-v00-Cktran', postfix: 'beaifs-m4ps-BE' },
      { netVer: 'EVT0_ML3_DEV00', revision: 'M3D0S2-minji-Wsaif-v01-Cktran', postfix: 'beaifs-m4ps-BE' },
    ],
    DDR: [
      { netVer: 'EVT0_ML1_DEV00', revision: 'M1D0S1-minji-Wsaif-v00-Cktran', postfix: 'beaifs-m3ps-BE' },
      { netVer: 'EVT0_ML2_DEV00', revision: 'M2D0S1-minji-Wsaif-v02-Cktran', postfix: 'beaifs-m3ps-BE' },
    ],
  }
  versionBlocks.forEach(block => {
    const slug = block.toLowerCase()
    versions[block].forEach((version, index) => {
      const preId = `set-${slug}-pre-${index}`, postId = `set-${slug}-post-${index}`
      revisionSets.push(
        { id: preId, projectId: versionProjectId, block, netVer: version.netVer, revision: version.revision, stage: 'PRE' },
        { id: postId, projectId: versionProjectId, block, netVer: version.netVer, revision: `${version.revision}_${version.postfix}`, stage: 'POST', sourcePreId: preId },
      )
      snapshots.push({ id: `${slug}-pre-${index}`, revisionSetId: preId, ecoVersion: null })
      const ecos = block === 'CPU' && index === 2 ? [initialEcoVersion, 'eco01', 'eco02', 'eco03'] : [initialEcoVersion, 'eco01']
      ecos.forEach(ecoVersion => snapshots.push({ id: `${slug}-post-${index}-${ecoVersion}`, revisionSetId: postId, ecoVersion }))
      if (index === versions[block].length - 1) pors.push({ projectId: versionProjectId, block, revisionSetId: postId, assignedBy: blockOwners(block)[0].id })
    })
  })
  const missing = new Set(['cpu-post-2-eco03', 'gpu-post-1-eco01', 'ddr-post-1-eco01'])
  const runs: VersionRun[] = snapshots.filter(snapshot => !missing.has(snapshot.id)).flatMap(snapshot => versionItems.map((item, index) => ({
    id: `run-${snapshot.id}-${index}`, snapshotId: snapshot.id, item, attempt: 1,
    result: snapshot.id === 'cpu-post-0-eco01' && item === 'Timing' ? 'FAIL' : 'PASS',
    reviews: [{ revision: 1, reviewer: '데모 검토자' }],
  })))
  return { revisionSets, snapshots, runs, pors }
}

export function reviewLatest(state: VersionState, userId: string, snapshotId: string): VersionState {
  const snapshot = snapshotOf(state, snapshotId)
  if (!canAssignPor(userId, revisionSetOf(state, snapshot.revisionSetId))) throw new Error('이 블록의 담당자만 검토할 수 있습니다.')
  const ids = versionItems.map(item => latestRun(state, snapshotId, item)?.id)
  const reviewer = demoUsers.find(user => user.id === userId)!.name
  return { ...state, runs: state.runs.map(run => ids.includes(run.id) && !run.reviews.length ? { ...run, reviews: [{ revision: 1, reviewer }] } : run) }
}
