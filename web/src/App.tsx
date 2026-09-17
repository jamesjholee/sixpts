import { API } from './api'
import { useEffect, useMemo, useState } from 'react'
import Teams from './Teams'
import Record from './Record'
import { supabase, sessionToken, signIn, signOut } from './auth'

type Row = {
  gsis_id: string; player: string; position: string; team: string; opp: string; game_id: string; kickoff: string
  implied: number; total: number; spread: number; new_team: boolean; games: number
  // formula-model fields (optional)
  targets?: number; rz_tgt?: number; ez_tgt?: number; carries?: number; rz_carry?: number; i5_carry?: number; td?: number; xtd?: number
  xtd_pg_2026?: number; xtd_pg_prior?: number; env_mult?: number; matchup_mult?: number; matchup_note?: string | null; xtd_proj?: number
  opp_score?: number; gravity_score?: number; matchup_score?: number; env_score?: number; score?: number
  // fitted-model fields (optional)
  xtd_pg_shrunk: number; p_model: number; fair_odds: number
  rz_tgt_pg?: number; rz_carry_pg?: number; i5_carry_pg?: number; ez_tgt_pg?: number; snap_pct?: number
  off_rz_trips?: number; off_rz_pass_rate?: number; off_pass_oe?: number
  def_rz_td_pct?: number; def_td_rb_pg?: number; def_td_wr_pg?: number; def_td_te_pg?: number
  c_role?: number; c_offense?: number; c_defense?: number; c_environment?: number
  p_low?: number; p_high?: number; certainty?: number; certainty_label?: string; flags?: string[]
  xtd_recent?: number; i5_carry_recent?: number; rz_share_open?: number; off_rz_pass_rate_trailing?: number; off_rz_pass_rate_leading?: number
  exp_targets?: number; exp_rec?: number; rec_sd?: number
  hit_l5?: number; n_l5?: number; xtd_l5?: number; verdict?: string
}
type Callouts = Record<string, string[]>
const erf = (x: number) => { const t = 1 / (1 + 0.3275911 * Math.abs(x)); const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x); return x >= 0 ? y : -y }
const pOver = (mean: number, sd: number, line: number) => 1 - 0.5 * (1 + erf((line + 0.5 - mean) / (sd * Math.SQRT2)))  // continuity-corrected normal
const amer = (p: number) => p >= 0.5 ? -100 * p / (1 - p) : 100 * (1 - p) / p
type Board = { week: number; season: number; model?: string; injury_report?: string; sources?: Record<string, string>; board: Row[]; slate: { game: string; kickoff: string; total: number }[] }
const n1 = (x?: number | null, d = 2) => x == null ? '—' : x.toFixed(d)
const pts = (x?: number | null) => x == null ? '—' : (x >= 0 ? '+' : '') + (x * 100).toFixed(1)
type Store = Record<string, { odds?: string; recLine?: string; picked?: boolean; pickedAt?: string }>

const implied = (o: string | undefined) => { const n = Number(o); if (!n) return null; return n < 0 ? -n / (-n + 100) : 100 / (n + 100) }
const fmtOdds = (o: number) => (o > 0 ? '+' : '') + Math.round(o)
const heat = (p: number) => p >= .6 ? 'h5' : p >= .45 ? 'h4' : p >= .3 ? 'h3' : p >= .18 ? 'h2' : 'h1'
const pct = (x: number) => Math.round(x * 100) + '%'
const rid = (r: Row) => r.gsis_id + '|' + r.game_id
const DEF: Record<string, [string, string]> = {
  player: ['Player', 'Position and team. "new team" = changed teams since last season, so last season counts for less.'],
  team: ['Matchup', 'This player\'s team, the point spread from their side, and the opponent.'],
  p_model: ['P(TD)', 'Model probability the player scores a rushing or receiving touchdown in this game.'],
  fair_odds: ['Fair odds', 'The American odds that match P(TD) with no vig. Bet when the book pays more than this.'],
  book: ['Book price', 'Type the odds your sportsbook is offering (e.g. +150 or -120).'],
  edge: ['Edge', 'Model probability minus the probability implied by the book price, in percentage points. 3+ is flagged.'],
  xtd_pg_shrunk: ['Expected TDs / game', 'How many touchdowns his touches "should" produce per game, given where on the field they happen. Blended with last season.'],
  rz_tgt_pg: ['Red zone targets / game', 'Targets inside the opponent\'s 20-yard line, per game.'],
  ez_tgt_pg: ['End zone targets / game', 'Targets where the throw was to the end zone, per game.'],
  rz_carry_pg: ['Red zone carries / game', 'Carries inside the opponent\'s 20-yard line, per game.'],
  i5_carry_pg: ['Goal-line carries / game', 'Carries inside the 5-yard line, per game. A carry from the 1 scores about half the time.'],
  def_rz_td_pct: ['Opponent red zone TD %', 'Share of red zone trips against this defense that ended in a touchdown (not a field goal). The model\'s most important matchup input.'],
  implied: ['Implied team total', 'Points the betting market expects this team to score (from the spread and over/under).'],
  c_defense: ['Defense effect', 'How many percentage points the opponent adds or subtracts versus an average defense.'],
  score: ['Score', 'Composite display score.'],
  certainty: ['Certainty', 'How much to trust this number: games of evidence (40%), week-to-week stability of his role (35%), how concentrated the red zone job is (25%). Low = a committee, a rookie, or a role in flux.'],
  range: ['Range', 'Where P(TD) lands if you believe only his season rate (one end) versus only his last two games (other end). A wide range means the role is moving.'],
  flags: ['Flags', 'Things worth calling out: role rising/falling, teammates out, questionable status, regression risk.'],
  hit_l5: ['TD hit rate, last 5', 'Games with a TD in his last 5, shown against how many TDs those games *should* have produced. 5 of 5 on 2.1 expected is a hot streak, not a lock — the expected number is what repeats.'],
  verdict: ['Verdict', 'The price to beat and the two or three reasons behind the number.'],
}
const H = ({ k }: { k: string }) => <span title={DEF[k]?.[1]} style={{ cursor: 'help', borderBottom: '1px dotted var(--muted)' }}>{DEF[k]?.[0] ?? k}</span>
const Stat = ({ k, v, label }: { k?: string; v: string; label?: string }) => <div className="stat"><span className="stat-l" title={k ? DEF[k]?.[1] : undefined}>{label ?? (k ? DEF[k]?.[0] : '')}</span><span className="stat-v">{v}</span></div>

export default function App() {
  const params = new URLSearchParams(location.search)
  const week = Number(params.get('week') || 2)
  const view = params.get('view') || 'board'
  const [token, setToken] = useState(() => { try { return localStorage.getItem('sixpts_token') || '' } catch { return '' } })
  const [session, setSession] = useState<string | null>(null); const [email, setEmail] = useState('')
  useEffect(() => { if (!supabase) return; sessionToken().then(setSession); const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s?.access_token ?? null)); return () => data.subscription.unsubscribe() }, [])
  const authHeaders = (): Record<string, string> => session ? { Authorization: 'Bearer ' + session } : token ? { 'X-Token': token } : {}
  const canSave = !!(session || token)
  const [data, setData] = useState<Board | null>(null); const [err, setErr] = useState('')
  const [market, setMarket] = useState<'td' | 'rec'>('td')
  const key = `sixpts_w${week}_${market}`
  const [store, setStore] = useState<Store>(() => { try { return JSON.parse(localStorage.getItem(key) || '{}') } catch { return {} } })
  useEffect(() => { try { setStore(JSON.parse(localStorage.getItem(key) || '{}')) } catch { setStore({}) } }, [key])
  const [pos, setPos] = useState(''); const [game, setGame] = useState(''); const [minp, setMinp] = useState(15)
  const [onlyEdge, setOnlyEdge] = useState(false); const [onlyPicks, setOnlyPicks] = useState(false); const [q, setQ] = useState('')
  const [sort, setSort] = useState<{ k: keyof Row | 'edge'; dir: 1 | -1 }>({ k: 'edge', dir: -1 })
  const [open, setOpen] = useState<string | null>(null)
  const COLS: { k: string; label: string; locked?: boolean }[] = [
    { k: 'player', label: 'Player', locked: true }, { k: 'team', label: 'Matchup' }, { k: 'p_model', label: 'P(TD)', locked: true }, { k: 'fair_odds', label: 'Fair odds' },
    { k: 'book', label: 'Book price' }, { k: 'edge', label: 'Edge' }, { k: 'xtd_pg_shrunk', label: 'Expected TDs / game' }, { k: 'rz_tgt_pg', label: 'Red zone targets' },
    { k: 'ez_tgt_pg', label: 'End zone targets' }, { k: 'rz_carry_pg', label: 'Red zone carries' }, { k: 'i5_carry_pg', label: 'Goal-line carries' },
    { k: 'def_rz_td_pct', label: 'Opp red zone TD %' }, { k: 'implied', label: 'Implied total' }, { k: 'c_defense', label: 'Defense effect' }, { k: 'certainty', label: 'Certainty' }, { k: 'range', label: 'Range' }, { k: 'hit_l5', label: 'TD hit rate L5' }, { k: 'flags', label: 'Flags' }, { k: 'pick', label: 'Pick button', locked: true }]
  const [hidden, setHidden] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem('sixpts_hidden_cols') || '[]')) } catch { return new Set() } })
  const [colsOpen, setColsOpen] = useState(false)
  const [callouts, setCallouts] = useState<Callouts>({}); const [coOpen, setCoOpen] = useState(false)
  const [mp, setMp] = useState<any>(null); const [picksTick, setPicksTick] = useState(0); const [mpOpen, setMpOpen] = useState<'bets' | 'passes' | null>('bets')
  useEffect(() => { if (market !== 'td') return; fetch(`${API}/api/model-picks/${week}`).then(r => r.ok ? r.json() : null).then(setMp).catch(() => {}) }, [week, market, picksTick])
  useEffect(() => { fetch(`${API}/api/teams/${week}`).then(r => r.ok ? r.json() : null).then(d => { if (d) { const c: Callouts = {}; Object.entries(d.teams).forEach(([k, v]: any) => { if (v.callouts?.length) c[k] = v.callouts }); setCallouts(c) } }).catch(() => {}) }, [week])
  const show = (k: string) => !hidden.has(k)
  const toggleCol = (k: string) => setHidden(h => { const n = new Set(h); n.has(k) ? n.delete(k) : n.add(k); try { localStorage.setItem('sixpts_hidden_cols', JSON.stringify([...n])) } catch {} ; return n })
  const visibleCount = COLS.filter(c => show(c.k)).length

  useEffect(() => {
    const url = token ? `${API}/api/private/board/${week}` : `${API}/api/board/${week}`
    fetch(url, { headers: token ? { 'X-Token': token } : {} }).then(r => { if (!r.ok) throw new Error(r.status === 401 ? 'Private token rejected' : `No board for week ${week}`); return r.json() })
      .then(setData).catch(e => setErr(e.message))
  }, [week, token])
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(store)) } catch {} }, [store, key])

  const rows = useMemo(() => {
    if (!data) return []
    return data.board.map(r => { const s = store[rid(r)] || {}
        if (market === 'rec') { const line = Number(s.recLine); const pm = (line && r.exp_rec != null) ? pOver(r.exp_rec, r.rec_sd || 2, line) : null; const ip = implied(s.odds)
          return { ...r, p_model: pm ?? r.p_model, fair_odds: pm ? amer(pm) : r.fair_odds, odds: s.odds || '', recLine: s.recLine || '', edge: (pm != null && ip != null) ? pm - ip : null, picked: !!s.picked } }
        const ip = implied(s.odds); return { ...r, odds: s.odds || '', recLine: s.recLine || '', edge: ip == null ? null : r.p_model - ip, picked: !!s.picked } })
      .filter(r => (!pos || r.position === pos) && (!game || r.game_id === game) && r.p_model * 100 >= minp && (!onlyEdge || (r.edge != null && r.edge >= .03)) && (!onlyPicks || r.picked) && (!q || `${r.player} ${r.team} ${r.opp}`.toLowerCase().includes(q.toLowerCase())))
      .sort((a: any, b: any) => { if (sort.k === 'edge') { const ax = a.edge == null, bx = b.edge == null; if (ax && bx) return b.p_model - a.p_model; if (ax) return 1; if (bx) return -1; return (b.edge - a.edge) * (sort.dir === -1 ? 1 : -1) } let x = a[sort.k], y = b[sort.k]; if (x == null) x = -1e9; if (y == null) y = -1e9; return typeof x === 'string' ? x.localeCompare(y) * sort.dir : (x - y) * sort.dir })
  }, [data, store, pos, game, minp, onlyEdge, onlyPicks, q, sort])

  const th = (k: keyof Row | 'edge', label: string, left = false, title?: string) => (
    <th className={(left ? 'l ' : '') + (sort.k === k ? 'sorted' : '')} title={DEF[k as string]?.[1] ?? title} onClick={() => setSort(s => ({ k, dir: s.k === k ? (s.dir * -1 as 1 | -1) : (left ? 1 : -1) }))}>{DEF[k as string]?.[0] ?? label}</th>)

  const picks = data ? data.board.filter(r => store[rid(r)]?.picked) : []
  const copyPicks = () => {
    const head = 'week,game,team,opp,player,position,p_model,fair_odds,book_odds,implied,edge,xtd_proj,picked_at\n'
    const body = picks.map(r => { const s = store[rid(r)]; const ip = implied(s.odds); return [week, r.game_id, r.team, r.opp, r.player, r.position, r.p_model.toFixed(3), fmtOdds(r.fair_odds), s.odds || '', ip == null ? '' : ip.toFixed(3), ip == null ? '' : (r.p_model - ip).toFixed(3), (r.xtd_pg_shrunk ?? 0).toFixed(3), s.pickedAt || ''].join(',') }).join('\n')
    navigator.clipboard?.writeText(head + body).catch(() => prompt('Copy:', head + body))
  }

  if (err) return <div className="wrap"><h1>SixPts</h1><p className="empty">{err}</p></div>
  if (!data) return <div className="wrap"><h1>SixPts</h1><p className="empty">Loading week {week}…</p></div>

  const nav = <div className="seg" style={{ marginBottom: 10 }}>{[['board', 'Board'], ['teams', 'Teams'], ['record', 'Record']].map(([v, l]) => <button key={v} className={view === v ? 'on' : ''} onClick={() => { params.set('view', v); location.search = params.toString() }}>{l}</button>)}</div>
  if (view === 'teams') return <div className="wrap">{nav}<Teams week={week} season={data.season} /></div>
  if (view === 'record') return <div className="wrap">{nav}<Record /></div>
  return (
    <div className="wrap">
      {nav}
      <header>
        <h1>SixPts <small>Week {data.week} · {market === 'rec' ? 'receptions' : 'anytime TD'}{data.model ? ' · ' + data.model : ''}</small></h1>
        <span className="meta">P(TD) = 1 − e<sup>−xTD</sup>. Expected touchdowns from where every touch happens, shrunk toward last season, adjusted for game environment and opponent.</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {supabase ? (session ? <button className="pick" onClick={() => signOut()}>Sign out</button> : <><input type="email" placeholder="email to save picks" value={email} onChange={e => setEmail(e.target.value)} /><button className="pick" onClick={() => { if (email) { signIn(email); alert('Check your email for the sign-in link.') } }}>Sign in</button></>) : null}
          <label className="meta">Admin key <input type="password" value={token} placeholder="optional" onChange={e => { setToken(e.target.value); try { localStorage.setItem('sixpts_token', e.target.value) } catch {} }} /></label>
        </span>
      </header>
      {data.injury_report && data.injury_report.startsWith('not') && <div className="banner">Injury report for this week {data.injury_report}. Availability flags and teammate-out adjustments will appear once it's loaded.</div>}
      <div className="slate">
        <div className={'chip' + (game ? '' : ' on')} onClick={() => setGame('')}><b>All games</b>{data.slate.length} games</div>
        {data.slate.map(g => { const [, , a, h] = g.game.split('_'); return <div key={g.game} className={'chip' + (game === g.game ? ' on' : '')} onClick={() => setGame(game === g.game ? '' : g.game)}><b>{a} @ {h}</b>{g.kickoff.slice(5)} · O/U {g.total}</div> })}
      </div>
      {market === 'td' && mp && <div className="best">
        <h2>Model picks <span className="meta">{mp.priced} players priced · Bet = 5+ pts edge, all-but-one signals green, high certainty · Lean = edge with mixed signals · Pass = says why</span></h2>
        {mp.priced === 0 && <div className="meta">{mp.note}</div>}
        {[...mp.bets, ...mp.leans].map((c: any) => <div key={c.gsis_id} className="best-row">
          <span className={'tier ' + c.tier.toLowerCase()}>{c.tier}</span> <b>{c.player}</b> <span className="meta">{c.position} {c.team} vs {c.opp}</span> · <span className="meta">stake {c.stake ? (c.stake * 100).toFixed(1) + '% (¼ Kelly)' : '—'}</span>
          <div>{c.headline}</div>
          <div className="sig">{Object.entries(c.signals).map(([k, v]: any) => <span key={k} className={'sigchip ' + (v ? 'ok' : 'no')}>{v ? '✓' : '✗'} {k.replace('_', ' ')}</span>)}</div>
          {c.reasons_for.length > 0 && <div className="meta">For: {c.reasons_for.join(' · ')}</div>}
          {c.reasons_against.length > 0 && <div className="meta">Against: {c.reasons_against.join(' · ')}</div>}
        </div>)}
        {mp.priced > 0 && mp.bets.length + mp.leans.length === 0 && <div className="meta">Nothing clears the bar at the prices entered. Passes below say why.</div>}
        {mp.passes.length > 0 && <div style={{ marginTop: 8 }}><button className="pick" onClick={() => setMpOpen(o => o === 'passes' ? 'bets' : 'passes')}>{mpOpen === 'passes' ? 'Hide' : 'Show'} passes ({mp.passes.length})</button>
          {mpOpen === 'passes' && mp.passes.map((c: any) => <div key={c.gsis_id} className="best-row"><span className="tier pass">Pass</span> <b>{c.player}</b> <span className="meta">{c.team} vs {c.opp}</span><div className="meta">{c.headline}</div></div>)}</div>}
      </div>}
      {Object.keys(callouts).length > 0 && <div className="callouts"><button className="pick" onClick={() => setCoOpen(o => !o)}>{coOpen ? 'Hide' : 'Show'} this week's call-outs ({Object.values(callouts).reduce((n, v) => n + v.length, 0)})</button>
        {coOpen && <div className="co-grid">{Object.entries(callouts).filter(([k]) => !game || game.includes(k)).map(([k, v]) => <div key={k} className="block"><h3>{k}</h3>{v.map((s, i) => <div key={i} className={'co ' + (/rising|due|newly|opened/.test(s) ? 'pos' : /falling|risk|Out|Doubtful|wind|cold/.test(s) ? 'neg' : '')}>{s}</div>)}</div>)}</div>}</div>}
      <div className="controls">
        <div className="seg"><button className={market === 'td' ? 'on' : ''} onClick={() => setMarket('td')}>Anytime TD</button><button className={market === 'rec' ? 'on' : ''} onClick={() => setMarket('rec')}>Receptions</button></div>
        <div className="seg">{['', 'RB', 'WR', 'TE', 'QB'].map(p => <button key={p} className={pos === p ? 'on' : ''} onClick={() => setPos(p)}>{p || 'All'}</button>)}</div>
        <label>Min P(TD) <input type="number" value={minp} min={0} max={100} step={5} onChange={e => setMinp(Number(e.target.value) || 0)} />%</label>
        <label><input type="checkbox" checked={onlyEdge} onChange={e => setOnlyEdge(e.target.checked)} /> only edge ≥ 3 pts</label>
        <label><input type="checkbox" checked={onlyPicks} onChange={e => setOnlyPicks(e.target.checked)} /> only my picks</label>
        <input type="search" placeholder="Search player or team" value={q} onChange={e => setQ(e.target.value)} />
        <span className="meta">{rows.length} players</span>
        <div style={{ position: 'relative', marginLeft: 'auto' }}>
          <button className="pick" onClick={() => setColsOpen(o => !o)}>Columns{hidden.size ? ` (${hidden.size} hidden)` : ''}</button>
          {colsOpen && <div className="colmenu">
            {COLS.map(c => <label key={c.k} className={c.locked ? 'meta' : ''}><input type="checkbox" checked={show(c.k)} disabled={c.locked} onChange={() => toggleCol(c.k)} /> {c.label}</label>)}
            <button onClick={() => { setHidden(new Set()); try { localStorage.removeItem('sixpts_hidden_cols') } catch {} }}>Show all</button>
          </div>}
        </div>
      </div>
      <div className="tablewrap"><table>
        <thead><tr>{th('player', 'Player', true)}{show('team') && th('team', 'Game', true)}{market === 'rec' && <th title="Projected targets and receptions from his shrunk target rate, catch rate by position, and the implied total">Proj tgt / rec</th>}{market === 'rec' && <th title="Enter the book's receptions line (e.g. 4.5)">Line</th>}{th('p_model', market === 'rec' ? 'P(over)' : 'P(TD)')}{show('fair_odds') && th('fair_odds', 'Fair')}{show('book') && <th title={DEF.book[1]}>{DEF.book[0]}</th>}{show('edge') && th('edge', 'Edge')}{show('xtd_pg_shrunk') && th('xtd_pg_shrunk', 'xTD/g')}{show('rz_tgt_pg') && th('rz_tgt_pg', 'RZ tgt/g')}{show('ez_tgt_pg') && th('ez_tgt_pg', 'EZ/g')}{show('rz_carry_pg') && th('rz_carry_pg', 'RZ car/g')}{show('i5_carry_pg') && th('i5_carry_pg', '≤5/g')}{show('def_rz_td_pct') && th('def_rz_td_pct', 'Opp RZ TD%')}{show('implied') && th('implied', 'Impl')}{show('c_defense') && th('c_defense', 'Def Δ')}{show('certainty') && th('certainty', 'Certainty')}{show('range') && <th title={DEF.range[1]}>{DEF.range[0]}</th>}{show('hit_l5') && <th title={DEF.hit_l5[1]}>{DEF.hit_l5[0]}</th>}{show('flags') && <th className="l" title={DEF.flags[1]}>{DEF.flags[0]}</th>}<th /></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={visibleCount} className="empty">No players match. Lower the minimum P(TD) or clear a filter.</td></tr>}
          {rows.map(r => { const id = rid(r); const s = store[id] || {}; return (<>
            <tr key={id} className="row" onClick={() => setOpen(open === id ? null : id)}>
              <td className="l"><span className="player">{r.player}<small>{r.position} {r.team}</small>{r.new_team && <span className="tag">new team</span>}</span></td>
              {show('team') && <td className="l">{r.team} {r.spread > 0 ? '+' : ''}{r.spread} vs {r.opp}</td>}
              {market === 'rec' && <td className="num">{n1(r.exp_targets, 1)} / {n1(r.exp_rec, 1)}</td>}
              {market === 'rec' && <td><input className="odds" type="number" step={0.5} placeholder="4.5" value={(r as any).recLine || ''} onClick={e => e.stopPropagation()} onChange={e => setStore(st => ({ ...st, [id]: { ...st[id], recLine: e.target.value } }))} /></td>}
              <td className="num">{market === 'rec' && !(r as any).recLine ? <span className="meta">enter line</span> : <span className={'p ' + heat(r.p_model)}>{pct(r.p_model)}</span>}</td>
              {show('fair_odds') && <td className="num">{market === 'rec' && !(r as any).recLine ? '—' : fmtOdds(r.fair_odds ?? 0)}</td>}
              {show('book') && <td><input className="odds" type="number" step={5} placeholder="+150" value={s.odds || ''} onClick={e => e.stopPropagation()} onChange={e => setStore(st => ({ ...st, [id]: { ...st[id], odds: e.target.value } }))} onBlur={e => { const v = Number(e.target.value); if (canSave && v) fetch(`${API}/api/odds`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ gsis_id: r.gsis_id, game_id: r.game_id, market: market === 'rec' ? 'receptions' : 'anytime_td', book: 'book', price: v, line: market === 'rec' ? Number((r as any).recLine) || null : null }) }).then(() => setPicksTick(t => t + 1)).catch(() => {}) }} /></td>}
              {show('edge') && <td className="num">{r.edge == null ? <span className="meta">—</span> : <span className={'edge ' + (r.edge >= 0 ? 'pos' : 'neg')}>{r.edge >= .03 ? <span className="flag">{(r.edge * 100).toFixed(1)}</span> : (r.edge * 100).toFixed(1)}</span>}</td>}
              {show('xtd_pg_shrunk') && <td className="num">{n1(r.xtd_pg_shrunk)}</td>}
              {show('rz_tgt_pg') && <td>{n1(r.rz_tgt_pg ?? r.rz_tgt, 1)}</td>}{show('ez_tgt_pg') && <td>{n1(r.ez_tgt_pg ?? r.ez_tgt, 1)}</td>}{show('rz_carry_pg') && <td>{n1(r.rz_carry_pg ?? r.rz_carry, 1)}</td>}{show('i5_carry_pg') && <td>{n1(r.i5_carry_pg ?? r.i5_carry, 1)}</td>}
              {show('def_rz_td_pct') && <td>{r.def_rz_td_pct == null ? '—' : Math.round(r.def_rz_td_pct * 100) + '%'}</td>}
              {show('implied') && <td>{r.implied.toFixed(1)}</td>}{show('c_defense') && <td className="num">{pts(r.c_defense)}</td>}
              {show('certainty') && <td><span className={'cert ' + (r.certainty_label || '')}>{r.certainty_label ?? '—'}</span></td>}
              {show('range') && <td className="meta">{r.p_low == null ? '—' : (Math.abs((r.p_high ?? 0) - (r.p_low ?? 0)) < 0.005 ? pct(r.p_model) : pct(r.p_low ?? 0) + '–' + pct(r.p_high ?? 0))}</td>}
              {show('hit_l5') && <td>{r.n_l5 ? <span className={(r.hit_l5 ?? 0) - (r.xtd_l5 ?? 0) >= 1.5 ? 'edge neg' : ''} title={(r.hit_l5 ?? 0) - (r.xtd_l5 ?? 0) >= 1.5 ? 'Scoring well above expectation — streak, not role' : ''}>{r.hit_l5} of {r.n_l5} <small className="meta">exp {n1(r.xtd_l5, 1)}</small></span> : '—'}</td>}
              {show('flags') && <td className="l" style={{ whiteSpace: 'normal', maxWidth: 260 }}>{(r.flags || []).map(f => <span key={f} className={'flag-chip' + (/QUESTIONABLE|falling|regression risk/.test(f) ? ' warn' : '')}>{f}</span>)}</td>}
              <td><button className={'pick ' + (s.picked ? 'on' : '')} onClick={e => { e.stopPropagation(); const now = !s.picked; setStore(st => ({ ...st, [id]: { ...st[id], picked: now, pickedAt: new Date().toISOString() } }))
                if (now && canSave && s.odds) fetch(`${API}/api/picks`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ gsis_id: r.gsis_id, game_id: r.game_id, market: market === 'rec' ? 'receptions' : 'anytime_td', player: r.player, team: r.team, opp: r.opp, line: market === 'rec' ? Number((r as any).recLine) : null, price_taken: Number(s.odds), p_model: r.p_model }) }).catch(() => {})
                else if (now && !s.odds) alert('Enter the book price first so the pick is logged with a price.') }}>{s.picked ? 'Picked' : 'Log pick'}</button></td>
            </tr>
            {open === id && <tr key={id + 'd'} className="detail"><td colSpan={visibleCount}>
              {r.verdict && <div className="verdict">{r.verdict}</div>}
              <div className="detail-head"><span className="player" style={{fontSize:18}}>{r.player}</span> <span className="meta">{r.position} · {r.team} {r.spread > 0 ? 'favored by ' + r.spread : 'underdog by ' + (-r.spread)} vs {r.opp}</span>
                <span className="meta" style={{marginLeft:'auto'}}>Each block shows how many percentage points that factor moves this player's touchdown probability versus an average player on this slate.</span></div>
              <div className="grid">
                <div className="block"><h3>His role <b className={'edge ' + ((r.c_role ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_role)}</b></h3>
                  <Stat k="xtd_pg_shrunk" v={n1(r.xtd_pg_shrunk)} /><Stat k="rz_tgt_pg" v={n1(r.rz_tgt_pg, 1)} /><Stat k="ez_tgt_pg" v={n1(r.ez_tgt_pg, 1)} /><Stat k="rz_carry_pg" v={n1(r.rz_carry_pg, 1)} /><Stat k="i5_carry_pg" v={n1(r.i5_carry_pg, 1)} />
                  <Stat label="Expected TDs, last 2 games" v={n1(r.xtd_recent)} /><Stat label="Snap share" v={r.snap_pct == null ? '—' : Math.round(r.snap_pct * 100) + '%'} /><Stat label="Games this season" v={String(r.games)} /><Stat label="Certainty" v={`${r.certainty ?? '—'} (${r.certainty_label ?? '—'})`} />{(r.rz_share_open ?? 0) > 0 && <Stat label="Red zone work opened by teammates out" v={Math.round((r.rz_share_open ?? 0) * 100) + '%'} />}{r.new_team && <Stat label="Changed teams" v="yes — last season weighted less" />}</div>
                <div className="block"><h3>His offense · {r.team} <b className={'edge ' + ((r.c_offense ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_offense)}</b></h3>
                  <Stat label="Red zone trips / game" v={n1(r.off_rz_trips, 1)} /><Stat label="Red zone pass rate" v={r.off_rz_pass_rate == null ? '—' : Math.round(r.off_rz_pass_rate * 100) + '%'} /><Stat label="Pass rate over expected" v={r.off_pass_oe == null ? '—' : (r.off_pass_oe >= 0 ? '+' : '') + r.off_pass_oe.toFixed(1)} /><Stat label="Red zone pass rate when trailing / leading" v={`${r.off_rz_pass_rate_trailing == null ? '—' : Math.round(r.off_rz_pass_rate_trailing * 100) + '%'} / ${r.off_rz_pass_rate_leading == null ? '—' : Math.round(r.off_rz_pass_rate_leading * 100) + '%'}`} /></div>
                <div className="block"><h3>The defense · {r.opp} <b className={'edge ' + ((r.c_defense ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_defense)}</b></h3>
                  <Stat k="def_rz_td_pct" v={r.def_rz_td_pct == null ? '—' : Math.round(r.def_rz_td_pct * 100) + '%'} /><Stat label="TDs allowed / game to RBs" v={n1(r.def_td_rb_pg)} /><Stat label="to WRs" v={n1(r.def_td_wr_pg)} /><Stat label="to TEs" v={n1(r.def_td_te_pg)} />{r.matchup_note && <Stat label="Coverage" v={r.matchup_note} />}</div>
                <div className="block"><h3>The game <b className={'edge ' + ((r.c_environment ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_environment)}</b></h3>
                  <Stat k="implied" v={r.implied.toFixed(1) + ' pts'} /><Stat label="Over / under" v={String(r.total)} /><Stat label="Spread" v={r.spread > 0 ? 'favored by ' + r.spread : 'underdog by ' + (-r.spread)} /></div>
              </div></td></tr>}
          </>) })}
        </tbody></table></div>
      {data.sources && <div className="sources"><b>Where this comes from:</b> {Object.entries(data.sources).map(([k, v]) => <span key={k}>{v}</span>)}<span>Verify a player is active before kickoff — the report can change after it's loaded.</span></div>}
      <div className="tray">
        <h2>Picks <span className="meta">{picks.length ? picks.length + ' logged' : 'none yet'}</span></h2>
        <div className="list">{picks.length === 0 ? <span className="meta">Log a pick from any row. Picks and prices stay in this browser.</span> : picks.map(r => { const s = store[rid(r)]; const ip = implied(s.odds); return <span key={rid(r)} className="item">{r.player} ({r.team} v {r.opp}) {pct(r.p_model)}{s.odds ? ' @ ' + fmtOdds(Number(s.odds)) : ''}{ip != null ? ` · edge ${((r.p_model - ip) * 100).toFixed(1)}` : ''}</span> })}</div>
        <button onClick={copyPicks}>Copy picks CSV</button>
        <button onClick={() => { if (confirm('Clear all picks and prices for this week?')) setStore({}) }}>Clear</button>
      </div>
    </div>)
}
