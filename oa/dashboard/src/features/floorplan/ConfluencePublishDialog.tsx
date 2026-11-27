import { useEffect, useState } from 'react'
import { Download, ExternalLink, KeyRound, Send } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { ConfluencePreviewData, ConfluenceTargetConfig, FloorplanVersionDetail, PlacementProposal } from './floorplanApi'

interface ConfluencePublishDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  projectName: string
  blockName: string
  blockId: string
  fpVer: string
  projectConfluence: ConfluenceTargetConfig
  blockConfluence?: ConfluenceTargetConfig
  patStatus: { registered: boolean; revision: number }
  proposals: PlacementProposal[]
  publishes: FloorplanVersionDetail['publishes']
  canWrite: boolean
  onSaveTargetConfig: (scope: 'project' | 'block', spaceKey: string, urlOrId: string) => Promise<void>
  onSavePat: (token: string | null) => Promise<void>
  onPreview: (opts: { include_overview: boolean; include_bist: boolean; include_datapath: boolean; proposal_id: string | null; title_suffix: string }) => Promise<ConfluencePreviewData>
  onPublish: (opts: { include_overview: boolean; include_bist: boolean; include_datapath: boolean; proposal_id: string | null; title_suffix: string }) => Promise<void>
}

function fmtKb(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KB`
}

export function ConfluencePublishDialog({
  open,
  onOpenChange,
  projectName,
  blockName,
  fpVer,
  projectConfluence,
  blockConfluence,
  patStatus,
  proposals,
  publishes,
  canWrite,
  onSaveTargetConfig,
  onSavePat,
  onPreview,
  onPublish,
}: ConfluencePublishDialogProps) {
  const [cfgScope, setCfgScope] = useState<'project' | 'block'>(blockConfluence?.parent_page_id ? 'block' : 'project')
  const activeCfg = cfgScope === 'block' ? (blockConfluence || { space_key: '', parent_page_id: '', raw_url: '' }) : projectConfluence
  const [spaceKey, setSpaceKey] = useState(activeCfg.space_key || '')
  const [parentUrl, setParentUrl] = useState(activeCfg.raw_url || activeCfg.parent_page_id || '')
  const [patInput, setPatInput] = useState('')
  const [includeOverview, setIncludeOverview] = useState(true)
  const [includeBist, setIncludeBist] = useState(true)
  const [includeDatapath, setIncludeDatapath] = useState(false)
  const [proposalId, setProposalId] = useState<string>('')
  const [titleSuffix, setTitleSuffix] = useState('')
  const [preview, setPreview] = useState<ConfluencePreviewData | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    setSpaceKey(activeCfg.space_key || '')
    setParentUrl(activeCfg.raw_url || activeCfg.parent_page_id || '')
  }, [cfgScope, activeCfg.space_key, activeCfg.raw_url, activeCfg.parent_page_id])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setError('')
    void onPreview({
      include_overview: includeOverview,
      include_bist: includeBist,
      include_datapath: includeDatapath,
      proposal_id: proposalId || null,
      title_suffix: titleSuffix,
    }).then(data => { if (!cancelled) setPreview(data) }).catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : '미리보기 생성 실패') })
    return () => { cancelled = true }
  }, [open, includeOverview, includeBist, includeDatapath, proposalId, titleSuffix])

  function downloadStandaloneHtml() {
    if (!preview) return
    const blob = new Blob([preview.standalone_html], { type: 'text/html;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${projectName}_${blockName}_${fpVer}_review.html`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <Dialog open={open} onOpenChange={v => { if (!busy) onOpenChange(v) }}>
      <DialogContent className="fp-confluence-dialog">
        <DialogHeader>
          <DialogTitle>Confluence 하위 페이지 생성 · HTML 내보내기</DialogTitle>
          <DialogDescription>
            쓰기 전용(Create-Only) 정책에 맞춰 GET/PUT/DELETE 없이 고유 제목(<code>Rev.N</code>)의 신규 하위 페이지와 경량 8-bit PNG 첨부만 생성합니다.
          </DialogDescription>
        </DialogHeader>

        {error && <p className="fp-error-banner" role="alert">{error}</p>}
        {notice && <p className="fp-notice-banner" role="status">{notice}</p>}

        <div className="fp-conf-grid">
          <section className="fp-card">
            <div className="fp-card-head">
              <h4>1. 부모 페이지 설정 (조회 API 무호출)</h4>
              <div className="fp-scope-switch">
                <button type="button" className={cfgScope === 'project' ? 'is-on' : ''} onClick={() => setCfgScope('project')}>Project 기본</button>
                <button type="button" className={cfgScope === 'block' ? 'is-on' : ''} onClick={() => setCfgScope('block')}>{blockName} 전용</button>
              </div>
            </div>
            <div className="fp-dp-row">
              <input type="text" placeholder="Space Key (예: SOCFP)" value={spaceKey} onChange={e => setSpaceKey(e.target.value)} style={{ maxWidth: 140 }} />
              <input type="text" placeholder="부모 페이지 URL (?pageId=12345) 또는 숫자 Page ID" value={parentUrl} onChange={e => setParentUrl(e.target.value)} />
              {canWrite && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true); setError(''); setNotice('')
                    try {
                      await onSaveTargetConfig(cfgScope, spaceKey, parentUrl)
                      setNotice('Confluence 부모 페이지 설정을 저장했습니다.')
                    } catch (err) {
                      setError(err instanceof Error ? err.message : '설정 저장 실패')
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  저장
                </Button>
              )}
            </div>
            <small className="text-muted">
              현재 적용 대상: Space <code>{preview?.space_key || '미설정'}</code> · Parent Page ID <code>{preview?.parent_page_id || '미설정'}</code>
            </small>
          </section>

          <section className="fp-card">
            <div className="fp-card-head">
              <h4><KeyRound size={14} /> 2. 내 개인 Confluence PAT (계정별 업로드 한도 분리)</h4>
              <span className={`fp-pill ${patStatus.registered ? 'is-ok' : 'is-warn'}`}>
                {patStatus.registered ? '등록됨 (암호화 보관)' : '미등록'}
              </span>
            </div>
            <div className="fp-dp-row">
              <input
                type="password"
                autoComplete="off"
                placeholder="본인 Confluence Personal Access Token 입력"
                value={patInput}
                onChange={e => setPatInput(e.target.value)}
              />
              <Button
                variant="outline"
                size="sm"
                disabled={busy || !patInput.trim()}
                onClick={async () => {
                  setBusy(true); setError(''); setNotice('')
                  try {
                    await onSavePat(patInput.trim())
                    setPatInput('')
                    setNotice('개인 Confluence PAT를 암호화해 저장했습니다.')
                  } catch (err) {
                    setError(err instanceof Error ? err.message : 'PAT 저장 실패')
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                {patStatus.registered ? 'PAT 교체' : 'PAT 등록'}
              </Button>
              {patStatus.registered && (
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => void onSavePat(null)}>해제</Button>
              )}
            </div>
          </section>

          <section className="fp-card">
            <div className="fp-card-head">
              <h4>3. 첨부 스냅샷 선택 · 용량 확인</h4>
              {preview && <strong>총 {preview.attachment_count}장 · {fmtKb(preview.total_attachment_bytes)}</strong>}
            </div>
            <div className="fp-tpl-toggle-list">
              <label className="fp-tpl-check"><input type="checkbox" checked={includeOverview} onChange={e => setIncludeOverview(e.target.checked)} /><span>기본 배치 + 리뷰 핀 번호 스냅샷</span></label>
              <label className="fp-tpl-check"><input type="checkbox" checked={includeBist} onChange={e => setIncludeBist(e.target.checked)} /><span>Memory BIST Group 색상 분포 스냅샷</span></label>
              <label className="fp-tpl-check"><input type="checkbox" checked={includeDatapath} onChange={e => setIncludeDatapath(e.target.checked)} /><span>Data-path 경로선 스냅샷</span></label>
            </div>
            <div className="fp-dp-row">
              {proposals.length > 0 && (
                <select value={proposalId} onChange={e => setProposalId(e.target.value)}>
                  <option value="">Designer 제안안 미포함</option>
                  {proposals.map(p => <option key={p.id} value={p.id}>제안안 포함: {p.title}</option>)}
                </select>
              )}
              <input type="text" placeholder="제목 부가 메모 (선택)" value={titleSuffix} onChange={e => setTitleSuffix(e.target.value)} />
            </div>
            {preview && <small className="fp-preview-title">생성될 하위 페이지 제목: <strong>{preview.title}</strong></small>}
          </section>

          {publishes.length > 0 && (
            <section className="fp-card">
              <h4>최근 게시 이력 ({publishes.length})</h4>
              <div className="fp-compact-list">
                {publishes.slice(0, 4).map(pub => (
                  <div key={pub.id} className="fp-list-row">
                    <span><code>Rev.{pub.publish_rev}</code> {pub.title}</span>
                    {pub.page_url ? (
                      <a href={pub.page_url} target="_blank" rel="noopener noreferrer" className="fp-dl-link">열기 <ExternalLink size={12} /></a>
                    ) : <code>ID {pub.page_id}</code>}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="fp-dialog-footer">
          <Button variant="outline" disabled={!preview} onClick={downloadStandaloneHtml}>
            <Download size={14} />단독 HTML 다운로드
          </Button>
          <Button
            disabled={busy || !canWrite || !patStatus.registered || !preview?.space_key || !preview?.parent_page_id}
            onClick={async () => {
              setBusy(true); setError(''); setNotice('')
              try {
                await onPublish({
                  include_overview: includeOverview,
                  include_bist: includeBist,
                  include_datapath: includeDatapath,
                  proposal_id: proposalId || null,
                  title_suffix: titleSuffix,
                })
                setNotice('Confluence 신규 하위 페이지를 생성했습니다.')
              } catch (err) {
                setError(err instanceof Error ? err.message : 'Confluence 게시에 실패했습니다.')
              } finally {
                setBusy(false)
              }
            }}
          >
            <Send size={14} />Confluence 신규 하위 페이지 생성 (Rev.{preview?.publish_rev ?? 1})
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
