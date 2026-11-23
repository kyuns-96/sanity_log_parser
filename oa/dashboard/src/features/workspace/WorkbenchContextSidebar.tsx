import { ArrowLeft, FileText, FolderTree, GitBranch, ShieldCheck } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { reportDisplayName, toolResultStatus, workspaceLabels as labels } from './workspaceApi'
import type { WorkbenchView } from './workspaceApi'

const n = (value: number) => value.toLocaleString('ko-KR')

interface WorkbenchContextSidebarProps {
  data: WorkbenchView
  production: boolean
  context?: { projectName?: string; blockName?: string; porLabel?: string }
  onBack: () => void
}

export function WorkbenchContextSidebar({ data, production, context, onBack }: WorkbenchContextSidebarProps) {
  return (
    <aside className="ws-studio-context" aria-label="프로젝트와 보고서 정보">
      <div className="ws-context-heading"><FolderTree size={16} /><h2>프로젝트</h2></div>
      <div className="ws-context-project"><strong>{context?.projectName || '프로젝트 정보 없음'}</strong><span>{context?.blockName || '블록 정보 없음'}</span></div>
      <div className="ws-context-version"><GitBranch size={15} /><div><strong>{data.version.net_ver}</strong><span>{data.version.layout} · {data.version.revision}</span><span>ECO {data.report.eco || '해당 없음'}</span></div></div>
      <div className="ws-context-por"><ShieldCheck size={15} /><div><span>현재 PoR</span><strong>{context?.porLabel || '미지정'}</strong></div></div>
      <div className="ws-context-heading"><FileText size={16} /><h2>검사 보고서</h2></div>
      <div className="ws-context-file">
        <strong>{data.report.item_id || data.report.rule}</strong>
        <span>{data.report.family}</span>
        <span title={data.report.id}>{reportDisplayName(data.report)}</span>
        <Badge variant="outline" className={`ws-status ws-status-${data.report.check_status.toLowerCase()}`}>도구 결과 {labels[toolResultStatus(data.report)]}</Badge>
      </div>
      <dl className="ws-context-metrics">
        <div><dt>전체 로그</dt><dd>{n(data.record_count)}</dd></div>
        <div><dt>분류 그룹</dt><dd>{n(data.summary.total)}</dd></div>
        <div><dt>분류 개정</dt><dd>{data.revision}</dd></div>
      </dl>
      <p className="ws-context-permission">{data.can_edit ? production ? '프로젝트 멤버 · 검토 가능' : '블록 담당자 · 검토 가능' : '조회 전용'}</p>
      <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft size={14} />보고서 목록</Button>
    </aside>
  )
}
