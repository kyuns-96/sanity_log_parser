import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AccessFlowDemo from './AccessFlowDemo'
import VersionFlowDemo from './VersionFlowDemo'
import ReportsPreview from './ReportsPreview'
import Workspace from './Workspace'
import 'pretendard/dist/web/variable/pretendardvariable.css'
import './style.css'
import './theme.css'
import './studio-system.css'
import './live-workbench.css'
import './button-motion.css'

document.documentElement.classList.add('studio-system')

const params = new URLSearchParams(location.search)
const accessDemo = params.get('demo') === '1' && params.get('flow') === 'access'
const versionDemo = params.get('demo') === '1' && params.get('flow') === 'versions'
const reportsPreview = params.get('demo') === '1' && params.get('dataset') === 'reports'
const workspaceDemo = params.get('demo') === '1' && !params.has('flow') && !params.has('dataset')
const productionWorkbench = location.pathname === '/' && params.get('page') === 'workbench'
const workspace = productionWorkbench || location.pathname === '/workspace' || location.pathname === '/workspace/' || workspaceDemo
if (workspace) document.title = `${productionWorkbench ? 'AI 검토 워크스페이스' : params.get('demo') === '1' ? '워크스페이스 데모' : '내 워크스페이스'} | Sanity Console`
if (versionDemo) document.title = 'PRE · POST · ECO · PoR | Sanity Demo'
if (reportsPreview) document.title = 'GCA · VC LP · SpyGlass | Sanity Preview'
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode>{workspace ? <Workspace /> : reportsPreview ? <ReportsPreview /> : versionDemo ? <VersionFlowDemo /> : accessDemo ? <AccessFlowDemo /> : <App />}</React.StrictMode>)
