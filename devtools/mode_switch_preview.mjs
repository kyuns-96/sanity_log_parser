#!/usr/bin/env node
// Isolated UI preview: synthetic memory data, no company settings, DB, SSO, or model.
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { readFile, stat, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, extname, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { randomUUID } from 'node:crypto'

if (!process.argv.includes('--synthetic')) throw new Error('Pass --synthetic to start the isolated UI preview')
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const portArg = process.argv.indexOf('--port')
const port = portArg < 0 ? 5188 : Number(process.argv[portArg + 1])
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Choose a port from 1024 to 65535')
const dist = resolve(root, 'oa/dashboard/dist')
await stat(resolve(dist, 'index.html'))
const { build } = createRequire(resolve(root, 'oa/dashboard/package.json'))('esbuild')
const runtime = await mkdtemp(resolve(tmpdir(), 'sanity-mode-preview-'))
const fixtureModule = resolve(runtime, 'fixture.mjs')
await build({ entryPoints: [resolve(root, 'oa/dashboard/e2e/support/sidebarModePreview.ts')], outfile: fixtureModule, bundle: true, platform: 'node', format: 'esm', logLevel: 'silent' })
const { SidebarModePreview } = await import(pathToFileURL(fixtureModule).href)
const sessions = new Map()
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.png': 'image/png' }

function shell(account) {
  const choices = [['dual', '관리자 + 블록 담당자'], ['user', '일반 사용자'], ['admin', '관리자 · 업무 권한 없음']]
  return `<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>관리자·사용자 모드 미리보기</title><style>html,body{margin:0;height:100%;font-family:system-ui,sans-serif;background:#151a22;color:#e8ecf3}body{display:flex;flex-direction:column}header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px 16px;padding:10px 16px;font-size:12px;border-bottom:1px solid #343e4f}header strong{font-weight:600}header span{color:#bac4d3}form{display:flex;align-items:center;gap:8px}select,button{font:inherit;color:inherit;border:1px solid #4c5b73;background:#202631;border-radius:5px;padding:7px 9px}iframe{border:0;flex:1;min-height:0;width:100%}</style><header><div><strong>모드 전환 · 합성 테스트 페이지</strong><br><span>사내 SSO·DB·AI 연결 없음 · 변경은 미리보기 메모리에만 저장</span></div><form action="/preview"><label for="account">테스트 계정</label><select id="account" name="account">${choices.map(([id, label]) => `<option value="${id}"${account === id ? ' selected' : ''}>${label}</option>`).join('')}</select><button>계정 적용</button></form></header><iframe src="/?page=workbench" title="실제 대시보드 UI 미리보기"></iframe></html>`
}

const server = createServer(async (request, response) => {
  response.setHeader('Cache-Control', 'no-store')
  response.setHeader('X-Content-Type-Options', 'nosniff')
  const send = (status, value) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)) }
  try {
    const url = new URL(request.url, 'http://127.0.0.1')
    const cookie = /(?:^|;\s*)sanity_mode_preview=([a-f0-9-]{36})(?:;|$)/.exec(request.headers.cookie || '')?.[1]
    let session = cookie && sessions.get(cookie)
    if (!session) {
      const key = randomUUID()
      session = new SidebarModePreview()
      sessions.set(key, session)
      response.setHeader('Set-Cookie', `sanity_mode_preview=${key}; Path=/; HttpOnly; SameSite=Strict`)
    }
    if (url.pathname === '/preview') {
      const account = ['dual', 'user', 'admin'].includes(url.searchParams.get('account')) ? url.searchParams.get('account') : 'dual'
      session.admin = account !== 'user'; session.readable = account !== 'admin'
      response.writeHead(200, { 'Content-Type': mime['.html'] }); response.end(shell(account)); return
    }
    if (url.pathname.startsWith('/api/')) {
      let body
      if (request.method !== 'GET') {
        if (request.headers['x-sanity-request'] !== '1') { send(403, { code: 'FORBIDDEN' }); return }
        const origin = request.headers.origin
        if (origin && new URL(origin).host !== request.headers.host) { send(403, { code: 'FORBIDDEN' }); return }
        const chunks = []; let size = 0
        for await (const chunk of request) { size += chunk.length; if (size > 65536) throw new Error('INVALID_REQUEST'); chunks.push(chunk) }
        body = JSON.parse(Buffer.concat(chunks).toString())
      }
      send(200, await session.request(url.pathname + url.search, request.method, body)); return
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') { send(405, { code: 'METHOD_NOT_ALLOWED' }); return }
    const file = resolve(dist, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname))
    if (!file.startsWith(dist + sep)) { send(404, { code: 'NOT_FOUND' }); return }
    const bytes = await readFile(file)
    response.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream' }); response.end(request.method === 'HEAD' ? undefined : bytes)
  } catch (error) {
    const code = error?.code || 'INVALID_REQUEST'
    send(code === 'FORBIDDEN' ? 403 : 404, { code: ['FORBIDDEN', 'NOT_FOUND', 'RESOURCE_NOT_FOUND'].includes(code) ? code : 'INVALID_REQUEST' })
  }
})
server.listen(port, '127.0.0.1', () => console.log(`Synthetic mode preview: http://127.0.0.1:${port}/preview`))
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => server.close(() => process.exit(0)))
