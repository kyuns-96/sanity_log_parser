import { useEffect, useMemo, useRef, useState } from 'react'
import { z } from 'zod'
import { request, type Auth } from './api'
import { Button, useAction } from './components/workspace'
import type { MappingDraft } from './mappingCsv'

const count = z.coerce.number().int().nonnegative()
const report = z.object({
  suggestions: z.array(z.object({ baseline: z.string(), current: z.string(), scope: z.enum(['node', 'subtree']),
    gain: count, reason: z.string(), pattern: z.string() })),
  held: z.array(z.object({ baseline: z.string(), alternatives: z.array(z.string()), reason: z.string() })),
  baseline_unmatched: count, current_unmatched: count,
})
const statusSchema = z.object({ state: z.string(), error: z.string().nullable(), result: report.nullable() })
type Report = z.infer<typeof report>

export function MappingSuggestions({ auth, current, baseline, mapping, draft, disabled, onSaved, onInspect }: {
  auth: Auth; current: string; baseline: string; mapping: string; draft: MappingDraft[]; disabled: boolean
  onSaved: (id: string) => Promise<void>
  onInspect: (baseline: string, current: string) => void
}) {
  const action = useAction()
  const [run, setRun] = useState('')
  const [result, setResult] = useState<Report | null>(null)
  const [message, setMessage] = useState('')
  const [checking, setChecking] = useState(false)
  const [poll, setPoll] = useState(0)
  const [selected, setSelected] = useState(new Set<number>())
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(0)
  const [heldPage, setHeldPage] = useState(0)
  const requestId = useRef('')
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])

  async function start() {
    if (!requestId.current) requestId.current = crypto.randomUUID()
    const row = await request(auth, '/mapping-proposals', z.object({ id: z.string() }), {
      current_upload_id: current, baseline_upload_id: baseline, mapping_id: mapping || null, request_id: requestId.current,
      draft: draft.map(r => ({ kind: 'one_to_one', scope: r.scope, baseline_paths: [r.baselinePath], current_paths: [r.currentPath] })),
    })
    if (live.current) { setRun(row.id); setMessage(''); setChecking(true); setResult(null); setSelected(new Set()); setPage(0); setHeldPage(0) }
  }
  useEffect(() => {
    if (!run) return
    const controller = new AbortController()
    let timer: number | undefined
    async function load() {
      try {
        const data = await request(auth, '/mapping-proposals/' + run, statusSchema, undefined, controller.signal)
        if (controller.signal.aborted) return
        if (data.state === 'failed') {
          setMessage('분석 실패 · ' + (data.error || 'unknown')); setChecking(false); setRun(''); requestId.current = ''
        } else if (data.result) {
          setResult(data.result); setMessage(''); setChecking(false)
        } else {
          setMessage(data.state === 'running' ? '경로 후보 분석 중…' : '중앙 VWP 작업 대기 중…')
          timer = window.setTimeout(load, 2000)
        }
      } catch (err) {
        if (!controller.signal.aborted) { setMessage(err instanceof Error ? err.message : '조회 실패'); setChecking(false) }
      }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [auth, run, poll])

  const patterns = useMemo(() => {
    const grouped = new Map<string, number[]>()
    result?.suggestions.forEach((row, index) => {
      const list = grouped.get(row.pattern)
      if (list) list.push(index); else grouped.set(row.pattern, [index])
    })
    return [...grouped.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [result])
  const matching = patterns.flatMap(([pattern, indices]): [string, number[]][] => {
    if (pattern.includes(filter)) return [[pattern, indices]]
    const matches = indices.filter(i => result!.suggestions[i].baseline.includes(filter) || result!.suggestions[i].current.includes(filter))
    return matches.length ? [[pattern, matches]] : []
  })
  const gain = result ? [...selected].reduce((n, i) => n + result.suggestions[i].gain, 0) : 0
  function toggle(indices: number[], checked: boolean) {
    setSelected(previous => { const next = new Set(previous); for (const i of indices) { if (checked) next.add(i); else next.delete(i) } return next })
  }
  async function approve() {
    const mapping = await request(auth, '/mapping-proposals/' + run + '/approve', z.object({ id: z.string() }), { selected: [...selected] })
    if (live.current) await onSaved(mapping.id)
  }
  return <section className="mapping-suggestions" aria-label="AI 매핑 제안">
    <div className="mapping-actions"><Button variant="outline" disabled={disabled || action.busy || checking || !!result || !!run || auth.demo} onClick={() => action.run(start, '')}>AI 매핑 제안 분석</Button>
      {!run && !checking && !auth.demo && <small>VWP에서 미대응 경로를 분석합니다. 선택한 제안과 작성 중인 규칙을 함께 저장합니다.</small>}
      {auth.demo && <small>합성 데모에서는 VWP 모델을 호출하지 않습니다.</small>}
      {message && <span role="status">{message}</span>}
      {run && !checking && !result && <Button variant="outline" onClick={() => { setChecking(true); setPoll(n => n + 1) }}>결과 다시 조회</Button>}
    </div>{action.feedback}
    {result && <>
      <p>제안 {result.suggestions.length}개 · 선택 적용 시 새로 연결 {gain}쌍 · 남는 미대응 기준 {result.baseline_unmatched - gain}개 / 현재 {result.current_unmatched - gain}개</p>
      {patterns.length > 0 && <><input aria-label="매핑 제안 검색" placeholder="변경 패턴 또는 경로 검색" value={filter} onChange={e => { setFilter(e.target.value); setPage(0) }} />
        {matching.slice(page * 12, page * 12 + 12).map(([pattern, indices]) => <Pattern key={pattern} pattern={pattern} indices={indices} rows={result.suggestions} selected={selected} toggle={toggle} />)}
        {matching.length > 12 && <Pages page={page} size={12} total={matching.length} set={setPage} />}
        <Button disabled={!selected.size || disabled || action.busy} onClick={() => action.run(approve, '대응을 저장했습니다.')}>선택한 제안 {selected.size}개 승인·대응 저장</Button>
      </>}
      {result.held.length > 0 && <details><summary>판단 보류 · 기준 {result.held.length}개</summary>
        <ul>{result.held.slice(heldPage * 20, heldPage * 20 + 20).map(h => <li key={h.baseline}>
          <strong>{h.baseline}</strong> · {({ ambiguous: '후보를 구별할 근거 부족', unresolved: '대응 근거 부족', possible_merge: '같은 현재 경로를 가리킴 · 병합 여부 확인', possible_split: '자식이 여러 후보로 나뉨 · 분할 여부 확인' } as Record<string,string>)[h.reason] || h.reason}
          <div className="mapping-actions">{(h.alternatives.length ? h.alternatives : ['']).map(path => <Button key={path} variant="ghost" disabled={disabled} onClick={() => onInspect(h.baseline, path)}>{path || '현재 경로 검색'} · 수동 대응에 입력</Button>)}</div>
        </li>)}</ul>
        {result.held.length > 20 && <Pages page={heldPage} size={20} total={result.held.length} set={setHeldPage} />}</details>}
    </>}
  </section>
}

function Pages({ page, size, total, set }: { page: number; size: number; total: number; set: (value: number) => void }) {
  return <div className="mapping-actions"><Button variant="ghost" disabled={!page} onClick={() => set(page - 1)}>이전</Button>{page + 1} / {Math.ceil(total / size)}<Button variant="ghost" disabled={(page + 1) * size >= total} onClick={() => set(page + 1)}>다음</Button></div>
}

function Pattern({ pattern, indices, rows, selected, toggle }: { pattern: string; indices: number[]; rows: Report['suggestions']; selected: Set<number>; toggle: (indices: number[], checked: boolean) => void }) {
  const [page, setPage] = useState(0)
  return <details><summary>{pattern} · {indices.length}개</summary>
    <label><input type="checkbox" checked={indices.every(i => selected.has(i))} onChange={e => toggle(indices, e.target.checked)} />표시된 패턴 {indices.length}개 선택</label>
    <ul>{indices.slice(page * 20, page * 20 + 20).map(i => <li key={i}><label><input type="checkbox" checked={selected.has(i)} onChange={e => toggle([i], e.target.checked)} />
      {rows[i].baseline} → {rows[i].current} · {rows[i].scope === 'subtree' ? '하위 트리' : '단일 경로'} · {rows[i].gain}쌍</label><small>{rows[i].reason}</small></li>)}</ul>
    {indices.length > 20 && <Pages page={page} size={20} total={indices.length} set={setPage} />}
  </details>
}
