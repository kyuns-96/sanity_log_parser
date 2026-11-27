/**
 * Data memory grouping (SRAM / RF only).
 *
 * Initial groups come from the parsed BIST group, falling back to the hierarchy prefix.
 * Reviewers can create, rename, delete groups and move memories between them; the
 * initial assignment is kept so the moves can be listed as feedback for the designer.
 * The state is held on the client until a delivery path (review save / report / export) is decided.
 */
import { BIST_PALETTE, orientedSize, type EffectiveMacroBox, type FloorplanScene } from './floorplanApi'

export interface MemoryGroup {
  id: string
  name: string
  color: string
  origin: 'bist' | 'hier' | 'user'
}

export interface MemoryGrouping {
  groups: MemoryGroup[]
  /** inst_name → group id (null = 미분류). */
  assign: Record<string, string | null>
  /** Assignment and group names at load time, for the change list. */
  initialAssign: Record<string, string | null>
  initialNames: Record<string, string>
}

export interface MemoryGroupStat {
  group: MemoryGroup
  members: EffectiveMacroBox[]
  bbox: [number, number, number, number] | null
  maxSpanUm: number
  dispersed: boolean
  movedIn: number
  movedOut: number
}

export interface MemoryMove {
  inst_name: string
  from: string
  to: string
}

export const UNGROUPED_LABEL = '미분류'

export function isMemory(m: { kind: string }) {
  return m.kind === 'sram'
}

function hierKey(m: { inst_name: string; hier_prefix?: string }) {
  if (m.hier_prefix) return m.hier_prefix
  const parts = m.inst_name.split('/')
  return parts.length > 1 ? parts.slice(0, -1).join('/') : ''
}

export function initMemoryGrouping(scene: FloorplanScene): MemoryGrouping {
  const groups: MemoryGroup[] = []
  const byKey = new Map<string, MemoryGroup>()
  const assign: Record<string, string | null> = {}
  for (const m of scene.macros) {
    if (!isMemory(m)) continue
    const origin: MemoryGroup['origin'] = m.bist_group ? 'bist' : 'hier'
    const name = m.bist_group || hierKey(m)
    if (!name) {
      assign[m.inst_name] = null
      continue
    }
    const key = `${origin}:${name}`
    let group = byKey.get(key)
    if (!group) {
      group = { id: `mg-${groups.length + 1}`, name, color: BIST_PALETTE[groups.length % BIST_PALETTE.length], origin }
      byKey.set(key, group)
      groups.push(group)
    }
    assign[m.inst_name] = group.id
  }
  return {
    groups,
    assign,
    initialAssign: { ...assign },
    initialNames: Object.fromEntries(groups.map(g => [g.id, g.name])),
  }
}

export function nextGroupColor(groups: MemoryGroup[]) {
  const used = new Set(groups.map(g => g.color))
  return BIST_PALETTE.find(c => !used.has(c)) || BIST_PALETTE[groups.length % BIST_PALETTE.length]
}

export function nextGroupName(groups: MemoryGroup[]) {
  const names = new Set(groups.map(g => g.name))
  for (let i = 1; ; i++) {
    const name = `MEM_G${i}`
    if (!names.has(name)) return name
  }
}

export function memoryGroupStats(grouping: MemoryGrouping, macros: EffectiveMacroBox[], dieBbox: [number, number, number, number]): MemoryGroupStat[] {
  const [x0, y0, x1, y1] = dieBbox
  const diag = Math.max(x1 - x0 + (y1 - y0), 1)
  return grouping.groups.map(group => {
    const members = macros.filter(m => grouping.assign[m.inst_name] === group.id)
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity
    const centers: [number, number][] = []
    for (const m of members) {
      const [ow, oh] = orientedSize(m.width, m.height, m.orient)
      bx0 = Math.min(bx0, m.llx); by0 = Math.min(by0, m.lly)
      bx1 = Math.max(bx1, m.llx + ow); by1 = Math.max(by1, m.lly + oh)
      centers.push([m.llx + ow / 2, m.lly + oh / 2])
    }
    let maxSpan = 0
    for (let i = 0; i < centers.length; i++) {
      for (let j = i + 1; j < centers.length; j++) {
        maxSpan = Math.max(maxSpan, Math.abs(centers[i][0] - centers[j][0]) + Math.abs(centers[i][1] - centers[j][1]))
      }
    }
    let movedIn = 0, movedOut = 0
    for (const [inst, gid] of Object.entries(grouping.assign)) {
      const before = grouping.initialAssign[inst] ?? null
      if (gid === group.id && before !== group.id) movedIn++
      if (before === group.id && gid !== group.id) movedOut++
    }
    return {
      group,
      members,
      bbox: members.length ? [bx0, by0, bx1, by1] : null,
      maxSpanUm: Math.round(maxSpan),
      dispersed: members.length > 1 && maxSpan / diag > 0.45,
      movedIn,
      movedOut,
    }
  })
}

export function memoryMoves(grouping: MemoryGrouping): MemoryMove[] {
  const nameOf = (gid: string | null, initial: boolean) => {
    if (!gid) return UNGROUPED_LABEL
    const live = grouping.groups.find(g => g.id === gid)?.name
    return initial ? grouping.initialNames[gid] ?? live ?? gid : live ?? grouping.initialNames[gid] ?? gid
  }
  return Object.keys(grouping.assign)
    .filter(inst => (grouping.initialAssign[inst] ?? null) !== (grouping.assign[inst] ?? null))
    .sort()
    .map(inst => ({ inst_name: inst, from: nameOf(grouping.initialAssign[inst] ?? null, true), to: nameOf(grouping.assign[inst] ?? null, false) }))
}

/** What the canvas uses to colour macros. */
export type ColorBy = 'kind' | 'bist' | 'memgroup'
