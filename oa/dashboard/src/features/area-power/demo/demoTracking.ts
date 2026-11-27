import type { AtlasCatalog } from '@/features/atlas/atlasContext'
import type { Payload } from '../api'
import { cas, fail, latest, list, now, object, record, records, scopedRecord, strings, text, touch, uploads } from './demoStore'

function trackingCornerId(c: Payload) {
  return [text(c.process_label), text(c.voltage_v) || '*', text(c.temp_c) || '*', text(c.pex) || '*'].join('|')
}

export function demoTrackingOverview(projectId: string, visible: Set<string>, catalog: AtlasCatalog) {
  const project = scopedRecord(projectId, 'project', visible)
  const nativeProject = catalog.projects.find(p => p.area_id === projectId)
  const projectName = text(project.payload.name) || nativeProject?.name || projectId
  const projCfgRow = latest([...records.values()].filter(r => r.record_type === 'tracking_project_config' && r.project_id === projectId))[0]
  const defaultCorners = projCfgRow ? list(projCfgRow.payload.default_required_corners).map(object) : [
    { label: 'TT', process_label: 'TT', voltage_v: '0.80', temp_c: '25', pex: 'RCMAX' },
  ]
  const projCfg = projCfgRow ? projCfgRow.payload : {
    enabled: false,
    owner_user_id: null,
    default_required_corners: defaultCorners,
    deadline_days: 7,
    deadline_time: '18:00',
    deadline_timezone: 'Asia/Seoul',
    top_recipients: [],
    thresholds: { area_increase_pct: '10', power_increase_pct: '10', min_area: '50' },
    typical_specs: [{ effective_net_ver: 'NET_001', corner: { label: 'TT', process_label: 'TT', voltage_v: '0.80', temp_c: '25', pex: 'RCMAX' }, analysis_conditions: {}, label: 'TT 0.80V' }],
    power_targets: [{ effective_net_ver: 'NET_001', target_mw: '85', label: '합성 목표 전력' }],
  }
  const blockCfgRows = latest([...records.values()].filter(r => r.record_type === 'tracking_block_config' && r.project_id === projectId))
  const blockCfgByBlock = new Map(blockCfgRows.map(r => [text(r.block_id), r]))
  let roundRows = [...records.values()].filter(r => r.record_type === 'tracking_round' && r.project_id === projectId)
  if (!roundRows.length && nativeProject?.blocks.some(b => b.area_id)) {
    const blocksWithArea = nativeProject.blocks.filter(b => b.area_id)
    const sampleNet = blocksWithArea.flatMap(b => b.versions).map(v => v.net_ver).find(Boolean) || 'NET_001'
    const arrived = new Date().toISOString().slice(0, 10)
    const due = new Date(Date.now() + 7 * 86400_000).toISOString().slice(0, 10)
    const seededRound = record('tracking_round', { project_id: projectId }, {
      net_ver: sampleNet, netlist_arrived_at: arrived, deadline_date: due, deadline_time: '18:00',
      deadline_timezone: 'Asia/Seoul', deadline_at: `${due}T09:00:00+00:00`,
      target_block_ids: blocksWithArea.map(b => b.area_id!), top_mail_state: 'waiting', top_mail_record_id: null,
      source: 'synthetic_memory_demo',
    })
    roundRows = [seededRound]
  }
  const allActiveBlocks = [...records.values()].filter(r => r.record_type === 'block' && r.project_id === projectId && r.state === 'active')
  const sortedRounds = [...roundRows].sort((a, b) =>
    (text(b.payload.netlist_arrived_at) || '').localeCompare(text(a.payload.netlist_arrived_at) || '')
    || (text(b.created_at) || '').localeCompare(text(a.created_at) || ''),
  )
  const rounds = sortedRounds.map((r, idx) => {
    const targetIds = strings(r.payload.target_block_ids)
    const targetSet = new Set(targetIds)
    const buildBlockRow = (bid: string, netVer: string) => {
      const blkRec = records.get(bid)
      const blkName = text(blkRec?.payload.name) || bid
      const isFc = blkName.trim() === projectName.trim()
      const bcfg = blockCfgByBlock.get(bid)?.payload || {}
      const reqCorners = list(bcfg.required_corners).length ? list(bcfg.required_corners).map(object) : defaultCorners
      const blkUploads = [...uploads.values()].filter(u => u.project_id === projectId && u.block_id === bid && u.state === 'ready')
      const blkComparisons = [...records.values()].filter(row => row.record_type === 'comparison' && row.project_id === projectId && row.block_id === bid && row.state === 'completed')
      let arrived = 0, completed = 0
      const missing: string[] = []
      const corners = reqCorners.map(c => {
        const cid = trackingCornerId(c)
        const match = blkUploads.find(u => text(u.process_label) === text(c.process_label))
        if (!match) {
          missing.push(text(c.label) || text(c.process_label))
          return { corner_id: cid, corner_label: text(c.label) || text(c.process_label), status: 'waiting_upload', upload_id: null, comparison_id: null, ai_run_id: null }
        }
        arrived += 1
        const comp = blkComparisons.find(row => row.upload_id === match.id)
        if (comp) completed += 1
        return {
          corner_id: cid, corner_label: text(c.label) || text(c.process_label),
          status: comp ? 'completed' : 'waiting_baseline', upload_id: match.id,
          baseline_upload_id: comp?.baseline_upload_id || null, comparison_id: comp?.id || null, ai_run_id: null,
        }
      })
      const firstDone = corners.find(c => c.comparison_id)
      const summaryParts: string[] = []
      if (missing.length) summaryParts.push(missing.map(m => `${m} 미도착`).join(', '))
      if (completed === reqCorners.length && reqCorners.length) summaryParts.push('합성 완료 · AI/메일 미호출')
      else if (arrived > completed) summaryParts.push('기준 실행 승인 또는 분석 대기')
      return {
        block_id: bid, block_name: blkName, is_full_chip: isFc,
        revision_label: nativeProject?.blocks.find(b => b.area_id === bid)?.versions.find(v => v.net_ver === netVer)?.revision || '',
        required_corners: reqCorners.length, arrived_corners: arrived, completed_corners: completed, missing_corners: missing,
        all_completed: reqCorners.length > 0 && completed === reqCorners.length,
        totals: { unexpected: 0, needs_review: 0, expected: 0, minor: 0, code_explained: 0 },
        power_evaluation: null, corners, post_top_mail_revised: false, status_summary: summaryParts.join(' · ') || '대기',
        next_action: firstDone ? { kind: 'result', label: isFc ? 'Full-chip 결과 보기' : '블록 결과 보기', comparison_id: firstDone.comparison_id } : { kind: 'configure', label: '추적 설정' },
        block_mail: { state: 'waiting', mail_id: null }, needs_attention: missing.length > 0 || arrived > completed,
      }
    }
    const blocks = targetIds.map(bid => buildBlockRow(bid, text(r.payload.net_ver)))
    const earlierRounds = sortedRounds.slice(idx + 1)
    const carried_over_blocks = allActiveBlocks
      .filter(b => !targetSet.has(b.id))
      .map(b => {
        const src = earlierRounds.find(prev => strings(prev.payload.target_block_ids).includes(b.id))
        if (src) {
          const srcNet = text(src.payload.net_ver)
          const row = buildBlockRow(b.id, srcNet)
          return {
            ...row,
            skipped_in_round: true,
            carried_from_round_id: src.id,
            carried_from_net_ver: srcNet,
            needs_attention: false,
            post_top_mail_revised: false,
            status_summary: `${srcNet} 유지 · ${row.status_summary}`,
          }
        }
        const blkName = text(b.payload.name) || b.id
        return {
          block_id: b.id, block_name: blkName, is_full_chip: blkName.trim() === projectName.trim(),
          revision_label: '', required_corners: 0, arrived_corners: 0, completed_corners: 0, missing_corners: [],
          all_completed: false, totals: { unexpected: 0, needs_review: 0, expected: 0, minor: 0, code_explained: 0 },
          power_evaluation: null, corners: [], post_top_mail_revised: false, needs_attention: false,
          status_summary: '이번 회차 제외 · 이전 추적 이력 없음',
          next_action: { kind: 'skipped', label: '이번 회차 제외' },
          skipped_in_round: true, carried_from_round_id: null, carried_from_net_ver: null,
        }
      })
    const fcs = blocks.filter(b => b.is_full_chip), subs = blocks.filter(b => !b.is_full_chip)
    return {
      id: r.id, row_version: r.row_version, state: r.state, net_ver: text(r.payload.net_ver),
      netlist_arrived_at: text(r.payload.netlist_arrived_at), deadline_date: text(r.payload.deadline_date),
      deadline_time: text(r.payload.deadline_time) || '18:00', deadline_timezone: text(r.payload.deadline_timezone) || 'Asia/Seoul',
      deadline_at: text(r.payload.deadline_at), target_block_ids: targetIds,
      full_chip_counts: { total: fcs.length, arrived: fcs.filter(b => b.arrived_corners > 0).length, completed: fcs.filter(b => b.all_completed).length },
      sub_block_counts: { total: subs.length, arrived: subs.filter(b => b.arrived_corners > 0).length, completed: subs.filter(b => b.all_completed).length },
      top_mail: { state: text(r.payload.top_mail_state) || 'waiting', mail_id: text(r.payload.top_mail_record_id) || null },
      has_post_top_revisions: false, blocks, carried_over_blocks,
    }
  })
  const expectations = latest([...records.values()].filter(r => r.record_type === 'tracking_expectation' && r.project_id === projectId && r.payload.archived !== true))
    .map(r => ({ id: r.id, entity_id: r.entity_id, row_version: r.row_version, block_id: r.block_id, ...r.payload }))
  return {
    project_id: projectId, project_name: projectName,
    project_config: { id: projCfgRow?.id || null, row_version: projCfgRow?.row_version || null, ...projCfg },
    block_configs: blockCfgRows.map(r => {
      const bvid = text(r.payload.baseline_version_id)
      const bver = bvid ? records.get(bvid) : undefined
      return {
        id: r.id, block_id: r.block_id, row_version: r.row_version, ...r.payload,
        ...(bver ? { baseline_version_info: { id: bver.id, net_ver: text(bver.payload.net_ver), revision: text(bver.payload.revision), layout: text(bver.payload.layout) } } : {}),
      }
    }),
    rounds, expectations, expectation_proposals: [],
    ai_model: { ready: false, error: 'synthetic_demo_model_not_called', connection_label: null, model: null, site: 'vwp' },
  }
}

export function handleTrackingRoute(route: string, body: Payload | undefined, visible: Set<string>, catalog: AtlasCatalog, actor: string): { handled: true; result: unknown } | { handled: false } {
  const trackingOverviewRoute = /^\/tracking\/projects\/([^/]+)\/overview$/.exec(route)
  if (trackingOverviewRoute && !body) {
    return { handled: true, result: demoTrackingOverview(decodeURIComponent(trackingOverviewRoute[1]), visible, catalog) }
  }
  const trackingProjCfgRoute = /^\/tracking\/projects\/([^/]+)\/config$/.exec(route)
  if (trackingProjCfgRoute && body) {
    const pid = decodeURIComponent(trackingProjCfgRoute[1])
    scopedRecord(pid, 'project', visible)
    const prev = latest([...records.values()].filter(r => r.record_type === 'tracking_project_config' && r.project_id === pid))[0]
    if (prev && body.expected_row_version != null) cas(prev, body.expected_row_version)
    const row = record('tracking_project_config', { project_id: pid }, { ...body, owner_user_id: actor, source: 'synthetic_memory_demo' }, body.enabled ? 'active' : 'paused')
    if (prev) { row.entity_id = prev.entity_id; row.revision = String(BigInt(prev.revision) + 1n); row.row_version = String(BigInt(prev.row_version) + 1n) }
    return { handled: true, result: { id: row.id, row_version: row.row_version, ...row.payload } }
  }
  const trackingBlkCfgRoute = /^\/tracking\/projects\/([^/]+)\/blocks\/([^/]+)\/config$/.exec(route)
  if (trackingBlkCfgRoute && body) {
    const pid = decodeURIComponent(trackingBlkCfgRoute[1]), bid = decodeURIComponent(trackingBlkCfgRoute[2])
    scopedRecord(bid, 'block', visible)
    const prev = latest([...records.values()].filter(r => r.record_type === 'tracking_block_config' && r.project_id === pid && r.block_id === bid))[0]
    if (prev && body.expected_row_version != null) cas(prev, body.expected_row_version)
    const row = record('tracking_block_config', { project_id: pid, block_id: bid }, { ...body, source: 'synthetic_memory_demo' }, body.enabled !== false ? 'active' : 'paused')
    if (prev) { row.entity_id = prev.entity_id; row.revision = String(BigInt(prev.revision) + 1n); row.row_version = String(BigInt(prev.row_version) + 1n) }
    return { handled: true, result: { id: row.id, block_id: bid, row_version: row.row_version, ...row.payload } }
  }
  const trackingRoundRoute = /^\/tracking\/projects\/([^/]+)\/rounds$/.exec(route)
  if (trackingRoundRoute && body) {
    const pid = decodeURIComponent(trackingRoundRoute[1])
    scopedRecord(pid, 'project', visible)
    const arrived = text(body.netlist_arrived_at)
    const days = Number(body.deadline_days || 7)
    const dueDate = new Date(new Date(`${arrived}T00:00:00Z`).getTime() + days * 86400_000).toISOString().slice(0, 10)
    const existing = [...records.values()].find(r => r.record_type === 'tracking_round' && r.project_id === pid && text(r.payload.net_ver) === text(body.net_ver))
    if (existing) {
      existing.payload = { ...existing.payload, netlist_arrived_at: arrived, deadline_date: dueDate, deadline_time: text(body.deadline_time) || '18:00', deadline_timezone: text(body.deadline_timezone) || 'Asia/Seoul', target_block_ids: strings(body.target_block_ids) }
      touch(existing)
      return { handled: true, result: { id: existing.id, row_version: existing.row_version, ...existing.payload } }
    }
    const created = record('tracking_round', { project_id: pid }, {
      net_ver: text(body.net_ver), netlist_arrived_at: arrived, deadline_date: dueDate,
      deadline_time: text(body.deadline_time) || '18:00', deadline_timezone: text(body.deadline_timezone) || 'Asia/Seoul',
      deadline_at: `${dueDate}T09:00:00+00:00`, target_block_ids: strings(body.target_block_ids),
      top_mail_state: 'waiting', top_mail_record_id: null, source: 'synthetic_memory_demo',
    })
    return { handled: true, result: { id: created.id, row_version: created.row_version, ...created.payload } }
  }
  const trackingReconcileRoute = /^\/tracking\/rounds\/([^/]+)\/reconcile$/.exec(route)
  if (trackingReconcileRoute && body) return { handled: true, result: { round_id: decodeURIComponent(trackingReconcileRoute[1]), job: null } }
  const trackingPreviewRoute = /^\/tracking\/projects\/([^/]+)\/preview$/.exec(route)
  if (trackingPreviewRoute && body) {
    const pid = decodeURIComponent(trackingPreviewRoute[1])
    const ov = demoTrackingOverview(pid, visible, catalog)
    const r = ov.rounds.find(item => item.net_ver === text(body.net_ver)) || ov.rounds[0]
    if (!r) return { handled: true, result: { round: null, top_mail_preview: null, block_mail_previews: [] } }
    return {
      handled: true,
      result: {
        round: { id: r.id, net_ver: r.net_ver },
        top_mail_preview: {
          subject: `[Design Atlas] TOP 요약 · ${ov.project_name} · ${r.net_ver} (합성 미리보기)`,
          body: `합성 데모 미리보기입니다 (실제 AI·메일 호출 없음).\n프로젝트: ${ov.project_name} · NET_VER: ${r.net_ver}\n마감: ${r.deadline_date} ${r.deadline_time} (${r.deadline_timezone})\n` +
            r.blocks.map(b => `- ${b.is_full_chip ? `${ov.project_name} · Full-chip` : b.block_name} | 도착 ${b.arrived_corners}/${b.required_corners} | 완료 ${b.completed_corners}/${b.required_corners} | ${b.status_summary}`).join('\n'),
        },
        block_mail_previews: r.blocks.map(b => ({
          block_id: b.block_id, block_name: b.block_name,
          subject: `[Design Atlas] ${ov.project_name} · ${b.block_name} · ${r.net_ver} (합성 미리보기)`,
          body: `합성 데모 블록 미리보기입니다 (실제 AI·메일 호출 없음).\n블록: ${b.block_name} · 도착 ${b.arrived_corners}/${b.required_corners} · 완료 ${b.completed_corners}/${b.required_corners}\n상태: ${b.status_summary}`,
        })),
      },
    }
  }
  const trackingExpRoute = /^\/tracking\/projects\/([^/]+)\/blocks\/([^/]+)\/expectations$/.exec(route)
  if (trackingExpRoute && body) {
    const pid = decodeURIComponent(trackingExpRoute[1]), bid = decodeURIComponent(trackingExpRoute[2])
    scopedRecord(bid, 'block', visible)
    const created = record('tracking_expectation', { project_id: pid, block_id: bid }, { ...body, approved_by: actor, approved_at: now(), source: 'synthetic_memory_demo' })
    return { handled: true, result: { id: created.id, entity_id: created.entity_id, row_version: created.row_version, ...created.payload } }
  }
  const trackingExpPropRoute = /^\/tracking\/projects\/([^/]+)\/blocks\/([^/]+)\/expectations\/propose$/.exec(route)
  if (trackingExpPropRoute && body) {
    return fail('합성 데모에서는 중앙 VWP AI 제안을 호출하지 않습니다. 아래 승인 등록 폼으로 직접 추가해 확인할 수 있습니다.')
  }
  const trackingReportsRoute = /^\/tracking\/projects\/([^/]+)\/reports$/.exec(route)
  if (trackingReportsRoute && !body) {
    const pid = decodeURIComponent(trackingReportsRoute[1])
    const ov = demoTrackingOverview(pid, visible, catalog)
    const live_reports = ov.rounds.map(r => ({
      round_id: r.id, net_ver: r.net_ver, deadline_date: r.deadline_date, deadline_time: r.deadline_time, top_mail_sent: false,
      top_report: {
        subject: `[Design Atlas] TOP 요약 · ${ov.project_name} · ${r.net_ver}`,
        body: `프로젝트: ${ov.project_name} · NET_VER: ${r.net_ver}\n마감: ${r.deadline_date} ${r.deadline_time} (${r.deadline_timezone})\n\n` +
          r.blocks.map(b => `- ${b.is_full_chip ? `${ov.project_name} · Full-chip` : b.block_name} | 도착 ${b.arrived_corners}/${b.required_corners} | 완료 ${b.completed_corners}/${b.required_corners} | ${b.status_summary}`).join('\n'),
        snapshot: { project_name: ov.project_name, net_ver: r.net_ver, blocks: r.blocks, carried_over_blocks: r.carried_over_blocks },
      },
      block_reports: r.blocks.map(b => ({
        block_id: b.block_id, block_name: b.block_name, is_full_chip: b.is_full_chip, revision_label: b.revision_label, mail_sent: false,
        subject: `[Design Atlas] ${ov.project_name} · ${b.block_name} · ${r.net_ver}`,
        body: `블록: ${b.block_name} (${r.net_ver} ${b.revision_label})\n도착 ${b.arrived_corners}/${b.required_corners} · 완료 ${b.completed_corners}/${b.required_corners}\n상태: ${b.status_summary}`,
        snapshot: { project_name: ov.project_name, block_name: b.block_name, net_ver: r.net_ver, revision_label: b.revision_label, corners: b.corners },
      })),
    }))
    return { handled: true, result: { project_id: pid, project_name: ov.project_name, sent_reports: [], live_reports } }
  }
  const trackingMailSnapRoute = /^\/tracking\/mails\/([^/]+)$/.exec(route)
  if (trackingMailSnapRoute && !body) {
    const id = decodeURIComponent(trackingMailSnapRoute[1])
    const row = records.get(id)
    if (!row || !['tracking_block_mail', 'tracking_top_mail'].includes(row.record_type)) return fail('발송 스냅샷을 찾을 수 없습니다.')
    return { handled: true, result: { id: row.id, kind: row.record_type, project_id: row.project_id, block_id: row.block_id || null, round_id: row.parent_id || '', created_at: row.created_at, delivery: { state: 'requested' }, snapshot: object(row.payload.snapshot), has_newer_results: false } }
  }
  return { handled: false }
}
