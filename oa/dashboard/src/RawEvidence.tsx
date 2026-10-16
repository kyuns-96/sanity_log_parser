import { useState } from 'react'
import { Button } from './components/ui/button'

type Slice = { raw: string; start: number; total_chars: number; redacted: boolean }
export function RawEvidence({ api, requestId, record, demo }: {
  api: <T>(path: string) => Promise<T>; requestId: string; demo: boolean;
  record: { record_id: string; raw: string; raw_truncated: boolean; redacted?: boolean }
}) {
  const [slice, setSlice] = useState<Slice | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function load(start: number) {
    setBusy(true); setError('')
    try { setSlice(await api<Slice>(`/api/evidence/${encodeURIComponent(requestId)}/records/${encodeURIComponent(record.record_id)}?start=${start}&length=8000`)) }
    catch (e) { setError(e instanceof Error ? e.message : '원문 조회 실패') }
    finally { setBusy(false) }
  }
  return <div className="code-example"><div>{record.record_id}</div>
    {(record.redacted || slice?.redacted) && <p>자격증명 마스킹 적용</p>}
    <pre>{slice?.raw ?? record.raw}</pre>
    {error && <p role="alert">{error}</p>}
    {record.raw_truncated && !slice && <p>미리보기 잘림</p>}
    {!demo && !slice && <Button size="sm" variant="outline" disabled={busy} onClick={() => void load(0)}>전체 원문 페이지 열기</Button>}
    {slice && <div className="cluster-actions"><span>{slice.start + 1}–{Math.min(slice.start + 8000, slice.total_chars)} / {slice.total_chars}자</span>
      <Button size="sm" variant="outline" disabled={busy || !slice.start} onClick={() => void load(Math.max(0, slice.start - 8000))}>원문 이전</Button>
      <Button size="sm" variant="outline" disabled={busy || slice.start + 8000 >= slice.total_chars} onClick={() => void load(slice.start + 8000)}>원문 다음</Button></div>}
  </div>
}
