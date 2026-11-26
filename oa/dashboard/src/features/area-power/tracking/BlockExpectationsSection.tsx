import { useMemo, useState } from 'react'
import { ChevronRight, Plus, Sparkles } from 'lucide-react'
import { array, format, request, text, unknownSchema, type Auth } from '../api'
import { Button } from '../components/workspace'

interface BlockExpectationsSectionProps {
  auth: Auth
  areaProjectId: string
  selectedBlockId: string
  canWrite: boolean
  expectations: Array<Record<string, unknown>>
  busy: boolean
  runAction: (fn: () => Promise<void>, success?: string) => Promise<void>
  onReload: () => Promise<void>
}

export function BlockExpectationsSection({
  auth,
  areaProjectId,
  selectedBlockId,
  canWrite,
  expectations,
  busy,
  runAction,
  onReload,
}: BlockExpectationsSectionProps) {
  const [expText, setExpText] = useState('')
  const [expNet, setExpNet] = useState('')
  const [expPath, setExpPath] = useState('')
  const [expMetric, setExpMetric] = useState<'logic_area' | 'pwr_all_total_mw'>('logic_area')
  const [expCond, setExpCond] = useState<'any' | 'typical' | 'same_condition'>('any')
  const [expDir, setExpDir] = useState<'increase' | 'decrease'>('increase')
  const [expMin, setExpMin] = useState('')
  const [expMax, setExpMax] = useState('')
  const [expQuote, setExpQuote] = useState('')

  const blockExpectations = useMemo(
    () => expectations.filter(item => text(item.block_id) === selectedBlockId),
    [expectations, selectedBlockId],
  )

  async function saveExpectation() {
    if (!selectedBlockId) throw new Error('블록을 먼저 선택하세요.')
    if (!expPath.trim()) throw new Error('대상 instance 경로를 입력하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/expectations`, unknownSchema, {
      effective_net_ver: expNet.trim() || null,
      target_paths: [expPath.trim()],
      scope: 'subtree',
      metric: expMetric,
      comparison_condition: expCond,
      direction: expDir,
      min_pct: expMin.trim() ? expMin.trim() : null,
      max_pct: expMax.trim() ? expMax.trim() : null,
      quote: expQuote.trim(),
    })
    setExpPath('')
    setExpMin('')
    setExpMax('')
    setExpQuote('')
    await onReload()
  }

  async function proposeExpectation() {
    if (!selectedBlockId) throw new Error('블록을 먼저 선택하세요.')
    if (!expText.trim()) throw new Error('설계 변경 원문을 입력하세요.')
    await request(auth, `/tracking/projects/${encodeURIComponent(areaProjectId)}/blocks/${encodeURIComponent(selectedBlockId)}/expectations/propose`, unknownSchema, {
      request_id: crypto.randomUUID(),
      effective_net_ver: expNet.trim() || null,
      source_text: expText.trim(),
    })
    await onReload()
  }

  return (
    <details className="settings-advanced">
      <summary>
        <ChevronRight size={15} className="chevron" aria-hidden="true" />
        <span>설계 변경 메모 · 예상 증감</span>
        <small>{blockExpectations.length}건 등록됨</small>
      </summary>
      <div className="settings-advanced-body">
        <p className="muted">미리 등록해 둔 예상 증감 범위 안의 변화는 분석 시 자동으로 ‘설명됨’으로 분류합니다.</p>
        <ul className="tracking-mini-list">
          {blockExpectations.map(exp => (
            <li key={text(exp.id)}>
              <span>
                <strong>{array(exp.target_paths).map(text).join(', ')}</strong> · {exp.metric === 'logic_area' ? 'Logic Area' : 'Total Power'} · {exp.direction === 'increase' ? '증가' : '감소'}{' '}
                {exp.min_pct != null || exp.max_pct != null ? `${format(exp.min_pct ?? 0)}~${format(exp.max_pct ?? '?')}%` : '(방향만)'}
                {text(exp.effective_net_ver) ? ` · ${text(exp.effective_net_ver)}부터` : ''}
                {text(exp.quote) ? ` · "${text(exp.quote)}"` : ''}
              </span>
            </li>
          ))}
          {!blockExpectations.length && <li className="muted">등록된 예상 변화가 없습니다.</li>}
        </ul>

        {canWrite && (
          <div className="tracking-expectation-editor">
            <div className="tracking-inline-add">
              <input placeholder="적용 시작 버전 (선택)" value={expNet} onChange={e => setExpNet(e.target.value)} />
              <input placeholder="instance 경로 (예: GPU/u_cache)" value={expPath} onChange={e => setExpPath(e.target.value)} />
              <select value={expMetric} onChange={e => setExpMetric(e.target.value as 'logic_area' | 'pwr_all_total_mw')}>
                <option value="logic_area">Logic Area</option>
                <option value="pwr_all_total_mw">Total Power</option>
              </select>
              <select value={expCond} onChange={e => setExpCond(e.target.value as 'any' | 'typical' | 'same_condition')}>
                <option value="any">모든 조건</option>
                <option value="typical">대표 조건</option>
                <option value="same_condition">동일 조건</option>
              </select>
              <select value={expDir} onChange={e => setExpDir(e.target.value as 'increase' | 'decrease')}>
                <option value="increase">증가</option>
                <option value="decrease">감소</option>
              </select>
              <input placeholder="최소 %" value={expMin} onChange={e => setExpMin(e.target.value)} />
              <input placeholder="최대 %" value={expMax} onChange={e => setExpMax(e.target.value)} />
              <input placeholder="사유 메모" value={expQuote} onChange={e => setExpQuote(e.target.value)} />
              <Button variant="outline" size="sm" disabled={busy} onClick={() => runAction(saveExpectation, '예상 변화를 등록했습니다.')}>
                <Plus size={14} />등록
              </Button>
            </div>
            <div className="tracking-ai-propose">
              <textarea rows={2} value={expText} onChange={e => setExpText(e.target.value)} placeholder="Design info 변경 메모를 붙여넣으면 중앙 VWP AI가 예상 증감 범위를 추출해 제안합니다." />
              <Button variant="outline" size="sm" disabled={busy || !expText.trim()} onClick={() => runAction(proposeExpectation, 'AI 예상 범위 제안을 요청했습니다.')}>
                <Sparkles size={14} />AI 추출 요청
              </Button>
            </div>
          </div>
        )}
      </div>
    </details>
  )
}
