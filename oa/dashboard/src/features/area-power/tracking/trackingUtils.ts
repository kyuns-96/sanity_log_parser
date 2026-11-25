import { z } from 'zod'
import { array, object, recordSchema, text, type Upload } from '../api'
import type { AtlasProject, AtlasVersion } from '@/features/atlas/atlasContext'
import type { CornerSpec } from '../trackingApi'

export interface ResolvedVersionMeta {
  netVer: string
  revision: string
  layout: string
  ecos: string[]
  isPor: boolean
}

export function versionEcoNumbers(version?: AtlasVersion | null): string[] {
  if (!version) return []
  const received = (version.ecos || []).filter((item): item is string => Boolean(item && item.trim()))
  if (received.length) return [...new Set(received)]
  const declared = (version.declared_ecos || []).filter((item): item is string => Boolean(item && item.trim()))
  return [...new Set(declared)]
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
    return {
      netVer: text(info.net_ver),
      revision: text(info.revision),
      layout: text(info.layout),
      ecos: [],
      isPor: false,
    }
  }
  return null
}

export const baselineSchema = z.object({
  project_id: z.string(),
  row_version: z.string(),
  baseline_project_id: z.string().nullable(),
  baseline_project: recordSchema.nullable(),
  status: z.enum(['unset', 'available', 'unavailable']),
  process: z.string().nullish(),
  baseline_process: z.string().nullish(),
})

export function uploadMatchesCorner(u: Upload, c: CornerSpec): boolean {
  if ((u.process_label || '').trim() !== c.process_label.trim()) return false
  if (c.voltage_v && (u.voltage_v || '').trim() !== c.voltage_v.trim()) return false
  if (c.temp_c && (u.temp_c || '').trim() !== c.temp_c.trim()) return false
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
