import type { AtlasCatalog } from '@/features/atlas/atlasContext'

export type Orient = 'N' | 'S' | 'E' | 'W' | 'FN' | 'FS' | 'FE' | 'FW'

export const DEF_TO_ICC2: Record<Orient, string> = {
  N: 'R0', S: 'R180', FN: 'MY', FS: 'MX', W: 'R90', E: 'R270', FW: 'MX90', FE: 'MY90',
}

export const BIST_PALETTE = [
  '#2563eb', '#dc2626', '#16a34a', '#d97706', '#9333ea',
  '#0891b2', '#db2777', '#4f46e5', '#65a30d', '#ea580c',
]

export interface MacroBox {
  inst_name: string
  master: string
  kind: 'sram' | 'ip' | 'subblock' | 'macro'
  llx: number
  lly: number
  width: number
  height: number
  orient: Orient
  status: 'FIXED' | 'PLACED'
  halo_um: [number, number, number, number]
  bist_group?: string | null
  bist_controller?: string | null
  hier_prefix?: string
  attributes?: Record<string, string | number | boolean>
}

export interface PinMarker {
  name: string
  direction: 'INPUT' | 'OUTPUT' | 'INOUT' | 'FEEDTHRU'
  x: number
  y: number
  layer?: string | null
  side: 'L' | 'R' | 'B' | 'T'
}

export interface RegionBox {
  name: string
  kind: 'region' | 'group' | 'blockage' | 'soft_blockage'
  points: [number, number][]
}

export interface ModuleRegionSummary {
  hier_path: string
  cell_count: number
  area_um2: number
  cx: number
  cy: number
  p10_x: number
  p10_y: number
  p90_x: number
  p90_y: number
  bins: [number, number, number][]
}

export interface BistGroupSummary {
  bist_group: string
  controller?: string | null
  member_count: number
  bbox_um: [number, number, number, number]
  max_manhattan_span_um: number
  dispersed_warning: boolean
}

export interface FloorplanScene {
  schema_version: number
  scope_kind: 'top' | 'subblock'
  design_name: string
  fp_ver: string
  prev_fp_ver?: string | null
  die_bbox: [number, number, number, number]
  die_polygon: [number, number][]
  macros: MacroBox[]
  pins: PinMarker[]
  regions: RegionBox[]
  density_grid_size: number
  density_grid: number[]
  module_regions: ModuleRegionSummary[]
  bist_summary: BistGroupSummary[]
  stats: Record<string, number>
}

export interface MacroOverride {
  inst_name: string
  llx: number
  lly: number
  orient: Orient
  halo_um?: [number, number, number, number] | null
  locked?: boolean
}

export interface ReviewPin {
  id: string
  number: number
  x: number
  y: number
  severity: 'PASS' | 'WARN' | 'ACTION'
  title: string
  comment: string
  resolved: boolean
  carried_from_fp_ver?: string | null
  author?: string
}

export interface DatapathWaypoint {
  label: string
  x: number
  y: number
}

export interface DatapathFlow {
  id: string
  name: string
  color: string
  waypoints: DatapathWaypoint[]
  note: string
}

export interface ChecklistTemplateItem {
  id: string
  title: string
  description: string
  active: boolean
}

export interface ChecklistVerdict {
  item_id: string
  title: string
  verdict: 'PENDING' | 'PASS' | 'WARN' | 'ACTION' | 'N/A'
  comment: string
}

export interface MetricSlice {
  area: Record<string, string | number | null | undefined>
  vth: Record<string, string | number | null | undefined>
  drive: {
    drv_sub1x_pct?: string | number | null
    drv_d1_d3_pct?: string | number | null
    drv_d4_plus_pct?: string | number | null
    bins?: Record<string, string | number>
  }
}

export interface FloorplanMetricsData {
  upload_id: string
  version_id: string
  original_filename?: string
  process_label?: string
  root: MetricSlice
  instances: (MetricSlice & { path: string; name: string; depth: number })[]
}

export interface PlacementProposal {
  id: string
  entity_id?: string
  row_version?: number
  title: string
  note: string
  overrides: MacroOverride[]
  changed_count: number
  out_of_bounds_count: number
  overlap_count: number
  created_by?: string
  created_at?: string
}

export interface FloorplanVersionSummary {
  id: string
  project_id: string
  block_id: string
  fp_ver: string
  prev_fp_ver?: string | null
  scope_kind: 'top' | 'subblock'
  title: string
  design_name: string
  master_artifact_id?: string | null
  linked_net_ver?: string | null
  linked_revision?: string | null
  linked_layout?: string | null
  linked_eco_num?: string | null
  linked_area_upload_id?: string | null
  stats: Record<string, number>
  overall_verdict: 'PENDING' | 'PASS' | 'WARN' | 'ACTION'
  open_action_pins: number
  created_at?: string
}

export interface ConfluenceTargetConfig {
  space_key: string
  parent_page_id: string
  raw_url: string
}

export interface FloorplanWorkspaceData {
  project_id: string
  project_name: string
  artifact_table_ready: boolean
  storage_mode?: 'di_design_artifact' | 'temp_tracking_fallback' | 'unavailable' // [TEMP_ARTIFACT_FALLBACK]
  versions: FloorplanVersionSummary[]
  checklist_template: {
    top_items: ChecklistTemplateItem[]
    subblock_items: ChecklistTemplateItem[]
  }
  project_confluence: ConfluenceTargetConfig
  block_confluence: Record<string, ConfluenceTargetConfig>
  available_area_uploads: { id: string; block_id: string; version_id: string; original_filename: string; process_label: string; created_at?: string }[]
  confluence_pat: { registered: boolean; revision: number; updated_at?: string | null }
}

export interface FloorplanVersionDetail {
  fp_version: FloorplanVersionSummary
  scene: FloorplanScene | null
  compare_scene: FloorplanScene | null
  attachments: { id: string; artifact_kind: string; view_name: string; title: string; caption: string; mime_type: string; byte_length: number; created_at?: string }[]
  review: {
    overall_verdict: 'PENDING' | 'PASS' | 'WARN' | 'ACTION'
    summary_note: string
    checklist: ChecklistVerdict[]
    review_pins: ReviewPin[]
    datapaths: DatapathFlow[]
  }
  carried_over: { prev_fp_ver?: string | null; pins: ReviewPin[]; checklist_issues: ChecklistVerdict[] }
  proposals: PlacementProposal[]
  metrics: FloorplanMetricsData | null
  publishes: { id: string; publish_rev: number; title: string; space_key: string; parent_page_id: string; page_id: string; page_url?: string | null; attachment_count: number; total_attachment_bytes: number; created_at?: string }[]
}

export interface ConfluencePreviewData {
  title: string
  publish_rev: number
  space_key: string
  parent_page_id: string
  attachment_count: number
  total_attachment_bytes: number
  attachments: { filename: string; byte_length: number; comment: string }[]
  storage_xhtml: string
  standalone_html: string
}

export function orientedSize(width: number, height: number, orient: Orient): [number, number] {
  return orient === 'E' || orient === 'W' || orient === 'FE' || orient === 'FW' ? [height, width] : [width, height]
}

export function pin1Offset(width: number, height: number, orient: Orient): [number, number] {
  const [ow, oh] = orientedSize(width, height, orient)
  switch (orient) {
    case 'N': return [0, 0]
    case 'S': return [ow, oh]
    case 'FN': return [ow, 0]
    case 'FS': return [0, oh]
    case 'W': return [ow, 0]
    case 'E': return [0, oh]
    case 'FW': return [0, 0]
    case 'FE': return [ow, oh]
  }
}

export function flipOrientX(orient: Orient): Orient {
  const map: Record<Orient, Orient> = { N: 'FN', FN: 'N', S: 'FS', FS: 'S', E: 'FE', FE: 'E', W: 'FW', FW: 'W' }
  return map[orient]
}

export function flipOrientY(orient: Orient): Orient {
  const map: Record<Orient, Orient> = { N: 'FS', FS: 'N', S: 'FN', FN: 'S', E: 'FW', FW: 'E', W: 'FE', FE: 'W' }
  return map[orient]
}

export function rotateOrient90(orient: Orient): Orient {
  const map: Record<Orient, Orient> = { N: 'W', W: 'S', S: 'E', E: 'N', FN: 'FW', FW: 'FS', FS: 'FE', FE: 'FN' }
  return map[orient]
}

export interface EffectiveMacroBox extends MacroBox {
  locked: boolean
  changed: boolean
}

export function isMacroModified(orig: MacroBox, ov?: MacroOverride): boolean {
  if (!ov) return false
  if (Math.abs(ov.llx - orig.llx) > 1e-3 || Math.abs(ov.lly - orig.lly) > 1e-3 || ov.orient !== orig.orient) {
    return true
  }
  if (ov.halo_um && ov.halo_um.some((v, idx) => Math.abs(v - orig.halo_um[idx]) > 1e-3)) {
    return true
  }
  return false
}

export function evaluateSandboxState(scene: FloorplanScene, overrideMap: Map<string, MacroOverride>) {
  const [x0, y0, x1, y1] = scene.die_bbox
  const outOfBounds = new Set<string>()
  const overlapping = new Set<string>()
  let changedCount = 0
  let lockedCount = 0
  const effectiveMacros: EffectiveMacroBox[] = scene.macros.map(m => {
    const ov = overrideMap.get(m.inst_name)
    const changed = isMacroModified(m, ov)
    const locked = Boolean(ov?.locked)
    if (changed) changedCount++
    if (locked) lockedCount++
    return ov
      ? { ...m, llx: ov.llx, lly: ov.lly, orient: ov.orient, halo_um: ov.halo_um ?? m.halo_um, locked, changed }
      : { ...m, locked: false, changed: false }
  })

  const boxes = effectiveMacros.map(m => {
    const [ow, oh] = orientedSize(m.width, m.height, m.orient)
    const urx = m.llx + ow
    const ury = m.lly + oh
    if (m.llx < x0 - 0.01 || m.lly < y0 - 0.01 || urx > x1 + 0.01 || ury > y1 + 0.01) {
      outOfBounds.add(m.inst_name)
    }
    const [hl, hb, hr, ht] = m.halo_um
    return { name: m.inst_name, ax0: m.llx - hl, ay0: m.lly - hb, ax1: urx + hr, ay1: ury + ht }
  })

  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j]
      if (a.ax0 < b.ax1 - 0.01 && a.ax1 > b.ax0 + 0.01 && a.ay0 < b.ay1 - 0.01 && a.ay1 > b.ay0 + 0.01) {
        overlapping.add(a.name)
        overlapping.add(b.name)
      }
    }
  }

  // Live recomputed BIST group spans
  const byGroup = new Map<string, MacroBox[]>()
  for (const m of effectiveMacros) {
    if (m.bist_group) {
      const arr = byGroup.get(m.bist_group) || []
      arr.push(m)
      byGroup.set(m.bist_group, arr)
    }
  }
  const diag = Math.max((x1 - x0) + (y1 - y0), 1)
  const liveBist: BistGroupSummary[] = [...byGroup.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([grp, members]) => {
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity
    const centers: [number, number][] = []
    for (const m of members) {
      const [ow, oh] = orientedSize(m.width, m.height, m.orient)
      bx0 = Math.min(bx0, m.llx); by0 = Math.min(by0, m.lly)
      bx1 = Math.max(bx1, m.llx + ow); by1 = Math.max(by1, m.lly + oh)
      centers.push([m.llx + ow * 0.5, m.lly + oh * 0.5])
    }
    let maxSpan = 0
    for (let i = 0; i < centers.length; i++) {
      for (let j = i + 1; j < centers.length; j++) {
        const d = Math.abs(centers[i][0] - centers[j][0]) + Math.abs(centers[i][1] - centers[j][1])
        if (d > maxSpan) maxSpan = d
      }
    }
    return {
      bist_group: grp,
      controller: members.find(m => m.bist_controller)?.bist_controller || null,
      member_count: members.length,
      bbox_um: [Number(bx0.toFixed(2)), Number(by0.toFixed(2)), Number(bx1.toFixed(2)), Number(by1.toFixed(2))],
      max_manhattan_span_um: Number(maxSpan.toFixed(1)),
      dispersed_warning: members.length > 1 && maxSpan / diag > 0.45,
    }
  })

  return { effectiveMacros, outOfBounds, overlapping, liveBist, changedCount, lockedCount }
}

export function generateClientTcl(
  scene: FloorplanScene,
  overrideMap: Map<string, MacroOverride>,
  format: 'innovus' | 'icc2' | 'csv',
  title: string,
  onlyChanged = false,
): string {
  const { outOfBounds, overlapping } = evaluateSandboxState(scene, overrideMap)
  if (format === 'csv') {
    const rows = ['inst_name,master,kind,bist_group,orig_llx_um,orig_lly_um,orig_orient,orig_halo_um,new_llx_um,new_lly_um,new_orient,new_halo_um,dx_um,dy_um,locked,changed']
    for (const m of scene.macros) {
      const ov = overrideMap.get(m.inst_name)
      const ch = isMacroModified(m, ov)
      if (onlyChanged && !ch) continue
      const nx = ov ? ov.llx : m.llx, ny = ov ? ov.lly : m.lly, no = ov ? ov.orient : m.orient
      const nh = ov?.halo_um ?? m.halo_um
      const dx = nx - m.llx, dy = ny - m.lly
      rows.push(`${m.inst_name},${m.master},${m.kind},${m.bist_group || ''},${m.llx.toFixed(3)},${m.lly.toFixed(3)},${m.orient},${m.halo_um.join('/')},${nx.toFixed(3)},${ny.toFixed(3)},${no},${nh.join('/')},${dx.toFixed(3)},${dy.toFixed(3)},${ov?.locked ? 'Y' : 'N'},${ch ? 'Y' : 'N'}`)
    }
    return rows.join('\n')
  }
  const lines = [
    `# Design Atlas — ${format === 'innovus' ? 'Cadence Innovus' : 'Synopsys ICC2 / FC'} Hard Macro Placement Script (SRAM / IP / Macro / Sub-block)`,
    `# Design: ${scene.design_name} | FP_VER: ${scene.fp_ver} | Proposal: ${title} | Mode: ${onlyChanged ? 'MODIFIED_ONLY' : 'ALL_HARD_MACROS'}`,
    `# Out-of-bounds: ${outOfBounds.size} | Overlaps: ${overlapping.size}`,
    '',
  ]
  for (const m of scene.macros) {
    const ov = overrideMap.get(m.inst_name)
    const ch = isMacroModified(m, ov)
    if (onlyChanged && !ch) continue
    const nx = ov ? ov.llx : m.llx, ny = ov ? ov.lly : m.lly, no = ov ? ov.orient : m.orient
    const nh = ov?.halo_um ?? m.halo_um
    const haloChanged = Boolean(ov?.halo_um && ov.halo_um.some((v, idx) => Math.abs(v - m.halo_um[idx]) > 1e-3))
    const hasHalo = nh.some(v => v > 0)
    if (format === 'innovus') {
      lines.push(`placeInstance {${m.inst_name}} ${nx.toFixed(4)} ${ny.toFixed(4)} ${no} -fixed ;# [${m.kind.toUpperCase()}]${ch ? ' MODIFIED' : ''}`)
      if (hasHalo || haloChanged) {
        lines.push(`deleteHaloFromBlock {${m.inst_name}}`)
        if (hasHalo) lines.push(`addHaloToBlock ${nh[0].toFixed(3)} ${nh[1].toFixed(3)} ${nh[2].toFixed(3)} ${nh[3].toFixed(3)} {${m.inst_name}}`)
      }
    } else {
      lines.push(
        `# [${m.kind.toUpperCase()}] ${m.master}${ch ? ' (MODIFIED)' : ''}`,
        `set _da_cell [get_cells -quiet {${m.inst_name}}]`,
        `if {[sizeof_collection $_da_cell] > 0} {`,
        `  set_attribute $_da_cell orientation ${DEF_TO_ICC2[no]}`,
        `  set_attribute $_da_cell origin {${nx.toFixed(4)} ${ny.toFixed(4)}}`,
        `  set_attribute $_da_cell physical_status fixed`,
      )
      if (hasHalo || haloChanged) {
        lines.push(`  remove_keepout_margins -quiet [get_keepout_margins -quiet -of_objects $_da_cell]`)
        if (hasHalo) lines.push(`  create_keepout_margin -type hard -outer {${nh[0].toFixed(3)} ${nh[1].toFixed(3)} ${nh[2].toFixed(3)} ${nh[3].toFixed(3)}} $_da_cell`)
      }
      lines.push(`}`)
    }
  }
  if (format === 'icc2') lines.push('unset -nocomplain _da_cell')
  return lines.join('\n') + '\n'
}

// Synthetic in-memory demo store for /?demo=1
const demoState: {
  patRegistered: boolean
  patRevision: number
  reviews: Record<string, FloorplanVersionDetail['review']>
  proposals: Record<string, PlacementProposal[]>
  publishes: Record<string, FloorplanVersionDetail['publishes']>
  confluenceCfg: Record<string, ConfluenceTargetConfig>
  templates: Record<string, FloorplanWorkspaceData['checklist_template']>
} = {
  patRegistered: true,
  patRevision: 1,
  reviews: {},
  proposals: {},
  publishes: {},
  confluenceCfg: {},
  templates: {},
}

function buildDemoScene(isTop: boolean, blockName: string, fpVer: string): FloorplanScene {
  if (isTop) {
    return {
      schema_version: 1,
      scope_kind: 'top',
      design_name: blockName,
      fp_ver: fpVer,
      prev_fp_ver: fpVer === 'FP_v1.1' ? 'FP_v1.0' : null,
      die_bbox: [0, 0, 2400, 1800],
      die_polygon: [[0, 0], [2400, 0], [2400, 1800], [0, 1800]],
      macros: [
        { inst_name: 'u_cpu_core', master: 'CPU_CORE', kind: 'subblock', llx: 120, lly: 920, width: 920, height: 760, orient: 'N', status: 'FIXED', halo_um: [15, 15, 15, 15] },
        { inst_name: 'u_gpu_shader', master: 'GPU_SHADER', kind: 'subblock', llx: 1120, lly: 780, width: 1140, height: 900, orient: 'N', status: 'FIXED', halo_um: [15, 15, 15, 15] },
        { inst_name: 'u_npu_tile', master: 'NPU_TILE', kind: 'subblock', llx: 120, lly: 120, width: 920, height: 700, orient: 'FN', status: 'FIXED', halo_um: [15, 15, 15, 15] },
        { inst_name: 'u_ddr_phy_0', master: 'DDR5_PHY', kind: 'ip', llx: 1140, lly: 90, width: 520, height: 220, orient: 'N', status: 'FIXED', halo_um: [20, 20, 20, 20] },
        { inst_name: 'u_ddr_phy_1', master: 'DDR5_PHY', kind: 'ip', llx: 1720, lly: 90, width: 520, height: 220, orient: 'FN', status: 'FIXED', halo_um: [20, 20, 20, 20] },
        { inst_name: 'u_sys_pll', master: 'PLL_CORE', kind: 'ip', llx: 1140, lly: 380, width: 260, height: 240, orient: 'N', status: 'FIXED', halo_um: [30, 30, 30, 30] },
      ],
      pins: [
        { name: 'clk_ref_i', direction: 'INPUT', x: 0, y: 900, side: 'L' },
        { name: 'pcie_rx[0]', direction: 'INPUT', x: 0, y: 450, side: 'L' },
        { name: 'ddr_dq[0]', direction: 'INOUT', x: 1400, y: 0, side: 'B' },
        { name: 'dp_tx[0]', direction: 'OUTPUT', x: 2400, y: 1200, side: 'R' },
      ],
      regions: [{ name: 'TOP_CHANNEL_SPINE', kind: 'region', points: [[1050, 100], [1110, 100], [1110, 1700], [1050, 1700]] }],
      density_grid_size: 16,
      density_grid: Array.from({ length: 256 }, (_, i) => ((i * 17) % 45) + 15),
      module_regions: [],
      bist_summary: [],
      stats: { die_width_um: 2400, die_height_um: 1800, macro_count: 6, sram_count: 0, bist_group_count: 0, pin_count: 4 },
    }
  }
  const shift = fpVer === 'FP_v1.1' ? -60 : 0
  return {
    schema_version: 1,
    scope_kind: 'subblock',
    design_name: blockName,
    fp_ver: fpVer,
    prev_fp_ver: fpVer === 'FP_v1.2' ? 'FP_v1.1' : null,
    die_bbox: [0, 0, 1200, 900],
    die_polygon: [[0, 0], [1200, 0], [1200, 900], [0, 900]],
    macros: [
      { inst_name: 'u_l1/sram_tag_0', master: 'SRAM_256x64', kind: 'sram', llx: 60, lly: 640, width: 150, height: 190, orient: 'N', status: 'FIXED', halo_um: [6, 6, 6, 6], bist_group: 'MBIST_G0', bist_controller: 'u_mbist_0', hier_prefix: 'u_l1' },
      { inst_name: 'u_l1/sram_tag_1', master: 'SRAM_256x64', kind: 'sram', llx: 230, lly: 640, width: 150, height: 190, orient: 'FN', status: 'FIXED', halo_um: [6, 6, 6, 6], bist_group: 'MBIST_G0', bist_controller: 'u_mbist_0', hier_prefix: 'u_l1' },
      { inst_name: 'u_l1/sram_data_0', master: 'SRAM_512x128', kind: 'sram', llx: 60, lly: 380, width: 180, height: 220, orient: 'N', status: 'FIXED', halo_um: [8, 8, 8, 8], bist_group: 'MBIST_G0', bist_controller: 'u_mbist_0', hier_prefix: 'u_l1' },
      { inst_name: 'u_tex/sram_buf_0', master: 'SRAM_512x128', kind: 'sram', llx: 940, lly: 610, width: 190, height: 220, orient: 'FN', status: 'FIXED', halo_um: [8, 8, 8, 8], bist_group: 'MBIST_G1', bist_controller: 'u_mbist_1', hier_prefix: 'u_tex' },
      { inst_name: 'u_tex/sram_buf_1', master: 'SRAM_512x128', kind: 'sram', llx: 940, lly: 350, width: 190, height: 220, orient: 'FN', status: 'FIXED', halo_um: [8, 8, 8, 8], bist_group: 'MBIST_G1', bist_controller: 'u_mbist_1', hier_prefix: 'u_tex' },
      { inst_name: 'u_warp/rf_bank_0', master: 'RF_128x64', kind: 'sram', llx: 420, lly: 90, width: 130, height: 140, orient: 'N', status: 'FIXED', halo_um: [5, 5, 5, 5], bist_group: 'MBIST_G2', bist_controller: 'u_mbist_2', hier_prefix: 'u_warp' },
      { inst_name: 'u_warp/rf_bank_1', master: 'RF_128x64', kind: 'sram', llx: 960 + shift, lly: 90, width: 130, height: 140, orient: 'S', status: 'FIXED', halo_um: [5, 5, 5, 5], bist_group: 'MBIST_G2', bist_controller: 'u_mbist_2', hier_prefix: 'u_warp' },
      { inst_name: 'u_clk/pll_core', master: 'PLL_FRAC_N', kind: 'ip', llx: 60, lly: 90, width: 160, height: 150, orient: 'N', status: 'FIXED', halo_um: [20, 20, 20, 20], hier_prefix: 'u_clk' },
      { inst_name: 'u_phy/serdes_lane0', master: 'SERDES_RXTX', kind: 'ip', llx: 460, lly: 710, width: 210, height: 120, orient: 'FN', status: 'FIXED', halo_um: [15, 15, 15, 15], hier_prefix: 'u_phy' },
      { inst_name: 'u_sec/aes_crypto_macro', master: 'AES_CRYPTO_HARD', kind: 'macro', llx: 710, lly: 720, width: 140, height: 110, orient: 'N', status: 'FIXED', halo_um: [10, 10, 10, 10], hier_prefix: 'u_sec' },
    ],
    pins: [
      { name: 'in_cmd_valid', direction: 'INPUT', x: 0, y: 720, side: 'L' },
      { name: 'in_pkt_data[127:0]', direction: 'INPUT', x: 0, y: 520, side: 'L' },
      { name: 'out_pix_data[127:0]', direction: 'OUTPUT', x: 1200, y: 480, side: 'R' },
    ],
    regions: [
      { name: 'KEEP_OUT_CENTER', kind: 'soft_blockage', points: [[540, 410], [660, 410], [660, 510], [540, 510]] },
    ],
    density_grid_size: 16,
    density_grid: Array.from({ length: 256 }, (_, i) => {
      const gx = i % 16, gy = Math.floor(i / 16)
      return gx >= 5 && gx <= 11 && gy >= 4 && gy <= 12 ? 68 : 24
    }),
    module_regions: [
      { hier_path: 'u_l1', cell_count: 42100, area_um2: 58400, cx: 290, cy: 560, p10_x: 190, p10_y: 390, p90_x: 430, p90_y: 780, bins: [[5, 14, 80], [6, 15, 95], [7, 16, 75]] },
      { hier_path: 'u_alu_cluster', cell_count: 118400, area_um2: 164200, cx: 610, cy: 500, p10_x: 450, p10_y: 310, p90_x: 790, p90_y: 710, bins: [[13, 12, 90], [14, 13, 100], [15, 14, 88], [16, 13, 85]] },
      { hier_path: 'u_tex', cell_count: 64300, area_um2: 89200, cx: 830, cy: 510, p10_x: 720, p10_y: 340, p90_x: 930, p90_y: 730, bins: [[21, 13, 82], [22, 14, 94]] },
      { hier_path: 'u_warp', cell_count: 38900, area_um2: 51000, cx: 620, cy: 210, p10_x: 410, p10_y: 80, p90_x: 880, p90_y: 290, bins: [[13, 5, 76], [17, 5, 65]] },
    ],
    bist_summary: [
      { bist_group: 'MBIST_G0', controller: 'u_mbist_0', member_count: 3, bbox_um: [60, 380, 380, 830], max_manhattan_span_um: 415, dispersed_warning: false },
      { bist_group: 'MBIST_G1', controller: 'u_mbist_1', member_count: 2, bbox_um: [940, 350, 1130, 830], max_manhattan_span_um: 260, dispersed_warning: false },
      { bist_group: 'MBIST_G2', controller: 'u_mbist_2', member_count: 2, bbox_um: [420, 90, 1090 + shift, 230], max_manhattan_span_um: 540 + shift, dispersed_warning: fpVer === 'FP_v1.2' },
    ],
    stats: { die_width_um: 1200, die_height_um: 900, macro_count: 10, sram_count: 7, ip_count: 2, custom_macro_count: 1, bist_group_count: 3, pin_count: 3, std_cell_count: 263700 },
  }
}

export async function floorplanRequest<T>(
  authRequest: <R>(path: string, body?: Record<string, unknown>, plain?: boolean | 'blob', signal?: AbortSignal) => Promise<R>,
  demo: boolean,
  catalog: AtlasCatalog | null,
  path: string,
  body?: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  if (!demo) {
    return authRequest<T>(`/api/floorplan${path}`, body, false, signal)
  }

  // Synthetic memory demo handler
  const proj = catalog?.projects?.[0]
  const areaProjId = proj?.area_id || proj?.id || 'demo-proj'
  const blocks = proj?.blocks || []
  const topBlk = blocks[0]
  const subBlk = blocks[1] || blocks[0]

  const defaultTpl = demoState.templates[areaProjId] || {
    top_items: [
      { id: 'top_subblock_aspect', title: 'Sub-block 위치 · 크기 · 종횡비 적정성', description: '각 Sub-block 경계와 인접 배치 확인', active: true },
      { id: 'top_hard_ip', title: 'Hard IP / PHY / PLL 배치 및 Keepout', description: '고속 IP 위치 및 격리 확인', active: true },
      { id: 'top_channel_feedthru', title: '블록 간 라우팅 채널 · Feedthrough 여유', description: '협소 채널 병목 확인', active: true },
    ],
    subblock_items: [
      { id: 'sub_macro_halo', title: 'Memory / Macro 배치 · 방향(Pin-1) · Halo/Channel', description: 'SRAM 핀 방향 및 채널 폭 확인', active: true },
      { id: 'sub_bist_cohesion', title: 'Memory BIST Group 응집도 검토', description: '동일 BIST 그룹 메모리 분산 여부 확인', active: true },
      { id: 'sub_datapath_flow', title: 'Data-path 흐름 정합성 (In → Logic → Memory → Out)', description: '데이터 흐름 정합성 확인', active: true },
      { id: 'sub_inst_locality', title: '주요 Sub-instance 밀집도 및 물리 위치', description: '핵심 인스턴스 응집 확인', active: true },
      { id: 'sub_metrics_health', title: 'Area · LVT Ratio · Drive Strength 분포 건전성', description: '면적, LVT 비율, Drive 분포 확인', active: true },
    ],
  }

  if (path === '/pat' && !body) {
    return { registered: demoState.patRegistered, revision: demoState.patRevision } as T
  }
  if (path === '/pat' && body) {
    demoState.patRegistered = true
    demoState.patRevision += 1
    return { registered: true, revision: demoState.patRevision } as T
  }
  if (path === '/pat/remove' && body) {
    demoState.patRegistered = false
    demoState.patRevision += 1
    return { registered: false, revision: demoState.patRevision } as T
  }

  const wsMatch = /^\/projects\/([^/]+)\/workspace/.exec(path)
  if (wsMatch && !body) {
    const url = new URL(`http://demo.local${path}`)
    const bid = url.searchParams.get('block_id') || ''
    const allVers: FloorplanVersionSummary[] = []
    if (topBlk) {
      allVers.push({
        id: 'demo-fp-top-11', project_id: areaProjId, block_id: topBlk.area_id || topBlk.id,
        fp_ver: 'FP_v1.1', prev_fp_ver: 'FP_v1.0', scope_kind: 'top', title: `${proj?.name || 'SOC'} TOP · FP_v1.1`,
        design_name: proj?.name || 'SOC_TOP', linked_net_ver: 'NET_02', stats: { die_width_um: 2400, die_height_um: 1800, macro_count: 6, sram_count: 0 },
        overall_verdict: demoState.reviews['demo-fp-top-11']?.overall_verdict || 'PASS', open_action_pins: 0,
      })
    }
    if (subBlk) {
      allVers.push(
        {
          id: 'demo-fp-sub-12', project_id: areaProjId, block_id: subBlk.area_id || subBlk.id,
          fp_ver: 'FP_v1.2', prev_fp_ver: 'FP_v1.1', scope_kind: 'subblock', title: `${subBlk.name} · FP_v1.2`,
          design_name: subBlk.name, linked_net_ver: 'NET_02', stats: { die_width_um: 1200, die_height_um: 900, macro_count: 7, sram_count: 7 },
          overall_verdict: demoState.reviews['demo-fp-sub-12']?.overall_verdict || 'WARN', open_action_pins: 1,
        },
        {
          id: 'demo-fp-sub-11', project_id: areaProjId, block_id: subBlk.area_id || subBlk.id,
          fp_ver: 'FP_v1.1', prev_fp_ver: null, scope_kind: 'subblock', title: `${subBlk.name} · FP_v1.1`,
          design_name: subBlk.name, linked_net_ver: 'NET_01', stats: { die_width_um: 1200, die_height_um: 900, macro_count: 7, sram_count: 7 },
          overall_verdict: 'ACTION', open_action_pins: 1,
        },
      )
    }
    return {
      project_id: areaProjId,
      project_name: proj?.name || 'DemoProject',
      artifact_table_ready: true,
      versions: bid ? allVers.filter(v => v.block_id === bid) : allVers,
      checklist_template: defaultTpl,
      project_confluence: demoState.confluenceCfg['project'] || { space_key: 'SOCFP', parent_page_id: '102030', raw_url: 'https://confluence.example/pages/viewpage.action?pageId=102030' },
      block_confluence: demoState.confluenceCfg,
      available_area_uploads: [{ id: 'demo-up-1', block_id: subBlk?.area_id || '', version_id: 'demo-ver-1', original_filename: 'area_power_net02.csv', process_label: 'TT' }],
      confluence_pat: { registered: demoState.patRegistered, revision: demoState.patRevision },
    } as T
  }

  const tplMatch = /^\/projects\/([^/]+)\/checklist-template$/.exec(path)
  if (tplMatch && body) {
    demoState.templates[areaProjId] = body as unknown as FloorplanWorkspaceData['checklist_template']
    return demoState.templates[areaProjId] as T
  }

  const cfgMatch = /^\/projects\/([^/]+)\/confluence-config$/.exec(path)
  if (cfgMatch && body) {
    const raw = String(body.parent_url_or_id || '')
    const m = /pageId=(\d+)|\/pages\/(\d+)|^(\d+)$/.exec(raw.trim())
    const pid = m ? (m[1] || m[2] || m[3]) : '102030'
    const key = body.block_id ? String(body.block_id) : 'project'
    demoState.confluenceCfg[key] = { space_key: String(body.space_key || 'SOCFP'), parent_page_id: pid, raw_url: raw }
    return demoState.confluenceCfg[key] as T
  }

  const verMatch = /^\/versions\/([^/?]+)/.exec(path)
  if (verMatch && !body) {
    const vid = verMatch[1]
    const isTop = vid.includes('top')
    const blkName = isTop ? (proj?.name || 'SOC_TOP') : (subBlk?.name || 'SUB_BLK')
    const fpVer = vid.endsWith('11') && !isTop ? 'FP_v1.1' : (isTop ? 'FP_v1.1' : 'FP_v1.2')
    const scene = buildDemoScene(isTop, blkName, fpVer)
    const savedRev = demoState.reviews[vid] || {
      overall_verdict: isTop ? 'PASS' : 'WARN',
      summary_note: isTop ? 'Sub-block 채널 간격 양호' : 'MBIST_G2의 rf_bank_1 우하단 이격 확인 필요',
      checklist: [],
      review_pins: isTop ? [] : [{ id: 'p1', number: 1, x: 1020, y: 160, severity: 'ACTION', title: 'MBIST_G2 rf_bank_1 이격 과다', comment: 'rf_bank_0 옆으로 이동 검토 (Designer 배치 툴 확인)', resolved: false }],
      datapaths: isTop ? [] : [{ id: 'dp1', name: 'Main Shader Pipe', color: '#2563eb', waypoints: [{ label: 'in_pkt_data', x: 10, y: 520 }, { label: 'u_l1', x: 290, y: 560 }, { label: 'u_alu_cluster', x: 610, y: 500 }, { label: 'u_tex', x: 830, y: 510 }, { label: 'out_pix_data', x: 1190, y: 480 }], note: '좌→우 직선 플로우' }],
    }
    return {
      fp_version: {
        id: vid, project_id: areaProjId, block_id: (isTop ? topBlk : subBlk)?.area_id || 'blk',
        fp_ver: fpVer, prev_fp_ver: fpVer === 'FP_v1.2' ? 'FP_v1.1' : null, scope_kind: isTop ? 'top' : 'subblock',
        title: `${blkName} · ${fpVer}`, design_name: blkName, linked_net_ver: 'NET_02', stats: scene.stats,
        overall_verdict: savedRev.overall_verdict, open_action_pins: savedRev.review_pins.filter(p => !p.resolved && p.severity === 'ACTION').length,
      },
      scene,
      compare_scene: path.includes('compare_fp_version_id=') ? buildDemoScene(isTop, blkName, 'FP_v1.1') : null,
      attachments: [],
      review: savedRev,
      carried_over: { prev_fp_ver: 'FP_v1.1', pins: [], checklist_issues: [] },
      proposals: demoState.proposals[vid] || [],
      metrics: {
        upload_id: 'demo-up-1', version_id: 'demo-ver-1', original_filename: 'area_power_net02.csv', process_label: 'TT',
        root: {
          area: { total_area: '482600', logic_area: '362800', sram_area: '98400', cts_area: '21400', cts_area_pct: '4.43', gate_count: 17276190 },
          vth: { vt_slvt_pct: '8.4', vt_lvt_pct: '29.6', vt_rvt_pct: '48.2', vt_hvt_pct: '13.8' },
          drive: { drv_sub1x_pct: '11.2', drv_d1_d3_pct: '72.5', drv_d4_plus_pct: '16.3', bins: { D0P5: '3.2', D0P7: '8.0', D1: '34.5', D2: '24.0', D3: '14.0', D4: '9.1', D6: '4.4', D8: '2.1', D12: '0.7' } },
        },
        instances: [
          { path: 'u_l1', name: 'u_l1', depth: 1, area: { total_area: '112000', logic_area: '58400', sram_area: '53600', cts_area_pct: '3.8', gate_count: 2780952 }, vth: { vt_slvt_pct: '6.0', vt_lvt_pct: '24.0', vt_rvt_pct: '55.0', vt_hvt_pct: '15.0' }, drive: { drv_sub1x_pct: '14.0', drv_d1_d3_pct: '74.0', drv_d4_plus_pct: '12.0', bins: { D1: '38.0', D2: '24.0', D4: '8.0' } } },
          { path: 'u_alu_cluster', name: 'u_alu_cluster', depth: 1, area: { total_area: '164200', logic_area: '164200', sram_area: '0', cts_area_pct: '5.1', gate_count: 7819047 }, vth: { vt_slvt_pct: '12.8', vt_lvt_pct: '36.4', vt_rvt_pct: '41.8', vt_hvt_pct: '9.0' }, drive: { drv_sub1x_pct: '7.5', drv_d1_d3_pct: '69.0', drv_d4_plus_pct: '23.5', bins: { D1: '30.0', D2: '25.0', D4: '13.5', D8: '6.2' } } },
          { path: 'u_tex', name: 'u_tex', depth: 1, area: { total_area: '131000', logic_area: '89200', sram_area: '41800', cts_area_pct: '4.1', gate_count: 4247619 }, vth: { vt_slvt_pct: '5.2', vt_lvt_pct: '26.8', vt_rvt_pct: '51.0', vt_hvt_pct: '17.0' }, drive: { drv_sub1x_pct: '13.2', drv_d1_d3_pct: '74.8', drv_d4_plus_pct: '12.0', bins: { D1: '36.0', D2: '25.8', D4: '8.0' } } },
          { path: 'u_warp', name: 'u_warp', depth: 1, area: { total_area: '75400', logic_area: '51000', sram_area: '24400', cts_area_pct: '4.6', gate_count: 2428571 }, vth: { vt_slvt_pct: '7.0', vt_lvt_pct: '25.0', vt_rvt_pct: '50.0', vt_hvt_pct: '18.0' }, drive: { drv_sub1x_pct: '12.0', drv_d1_d3_pct: '75.0', drv_d4_plus_pct: '13.0', bins: { D1: '35.0', D2: '26.0', D4: '9.0' } } },
        ],
      },
      publishes: demoState.publishes[vid] || [],
    } as T
  }

  const revSaveMatch = /^\/versions\/([^/]+)\/review$/.exec(path)
  if (revSaveMatch && body) {
    demoState.reviews[revSaveMatch[1]] = body as unknown as FloorplanVersionDetail['review']
    return { id: 'rev-1', ...body } as T
  }

  const propSaveMatch = /^\/versions\/([^/]+)\/proposals$/.exec(path)
  if (propSaveMatch && body) {
    const vid = propSaveMatch[1]
    const list = demoState.proposals[vid] || []
    const item: PlacementProposal = {
      id: `prop-${list.length + 1}`,
      title: String(body.title || 'Proposal'),
      note: String(body.note || ''),
      overrides: (body.overrides as MacroOverride[]) || [],
      changed_count: ((body.overrides as MacroOverride[]) || []).length,
      out_of_bounds_count: 0,
      overlap_count: 0,
      created_by: 'demo_user',
      created_at: new Date().toISOString(),
    }
    demoState.proposals[vid] = [item, ...list]
    return item as T
  }

  const prevMatch = /^\/versions\/([^/]+)\/confluence-preview$/.exec(path)
  if (prevMatch && body) {
    const vid = prevMatch[1]
    const revNo = (demoState.publishes[vid]?.length || 0) + 1
    const title = `[${proj?.name || 'Atlas'}][${subBlk?.name || 'Block'}] Floorplan Review - FP_v1.2 (Rev.${revNo} · 합성 미리보기)`
    const atts = [
      ...(body.include_overview !== false ? [{ filename: 'fp_01_overview.png', byte_length: 62400, comment: 'Floorplan Overview' }] : []),
      ...(body.include_bist !== false ? [{ filename: 'fp_02_bist_groups.png', byte_length: 54800, comment: 'Memory BIST Groups' }] : []),
    ]
    return {
      title,
      publish_rev: revNo,
      space_key: 'SOCFP',
      parent_page_id: '102030',
      attachment_count: atts.length,
      total_attachment_bytes: atts.reduce((s, a) => s + a.byte_length, 0),
      attachments: atts,
      storage_xhtml: `<p><strong>${title}</strong></p>`,
      standalone_html: `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title></head><body><h1>${title}</h1><p>합성 데모 내보내기 문서입니다.</p></body></html>`,
    } as T
  }

  const pubMatch = /^\/versions\/([^/]+)\/confluence-publish$/.exec(path)
  if (pubMatch && body) {
    const vid = pubMatch[1]
    const list = demoState.publishes[vid] || []
    const revNo = list.length + 1
    const entry = {
      id: `pub-${revNo}`,
      publish_rev: revNo,
      title: `[${proj?.name || 'Atlas'}] Floorplan Review (Rev.${revNo})`,
      space_key: 'SOCFP',
      parent_page_id: '102030',
      page_id: String(900100 + revNo),
      page_url: `https://confluence.example/pages/viewpage.action?pageId=${900100 + revNo}`,
      attachment_count: 2,
      total_attachment_bytes: 117200,
      created_at: new Date().toISOString(),
    }
    demoState.publishes[vid] = [entry, ...list]
    return entry as T
  }

  return {} as T
}
