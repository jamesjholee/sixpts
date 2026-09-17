import { JSDOM } from 'jsdom'
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/?week=2' })
globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.localStorage = dom.window.localStorage; Object.defineProperty(globalThis, "location", { value: dom.window.location, writable: true })
globalThis.HTMLInputElement = dom.window.HTMLInputElement
import fs from 'fs'
const board = JSON.parse(fs.readFileSync('../data/board_w2_public.json'))
const teams = JSON.parse(fs.readFileSync('../data/teams_w2.json'))
globalThis.fetch = async (url, opts) => { const u = String(url); const ok = (j) => ({ ok: true, status: 200, json: async () => j })
  if (u.includes('/api/board/')) return ok(board); if (u.includes('/api/teams/')) return ok(teams); if (u.includes('/api/weeks')) return ok({ weeks: [2], latest: 2 })
  if (u.includes('/api/evaluate')) return ok({ week: 2, priced: 0, bets: [], leans: [], passes: [] }); if (u.includes('/api/model-picks')) return ok({ priced: 0, bets: [], leans: [], passes: [] }); if (u.includes('/api/odds')) return ok({}); if (u.includes('/api/record')) return ok({ summary: [], recent: [] })
  return { ok: false, status: 404, json: async () => ({}) } }
localStorage.setItem('sixpts_21', '1')
const React = (await import('react')).default; const { createRoot } = await import('react-dom/client'); const { act } = await import('react')
const { default: App } = await import('./dist-test/App.js')
const errors = []; const origErr = console.error; console.error = (...a) => { errors.push(a.join(' ')) }
const root = createRoot(document.getElementById('root'))
await act(async () => { root.render(React.createElement(App)) }); await new Promise(r => setTimeout(r, 300)); await act(async () => {})
const html = document.body.innerHTML
console.log('rows rendered:', (html.match(/class="row/g) || []).length, '| has picks panel:', html.includes('SixPts picks'), '| has how-to:', html.includes('How to read this'))
const real = errors.filter(e => !/act\(|not wrapped|Warning:/.test(e)); console.log('runtime errors:', real.length); real.slice(0, 3).forEach(e => console.log('  ', e.slice(0, 200)))
// games view
dom.reconfigure({ url: 'http://localhost/?week=2&view=games' })
const root2 = createRoot(document.body.appendChild(document.createElement('div')))
await act(async () => { root2.render(React.createElement(App)) }); await new Promise(r => setTimeout(r, 300)); await act(async () => {})
console.log('games view team cards:', (document.body.innerHTML.match(/team-card/g) || []).length)
process.exit(real.length ? 1 : 0)
