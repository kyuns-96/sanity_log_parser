export function WorkspaceBrand({ href }: { href?: string }) {
  const content = <>
    <span className="console-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="currentColor"><rect x="3" y="3" width="7.5" height="7.5" rx="1.5" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" /><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" /></svg>
    </span>
    <span className="console-wordmark">sanity<span>검토 워크스페이스</span></span>
  </>
  return href ? <a className="console-brand console-studio-brand" href={href} aria-label="Sanity Console">{content}</a>
    : <div className="console-brand console-studio-brand" aria-label="Sanity Console">{content}</div>
}
