import { useEffect, useMemo, useState } from 'react'
import { API } from './api'
import Games from './Games'
import Record from './Record'
import Landing from './Landing'
import HowTo from './HowTo'
import { track } from './analytics'
import { supabase, sessionToken, signIn, signOut } from './auth'

export type Row = {
  gsis_id: string; player: string; position: string; team: string; opp: string; game_id: string; kickoff: string
  implied: number; total: number; spread: number; new_team: boolean; games: number
  xtd_pg_shrunk: number; p_model: number; fair_odds: number
  rz_tgt_pg?: number; rz_carry_pg?: number; i5_carry_pg?: number; ez_tgt_pg?: number; snap_pct?: number
  off_rz_trips?: number; off_rz_pass_rate?: number; off_pass_oe?: number; off_rz_pass_rate_trailing?: number; off_rz_pass_rate_leading?: number
  def_rz_td_pct?: number; def_td_rb_pg?: number; def_td_wr_pg?: number; def_td_te_pg?: number
  c_role?: number; c_offense?: number; c_defense?: number; c_environment?: number
  p_low?: number; p_high?: number; certainty?: number; certainty_label?: string; flags?: string[]
  xtd_recent?: number; rz_share_open?: number; exp_targets?: number; exp_rec?: number; rec_sd?: number
  hit_l5?: number; n_l5?: number; xtd_l5?: number; verdict?: string; matchup_note?: string | null
}
type Board = { week: number; season: number; model?: string; injury_report?: string; sources?: Record<string, string>; board: Row[]; slate: { game: string; kickoff: string; total: number }[] }
type Store = Record<string, { odds?: string; book?: string; recLine?: string; picked?: boolean; pickedAt?: string }>
type BookMap = Record<string, Record<string, number>>

export const implied = (o: string | number | undefined) => { const n = Number(o); if (!n) return null; return n < 0 ? -n / (-n + 100) : 100 / (n + 100) }
export const fmtOdds = (o: number) => (o > 0 ? '+' : '') + Math.round(o)
const heat = (p: number) => p >= .6 ? 'h5' : p >= .45 ? 'h4' : p >= .3 ? 'h3' : p >= .18 ? 'h2' : 'h1'
const pct = (x: number) => Math.round(x * 100) + '%'
const rid = (r: Row) => r.gsis_id + '|' + r.game_id
const n1 = (x?: number | null, d = 2) => x == null ? '—' : x.toFixed(d)
const pts = (x?: number | null) => x == null ? '—' : (x >= 0 ? '+' : '') + (x * 100).toFixed(1)
const erf = (x: number) => { const t = 1 / (1 + 0.3275911 * Math.abs(x)); const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x); return x >= 0 ? y : -y }
const pOver = (mean: number, sd: number, line: number) => 1 - 0.5 * (1 + erf((line + 0.5 - mean) / (sd * Math.SQRT2)))
const amer = (p: number) => p >= 0.5 ? -100 * p / (1 - p) : 100 * (1 - p) / p
const BOOK_ABBR: Record<string, string> = { draftkings: 'DK', fanduel: 'FD', betmgm: 'MGM', caesars: 'CZR', book: 'BK' }

const DEF: Record<string, [string, string]> = {
  player: ['Player', 'Position and team. NEW = changed teams since last season, so last season counts for less.'],
  team: ['Matchup', 'Spread from this team\'s side, and the opponent. Tag = how the opponent defends this position in the red zone.'],
  p_model: ['P(TD)', 'Model probability the player scores a rushing or receiving touchdown in this game.'],
  fair_odds: ['Fair', 'American odds matching P(TD) with no vig. Bet only when the book pays more than this.'],
  book: ['Book', 'Prices by sportsbook when loaded (best is highlighted; click to use it). Otherwise type one.'],
  edge: ['Edge', 'Model probability minus the book\'s implied probability, in points. 3+ is flagged.'],
  xtd_pg_shrunk: ['xTD/g', 'Expected touchdowns per game from where his touches happen, blended with last season.'],
  rz_tgt_pg: ['RZ tgt', 'Red zone targets per game.'], ez_tgt_pg: ['EZ tgt', 'End zone targets per game.'],
  rz_carry_pg: ['RZ car', 'Red zone carries per game.'], i5_carry_pg: ['GL car', 'Carries inside the 5 per game. A carry from the 1 scores about half the time.'],
  def_rz_td_pct: ['Opp RZ TD%', 'Share of red zone trips against this defense that end in a touchdown. The model\'s top matchup input.'],
  implied: ['Implied', 'Points the market expects this team to score.'],
  c_defense: ['Def Δ', 'Points the opponent adds or subtracts versus an average defense.'],
  certainty: ['Certainty', 'Evidence, week-to-week stability, and how concentrated the red zone job is. Low = committee, rookie, or role in flux.'],
  range: ['Range', 'P(TD) using only the season rate vs only the last two games. Wide = the role is moving.'],
  hit_l5: ['L5', 'Games with a TD in his last 5, against the TDs those games should have produced. 5/5 on 2.1 expected is a streak, not a role.'],
  flags: ['Notes', 'Role rising/falling, teammates out, questionable, regression risk.'],
}
const Stat = ({ k, v, label }: { k?: string; v: string; label?: string }) => <div className="stat"><span className="stat-l" title={k ? DEF[k]?.[1] : undefined}>{label ?? (k ? DEF[k]?.[0] : '')}</span><span className="stat-v">{v}</span></div>

export default function App() {
  const params = new URLSearchParams(location.search)
  const [latest, setLatest] = useState<number | null>(null)
  useEffect(() => { if (params.get('week')) return; fetch(`${API}/api/weeks`).then(r => r.json()).then(d => { if (d.latest) setLatest(d.latest) }).catch(() => {}) }, [])
  const week = Number(params.get('week') || latest || 0)
  const entered = (() => { try { return localStorage.getItem('sixpts_21') === '1' } catch { return false } })()
  const view = params.get('view') || (entered ? 'board' : 'home')
  const go = (v: string, w = week) => { const p = new URLSearchParams(location.search); p.set('view', v); p.set('week', String(w)); location.search = p.toString() }

  const enter = () => { try { localStorage.setItem('sixpts_21', '1') } catch {} ; track('entered_from_landing'); go('board') }

  // ---- auth
  const [token, setToken] = useState(() => { try { return localStorage.getItem('sixpts_token') || '' } catch { return '' } })
  const [session, setSession] = useState<string | null>(null); const [email, setEmail] = useState('')
  useEffect(() => { if (!supabase) return; sessionToken().then(setSession); const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s?.access_token ?? null)); return () => data.subscription.unsubscribe() }, [])
  const authHeaders = (): Record<string, string> => session ? { Authorization: 'Bearer ' + session } : token ? { 'X-Token': token } : {}
  const canSave = !!(session || token)

  // ---- data
  const [data, setData] = useState<Board | null>(null); const [err, setErr] = useState('')
  const [books, setBooks] = useState<BookMap>({}); const [mp, setMp] = useState<any>(null); const [tick, setTick] = useState(0); const [live, setLive] = useState<any[]>([])
  const [market, setMarket] = useState<'td' | 'rec'>('td')
  useEffect(() => {
    if (!week) return
    const url = token ? `${API}/api/private/board/${week}` : `${API}/api/board/${week}`
    fetch(url, { headers: token ? { 'X-Token': token } : {} }).then(async r => { if (!r.ok) { if (r.status === 401) throw new Error('Admin key rejected'); const j = await r.json().catch(() => ({})); const d = j.detail || {}; throw new Error(d.latest ? `Week ${week} isn't published yet. Latest is Week ${d.latest}.` : `Week ${week} isn't published yet.`) } return r.json() }).then(d => { setData(d); track('board_view', { week }) }).catch(e => setErr(e.message))
  }, [week, token])
  useEffect(() => { if (!week) return; const pull = () => fetch(`${API}/api/live/${week}`).then(r => r.ok ? r.json() : null).then(d => d && setLive(d.touchdowns || [])).catch(() => {}); pull(); const t = setInterval(pull, 30000); return () => clearInterval(t) }, [week])
  useEffect(() => { if (!week) return; fetch(`${API}/api/odds/${week}?market=${market === 'rec' ? 'receptions' : 'anytime_td'}`, { headers: authHeaders() }).then(r => r.ok ? r.json() : {}).then(setBooks).catch(() => {}) }, [week, market, canSave, tick, session, token])

  // ---- ui state
  const key = `sixpts_w${week}_${market}`
  const [store, setStore] = useState<Store>(() => { try { return JSON.parse(localStorage.getItem(key) || '{}') } catch { return {} } })
  useEffect(() => { try { setStore(JSON.parse(localStorage.getItem(key) || '{}')) } catch { setStore({}) } }, [key])
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(store)) } catch {} }, [store, key])
  useEffect(() => { if (market !== 'td' || !week) return
    const typed: Record<string, number> = {}; for (const [k, v] of Object.entries(store)) { const n = Number(v?.odds); if (n) typed[k] = n }
    fetch(`${API}/api/model-picks/${week}`, { headers: authHeaders() }).then(r => r.ok ? r.json() : null).then(async (server) => {
      if (server && server.priced > 0 && Object.keys(typed).length === 0) return setMp(server)
      // blend: anything you typed overrides the feed
      const mine = await fetch(`${API}/api/evaluate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ week, prices: typed }) }).then(r => r.ok ? r.json() : null).catch(() => null)
      if (!server) return setMp(mine)
      if (!mine || mine.priced === 0) return setMp(server)
      const byId = (c: any) => c.gsis_id + c.game_id
      const over = new Set([...mine.bets, ...mine.leans, ...mine.passes].map(byId))
      const merge = (k: 'bets' | 'leans' | 'passes') => [...mine[k], ...server[k].filter((c: any) => !over.has(byId(c)))]
      setMp({ ...server, priced: server.priced + mine.priced, bets: merge('bets'), leans: merge('leans'), passes: merge('passes') })
    }).catch(() => {})
  }, [week, market, tick, canSave, store, session, token])
  const [pos, setPos] = useState(''); const [game, setGame] = useState(''); const [minp, setMinp] = useState(15)
  const [onlyEdge, setOnlyEdge] = useState(false); const [onlyPicks, setOnlyPicks] = useState(false); const [q, setQ] = useState('')
  const [sort, setSort] = useState<{ k: string; dir: 1 | -1 }>({ k: 'edge', dir: -1 })
  const [open, setOpen] = useState<string | null>(null)
  const [density, setDensity] = useState<'simple' | 'full'>(() => { try { return (localStorage.getItem('sixpts_density') as any) || 'simple' } catch { return 'simple' } })
  const setDens = (d: 'simple' | 'full') => { setDensity(d); try { localStorage.setItem('sixpts_density', d) } catch {} }
  const [more, setMore] = useState(false); const [tier, setTier] = useState<'bets' | 'leans' | 'passes'>('bets'); const [pickOpen, setPickOpen] = useState<string | null>(null); const [picksOpen, setPicksOpen] = useState(() => { try { return localStorage.getItem('sixpts_picks_open') !== '0' } catch { return true } })
  const togglePicks = () => setPicksOpen(o => { try { localStorage.setItem('sixpts_picks_open', o ? '0' : '1') } catch {} ; return !o })
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth < 720)
  useEffect(() => { const f = () => setIsMobile(window.innerWidth < 720); window.addEventListener('resize', f); return () => window.removeEventListener('resize', f) }, [])
  const COLS: { k: string; label: string; locked?: boolean }[] = [
    { k: 'player', label: 'Player', locked: true }, { k: 'team', label: 'Matchup' }, { k: 'p_model', label: 'P(TD)', locked: true }, { k: 'fair_odds', label: 'Fair odds' },
    { k: 'book', label: 'Book' }, { k: 'edge', label: 'Edge' }, { k: 'xtd_pg_shrunk', label: 'xTD / game' }, { k: 'rz_tgt_pg', label: 'RZ targets' }, { k: 'ez_tgt_pg', label: 'EZ targets' },
    { k: 'rz_carry_pg', label: 'RZ carries' }, { k: 'i5_carry_pg', label: 'Goal-line carries' }, { k: 'def_rz_td_pct', label: 'Opp RZ TD%' }, { k: 'implied', label: 'Implied' },
    { k: 'c_defense', label: 'Def Δ' }, { k: 'certainty', label: 'Certainty' }, { k: 'range', label: 'Range' }, { k: 'hit_l5', label: 'L5' }, { k: 'flags', label: 'Notes' }, { k: 'pick', label: 'Pick', locked: true }]
  const [hidden, setHidden] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem('sixpts_hidden_cols') || '[]')) } catch { return new Set() } })
  const [colsOpen, setColsOpen] = useState(false)
  const toggleCol = (k: string) => setHidden(h => { const n = new Set(h); n.has(k) ? n.delete(k) : n.add(k); try { localStorage.setItem('sixpts_hidden_cols', JSON.stringify([...n])) } catch {} ; return n })
  const SIMPLE = new Set(['player', 'team', 'p_model', 'book', 'edge', 'certainty', 'flags', 'pick'])
  const show = (k: string) => density === 'simple' ? SIMPLE.has(k) : !hidden.has(k)
  const visibleCount = COLS.filter(c => show(c.k)).length
  const activeFilters = (minp !== 15 ? 1 : 0) + (onlyEdge ? 1 : 0) + (onlyPicks ? 1 : 0) + (q ? 1 : 0)

  // ---- odds helpers
  const realBooks = (r: Row) => { const b = books[rid(r)]; if (!b) return null; const e = Object.entries(b).filter(([k]) => k !== 'manual'); return e.length ? Object.fromEntries(e) : null }
  const bestBook = (r: Row) => { const b = realBooks(r) || books[rid(r)]; if (!b) return null; let best: [string, number] | null = null; for (const [k, v] of Object.entries(b)) if (!best || v > best[1]) best = [k, v]; return best }
  const priceFor = (r: Row, s: Store[string]) => { if (s?.odds) return { price: Number(s.odds), book: s.book || 'manual' }; const b = bestBook(r); return b ? { price: b[1], book: b[0] } : null }

  const rows = useMemo(() => {
    if (!data) return []
    return data.board.map(r => { const s = store[rid(r)] || {}; const pr = priceFor(r, s); const ip = pr ? implied(pr.price) : null
        if (market === 'rec') { const line = Number(s.recLine); const pm = (line && r.exp_rec != null) ? pOver(r.exp_rec, r.rec_sd || 2, line) : null
          return { ...r, p_model: pm ?? r.p_model, fair_odds: pm ? amer(pm) : r.fair_odds, price: pr, recLine: s.recLine || '', edge: (pm != null && ip != null) ? pm - ip : null, picked: !!s.picked } }
        return { ...r, price: pr, recLine: s.recLine || '', edge: ip == null ? null : r.p_model - ip, picked: !!s.picked } })
      .filter(r => (!pos || r.position === pos) && (!game || r.game_id === game) && r.p_model * 100 >= minp && (!onlyEdge || (r.edge != null && r.edge >= .03)) && (!onlyPicks || r.picked) && (!q || `${r.player} ${r.team} ${r.opp}`.toLowerCase().includes(q.toLowerCase())))
      .sort((a: any, b: any) => { if (sort.k === 'edge') { const ax = a.edge == null, bx = b.edge == null; if (ax && bx) return b.p_model - a.p_model; if (ax) return 1; if (bx) return -1; return (b.edge - a.edge) * (sort.dir === -1 ? 1 : -1) } let x = a[sort.k], y = b[sort.k]; if (x == null) x = -1e9; if (y == null) y = -1e9; return typeof x === 'string' ? x.localeCompare(y) * sort.dir : (x - y) * sort.dir })
  }, [data, store, books, pos, game, minp, onlyEdge, onlyPicks, q, sort, market])

  const logPick = (r: any, s: Store[string]) => {
    const now = !s?.picked; const pr = priceFor(r, s || {})
    setStore(st => ({ ...st, [rid(r)]: { ...st[rid(r)], picked: now, pickedAt: new Date().toISOString() } }))
    if (now && !pr) { alert('No price yet — type the book price first.'); return }
    if (now) track('pick_logged', { week, market })
    if (now && canSave && pr) fetch(`${API}/api/picks`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ gsis_id: r.gsis_id, game_id: r.game_id, market: market === 'rec' ? 'receptions' : 'anytime_td', player: r.player, team: r.team, opp: r.opp, line: market === 'rec' ? Number(s?.recLine) || null : null, book: pr.book, price_taken: pr.price, p_model: r.p_model }) }).catch(() => {})
  }
  const setManual = (r: Row, v: string) => setStore(st => ({ ...st, [rid(r)]: { ...st[rid(r)], odds: v, book: 'manual' } }))
  const saveManual = (r: Row) => { const n = Number(store[rid(r)]?.odds); if (n) track('price_entered', { week }); if (canSave && n) fetch(`${API}/api/odds`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ gsis_id: r.gsis_id, game_id: r.game_id, market: market === 'rec' ? 'receptions' : 'anytime_td', book: 'manual', price: n, line: market === 'rec' ? Number(store[rid(r)]?.recLine) || null : null }) }).then(() => setTick(t => t + 1)).catch(() => {}) }
  const useBook = (r: Row, book: string, price: number) => setStore(st => ({ ...st, [rid(r)]: { ...st[rid(r)], odds: String(price), book } }))
  const matchTag = (r: Row) => { const d = r.c_defense ?? 0; return d >= .02 ? ['fav', 'Favorable'] : d <= -.02 ? ['unfav', 'Tough'] : ['', 'Neutral'] }

  const picks = data ? data.board.filter(r => store[rid(r)]?.picked) : []
  const copyPicks = () => { const head = 'week,game,team,opp,player,position,p_model,fair_odds,book,book_odds,implied,edge,picked_at\n'
    const body = picks.map(r => { const s = store[rid(r)]; const pr = priceFor(r, s); const ip = pr ? implied(pr.price) : null; return [week, r.game_id, r.team, r.opp, r.player, r.position, r.p_model.toFixed(3), fmtOdds(r.fair_odds), pr?.book || '', pr?.price || '', ip == null ? '' : ip.toFixed(3), ip == null ? '' : (r.p_model - ip).toFixed(3), s.pickedAt || ''].join(',') }).join('\n')
    navigator.clipboard?.writeText(head + body).catch(() => prompt('Copy:', head + body)) }

  const th = (k: string, left = false) => <th className={(left ? 'l ' : '') + (sort.k === k ? 'sorted' : '')} title={DEF[k]?.[1]} onClick={() => setSort(s => ({ k, dir: s.k === k ? (s.dir * -1 as 1 | -1) : (left ? 1 : -1) }))}>{DEF[k]?.[0] ?? k}</th>

  // ---- shell
  const shell = (body: React.ReactNode) => (<>
    <div className="top">
      <div className="wordmark"><i />SixPts</div>
      <nav className="nav">{[['board', 'Board'], ['games', 'Games'], ['record', 'Record']].map(([v, l]) => <a key={v} className={view === v ? 'on' : ''} onClick={() => go(v)}>{l}</a>)}</nav>
      <div className="right">
        <div className="week"><button onClick={() => go(view, Math.max(1, week - 1))}>‹</button><span>Week {week}</span><button onClick={() => go(view, week + 1)}>›</button></div>
        {supabase ? (session ? <button className="btn" onClick={() => signOut()}>Sign out</button> : <><input className="keyfield" type="email" placeholder="email" value={email} onChange={e => setEmail(e.target.value)} /><button className="btn" onClick={() => { if (email) { signIn(email); alert('Check your email for the sign-in link.') } }}>Sign in</button></>) : null}
        <input className="keyfield" type="password" placeholder="admin key" value={token} onChange={e => { setToken(e.target.value); try { localStorage.setItem('sixpts_token', e.target.value) } catch {} }} />
      </div>
    </div>
    <div className="wrap">{body}</div></>)

  if (view === 'home') return <Landing onEnter={enter} />
  if (err) return shell(<p className="empty">{err}</p>)
  if (!data) return shell(<p className="empty">{week ? `Loading week ${week}…` : 'Loading…'}</p>)
  if (view === 'games') return shell(<Games week={week} season={data.season} />)
  if (view === 'record') return shell(<Record />)

  const nBook = Object.keys(books).length
  return shell(<>
    {live.length > 0 && <div className="panel"><div className="head"><h2>Touchdowns today</h2><span className="muted">{live.length} · what we said before the game</span></div>
      <div className="body live">{[...live].reverse().slice(0, 12).map((t: any, i: number) => { const r = data.board.find(x => x.player.toLowerCase().replace(/[.']/g, '') === String(t.scorer).toLowerCase().replace(/[.']/g, ''))
        return <div key={i} className="td-row"><b>{t.scorer}</b> <span className="muted">{t.team} · {t.yards ? `${t.yards} yd ${t.how}` : t.how} · Q{t.quarter} {t.clock}</span>
          {r ? <span className={'match ' + (r.p_model >= .3 ? 'fav' : '')}>we said {Math.round(r.p_model * 100)}%</span> : <span className="match">not on our board</span>}</div> })}</div></div>}
    <HowTo />
    {data.injury_report && data.injury_report.startsWith('not') && <div className="banner">Injury report {data.injury_report}. Availability notes appear once it's loaded — verify a player is active before kickoff.</div>}

    <div className="games">
      <div className={'game' + (game ? '' : ' on')} onClick={() => setGame('')}><b>All games</b><small>{data.slate.length} this week</small></div>
      {data.slate.map(g => { const [, , a, h] = g.game.split('_'); return <div key={g.game} className={'game' + (game === g.game ? ' on' : '')} onClick={() => setGame(game === g.game ? '' : g.game)}><b>{a} @ {h}</b><small>{g.kickoff.slice(5)} · O/U {g.total}</small></div> })}
    </div>

    {market === 'td' && mp && <div className="panel">
      <div className="head" onClick={togglePicks}><h2>SixPts picks</h2><span className="muted">{mp.priced ? `${mp.bets.length} bet · ${mp.leans.length} lean · ${mp.passes.length} pass` : 'no prices loaded'}</span><span className="caret">{picksOpen ? '▾' : '▸'}</span></div>
      {picksOpen && <div className="body">
        {mp.priced === 0 ? <p className="muted">Prices aren't posted for this week yet. Type the price your book is offering on any row and the model will reason over it.</p> : <>
          <div className="bar" style={{ margin: '10px 0 4px' }}><div className="seg">{(['bets', 'leans', 'passes'] as const).map(t => <button key={t} className={tier === t ? 'on' : ''} onClick={() => setTier(t)}>{t === 'bets' ? 'Bet' : t === 'leans' ? 'Lean' : 'Pass'} · {mp[t].filter((c: any) => !game || c.game_id === game).length}</button>)}</div>
            <span className="muted">{tier === 'bets' ? '5+ pts edge, all-but-one signals green, high certainty' : tier === 'leans' ? 'edge, but mixed signals or lower certainty' : 'no edge at the price, a red flag, or too few green signals — tap a row for why'}</span></div>
          {mp[tier].filter((c: any) => !game || c.game_id === game).length === 0 && <p className="muted">None{game ? ' in this game' : ''} at current prices.</p>}
          {mp[tier].filter((c: any) => !game || c.game_id === game).map((c: any) => { const k = c.gsis_id + c.game_id; const o = pickOpen === k || tier !== 'passes'; return <div key={k} className="pick-row" onClick={() => setPickOpen(pickOpen === k ? null : k)} style={{ cursor: tier === 'passes' ? 'pointer' : 'default' }}>
            <span className={'tier ' + c.tier.toLowerCase()}>{c.tier}</span>
            <div><div className="who">{c.player} <span className="muted">{c.position} · {c.team} vs {c.opp}</span></div><div className="why">{c.headline}</div>
              {o && <><div className="sig">{Object.entries(c.signals).map(([k2, v]: any) => <span key={k2} className={v ? 'ok' : 'no'}>{v ? '✓' : '✗'} {k2.replace('_', ' ')}</span>)}</div>
                {c.reasons_for.length > 0 && <div className="muted" style={{ marginTop: 4 }}>For: {c.reasons_for.join(' · ')}</div>}
                {c.reasons_against.length > 0 && <div className="muted" style={{ marginTop: 2 }}>Against: {c.reasons_against.join(' · ')}</div>}</>}</div>
            <div className="price"><b>{fmtOdds(c.price)}</b><div className="muted">{BOOK_ABBR[c.book] || c.book}{c.stake ? ` · stake ${(c.stake * 100).toFixed(1)}%` : ''}</div></div>
          </div> })}
        </>}
      </div>}
    </div>}

    <div className="bar">
      <div className="seg"><button className={market === 'td' ? 'on' : ''} onClick={() => setMarket('td')}>Anytime TD</button><button className={market === 'rec' ? 'on' : ''} onClick={() => setMarket('rec')}>Receptions</button></div>
      <div className="seg">{['', 'RB', 'WR', 'TE', 'QB'].map(p => <button key={p} className={pos === p ? 'on' : ''} onClick={() => setPos(p)}>{p || 'All'}</button>)}</div>
      <div className="seg"><button className={density === 'simple' ? 'on' : ''} onClick={() => setDens('simple')}>Simple</button><button className={density === 'full' ? 'on' : ''} onClick={() => setDens('full')}>Full</button></div>
      <button className={'btn' + (activeFilters ? ' on' : '')} onClick={() => setMore(o => !o)}>Filters{activeFilters ? ` · ${activeFilters}` : ''}</button>
      <span className="muted" style={{ marginLeft: 'auto' }}>{rows.length} players{nBook ? ` · ${nBook} priced` : ''}</span>
    </div>
    {more && <div className="bar sub">
      <label>Min P(TD) <input type="number" value={minp} min={0} max={100} step={5} onChange={e => setMinp(Number(e.target.value) || 0)} />%</label>
      <label><input type="checkbox" checked={onlyEdge} onChange={e => setOnlyEdge(e.target.checked)} /> edge ≥ 3 only</label>
      <label><input type="checkbox" checked={onlyPicks} onChange={e => setOnlyPicks(e.target.checked)} /> my picks only</label>
      <input type="search" placeholder="Search player or team" value={q} onChange={e => setQ(e.target.value)} />
      {density === 'full' && <div style={{ position: 'relative' }}><button className="btn" onClick={() => setColsOpen(o => !o)}>Columns{hidden.size ? ` · ${hidden.size} hidden` : ''}</button>
        {colsOpen && <div className="colmenu">{COLS.map(c => <label key={c.k} className={c.locked ? 'muted' : ''}><input type="checkbox" checked={!hidden.has(c.k)} disabled={c.locked} onChange={() => toggleCol(c.k)} /> {c.label}</label>)}<button className="btn" onClick={() => { setHidden(new Set()); try { localStorage.removeItem('sixpts_hidden_cols') } catch {} }}>Show all</button></div>}</div>}
    </div>}

    {isMobile ? <div className="cards">
      {rows.length === 0 && <div className="empty">No players match.</div>}
      {rows.map((r: any) => { const id = rid(r); const s = store[id] || {}; const [mc, ml] = matchTag(r); const bb = realBooks(r); return (
        <div key={id} className={'card' + (open === id ? ' open' : '')}>
          <div className="card-head" onClick={() => setOpen(open === id ? null : id)}>
            <div><div className="player">{r.player}{r.new_team && <span className="tag">new</span>}</div><div className="muted">{r.position} · {r.team} {r.spread > 0 ? '+' : ''}{r.spread} vs {r.opp} · <span className={'match ' + mc}>{ml}</span></div></div>
            <div className="card-num"><span className={'p ' + heat(r.p_model)}>{pct(r.p_model)}</span><div className="muted">fair {fmtOdds(r.fair_odds)}</div></div>
          </div>
          <div className="card-row">
            {bb ? <span className="books">{Object.entries(bb).map(([b, p]) => <span key={b} className={'book' + (bestBook(r)?.[0] === b ? ' best' : '') + (s.book === b ? ' on' : '')} onClick={() => useBook(r, b, p as number)}><small>{BOOK_ABBR[b] || b}</small><b>{fmtOdds(p as number)}</b></span>)}</span>
              : <input className="odds" type="number" step={5} placeholder="+150" value={s.odds || ''} onChange={e => setManual(r, e.target.value)} onBlur={() => saveManual(r)} onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />}
            <span className="num">{r.edge == null ? <span className="muted">no edge yet</span> : <b className={'edge ' + (r.edge >= 0 ? 'pos' : 'neg')}>{r.edge >= .03 ? <span className="flag">{pts(r.edge)}</span> : pts(r.edge)}</b>}</span>
            <button className={'pick ' + (s.picked ? 'on' : '')} onClick={() => logPick(r, s)}>{s.picked ? 'Picked' : 'Pick'}</button>
          </div>
          {(r.flags || []).length > 0 && <div className="card-flags">{(r.flags || []).slice(0, open === id ? 99 : 2).map((f: string) => <span key={f} className={'chip' + (/QUESTIONABLE|falling|regression risk/.test(f) ? ' warn' : '')}>{f}</span>)}</div>}
          {open === id && <div className="card-detail">{detail(r)}</div>}
        </div>) })}
    </div> : <div className={'tablewrap' + (density === 'full' ? ' pin' : '')}><table className={density}>
      <thead><tr>{th('player', true)}{show('team') && th('team', true)}{th('p_model')}{show('fair_odds') && th('fair_odds')}{show('book') && <th className="l" title={DEF.book[1]}>{DEF.book[0]}</th>}{show('edge') && th('edge')}
        {market === 'rec' && <th title="Projected targets / receptions">Proj</th>}{market === 'rec' && <th>Line</th>}
        {show('xtd_pg_shrunk') && th('xtd_pg_shrunk')}{show('rz_tgt_pg') && th('rz_tgt_pg')}{show('ez_tgt_pg') && th('ez_tgt_pg')}{show('rz_carry_pg') && th('rz_carry_pg')}{show('i5_carry_pg') && th('i5_carry_pg')}{show('def_rz_td_pct') && th('def_rz_td_pct')}{show('implied') && th('implied')}{show('c_defense') && th('c_defense')}
        {show('certainty') && th('certainty')}{show('range') && <th title={DEF.range[1]}>Range</th>}{show('hit_l5') && <th title={DEF.hit_l5[1]}>L5</th>}{show('flags') && <th className="l" title={DEF.flags[1]}>Notes</th>}<th /></tr></thead>
      <tbody>
        {rows.length === 0 && <tr><td colSpan={visibleCount + 2} className="empty">No players match.</td></tr>}
        {rows.map((r: any) => { const id = rid(r); const s = store[id] || {}; const [mc, ml] = matchTag(r); const bb = realBooks(r); const best = bestBook(r); return (<>
          <tr key={id} className={'row' + (open === id ? ' open' : '')} onClick={() => setOpen(open === id ? null : id)}>
            <td className="l"><span className="player">{r.player}<small>{r.position} {r.team}</small>{r.new_team && <span className="tag">new</span>}</span></td>
            {show('team') && <td className="l">{r.spread > 0 ? '+' : ''}{r.spread} vs {r.opp} <span className={'match ' + mc}>{ml}</span></td>}
            <td className="num"><span className={'p ' + heat(r.p_model)}>{market === 'rec' && !r.recLine ? '—' : pct(r.p_model)}</span></td>
            {show('fair_odds') && <td className="num">{market === 'rec' && !r.recLine ? '—' : fmtOdds(r.fair_odds)}</td>}
            {show('book') && <td className="l" onClick={e => e.stopPropagation()}>{bb ? <span className="books">{Object.entries(bb).map(([b, p]) => <span key={b} className={'book' + (best?.[0] === b ? ' best' : '') + (s.book === b ? ' on' : '')} title={s.book === b ? 'Using this price' : 'Click to use this price'} onClick={() => useBook(r, b, p as number)}><small>{BOOK_ABBR[b] || b}</small><b>{fmtOdds(p as number)}</b></span>)}</span>
              : <input className="odds" type="number" step={5} placeholder="+150" value={s.odds || ''} onChange={e => setManual(r, e.target.value)} onBlur={() => saveManual(r)} onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />}</td>}
            {show('edge') && <td className="num">{r.edge == null ? <span className="muted">—</span> : <span className={'edge ' + (r.edge >= 0 ? 'pos' : 'neg')}>{r.edge >= .03 ? <span className="flag">{pts(r.edge)}</span> : pts(r.edge)}</span>}</td>}
            {market === 'rec' && <td className="num">{n1(r.exp_targets, 1)} / {n1(r.exp_rec, 1)}</td>}
            {market === 'rec' && <td onClick={e => e.stopPropagation()}><input className="odds" type="number" step={0.5} placeholder="4.5" value={r.recLine} onChange={e => setStore(st => ({ ...st, [id]: { ...st[id], recLine: e.target.value } }))} /></td>}
            {show('xtd_pg_shrunk') && <td className="num">{n1(r.xtd_pg_shrunk)}</td>}{show('rz_tgt_pg') && <td className="num">{n1(r.rz_tgt_pg, 1)}</td>}{show('ez_tgt_pg') && <td className="num">{n1(r.ez_tgt_pg, 1)}</td>}{show('rz_carry_pg') && <td className="num">{n1(r.rz_carry_pg, 1)}</td>}{show('i5_carry_pg') && <td className="num">{n1(r.i5_carry_pg, 1)}</td>}
            {show('def_rz_td_pct') && <td className="num">{r.def_rz_td_pct == null ? '—' : Math.round(r.def_rz_td_pct * 100) + '%'}</td>}{show('implied') && <td className="num">{r.implied.toFixed(1)}</td>}{show('c_defense') && <td className="num">{pts(r.c_defense)}</td>}
            {show('certainty') && <td><span className={'cert ' + (r.certainty_label || '')}>{r.certainty_label ?? '—'}</span></td>}
            {show('range') && <td className="muted num">{r.p_low == null ? '—' : (Math.abs((r.p_high ?? 0) - (r.p_low ?? 0)) < 0.005 ? pct(r.p_model) : pct(r.p_low ?? 0) + '–' + pct(r.p_high ?? 0))}</td>}
            {show('hit_l5') && <td className="num">{r.n_l5 ? <span className={(r.hit_l5 ?? 0) - (r.xtd_l5 ?? 0) >= 1.5 ? 'neg' : ''} title={(r.hit_l5 ?? 0) - (r.xtd_l5 ?? 0) >= 1.5 ? 'Scoring above expectation — streak, not role' : ''}>{r.hit_l5}/{r.n_l5} <span className="muted">exp {n1(r.xtd_l5, 1)}</span></span> : '—'}</td>}
            {show('flags') && <td className="l" style={{ whiteSpace: 'normal', maxWidth: 240 }}>{(r.flags || []).slice(0, density === 'simple' && open !== id ? 1 : 99).map((f: string) => <span key={f} className={'chip' + (/QUESTIONABLE|falling|regression risk/.test(f) ? ' warn' : '')}>{f}</span>)}{density === 'simple' && open !== id && (r.flags || []).length > 1 && <span className="muted">+{(r.flags || []).length - 1}</span>}</td>}
            <td onClick={e => e.stopPropagation()}><button className={'pick ' + (s.picked ? 'on' : '')} onClick={() => logPick(r, s)}>{s.picked ? 'Picked' : 'Pick'}</button></td>
          </tr>
          {open === id && <tr key={id + 'd'} className="detail"><td colSpan={visibleCount + 2}>{detail(r)}</td></tr>}
        </>) })}
      </tbody></table></div>}

    {data.sources && <div className="sources"><b>Sources</b>{Object.entries(data.sources).map(([k, v]) => <span key={k}>{v}</span>)}<span>Verify a player is active before kickoff.</span></div>}
    <div className="legal">SixPts is a research tool, not a sportsbook and not advice. 21+ · Gambling problem? Call 1-800-GAMBLER. · <a href="mailto:hello@sixpts.com">Contact</a></div>

    <div className="tray">
      <h2>Picks <span className="muted">{picks.length ? picks.length + ' logged' : 'none'}</span></h2>
      <div className="list">{picks.length === 0 ? <span className="muted">Pick from any row. Saved to the server when signed in; otherwise this browser only.</span> : picks.map(r => { const s = store[rid(r)]; const pr = priceFor(r, s); const ip = pr ? implied(pr.price) : null; return <span key={rid(r)} className="item">{r.player} · {pct(r.p_model)}{pr ? ` @ ${fmtOdds(pr.price)}` : ''}{ip != null ? ` · ${pts(r.p_model - ip)}` : ''}</span> })}</div>
      <button className="btn" onClick={copyPicks}>Copy CSV</button>
      <button className="btn" onClick={() => { if (confirm('Clear all picks and prices for this week?')) setStore({}) }}>Clear</button>
    </div>
  </>)

  function detail(r: any) {
    return (<>
      {r.verdict && <div className="verdict">{r.verdict}</div>}
      <div className="grid">
        <div className="block"><h3>Role <b className={(r.c_role ?? 0) >= 0 ? 'pos' : 'neg'}>{pts(r.c_role)}</b></h3>
          <Stat k="xtd_pg_shrunk" v={n1(r.xtd_pg_shrunk)} /><Stat label="Expected TDs, last 2 games" v={n1(r.xtd_recent)} /><Stat k="rz_tgt_pg" v={n1(r.rz_tgt_pg, 1)} /><Stat k="ez_tgt_pg" v={n1(r.ez_tgt_pg, 1)} /><Stat k="rz_carry_pg" v={n1(r.rz_carry_pg, 1)} /><Stat k="i5_carry_pg" v={n1(r.i5_carry_pg, 1)} />
          <Stat label="Snap share" v={r.snap_pct == null ? '—' : Math.round(r.snap_pct * 100) + '%'} /><Stat label="TD hit rate, last 5" v={r.n_l5 ? `${r.hit_l5} of ${r.n_l5} (exp ${n1(r.xtd_l5, 1)})` : '—'} /><Stat label="Certainty" v={`${r.certainty ?? '—'} · ${r.certainty_label ?? '—'}`} />
          {(r.rz_share_open ?? 0) > 0 && <Stat label="RZ work opened by injuries" v={Math.round(r.rz_share_open * 100) + '%'} />}</div>
        <div className="block"><h3>Offense · {r.team} <b className={(r.c_offense ?? 0) >= 0 ? 'pos' : 'neg'}>{pts(r.c_offense)}</b></h3>
          <Stat label="Red zone trips / game" v={n1(r.off_rz_trips, 1)} /><Stat label="Red zone pass rate" v={r.off_rz_pass_rate == null ? '—' : Math.round(r.off_rz_pass_rate * 100) + '%'} /><Stat label="Pass rate over expected" v={r.off_pass_oe == null ? '—' : (r.off_pass_oe >= 0 ? '+' : '') + r.off_pass_oe.toFixed(1)} />
          <Stat label="RZ pass rate trailing / leading" v={`${r.off_rz_pass_rate_trailing == null ? '—' : Math.round(r.off_rz_pass_rate_trailing * 100) + '%'} / ${r.off_rz_pass_rate_leading == null ? '—' : Math.round(r.off_rz_pass_rate_leading * 100) + '%'}`} /></div>
        <div className="block"><h3>Defense · {r.opp} <b className={(r.c_defense ?? 0) >= 0 ? 'pos' : 'neg'}>{pts(r.c_defense)}</b></h3>
          <Stat k="def_rz_td_pct" v={r.def_rz_td_pct == null ? '—' : Math.round(r.def_rz_td_pct * 100) + '%'} /><Stat label="TDs / game to RB" v={n1(r.def_td_rb_pg)} /><Stat label="to WR" v={n1(r.def_td_wr_pg)} /><Stat label="to TE" v={n1(r.def_td_te_pg)} />{r.matchup_note && <Stat label="Coverage" v={r.matchup_note} />}</div>
        <div className="block"><h3>Game <b className={(r.c_environment ?? 0) >= 0 ? 'pos' : 'neg'}>{pts(r.c_environment)}</b></h3>
          <Stat k="implied" v={r.implied.toFixed(1) + ' pts'} /><Stat label="Over / under" v={String(r.total)} /><Stat label="Spread" v={r.spread > 0 ? 'favored by ' + r.spread : 'underdog by ' + (-r.spread)} /></div>
      </div></>)
  }
}
