import { useEffect, useMemo, useState } from 'react'
import { Mail, Plus, RefreshCw } from 'lucide-react'
import { date, request, text, unknownSchema, type Auth } from '../api'
import { Badge, Button, Field } from '../components/workspace'
import type { AtlasBlock, AtlasProject } from '@/features/atlas/atlasContext'
import {
  MAIL_STATE_LABEL,
  trackingMailSnapshotSchema,
  type TrackingMailSnapshot,
  type TrackingRound,
} from '../trackingApi'

interface TrackingRoundsCardProps {
  auth: Auth
  project: AtlasProject
  areaProjectId: string
  areaBlocks: AtlasBlock[]
  rounds: TrackingRound[]
  deadlineDays: string
  deadlineTime: string
  busy: boolean
  runAction: (fn: () => Promise<void>, success?: string) => Promise<void>
  onReload: () => Promise<void>
}

export function TrackingRoundsCard({
  auth,
  project,
  areaProjectId,
  areaBlocks,
  rounds,
  deadlineDays,
  deadlineTime,
  busy,
  runAction,
  onReload,
}: TrackingRoundsCardProps) {
  const knownNetVers = useMemo(() => {
    const set = new Set<string>()
    for (const block of project.blocks) {
      for (const version of block.versions) {
        if (version.net_ver) set.add(version.net_ver)
      }
    }
    return [...set]
  }, [project.blocks])

  const [addingRound, setAddingRound] = useState(false)
  const [netVerDraft, setNetVerDraft] = useState('')
  const [arrivedDraft, setArrivedDraft] = useState(() => new Date().toISOString().slice(0, 10))
  const [targetBlocks, setTargetBlocks] = useState<string[]>([])
  const [mailSnapshot, setMailSnapshot] = useState<TrackingMailSnapshot | null>(null)

  useEffect(() => {
    if (addingRound && !targetBlocks.length && areaBlocks.length) {
      setTargetBlocks(areaBlocks.map(b => b.area_id!))
    }
  }, [addingRound, areaBlocks, targetBlocks.length])

  async function submitRound() {
    const trimmed = netVerDraft.trim()
    if (!trimmed) throw new Error('버전(NET_VER)을 입력하거나 선택하세요.')
    if (!arrivedDraft) throw new Error('Netlist 도착일을 입력하세요.')
    if (!targetBlocks.length) throw new Error('대상 블록을 하나 이상 선택하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/rounds`, unknownSchema, {
      net_ver: trimmed,
      netlist_arrived_at: arrivedDraft,
      target_block_ids: targetBlocks,
    })
    setAddingRound(false)
    setNetVerDraft('')
    await onReload()
  }

  async function reconcileRound(roundId: string) {
    await request(auth, `/tracking/rounds/${encodeURIComponent(roundId)}/reconcile`, unknownSchema, {})
    await onReload()
  }

  async function openMailSnapshot(mailId: string) {
    const snap = await request(auth, `/tracking/mails/${encodeURIComponent(mailId)}`, trackingMailSnapshotSchema)
    setMailSnapshot(snap)
  }

  return (
    <section className="panel" aria-label="버전 회차 관리">
      <div className="section-heading">
        <div>
          <h2>버전 회차 (NET_VER) 관리</h2>
          <p>Netlist 도착일과 추적 대상 블록을 등록해 마감 및 TOP 요약 메일 일정을 관리합니다.</p>
        </div>
        {project.can_write && (
          <Button variant="outline" size="sm" onClick={() => setAddingRound(v => !v)}>
            <Plus size={14} />{addingRound ? '입력 닫기' : '새 버전 회차 등록'}
          </Button>
        )}
      </div>

      {addingRound && (
        <div className="tracking-round-form-panel admin-round-form">
          <div className="tracking-form-row">
            <Field label="버전 (NET_VER)">
              <input
                type="text"
                list="admin-net-ver-options"
                value={netVerDraft}
                onChange={event => setNetVerDraft(event.target.value)}
                placeholder="예: NET_023"
              />
              <datalist id="admin-net-ver-options">
                {knownNetVers.map(nv => <option key={nv} value={nv} />)}
              </datalist>
            </Field>
            <Field label="Netlist 도착일">
              <input type="date" value={arrivedDraft} onChange={event => setArrivedDraft(event.target.value)} />
              <small>마감: 도착일 + {deadlineDays}일 {deadlineTime}</small>
            </Field>
          </div>
          <div className="tracking-block-checks">
            <span>추적 대상 블록</span>
            <div className="tracking-check-grid">
              {areaBlocks.map(block => {
                const id = block.area_id!
                const checked = targetBlocks.includes(id)
                const isFc = block.name.trim() === project.name.trim()
                return (
                  <label key={id} className="checkbox">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={event => setTargetBlocks(prev => event.target.checked ? [...prev, id] : prev.filter(item => item !== id))}
                    />
                    <span>{block.name}{isFc ? ' (Full-chip)' : ''}</span>
                  </label>
                )
              })}
              {!areaBlocks.length && <small className="text-muted">먼저 블록이 연결되어야 합니다.</small>}
            </div>
          </div>
          <div className="actions">
            <Button size="sm" disabled={busy || !project.can_write || !areaBlocks.length} onClick={() => runAction(submitRound, '버전 회차를 등록했습니다.')}>
              회차 등록
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setAddingRound(false)}>취소</Button>
          </div>
        </div>
      )}

      {rounds.length > 0 ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>NET_VER</th>
                <th>도착일 · 마감</th>
                <th>진행 현황</th>
                <th>TOP 메일 상태</th>
                <th>관리</th>
              </tr>
            </thead>
            <tbody>
              {rounds.map(r => (
                <tr key={r.id}>
                  <td>
                    <strong>{r.net_ver}</strong>
                    {r.has_post_top_revisions && <Badge value="발송 후 수정" />}
                  </td>
                  <td>{r.netlist_arrived_at || '—'} → {r.deadline_date || '—'} {r.deadline_time || ''}</td>
                  <td>
                    {r.full_chip_counts.total > 0 && `Full-chip ${r.full_chip_counts.completed}/${r.full_chip_counts.total} · `}
                    블록 {r.sub_block_counts.completed}/{r.sub_block_counts.total} 완료
                  </td>
                  <td>
                    <Badge value={MAIL_STATE_LABEL[r.top_mail.state] || r.top_mail.state} />
                  </td>
                  <td>
                    <div className="actions">
                      {project.can_write && (
                        <Button variant="outline" size="sm" disabled={busy} onClick={() => runAction(() => reconcileRound(r.id), '회차 상태를 재점검하도록 요청했습니다.')}>
                          <RefreshCw size={13} />재점검
                        </Button>
                      )}
                      {r.top_mail.mail_id && (
                        <Button variant="ghost" size="sm" onClick={() => runAction(() => openMailSnapshot(r.top_mail.mail_id!))}>
                          <Mail size={13} />발송 스냅샷
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">등록된 버전 회차가 없습니다. ‘새 버전 회차 등록’으로 추적을 시작하세요.</p>
      )}

      {mailSnapshot && (
        <div className="tracking-subblock">
          <div className="tracking-snapshot-head">
            <strong>
              <Mail size={14} aria-hidden="true" /> 발송 당시 스냅샷 · {date(text(mailSnapshot.snapshot.frozen_at) || mailSnapshot.created_at)}
            </strong>
            <Button variant="ghost" size="sm" onClick={() => setMailSnapshot(null)}>닫기</Button>
          </div>
          <p className="muted">
            상태: {MAIL_STATE_LABEL[mailSnapshot.delivery.state] || mailSnapshot.delivery.state}
            {mailSnapshot.delivery.recipients?.length ? ` · 수신자: ${mailSnapshot.delivery.recipients.join(', ')}` : ''}
          </p>
        </div>
      )}
    </section>
  )
}
