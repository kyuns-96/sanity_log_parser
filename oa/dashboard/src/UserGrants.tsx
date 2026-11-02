import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Search, ShieldCheck, Users } from 'lucide-react'
import { Badge } from './studio/components/ui/badge'
import { Button } from './studio/components/ui/button'
import { Input } from './studio/components/ui/input'
import { roleLabel } from './accessApi'
import type { AccessCatalog, AccessGrant, AdminAccess } from './accessApi'
import './UserGrants.css'

const pageSize = 10
const normalize = (value: string) => value.trim().toLocaleLowerCase()
const pageStart = (offset: number, length: number) => Math.min(offset, Math.max(0, Math.ceil(length / pageSize) - 1) * pageSize)

function Pagination({ offset, total, label, onChange }: { offset: number; total: number; label: string; onChange: (offset: number) => void }) {
  return <div className="ug-pagination">
    <span aria-live="polite">{total ? `${offset + 1}–${Math.min(offset + pageSize, total)} / ${total}` : '0개'}</span>
    <div><Button variant="ghost" size="sm" aria-label={`이전 ${label}`} disabled={!offset} onClick={() => onChange(offset - pageSize)}><ChevronLeft size={15} /></Button><Button variant="ghost" size="sm" aria-label={`다음 ${label}`} disabled={offset + pageSize >= total} onClick={() => onChange(offset + pageSize)}><ChevronRight size={15} /></Button></div>
  </div>
}

export default function UserGrants({ access, catalog, busy, onRevoke }: { access: AdminAccess; catalog: AccessCatalog; busy: boolean; onRevoke: (grant: AccessGrant) => void }) {
  const [selectedId, setSelectedId] = useState('')
  const [userQuery, setUserQuery] = useState('')
  const [userOffset, setUserOffset] = useState(0)
  const [grantQuery, setGrantQuery] = useState('')
  const [projectId, setProjectId] = useState('')
  const [grantOffset, setGrantOffset] = useState(0)
  const { names, byUser, people } = useMemo(() => {
    const names = new Map(access.users.map(user => [user.user_id, user.display_name || user.user_id]))
    const byUser = new Map<string, AccessGrant[]>()
    for (const grant of access.grants) {
      const grants = byUser.get(grant.user_id)
      if (grants) grants.push(grant)
      else byUser.set(grant.user_id, [grant])
    }
    const people = [...byUser].map(([id, grants]) => ({ id, name: names.get(id) || id, count: grants.length }))
      .sort((a, b) => a.name.localeCompare(b.name, 'ko') || a.id.localeCompare(b.id))
    return { names, byUser, people }
  }, [access.users, access.grants])
  // Preserve the selected identity even when their last grant is revoked.
  useEffect(() => { if (!selectedId && people.length) setSelectedId(people[0].id) }, [people, selectedId])

  const { projects, grants } = useMemo(() => {
    const projectNames = new Map(catalog.projects.map(project => [project.id, project.name]))
    const grants = (byUser.get(selectedId) || []).map(grant => {
      const project = projectNames.get(grant.project_id) || grant.project_id
      return { grant, scope: project,
        search: normalize(`${project} ${grant.project_id} ${roleLabel(grant.role)}`) }
    }).sort((a, b) => a.scope.localeCompare(b.scope, 'ko') || a.grant.grant_id.localeCompare(b.grant.grant_id))
    const projects = [...new Set(grants.map(item => item.grant.project_id))]
      .map(id => ({ id, name: projectNames.get(id) || id }))
      .sort((a, b) => a.name.localeCompare(b.name, 'ko') || a.id.localeCompare(b.id))
    return { grants, projects }
  }, [byUser, selectedId, catalog])
  const query = normalize(userQuery)
  const filteredPeople = people.filter(user => normalize(`${user.name} ${user.id}`).includes(query))
  const filteredGrants = grants.filter(item => (!projectId || item.grant.project_id === projectId) && item.search.includes(normalize(grantQuery)))
  const usersStart = pageStart(userOffset, filteredPeople.length)
  const grantsStart = pageStart(grantOffset, filteredGrants.length)
  const selectedName = names.get(selectedId) || selectedId

  function selectUser(id: string) {
    if (id === selectedId) return
    setSelectedId(id); setGrantQuery(''); setProjectId(''); setGrantOffset(0)
  }

  return <section className="am-panel ug-panel" aria-label="사용자별 권한 관리">
    <div className="am-panel-heading"><div><h2>부여된 권한</h2><p className="studio-section-description">사용자를 선택해 참여 프로젝트를 확인하고 멤버 권한을 회수하세요.</p></div><span>{people.length}명 · 프로젝트 배정 {access.grants.length}개</span></div>
    <div className="ug-layout">
      <div className="ug-directory">
        <label className="ug-search"><span><Search size={14} />사용자 검색</span><Input aria-label="사용자 검색" placeholder="이름 또는 계정 ID" value={userQuery} onChange={event => { setUserQuery(event.target.value); setUserOffset(0) }} /></label>
        <ul className="ug-users" aria-label="권한 보유 사용자">{filteredPeople.slice(usersStart, usersStart + pageSize).map(user => <li key={user.id}><button type="button" aria-pressed={selectedId === user.id} aria-label={`${user.name} · ${user.id} · 프로젝트 ${user.count}개`} disabled={busy} onClick={() => selectUser(user.id)}><Users size={16} /><span><strong>{user.name}</strong><small>{user.id}</small></span><span className="ug-count">{user.count}</span></button></li>)}</ul>
        {!filteredPeople.length && <div className="am-empty am-empty-small"><Users size={24} /><p>{query ? '검색한 사용자가 없습니다.' : '권한을 가진 사용자가 없습니다.'}</p></div>}
        <Pagination offset={usersStart} total={filteredPeople.length} label="사용자" onChange={setUserOffset} />
      </div>
      <div className="ug-detail">
        {selectedId ? <>
          <div className="ug-person"><div><span className="ug-eyebrow">선택한 사용자</span><h3>{selectedName}</h3><code>{selectedId}</code></div><Badge variant="secondary">프로젝트 {grants.length}개</Badge></div>
          <div className="ug-role-counts"><span><ShieldCheck size={14} />참여 프로젝트의 모든 블록 조회·검토·수정</span></div>
          <div className="ug-filters">
            <label>권한 검색<Input aria-label="권한 검색" placeholder="프로젝트 이름 또는 ID" value={grantQuery} onChange={event => { setGrantQuery(event.target.value); setGrantOffset(0) }} /></label>
            <label>프로젝트<select className="studio-native-select" aria-label="권한 프로젝트" value={projectId} onChange={event => { setProjectId(event.target.value); setGrantOffset(0) }}><option value="">전체 프로젝트</option>{projects.map(project => <option value={project.id} key={project.id}>{project.name}</option>)}{projectId && !projects.some(project => project.id === projectId) && <option value={projectId}>{catalog.projects.find(project => project.id === projectId)?.name || projectId}</option>}</select></label>
          </div>
          <ul className="am-list ug-grants" aria-label="선택한 사용자의 권한">{filteredGrants.slice(grantsStart, grantsStart + pageSize).map(({ grant, scope }) => <li key={grant.grant_id}><div><strong>{scope}</strong><span>{roleLabel(grant.role)}</span><code>{grant.project_id}</code></div><Button size="sm" variant="outline" disabled={busy} onClick={() => onRevoke(grant)}>권한 회수</Button></li>)}</ul>
          {!filteredGrants.length && <div className="am-empty"><ShieldCheck size={25} /><p>{grants.length ? '조건에 맞는 권한이 없습니다.' : '이 사용자에게 부여된 권한이 없습니다.'}</p></div>}
          <Pagination offset={grantsStart} total={filteredGrants.length} label="권한" onChange={setGrantOffset} />
        </> : <div className="am-empty ug-placeholder"><ShieldCheck size={30} /><h3>사용자별 참여 프로젝트</h3><p>{people.length ? '왼쪽에서 사용자를 선택하세요.' : '승인된 권한이 여기에 표시됩니다.'}</p></div>}
      </div>
    </div>
  </section>
}
