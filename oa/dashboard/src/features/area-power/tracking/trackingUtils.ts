import { array, cornerDecimal, object, text, type Upload } from '../api'
import type { AtlasProject, AtlasVersion } from '@/features/atlas/atlasContext'
import type { CornerSpec, TrackingBlockRow } from '../trackingApi'
import { baselineSchema } from '../comparison/hierarchyUtils'

export { baselineSchema }

export interface ResolvedVersionMeta {
  netVer: string
  revision: string
  layout: string
  ecos: string[]
  isPor: boolean
}

export function parseCornerSpec(raw: unknown): CornerSpec {
  const o = object(raw)
  const label = text(o.label) || text(o.process_label)
  const processLabel = text(o.process_label) || text(o.label)
  return {
    label,
    process_label: processLabel,
    voltage_v: text(o.voltage_v) || null,
    temp_c: text(o.temp_c) || null,
    pex: text(o.pex) || null,
  }
}

export function parseCornerSpecList(raw: unknown): CornerSpec[] {
  return array(raw).map(parseCornerSpec)
}

export function uploadEcoNum(upload?: Upload | null): string {
  if (!upload) return ''
  const meta = object(object(upload.input_contract).metadata)
  return text(meta.eco_num).trim()
}

export function versionEcoNumbers(version?: AtlasVersion | null, upload?: Upload | null): string[] {
  const own = version?.eco_num?.trim()
  if (own) return [own]
  if (version) {
    const received = (version.ecos || []).filter((item): item is string => Boolean(item && item.trim()))
    if (received.length) return [...new Set(received)]
    const declared = (version.declared_ecos || []).filter((item): item is string => Boolean(item && item.trim()))
    if (declared.length) return [...new Set(declared)]
  }
  const fromUpload = uploadEcoNum(upload)
  return fromUpload ? [fromUpload] : []
}

export function resolveBaselineVersionMeta(
  cfg: Record<string, unknown> | undefined,
  targetProject: AtlasProject | undefined,
  targetBlockAreaId: string,
): ResolvedVersionMeta | null {
  if (!cfg) return null
  const bvid = text(cfg.baseline_version_id) || text(object(array(cfg.approved_baselines)[0]).version_id)
  const blk = targetProject?.blocks.find(b => b.area_id === targetBlockAreaId)
  const ver = bvid ? blk?.versions.find(v => v.area_id === bvid) : undefined
  if (ver) {
    return {
      netVer: ver.net_ver || '',
      revision: ver.revision || '',
      layout: ver.layout || '',
      ecos: versionEcoNumbers(ver),
      isPor: Boolean(blk?.por?.version_id && ver.id === blk.por.version_id),
    }
  }
  const info = object(cfg.baseline_version_info)
  if (text(info.net_ver) || text(info.revision)) {
    const eco = text(info.eco_num).trim()
    return {
      netVer: text(info.net_ver),
      revision: text(info.revision),
      layout: text(info.layout),
      ecos: eco ? [eco] : [],
      isPor: false,
    }
  }
  return null
}

export function uploadMatchesCorner(u: Upload, c: CornerSpec): boolean {
  if ((u.process_label || '').trim() !== c.process_label.trim()) return false
  if (c.voltage_v && cornerDecimal(u.voltage_v?.trim()) !== cornerDecimal(c.voltage_v.trim())) return false
  if (c.temp_c && cornerDecimal(u.temp_c?.trim()) !== cornerDecimal(c.temp_c.trim())) return false
  if (c.pex && (u.pex || '').trim() !== c.pex.trim()) return false
  return true
}

export function resolveBaselineVersionLabel(
  cfg: Record<string, unknown> | undefined,
  targetProject: AtlasProject | undefined,
  targetBlockAreaId: string,
): string {
  const meta = resolveBaselineVersionMeta(cfg, targetProject, targetBlockAreaId)
  if (!meta) return ''
  const rev = [meta.revision, meta.layout].filter(Boolean).join(' ')
  const eco = meta.ecos.length ? `ECO ${meta.ecos.join(', ')}` : ''
  return [meta.netVer, rev, eco].filter(Boolean).join(' · ')
}

export function formatBlockBaselineVersion(
  cfg: Record<string, unknown>,
  blockAreaId: string,
  project: AtlasProject,
  baselineProject?: AtlasProject,
): string {
  const kind = text(cfg.comparison_kind)
  const sideProj = kind === 'previous_version' ? project : baselineProject
  const blkId = kind === 'previous_version' ? blockAreaId : text(cfg.baseline_block_id)
  return resolveBaselineVersionLabel(cfg, sideProj, blkId) || '미지정'
}

export function formatSignedPct(raw: unknown): string {
  const s = text(raw)
  if (!s) return ''
  return `${s.startsWith('-') || s.startsWith('+') ? '' : '+'}${s}%`
}

function blockSortRank(name: string, isFullChip: boolean): number {
  if (name.trim().toLowerCase() === 'chip_top') return 0
  if (isFullChip) return 1
  return 2
}

export function compareTrackingBlocks(
  a: { name: string; isFullChip: boolean },
  b: { name: string; isFullChip: boolean },
): number {
  const rankDiff = blockSortRank(a.name, a.isFullChip) - blockSortRank(b.name, b.isFullChip)
  if (rankDiff !== 0) return rankDiff
  const aName = a.name.trim()
  const bName = b.name.trim()
  return aName.localeCompare(bName, 'en', { numeric: true, sensitivity: 'base' })
    || aName.localeCompare(bName)
}

export function describeBlockBaseline(options: {
  blockAreaId: string
  currentBlockName: string
  cfg: Record<string, unknown> | undefined
  project: AtlasProject
  baselineProject?: AtlasProject
  baselineName: string
  baselineStatus: 'unset' | 'available' | 'unavailable'
}): { label: string; scopeTag?: string; emptyHint: string; version: ResolvedVersionMeta | null; configured: boolean } {
  const { blockAreaId, currentBlockName, cfg, project, baselineProject, baselineName, baselineStatus } = options
  const kind = text(cfg?.comparison_kind)
  if (kind === 'previous_version') {
    const version = resolveBaselineVersionMeta(cfg, project, blockAreaId)
    return {
      label: `${project.name} · ${currentBlockName}`,
      scopeTag: '이전 버전',
      emptyHint: '기준 버전 미지정',
      version,
      configured: Boolean(version),
    }
  }
  const baseBlockId = text(cfg?.baseline_block_id)
  const baseBlock = baselineProject?.blocks.find(b => b.area_id === baseBlockId)
  if (baseBlock) {
    const version = resolveBaselineVersionMeta(cfg, baselineProject, baseBlockId)
    return {
      label: `${baselineProject?.name || baselineName} · ${baseBlock.name}`,
      emptyHint: '기준 버전 미지정',
      version,
      configured: Boolean(version),
    }
  }
  if (baselineStatus === 'available') return { label: baselineName, emptyHint: '블록 기준 미지정', version: null, configured: false }
  return { label: '기준 프로젝트 미설정', emptyHint: '', version: null, configured: false }
}

export function resolveRoundVersionMeta(
  row: TrackingBlockRow,
  roundNetVer: string,
  project: AtlasProject,
  uploadById: Map<string, Upload>,
): ResolvedVersionMeta {
  const blk = project.blocks.find(b => b.area_id === row.block_id)
  if (blk) {
    const cornerUpload = row.corners
      .map(c => (c.upload_id ? uploadById.get(c.upload_id) : undefined))
      .find((u): u is Upload => Boolean(u))
    const matched = (cornerUpload && blk.versions.find(v => v.area_id === cornerUpload.version_id))
      || blk.versions.find(v => v.net_ver === roundNetVer && (!row.revision_label || v.revision === row.revision_label))
      || blk.versions.find(v => v.net_ver === roundNetVer)
    if (matched) {
      const ecos = versionEcoNumbers(matched, cornerUpload)
      const fallbackEcos = !ecos.length && row.eco_label ? row.eco_label.split(',').map(v => v.trim()).filter(Boolean) : ecos
      return {
        netVer: matched.net_ver || roundNetVer,
        revision: matched.revision || row.revision_label || '',
        layout: matched.layout || '',
        ecos: fallbackEcos,
        isPor: Boolean(blk.por?.version_id && matched.id === blk.por.version_id),
      }
    }
  }
  const rowEcos = row.eco_label ? row.eco_label.split(',').map(v => v.trim()).filter(Boolean) : []
  return { netVer: roundNetVer, revision: row.revision_label || '', layout: '', ecos: rowEcos, isPor: false }
}
