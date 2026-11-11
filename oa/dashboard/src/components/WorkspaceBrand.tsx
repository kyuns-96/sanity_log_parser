import { appPathname, appUrl } from '../appUrl'
import { useAtlas } from '../atlasContext'

function homeHref() {
  if (typeof location === 'undefined') return '/'
  const current = new URL(location.href)
  const workspace = appPathname(current.pathname) === '/workspace' || appPathname(current.pathname) === '/workspace/'
  if (current.searchParams.get('demo') === '1') {
    if (workspace) return '/workspace?demo=1'
    const query = new URLSearchParams({ demo: '1' })
    const flow = current.searchParams.get('flow')
    const dataset = current.searchParams.get('dataset')
    if (flow === 'access' || flow === 'versions') query.set('flow', flow)
    if (dataset === 'gca' || dataset === 'reports') query.set('dataset', dataset)
    return `/?${query}`
  }
  return '/'
}

export function WorkspaceBrand({ href }: { href?: string }) {
  const atlas = useAtlas()
  const content = <>
    <span className="console-mark" aria-hidden="true">
      <img src={appUrl('/design-atlas.svg')} alt="" width="100%" height="100%" />
    </span>
    <span className="console-wordmark"><strong className="console-brand-name">Design Atlas</strong><span className="console-brand-tagline">로그 검토 · 구성·전력 분석</span></span>
  </>
  const target = href ?? homeHref()
  return <a className="console-brand console-studio-brand" href={atlas ? atlas.href(target) : appUrl(target)} aria-label="Design Atlas 홈">{content}</a>
}
