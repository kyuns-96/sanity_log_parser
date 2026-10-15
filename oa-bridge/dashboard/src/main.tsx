import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AccessFlowDemo from './AccessFlowDemo'
import VersionFlowDemo from './VersionFlowDemo'
import './style.css'
import './theme.css'

const params = new URLSearchParams(location.search)
const accessDemo = params.get('demo') === '1' && params.get('flow') === 'access'
const versionDemo = params.get('demo') === '1' && params.get('flow') === 'versions'
if (versionDemo) document.title = 'PRE · POST · ECO · PoR | Sanity Demo'
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode>{versionDemo ? <VersionFlowDemo /> : accessDemo ? <AccessFlowDemo /> : <App />}</React.StrictMode>)
