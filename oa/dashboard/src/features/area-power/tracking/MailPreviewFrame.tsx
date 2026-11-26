import { useState } from 'react'
import { Code2, Copy, FileText, Mail } from 'lucide-react'
import { Button } from '../components/workspace'

interface MailPreviewFrameProps {
  title: string
  subject?: string | null
  textBody: string
  htmlBody?: string | null
  defaultHeight?: number
}

export function MailPreviewFrame({
  title,
  subject,
  textBody,
  htmlBody,
  defaultHeight = 560,
}: MailPreviewFrameProps) {
  const hasHtml = Boolean(htmlBody && htmlBody.trim())
  const [view, setView] = useState<'html' | 'text'>(hasHtml ? 'html' : 'text')
  const [copiedMode, setCopiedMode] = useState<'text' | 'html' | null>(null)
  const activeView = view === 'html' && hasHtml ? 'html' : 'text'

  function copyContent(mode: 'text' | 'html') {
    const payload = mode === 'html' && htmlBody
      ? htmlBody
      : (subject ? `${subject}\n\n${textBody}` : textBody)
    void navigator.clipboard?.writeText(payload).then(() => {
      setCopiedMode(mode)
      window.setTimeout(() => setCopiedMode(null), 1800)
    }).catch(() => undefined)
  }

  return (
    <div className="mail-preview-frame">
      <div className="mail-preview-bar">
        {hasHtml ? (
          <div className="view-switch" role="group" aria-label="메일 미리보기 형식">
            <button type="button" aria-pressed={activeView === 'html'} onClick={() => setView('html')}>
              <Mail size={12} aria-hidden="true" /> HTML 메일
            </button>
            <button type="button" aria-pressed={activeView === 'text'} onClick={() => setView('text')}>
              <FileText size={12} aria-hidden="true" /> 텍스트 원문
            </button>
          </div>
        ) : (
          <small className="muted">텍스트 원문</small>
        )}
        <div className="mail-preview-actions">
          <Button variant="outline" size="xs" onClick={() => copyContent('text')}>
            <Copy size={12} />{copiedMode === 'text' ? '복사됨' : '텍스트 복사'}
          </Button>
          {hasHtml && (
            <Button variant="outline" size="xs" onClick={() => copyContent('html')}>
              <Code2 size={12} />{copiedMode === 'html' ? 'HTML 복사됨' : 'HTML 복사'}
            </Button>
          )}
        </div>
      </div>

      {activeView === 'html' && htmlBody ? (
        <iframe
          className="mail-preview-iframe"
          title={title}
          sandbox=""
          srcDoc={htmlBody}
          style={{ height: defaultHeight }}
        />
      ) : (
        <pre className="report-mail-body">{textBody}</pre>
      )}
    </div>
  )
}
