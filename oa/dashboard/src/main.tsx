import React from 'react'
import ReactDOM from 'react-dom/client'
import App from '@/features/review/BundleReviewApp'
import AccessFlowDemo from '@/features/demo/AccessFlowDemo'
import VersionFlowDemo from '@/features/demo/VersionFlowDemo'
import ReportsPreview from '@/features/demo/ReportsPreview'
import Workspace from '@/features/workspace/Workspace'
import DashboardNavigation from '@/features/atlas/DashboardNavigation'
import { initializeColorMode } from '@/lib/colorMode'
import { appPathname, appUrl } from '@/lib/appUrl'
import 'pretendard/dist/web/variable/pretendardvariable.css'
import '@/styles/style.css'
import '@/styles/theme.css'
import '@/styles/studio-system.css'
import '@/styles/live-workbench.css'
import '@/styles/button-motion.css'
import '@/styles/color-mode.css'
import '@/styles/select-controls.css'
import '@/styles/sidebar-layout.css'
import '@/features/area-power/styles.css'
import '@/styles/module-navigation.css'
import '@/styles/atlas.css'


const AtlasDemo = React.lazy(() => import('@/features/demo/AtlasDemo'))
document.documentElement.classList.add('studio-system')
initializeColorMode()

if (['/workspace', '/workspace/'].includes(appPathname()) && new URLSearchParams(location.search).get('demo') !== '1') {
  const target = new URL(appUrl('/'), location.origin)
  target.search = location.search
  target.searchParams.set('page', 'workbench')
  history.replaceState(null, '', target.pathname + target.search)
}
const params = new URLSearchParams(location.search)
const accessDemo = params.get('demo') === '1' && params.get('flow') === 'access'
const versionDemo = params.get('demo') === '1' && params.get('flow') === 'versions'
const reportsPreview = params.get('demo') === '1' && params.get('dataset') === 'reports'
const workspaceDemo = params.get('demo') === '1' && !params.has('flow') && !params.has('dataset')
const applicationPath = appPathname()
const productionWorkbench = applicationPath === '/' && params.get('page') === 'workbench'
const areaPower = applicationPath === '/' && params.get('page') === 'area-power'
const workspace = productionWorkbench || applicationPath === '/workspace' || applicationPath === '/workspace/' || workspaceDemo
const integratedDemo = workspaceDemo && ['/', '/workspace', '/workspace/'].includes(applicationPath)
const dashboard = applicationPath === '/' && (productionWorkbench || areaPower || params.get('demo') !== '1')
if (dashboard) document.title = 'Design Atlas · 설계 검토와 구성·전력 분석'
else if (workspace) document.title = `${params.get('demo') === '1' ? '합성 워크스페이스 데모' : '로그 워크스페이스'} | Design Atlas`
if (versionDemo) document.title = 'PRE · POST · ECO · PoR | Design Atlas · 데모'
if (reportsPreview) document.title = 'GCA · VC LP · SpyGlass | Design Atlas · 미리보기'
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode>{integratedDemo ? <React.Suspense fallback={<p role="status">통합 합성 데모를 준비하고 있습니다…</p>}><AtlasDemo /></React.Suspense> : dashboard ? <DashboardNavigation /> : workspace ? <Workspace /> : reportsPreview ? <ReportsPreview /> : versionDemo ? <VersionFlowDemo /> : accessDemo ? <AccessFlowDemo /> : <App />}</React.StrictMode>)
