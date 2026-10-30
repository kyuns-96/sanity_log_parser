function homeHref() {
  if (typeof location === 'undefined') return '/'
  const current = new URL(location.href)
  const workspace = current.pathname === '/workspace' || current.pathname === '/workspace/'
  if (current.searchParams.get('demo') === '1') {
    if (workspace) return '/workspace?demo=1'
    const query = new URLSearchParams({ demo: '1' })
    const flow = current.searchParams.get('flow')
    const dataset = current.searchParams.get('dataset')
    if (flow === 'access' || flow === 'versions') query.set('flow', flow)
    if (dataset === 'gca' || dataset === 'reports') query.set('dataset', dataset)
    return `/?${query}`
  }
  return workspace ? '/workspace' : '/'
}

export function WorkspaceBrand({ href }: { href?: string }) {
  const content = <>
    <span className="console-mark" aria-hidden="true">
      <svg viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round"><circle cx="15" cy="15" r="10.5" strokeWidth="1.5" /><circle cx="15" cy="15" r="7.7" strokeWidth="1.2" /><path d="M15 11.8 17.77 13.4 17.77 16.6 15 18.2 12.23 16.6 12.23 13.4Z M15 11.8 21.67 11.15 M17.77 13.4 21.67 18.85 M17.77 16.6 15 22.7 M15 18.2 8.33 18.85 M12.23 16.6 8.33 11.15 M12.23 13.4 15 7.3" strokeWidth="1.15" /></svg>
    </span>
    <span className="console-wordmark"><strong className="console-brand-name">Sanity Lens <span className="console-brand-ai">AI</span></strong><span className="console-brand-tagline">AI 로그 분석 · 판정 지원</span></span>
  </>
  return <a className="console-brand console-studio-brand" href={href ?? homeHref()} aria-label="Sanity Lens AI 홈">{content}</a>
}
