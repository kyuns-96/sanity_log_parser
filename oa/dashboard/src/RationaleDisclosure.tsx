import { useEffect, useId, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { Button } from './studio/components/ui/button'
import './RationaleDisclosure.css'

export default function RationaleDisclosure({ label, reason, summary }: { label: string; reason: string; summary?: string }) {
  const id = useId()
  const [expanded, setExpanded] = useState(false)
  useEffect(() => setExpanded(false), [label, reason, summary])
  const normalized = reason.replace(/\s+/gu, ' ').trim()
  const firstSentence = normalized.match(/^.*?[.!?](?=\s|$)/u)?.[0] || normalized
  const excerpt = Array.from(firstSentence)
  const shortSummary = summary?.trim() || `${excerpt.slice(0, 120).join('')}${excerpt.length > 120 ? '…' : ''}`
  return <div className="ws-rationale-readout">
    <div className="ws-rationale-summary" aria-label={`${label} 요약`}><span>{summary?.trim() ? '핵심 요약' : '기존 근거 발췌'}</span><p>{shortSummary || '아직 작성된 근거가 없습니다.'}</p></div>
    <Button className="ws-rationale-toggle" type="button" size="sm" variant="outline" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>상세 {label} {expanded ? '접기' : '보기'}{expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</Button>
    <div className="ws-rationale-full" id={id} hidden={!expanded}><p className="ws-rationale-text">{reason}</p></div>
  </div>
}
