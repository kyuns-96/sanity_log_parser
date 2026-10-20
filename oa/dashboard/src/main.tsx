import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AccessFlowDemo from './AccessFlowDemo'
import VersionFlowDemo from './VersionFlowDemo'
import ReportsPreview from './ReportsPreview'
import Workspace from './Workspace'
import './style.css'
import './theme.css'

const params = new URLSearchParams(location.search)
const accessDemo = params.get('demo') === '1' && params.get('flow') === 'access'
const versionDemo = params.get('demo') === '1' && params.get('flow') === 'versions'
const reportsPreview = params.get('demo') === '1' && params.get('dataset') === 'reports'
const workspaceDemo = params.get('demo') === '1' && !params.has('flow') && !params.has('dataset')
const workspace = location.pathname === '/workspace' || location.pathname === '/workspace/' || workspaceDemo
if (workspace) document.title = `${params.get('demo') === '1' ? '워크스페이스 데모' : '내 워크스페이스'} | Sanity Console`
if (versionDemo) document.title = 'PRE · POST · ECO · PoR | Sanity Demo'
if (reportsPreview) document.title = 'GCA · VC LP · SpyGlass | Sanity Preview'
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode>{workspace ? <Workspace /> : reportsPreview ? <ReportsPreview /> : versionDemo ? <VersionFlowDemo /> : accessDemo ? <AccessFlowDemo /> : <App />}</React.StrictMode>)
