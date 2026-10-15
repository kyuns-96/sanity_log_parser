import { ArrowDownToLine, ArrowUpRight, BookOpen } from 'lucide-react'
import { Button } from './components/ui/button'
import type { Bundle, Unit } from './types'

export function GcaDemoScope({ bundles, selected, select, disabled }: {
  bundles: Bundle[]; selected: string; select: (id: string) => void; disabled: boolean
}) {
  const current = bundles.find(b => b.bundle_id === selected)
  if (!current) return null
  const projects = [...new Set(bundles.map(b => b.project))]
  const blocks = [...new Set(bundles.filter(b => b.project === current.project).map(b => b.design!))]
  return <section className="gca-scope" aria-label="GCA 예시 선택">
    <div className="gca-selectors"><label>프로젝트<select aria-label="GCA 프로젝트" value={current.project} disabled={disabled}
      onChange={e => select(bundles.find(b => b.project === e.target.value)!.bundle_id)}>
      {projects.map(p => <option key={p}>{p}</option>)}</select></label>
      <label>블록<select aria-label="GCA 블록" value={current.design} disabled={disabled}
        onChange={e => select(bundles.find(b => b.project === current.project && b.design === e.target.value)!.bundle_id)}>
        {blocks.map(b => <option key={b}>{b}</option>)}</select></label></div>
    <small>{projects.length}개 프로젝트 · {new Set(bundles.map(b => `${b.project}/${b.design}`)).size}개 블록 · {bundles.length}개 실행 · 문서를 참고한 예시</small>
  </section>
}

export function GcaReportSource({ unit }: { unit: Unit }) {
  const report = unit.demo_report
  if (!report) return null
  function download() {
    const url = URL.createObjectURL(new Blob([report!.text!], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `example-${unit.review_unit_id}.rpt`; anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <section className="gca-report-source" aria-label="GCA 예시와 출처">
    <div className="gca-source-heading"><BookOpen size={17} /><strong>공개 문서를 참고한 GCA 예시 보고서</strong></div>
    <p>{report.summary}</p><p className="tiny">프로젝트·객체·수치·문구는 데모용으로 재구성했습니다. 실제 도구 출력 형식이나 실행 결과를 보증하지 않습니다.</p>
    <ul>{report.sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title}<ArrowUpRight size={12} /></a><small>{source.section}</small></li>)}</ul>
    {report.text ? <><details><summary>예시 보고서 전체 보기</summary><pre>{report.text}</pre></details>
      <Button size="sm" variant="outline" onClick={download}><ArrowDownToLine />예시 보고서 다운로드 (.rpt)</Button></> : <p className="unit-alert">이 실행의 report가 아직 도착하지 않은 상황을 보여줍니다.</p>}
  </section>
}
