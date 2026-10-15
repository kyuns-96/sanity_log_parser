export function WorkspaceBrand({ href }: { href?: string }) {
  const content = <>
    <span className="console-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none"><path d="M23 7H9v9h14v9H9" stroke="currentColor" strokeWidth="3.5" strokeLinejoin="miter" /><path d="M23 7v4M9 21v4" stroke="currentColor" strokeWidth="3.5" /></svg>
    </span>
    <span className="console-wordmark">sanity<span>console</span></span>
  </>
  return href ? <a className="console-brand" href={href} aria-label="Sanity Console">{content}</a>
    : <div className="console-brand" aria-label="Sanity Console">{content}</div>
}
