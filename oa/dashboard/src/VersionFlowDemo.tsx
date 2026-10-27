import { useState } from 'react'
import { ArrowUpRight, Check, CheckCircle2, ChevronRight, ClipboardCheck, Layers3, RotateCcw, Sparkles, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { WorkspaceBrand } from './components/WorkspaceBrand'
import { ColorModeControl } from './components/ColorModeControl'
import { assignPor, blockOwners, canAssignPor, initialVersionState, latestRun, porOf, reviewLatest, revisionSetOf, snapshotsFor, versionItems, versionUsers, visibleVersionBlocks } from './versionDemoModel'
import type { DemoResult, DesignSnapshot, LayoutStage, RevisionSet, VersionBlock, VersionItem, VersionState } from './versionDemoModel'
import './versionFlow.css'

function Result({ value }: { value: DemoResult | 'MISSING' }) {
  return <span className={`vf-result vf-result-${value.toLowerCase()}`}><i />{value === 'MISSING' ? '미수신' : value}</span>
}
function VersionIdentifier({ set }: { set: RevisionSet }) {
  return <dl className="vf-version-fields"><div><dt>NET_VER</dt><dd>{set.netVer}</dd></div><div><dt>REVISION</dt><dd className="vf-revision">{set.revision}</dd></div></dl>
}

export default function VersionFlowDemo() {
  const [state, setState] = useState(initialVersionState)
  const [userId, setUserId] = useState('u-lee')
  const [setId, setSetId] = useState('set-cpu-post-2')
  const [selectedId, setSelectedId] = useState('cpu-post-2-eco03')
  const [notice, setNotice] = useState('')
  const currentSet = revisionSetOf(state, setId)
  const { block, stage } = currentSet
  const por = porOf(state, block)
  const blocks = visibleVersionBlocks(userId)
  const versions = state.revisionSets.filter(set => set.projectId === currentSet.projectId && set.block === block && set.stage === stage)
  const snapshots = snapshotsFor(state, currentSet.id)
  const selected = snapshots.find(snapshot => snapshot.id === selectedId) || snapshots.at(-1)!
  const latest = versionItems.map(item => latestRun(state, selected.id, item))
  const isPor = por.revisionSetId === currentSet.id
  const canAssign = canAssignPor(userId, currentSet)

  function chooseVersion(id: string) { setSetId(id); setSelectedId(''); setNotice('') }
  function chooseBlock(next: VersionBlock) { chooseVersion(porOf(state, next).revisionSetId) }
  function chooseUser(id: string) {
    setUserId(id)
    const visible = visibleVersionBlocks(id)
    if (!visible.includes(block)) chooseBlock(visible[0])
    setNotice('')
  }
  function chooseStage(next: LayoutStage) {
    if (next === stage) return
    const related = next === 'PRE' ? revisionSetOf(state, currentSet.sourcePreId!)
      : state.revisionSets.find(set => set.sourcePreId === currentSet.id && set.projectId === currentSet.projectId && set.block === block)!
    chooseVersion(related.id)
  }
  function designatePor() {
    setState(assignPor(state, userId, currentSet.id))
    setNotice(`${block} PoR을 ${currentSet.netVer} / ${currentSet.revision}로 지정했습니다.`)
  }
  function reset() {
    setState(initialVersionState()); setUserId('u-lee'); setSetId('set-cpu-post-2')
    setSelectedId('cpu-post-2-eco03'); setNotice('데모를 처음 상태로 되돌렸습니다.')
  }

  return <div className="vf-root"><a className="skip-link" href="#workspace-main">본문으로 건너뛰기</a>
    <div className="vf-demo-bar"><span><Sparkles size={14} /><strong>VERSION DEMO</strong><span>예시 데이터로 보는 버전 구조</span></span><div><a href="/?demo=1&flow=access">담당자 배정 데모 <ArrowUpRight size={13} /></a><button onClick={reset}><RotateCcw size={14} />초기화</button></div></div>
    <div className="vf-shell">
      <aside className="vf-sidebar">
        <WorkspaceBrand href="/?demo=1&flow=versions" />
        <div className="vf-workspace"><span>A</span><div><strong>Atlas</strong><small>SoC DESIGN PROJECT</small></div></div>
        <div className="vf-nav-label">VERSION WORKSPACE</div>
        <nav aria-label="버전 데모 메뉴"><a className="is-active" aria-current="page" href="/?demo=1&flow=versions"><Layers3 size={16} />블록별 PoR 조회</a><a href="/?demo=1&dataset=gca"><ClipboardCheck size={16} />GCA report 예시<ArrowUpRight size={12} /></a></nav>
        <div className="vf-sidebar-note"><strong>블록 → NET_VER + REVISION</strong><p>각 블록 담당자가 PoR을 정합니다.<br />ECO는 해당 세트 아래에서 관리합니다.</p></div>
      </aside>
      <div className="vf-main-shell">
        <header className="vf-topbar"><div>Atlas <ChevronRight size={13} /><strong>블록별 PoR·sanity 현황</strong></div><div className="color-mode-actions"><ColorModeControl /><span>새로고침 시 데모 초기화</span></div></header>
        <main className="vf-main" id="workspace-main" tabIndex={-1}>
          <div className="vf-page-title"><div className="vf-eyebrow">BLOCK VERSION WORKSPACE</div><h1>블록별 PoR·sanity 현황</h1><p>블록마다 NET_VER·REVISION을 따로 관리하고, 해당 블록 담당자가 PoR을 지정합니다.</p></div>
          <div className="vf-user-toolbar"><label>데모 사용자<select aria-label="데모 사용자" value={userId} onChange={event => chooseUser(event.target.value)}>{versionUsers.map(user => <option key={user.id} value={user.id}>{user.name} · {user.id === 'u-lee' ? 'Atlas TOP' : user.id === 'u-kim' ? 'CPU 담당자' : 'GPU·DDR 담당자'}</option>)}</select></label><p>{userId === 'u-lee' ? 'TOP은 모든 블록의 PoR과 검사 결과를 조회합니다.' : '담당 블록의 버전을 조회하고 PoR을 지정할 수 있습니다.'}</p></div>
          {notice && <div className="vf-notice" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button aria-label="안내 닫기" onClick={() => setNotice('')}><X size={15} /></button></div>}

          <section className="vf-panel vf-block-overview" aria-label="블록별 현재 PoR">
            <header className="vf-panel-heading"><div><h2>Atlas · 블록별 현재 PoR</h2><p>블록을 선택하면 해당 블록의 버전 목록과 검사 결과를 볼 수 있습니다.</p></div><span className="vf-small-label">{blocks.length}개 블록</span></header>
            <div className="vf-table-wrap"><table className="vf-block-table"><thead><tr><th scope="col">블록</th><th scope="col">담당자</th><th scope="col">NET_VER</th><th scope="col">REVISION</th></tr></thead><tbody>{blocks.map(item => {
              const set = revisionSetOf(state, porOf(state, item).revisionSetId)
              return <tr key={item} className={item === block ? 'is-selected' : ''} data-testid={`block-${item}`}>
                <th scope="row"><button aria-label={`${item} 블록 조회`} aria-pressed={item === block} onClick={() => chooseBlock(item)}><span className="vf-selection-dot">{item === block && <Check size={10} />}</span>{item}</button></th>
                <td data-label="담당자">{blockOwners(item).map(user => user.name).join(', ')}</td><td data-label="NET_VER">{set.netVer}</td><td data-label="REVISION"><span className="vf-revision">{set.revision}</span><small className="vf-layout-label">{set.stage} layout</small></td>
              </tr>
            })}</tbody></table></div>
          </section>

          <section className="vf-panel vf-por-catalog" aria-label="블록 버전 목록">
            <header className="vf-panel-heading"><div><h2>{block} · 버전 목록</h2><p>{block} 블록의 NET_VER·REVISION 조합입니다. PoR 지정은 담당자가 결정합니다.</p></div><div className="vf-stage-tabs" role="group" aria-label="Layout 단계">{(['PRE', 'POST'] as const).map(item => <button key={item} aria-pressed={item === stage} onClick={() => chooseStage(item)}>{item} layout</button>)}</div></header>
            <div className="vf-table-wrap"><table className="vf-por-catalog-table"><thead><tr><th scope="col">NET_VER</th><th scope="col">PoR</th><th scope="col">REVISION</th></tr></thead><tbody>{[...versions].reverse().map(set => <tr key={set.id} className={set.id === currentSet.id ? 'is-selected' : ''} data-testid={`version-${set.id}`}>
              <th scope="row"><button aria-label={`${set.netVer} 버전 조회`} aria-pressed={set.id === currentSet.id} onClick={() => chooseVersion(set.id)}><span className="vf-selection-dot">{set.id === currentSet.id && <Check size={10} />}</span>{set.netVer}</button></th>
              <td data-label="PoR">{set.id === por.revisionSetId ? '현재 PoR' : '—'}</td><td data-label="REVISION" className="vf-revision">{set.revision}</td>
            </tr>)}</tbody></table></div>
          </section>

          <section className="vf-por-context" aria-label="조회 중인 블록 버전">
            <header><Layers3 size={18} /><h2>{block} · 조회 중인 버전</h2><span>{stage} layout{isPor ? ' · 현재 PoR' : ''}</span></header>
            <VersionIdentifier set={currentSet} />
            <div className="vf-por-action"><p>{isPor ? `${block} 담당자가 지정한 현재 PoR입니다.` : '조회 중인 버전입니다. 버전이나 ECO를 조회해도 PoR 지정은 바뀌지 않습니다.'}</p>{canAssign && <Button variant="outline" size="sm" disabled={isPor} onClick={designatePor}>{isPor ? '현재 PoR' : `${block} PoR로 지정`}</Button>}</div>
          </section>

          {stage === 'POST' && <section className="vf-panel vf-eco-panel" aria-label="ECO 목록">
            <header className="vf-panel-heading"><div><h2>{block} · ECO 목록</h2><p>이 블록의 NET_VER·REVISION에 속한 ECO입니다. 최초 버전은 chip_finish입니다.</p></div><span className="vf-small-label">{snapshots.length}개 ECO</span></header>
            <div className="vf-table-wrap"><table className="vf-eco-table"><thead><tr><th scope="col">ECO 버전</th>{versionItems.map(item => <th scope="col" key={item}>{item}</th>)}</tr></thead><tbody>{[...snapshots].reverse().map(snapshot => <tr key={snapshot.id} className={snapshot.id === selected.id ? 'is-selected' : ''} data-testid={`eco-${snapshot.id}`}>
              <th scope="row"><button aria-label={`${snapshot.ecoVersion} 검사 결과 보기`} aria-pressed={snapshot.id === selected.id} onClick={() => { setSelectedId(snapshot.id); setNotice('') }}><span className="vf-selection-dot">{snapshot.id === selected.id && <Check size={10} />}</span>{snapshot.ecoVersion}</button></th>
              {versionItems.map(item => <td key={item} data-label={item}><Result value={latestRun(state, snapshot.id, item)?.result || 'MISSING'} /></td>)}
            </tr>)}</tbody></table></div>
          </section>}

          <div className="vf-selection-path" aria-label="현재 검사 대상"><span>Atlas</span><ChevronRight size={12} /><span>{block}</span><ChevronRight size={12} /><span>{currentSet.netVer}</span><ChevronRight size={12} /><span>{stage}</span>{selected.ecoVersion && <><ChevronRight size={12} /><strong>{selected.ecoVersion}</strong></>}</div>
          <section className="vf-panel vf-results-panel" aria-label="선택 버전의 sanity 결과">
            <header className="vf-panel-heading"><div><h2>{block} · {selected.ecoVersion || 'PRE'} sanity 결과</h2><p>각 항목의 최신 실행입니다. 이전 실행 기록도 그대로 보관합니다.</p></div></header>
            <div>{versionItems.map(item => <RunRow key={`${selected.id}-${item}`} state={state} snapshot={selected} item={item} />)}</div>
            <footer className="vf-results-footer"><span>{latest.filter(Boolean).length} / 3개 검사 수신 · {latest.filter(run => run?.reviews.length).length}개 검토 완료</span>{canAssign && <Button variant="outline" size="sm" disabled={!latest.some(run => run && !run.reviews.length)} onClick={() => { setState(reviewLatest(state, userId, selected.id)); setNotice('선택한 검사 대상의 최신 결과를 검토 확인했습니다.') }}><ClipboardCheck />최신 결과 검토 확인</Button>}</footer>
          </section>
          <footer className="vf-footer"><span>SANITY / VERSION WORKSPACE</span><span>예시 검사 결과와 계정으로 동작합니다. PoR 변경은 현재 탭에만 적용됩니다.</span></footer>
        </main>
      </div>
    </div>
  </div>
}

function RunRow({ state, snapshot, item }: { state: VersionState; snapshot: DesignSnapshot; item: VersionItem }) {
  const runs = state.runs.filter(run => run.snapshotId === snapshot.id && run.item === item)
  const latest = runs.at(-1)
  return <div className="vf-run-row" data-testid={`run-${item}`}>
    <div className="vf-run-main"><span className="vf-item-icon"><ClipboardCheck size={18} /></span><div><strong>{item}</strong><small>{latest ? `최신 실행 #${latest.attempt} · ${latest.reviews.length ? '검토 완료' : '검토 대기'}` : '아직 수신한 검사 결과가 없습니다'}</small></div><Result value={latest?.result || 'MISSING'} /></div>
    {runs.length > 0 && <details className="vf-run-history"><summary>실행 이력 {runs.length}건</summary>{[...runs].reverse().map(run => <div key={run.id}><span>실행 #{run.attempt}</span><Result value={run.result} /><small>{run.reviews.length ? `검토 r${run.reviews.at(-1)!.revision}` : '검토 대기'}</small></div>)}</details>}
  </div>
}
