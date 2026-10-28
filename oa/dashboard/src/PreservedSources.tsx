import { useEffect, useState } from 'react'
import { Button } from './studio/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './components/ui/dialog'
import { workspaceApiBase, workspaceRequest as request } from './workspaceApi'
import './preserved-sources.css'

export type PreservedSource = {
  id: string; path: string; role: 'canonical_companion' | 'constraint_input';
  binding: { kind: 'unit' | 'execution' | 'units' | 'executions'; review_unit_id?: string; anchor_unit_id?: string; review_unit_ids?: string[]; anchor_unit_ids?: string[] };
  size_bytes: number; sha256: string; unit_key?: string; execution_id?: string;
  targets?: { unit_key: string; execution_id: string }[];
  storage_status: 'PRESERVED'; content_available_to_ai: boolean;
  extraction?: { line_count: number; text_sha256: string; text_bytes: number; redacted: boolean };
}
export type PreservedCitation = { target_id: string; evidence_ids: string[]; reason: string }
export type PreservedCitationSource = { id: string; source_id: string; line_start: number; line_end: number; source_sha256: string; redacted: boolean }
type Segment = PreservedCitationSource & { text: string; text_sha256: string; byte_start: number; byte_end: number }
type TextPage = { source: PreservedSource; items: Segment[]; total: number; offset: number; limit: number; complete: boolean; masked: boolean }
const roles = { canonical_companion: '원천 보고서·카탈로그', constraint_input: '분석 입력 제약' }

export function PreservedCitations({ citations, sources, files, target }: { citations?: PreservedCitation[]; sources?: PreservedCitationSource[]; files?: PreservedSource[]; target?: string }) {
  const selected = citations?.filter(c => !target || c.target_id === target) || []
  if (!selected.length) return null
  return <details className="ws-preserved-citations"><summary>AI가 인용한 보존 본문 근거</summary>{selected.map(cite => <div key={cite.target_id}>
    <p>{cite.reason}</p><ul>{cite.evidence_ids.map(id => {
      const source = sources?.find(s => s.id === id)
      const file = files?.find(f => f.id === source?.source_id)
      return <li key={id}>{file?.path || '보존 파일'} · {source?.line_start}–{source?.line_end}행{source?.redacted ? ' · 마스킹 포함' : ''}<code>{id}</code></li>
    })}</ul>
  </div>)}</details>
}

export default function PreservedSources({ files, base }: { files?: PreservedSource[]; base?: string }) {
  const [selected, setSelected] = useState<PreservedSource | null>(null)
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<TextPage | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { setSelected(null); setPage(null); setError('') }, [base])
  useEffect(() => {
    setPage(null); setError('')
    if (!selected || !base) return
    const abort = new AbortController()
    request<TextPage>(`${base}/preserved/${selected.id}?offset=${offset}&limit=10`, { signal: abort.signal })
      .then(setPage).catch(e => { if (!abort.signal.aborted) setError(e instanceof Error ? e.message : '본문을 불러오지 못했습니다.') })
    return () => abort.abort()
  }, [base, selected, offset])
  if (!files?.length) return null
  const available = files.filter(f => f.content_available_to_ai).length
  return <>
    <details className="ws-panel ws-preserved-sources" aria-label="보존 근거 파일">
      <summary><strong>보존 근거 파일 {files.length}개</strong></summary>
      <p>{available ? `원본은 VWP에 보관됩니다. ${available}개 파일의 마스킹 본문이 AI 검토에 사용할 근거로 준비되었습니다. 본문 제공이 없는 파일은 목록·귀속 정보만 전달됩니다.` : '원본은 VWP에 보관됩니다. AI에는 아래 목록과 귀속 정보가 전달되며 파일 본문은 아직 전달되지 않습니다.'}</p>
      <div className="ws-table-scroll"><table><thead><tr><th>파일·역할</th><th>적용 대상</th><th>크기·무결성</th></tr></thead><tbody>
        {files.map(file => <tr key={file.id}>
          <td>{file.path}<small>{roles[file.role]}</small>{file.content_available_to_ai && (base ? <Button variant="outline" size="sm" onClick={() => { setOffset(0); setSelected(file) }}>마스킹 본문 보기</Button> : <small>접수 확정 후 본문 조회 가능</small>)}</td>
          <td>{['unit', 'units'].includes(file.binding.kind) ? '검사 단위' : '원천 실행'}<small>{file.binding.review_unit_id || file.binding.anchor_unit_id || (file.binding.review_unit_ids || file.binding.anchor_unit_ids)?.join(', ')}</small></td>
          <td>{file.size_bytes.toLocaleString('ko-KR')} bytes<details><summary>SHA256 확인</summary><code>{file.sha256}</code></details>{file.extraction && <small>{file.extraction.line_count.toLocaleString('ko-KR')}행 · {file.extraction.redacted ? '마스킹 적용' : '비밀 패턴 검사 완료'}</small>}</td>
        </tr>)}
      </tbody></table></div>
    </details>
    <Dialog open={!!selected} onOpenChange={open => { if (!open) setSelected(null) }}><DialogContent className="ws-preserved-dialog">
      <DialogHeader><DialogTitle>보존 파일 마스킹 본문</DialogTitle><DialogDescription>원본 위치를 유지한 검토용 텍스트입니다. 원본 파일은 VWP에 보관되며, 마스킹한 내용은 이 화면과 AI에 제공되지 않습니다.</DialogDescription></DialogHeader>
      <p>{selected?.path}</p>{error ? <p role="alert">{error}</p> : !page ? <p role="status">본문을 확인하고 있습니다.</p> : <>
        <p>전체 {page.source.extraction?.line_count.toLocaleString('ko-KR')}행 · 근거 구간 {page.total}개 중 {page.total ? offset + 1 : 0}–{Math.min(offset + page.limit, page.total)}개 표시</p>
        <a href={`${workspaceApiBase()}${base}/preserved/${selected!.id}?download=true`} download="preserved-masked.txt">마스킹 본문 내려받기</a>
        <div className="ws-preserved-segments">{page.items.map(segment => <section key={segment.id}>
          <strong>{segment.line_start}–{segment.line_end}행{segment.redacted ? ' · 마스킹 포함' : ''}</strong><code>{segment.id}</code><pre>{segment.text}</pre>
        </section>)}</div>
        <div className="submission-intake-actions"><Button variant="outline" disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 10))}>이전 근거</Button><Button variant="outline" disabled={offset + page.limit >= page.total} onClick={() => setOffset(v => v + 10)}>다음 근거</Button></div>
      </>}
    </DialogContent></Dialog>
  </>
}
