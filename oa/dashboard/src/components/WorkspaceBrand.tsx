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
      <svg viewBox="0 0 30 30" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12.5" cy="12.5" r="8.5" /><path d="m19 19 7 7M8 14l4-4 5 4" /><g fill="currentColor" stroke="none"><circle cx="8" cy="14" r="1.4" /><circle cx="12" cy="10" r="1.4" /><circle cx="17" cy="14" r="1.4" /></g></svg>
    </span>
    <span className="console-wordmark"><strong className="console-brand-name">Sanity Lens <span className="console-brand-ai">AI</span></strong><span className="console-brand-tagline">AI 로그 분석 · 판정 지원</span></span>
  </>
  return <a className="console-brand console-studio-brand" href={href ?? homeHref()} aria-label="Sanity Lens AI 홈">{content}</a>
}
