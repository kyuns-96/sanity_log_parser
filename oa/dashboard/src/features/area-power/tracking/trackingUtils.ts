import { z } from 'zod'
import { array, object, recordSchema, text, type Upload } from '../api'
import type { AtlasProject } from '@/features/atlas/atlasContext'
import type { CornerSpec } from '../trackingApi'

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
  if (!cfg) return ''
  const info = object(cfg.baseline_version_info)
  if (text(info.net_ver)) {
    const rev = [text(info.revision), text(info.layout)].filter(Boolean).join(' ')
    return rev ? `${text(info.net_ver)} · ${rev}` : text(info.net_ver)
  }
  const bvid = text(cfg.baseline_version_id) || text(object(array(cfg.approved_baselines)[0]).version_id)
  if (!bvid || !targetProject) return ''
  const blk = targetProject.blocks.find(b => b.area_id === targetBlockAreaId)
  const ver = blk?.versions.find(v => v.area_id === bvid)
  if (!ver) return ''
  const rev = [ver.revision, ver.layout].filter(Boolean).join(' ')
  return rev ? `${ver.net_ver} · ${rev}` : ver.net_ver
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
