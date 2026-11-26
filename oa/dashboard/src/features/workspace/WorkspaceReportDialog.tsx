import { ArrowRight, Download } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import PreservedSources from './PreservedSources'
import WorkspaceReview from './WorkspaceReview'
import { reportDisplayName, toolResultStatus, workspaceLabels as labels } from './workspaceApi'
import type { ReviewCase, WorkspaceReport, WorkspaceSession, WorkspaceVersion } from './workspaceApi'

const n = (value: number) => value.toLocaleString('ko-KR')

function Status({ value }: { value: string }) {
  return <Badge variant="outline" className={`ws-status ws-status-${value.toLowerCase()}`}><i />{labels[value] || value}</Badge>
}

function Loading({ lines = 3 }: { lines?: number }) {
  return <div className="ws-skeleton" aria-label="불러오는 중" role="status">{Array.from({ length: lines }, (_, i) => <span key={i} />)}</div>
}

export interface WorkspaceReportDialogProps {
  report: WorkspaceReport | null
  loading: boolean
  production: boolean
  session: WorkspaceSession
  projectName?: string
  blockName?: string
  version?: WorkspaceVersion
  eco: string
  onClose: () => void
  onOpenWorkbench: (reportId: string) => void
  onReviewSaved: (event: ReviewCase) => void
  onExpired: () => void
}

export function WorkspaceReportDialog({
  report,
  loading,
  production,
  session,
  projectName,
  blockName,
  version,
  eco,
  onClose,
  onOpenWorkbench,
  onReviewSaved,
  onExpired,
}: WorkspaceReportDialogProps) {
  function downloadReport() {
    if (!report?.raw_report) return
    const url = URL.createObjectURL(new Blob([report.raw_report], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${report.id}.rpt`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <Dialog open={!!report || loading} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="ws-report-dialog" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{report ? reportDisplayName(report) : '검사 근거'} · 보고서</DialogTitle>
          <DialogDescription>{projectName} / {blockName} · {version?.net_ver} · {version?.layout}{eco && ` / ${eco}`}</DialogDescription>
        </DialogHeader>
        {loading ? <Loading /> : report && (
          <>
            <div className="ws-report-meta">
              <span>규칙 판정</span>
              <Status value={toolResultStatus(report)} />
              <Status value={report.parse_status} />
              <span>{labels[report.family]} · {report.rule}</span>
            </div>
            <Tabs defaultValue="source" key={report.id} className="ws-report-tabs">
              <TabsList className="ws-studio-dialog-tabs">
                <TabsTrigger value="source">보고서·근거</TabsTrigger>
                <TabsTrigger value="pipeline">Clustering·그룹 판정</TabsTrigger>
                {!production && <TabsTrigger value="review">사용자 판정·AI review</TabsTrigger>}
              </TabsList>
              <TabsContent value="source">
                <div className="ws-report-dialog-scroll">
                  {report.blockers.length > 0 && (
                    <div className="ws-report-blockers">
                      <strong>파싱 완료를 막는 사유</strong>
                      <ul>{report.blockers.map(code => <li key={code}>{code}</li>)}</ul>
                    </div>
                  )}
                  <PreservedSources files={report.preserved_sources} base={production ? `/blocks/${report.block_id}/versions/${report.version_id}/reports/${report.id}` : undefined} />
                  {report.source_storage === 'PARTS_V2' ? (
                    <>
                      <h3>수신한 검사 근거 {n(report.record_count)}건</h3>
                      <p className="ws-muted">로그를 페이지로 나누어 저장했습니다. 검토 화면에서 전체 로그를 확인하고 그룹 분류를 시작할 수 있습니다. 원본 report 파일은 VWP에 보관됩니다.</p>
                      {!!report.delivery_blockers?.length && <p className="ws-error">검토 대기: {report.delivery_blockers.join(', ')}</p>}
                      <Button onClick={() => onOpenWorkbench(report.id)}>전체 로그·검토 열기<ArrowRight size={14} /></Button>
                    </>
                  ) : (
                    <>
                      <h3>추출된 검사 근거</h3>
                      {report.records?.length ? report.records.map(record => (
                        <article className="ws-evidence" key={record.id}>
                          <header><Status value={toolResultStatus(record)} /><span>원문 {record.line_start}–{record.line_end}행</span></header>
                          <dl>{Object.entries(record.fields).filter(([key]) => key !== 'message').map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl>
                          <pre>{record.raw}</pre>
                        </article>
                      )) : <p className="ws-muted">보고서가 미수신되어 추출 근거가 없습니다.</p>}
                      <h3>보고서 전체 원문</h3>
                      <pre>{report.raw_report || '예상 검사 목록에는 포함되지만 원본 보고서가 아직 수신되지 않았습니다.'}</pre>
                      {!production && <p className="ws-muted">공개 형식을 참고해 작성한 예시 보고서입니다. 실제 사내 report는 포함하지 않습니다.</p>}
                    </>
                  )}
                </div>
              </TabsContent>
              <TabsContent value="pipeline" forceMount className="data-[state=inactive]:hidden">
                <div className="ws-report-dialog-scroll">
                  <p className="ws-muted">전체 로그 분류와 그룹별 근거 확인을 순서대로 진행합니다.</p>
                  <Button onClick={() => onOpenWorkbench(report.id)}>그룹별 근거·순차 검토 열기<ArrowRight size={14} /></Button>
                </div>
              </TabsContent>
              {!production && (
                <TabsContent value="review" forceMount className="data-[state=inactive]:hidden">
                  <div className="ws-report-dialog-scroll">
                    <WorkspaceReview report={report} session={session} onSaved={onReviewSaved} onExpired={onExpired} />
                  </div>
                </TabsContent>
              )}
            </Tabs>
            <div className="ws-dialog-actions">
              <Button variant="outline" disabled={!report.raw_report} onClick={downloadReport}><Download size={15} />보고서 다운로드</Button>
              <Button onClick={onClose}>닫기</Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
