import { useEffect, useMemo, useState } from 'react'
import Teams from './Teams'

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
}
type Board = { week: number; season: number; model?: string; board: Row[]; slate: { game: string; kickoff: string; total: number }[] }
const n1 = (x?: number | null, d = 2) => x == null ? '—' : x.toFixed(d)
const pts = (x?: number | null) => x == null ? '—' : (x >= 0 ? '+' : '') + (x * 100).toFixed(1)
type Store = Record<string, { odds?: string; picked?: boolean; pickedAt?: string }>

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
}
const H = ({ k }: { k: string }) => <span title={DEF[k]?.[1]} style={{ cursor: 'help', borderBottom: '1px dotted var(--muted)' }}>{DEF[k]?.[0] ?? k}</span>
const Stat = ({ k, v, label }: { k?: string; v: string; label?: string }) => <div className="stat"><span className="stat-l" title={k ? DEF[k]?.[1] : undefined}>{label ?? (k ? DEF[k]?.[0] : '')}</span><span className="stat-v">{v}</span></div>

export default function App() {
  const params = new URLSearchParams(location.search)
  const week = Number(params.get('week') || 2)
  const view = params.get('view') || 'board'
  const [token, setToken] = useState(() => { try { return localStorage.getItem('sixpts_token') || '' } catch { return '' } })
  const [data, setData] = useState<Board | null>(null); const [err, setErr] = useState('')
  const key = `sixpts_w${week}`
  const [store, setStore] = useState<Store>(() => { try { return JSON.parse(localStorage.getItem(key) || '{}') } catch { return {} } })
  const [pos, setPos] = useState(''); const [game, setGame] = useState(''); const [minp, setMinp] = useState(15)
  const [onlyEdge, setOnlyEdge] = useState(false); const [onlyPicks, setOnlyPicks] = useState(false); const [q, setQ] = useState('')
  const [sort, setSort] = useState<{ k: keyof Row | 'edge'; dir: 1 | -1 }>({ k: 'p_model', dir: -1 })
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    const url = token ? `/api/private/board/${week}` : `/api/board/${week}`
    fetch(url, { headers: token ? { 'X-Token': token } : {} }).then(r => { if (!r.ok) throw new Error(r.status === 401 ? 'Private token rejected' : `No board for week ${week}`); return r.json() })
      .then(setData).catch(e => setErr(e.message))
  }, [week, token])
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(store)) } catch {} }, [store, key])

  const rows = useMemo(() => {
    if (!data) return []
    return data.board.map(r => { const s = store[rid(r)] || {}; const ip = implied(s.odds); return { ...r, odds: s.odds || '', edge: ip == null ? null : r.p_model - ip, picked: !!s.picked } })
      .filter(r => (!pos || r.position === pos) && (!game || r.game_id === game) && r.p_model * 100 >= minp && (!onlyEdge || (r.edge != null && r.edge >= .03)) && (!onlyPicks || r.picked) && (!q || `${r.player} ${r.team} ${r.opp}`.toLowerCase().includes(q.toLowerCase())))
      .sort((a: any, b: any) => { let x = a[sort.k], y = b[sort.k]; if (x == null) x = -1e9; if (y == null) y = -1e9; return typeof x === 'string' ? x.localeCompare(y) * sort.dir : (x - y) * sort.dir })
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

  const nav = <div className="seg" style={{ marginBottom: 10 }}>{[['board', 'Board'], ['teams', 'Teams']].map(([v, l]) => <button key={v} className={view === v ? 'on' : ''} onClick={() => { params.set('view', v); location.search = params.toString() }}>{l}</button>)}</div>
  if (view === 'teams') return <div className="wrap">{nav}<Teams week={week} season={data.season} /></div>
  return (
    <div className="wrap">
      {nav}
      <header>
        <h1>SixPts <small>Week {data.week} · anytime TD{data.model ? ' · ' + data.model : ''}</small></h1>
        <span className="meta">P(TD) = 1 − e<sup>−xTD</sup>. Expected touchdowns from where every touch happens, shrunk toward last season, adjusted for game environment and opponent.</span>
        <label className="meta" style={{ marginLeft: 'auto' }}>Private key <input type="password" value={token} placeholder="optional" onChange={e => { setToken(e.target.value); try { localStorage.setItem('sixpts_token', e.target.value) } catch {} }} /></label>
      </header>
      <div className="slate">
        <div className={'chip' + (game ? '' : ' on')} onClick={() => setGame('')}><b>All games</b>{data.slate.length} games</div>
        {data.slate.map(g => { const [, , a, h] = g.game.split('_'); return <div key={g.game} className={'chip' + (game === g.game ? ' on' : '')} onClick={() => setGame(game === g.game ? '' : g.game)}><b>{a} @ {h}</b>{g.kickoff.slice(5)} · O/U {g.total}</div> })}
      </div>
      <div className="controls">
        <div className="seg">{['', 'RB', 'WR', 'TE', 'QB'].map(p => <button key={p} className={pos === p ? 'on' : ''} onClick={() => setPos(p)}>{p || 'All'}</button>)}</div>
        <label>Min P(TD) <input type="number" value={minp} min={0} max={100} step={5} onChange={e => setMinp(Number(e.target.value) || 0)} />%</label>
        <label><input type="checkbox" checked={onlyEdge} onChange={e => setOnlyEdge(e.target.checked)} /> only edge ≥ 3 pts</label>
        <label><input type="checkbox" checked={onlyPicks} onChange={e => setOnlyPicks(e.target.checked)} /> only my picks</label>
        <input type="search" placeholder="Search player or team" value={q} onChange={e => setQ(e.target.value)} />
        <span className="meta">{rows.length} players</span>
      </div>
      <div className="tablewrap"><table>
        <thead><tr>{th('player', 'Player', true)}{th('team', 'Game', true)}{th('p_model', 'P(TD)')}{th('fair_odds', 'Fair')}<th title={DEF.book[1]}>{DEF.book[0]}</th>{th('edge', 'Edge')}{th('xtd_pg_shrunk', 'xTD/g')}{th('rz_tgt_pg', 'RZ tgt/g')}{th('ez_tgt_pg', 'EZ/g')}{th('rz_carry_pg', 'RZ car/g')}{th('i5_carry_pg', '≤5/g')}{th('def_rz_td_pct', 'Opp RZ TD%')}{th('implied', 'Impl')}{th('c_defense', 'Def Δ')}<th /></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={15} className="empty">No players match. Lower the minimum P(TD) or clear a filter.</td></tr>}
          {rows.map(r => { const id = rid(r); const s = store[id] || {}; return (<>
            <tr key={id} className="row" onClick={() => setOpen(open === id ? null : id)}>
              <td className="l"><span className="player">{r.player}<small>{r.position} {r.team}</small>{r.new_team && <span className="tag">new team</span>}</span></td>
              <td className="l">{r.team} {r.spread > 0 ? '+' : ''}{r.spread} vs {r.opp}</td>
              <td className="num"><span className={'p ' + heat(r.p_model)}>{pct(r.p_model)}</span></td>
              <td className="num">{fmtOdds(r.fair_odds)}</td>
              <td><input className="odds" type="number" step={5} placeholder="+150" value={s.odds || ''} onClick={e => e.stopPropagation()} onChange={e => setStore(st => ({ ...st, [id]: { ...st[id], odds: e.target.value } }))} /></td>
              <td className="num">{r.edge == null ? <span className="meta">—</span> : <span className={'edge ' + (r.edge >= 0 ? 'pos' : 'neg')}>{r.edge >= .03 ? <span className="flag">{(r.edge * 100).toFixed(1)}</span> : (r.edge * 100).toFixed(1)}</span>}</td>
              <td className="num">{n1(r.xtd_pg_shrunk)}</td>
              <td>{n1(r.rz_tgt_pg ?? r.rz_tgt, 1)}</td><td>{n1(r.ez_tgt_pg ?? r.ez_tgt, 1)}</td><td>{n1(r.rz_carry_pg ?? r.rz_carry, 1)}</td><td>{n1(r.i5_carry_pg ?? r.i5_carry, 1)}</td>
              <td>{r.def_rz_td_pct == null ? '—' : Math.round(r.def_rz_td_pct * 100) + '%'}</td>
              <td>{r.implied.toFixed(1)}</td><td className="num">{pts(r.c_defense)}</td>
              <td><button className={'pick ' + (s.picked ? 'on' : '')} onClick={e => { e.stopPropagation(); setStore(st => ({ ...st, [id]: { ...st[id], picked: !st[id]?.picked, pickedAt: new Date().toISOString() } })) }}>{s.picked ? 'Picked' : 'Log pick'}</button></td>
            </tr>
            {open === id && <tr key={id + 'd'} className="detail"><td colSpan={15}>
              <div className="detail-head"><span className="player" style={{fontSize:18}}>{r.player}</span> <span className="meta">{r.position} · {r.team} {r.spread > 0 ? 'favored by ' + r.spread : 'underdog by ' + (-r.spread)} vs {r.opp}</span>
                <span className="meta" style={{marginLeft:'auto'}}>Each block shows how many percentage points that factor moves this player's touchdown probability versus an average player on this slate.</span></div>
              <div className="grid">
                <div className="block"><h3>His role <b className={'edge ' + ((r.c_role ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_role)}</b></h3>
                  <Stat k="xtd_pg_shrunk" v={n1(r.xtd_pg_shrunk)} /><Stat k="rz_tgt_pg" v={n1(r.rz_tgt_pg, 1)} /><Stat k="ez_tgt_pg" v={n1(r.ez_tgt_pg, 1)} /><Stat k="rz_carry_pg" v={n1(r.rz_carry_pg, 1)} /><Stat k="i5_carry_pg" v={n1(r.i5_carry_pg, 1)} />
                  <Stat label="Snap share" v={r.snap_pct == null ? '—' : Math.round(r.snap_pct * 100) + '%'} /><Stat label="Games this season" v={String(r.games)} />{r.new_team && <Stat label="Changed teams" v="yes — last season weighted less" />}</div>
                <div className="block"><h3>His offense · {r.team} <b className={'edge ' + ((r.c_offense ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_offense)}</b></h3>
                  <Stat label="Red zone trips / game" v={n1(r.off_rz_trips, 1)} /><Stat label="Red zone pass rate" v={r.off_rz_pass_rate == null ? '—' : Math.round(r.off_rz_pass_rate * 100) + '%'} /><Stat label="Pass rate over expected" v={r.off_pass_oe == null ? '—' : (r.off_pass_oe >= 0 ? '+' : '') + r.off_pass_oe.toFixed(1)} /></div>
                <div className="block"><h3>The defense · {r.opp} <b className={'edge ' + ((r.c_defense ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_defense)}</b></h3>
                  <Stat k="def_rz_td_pct" v={r.def_rz_td_pct == null ? '—' : Math.round(r.def_rz_td_pct * 100) + '%'} /><Stat label="TDs allowed / game to RBs" v={n1(r.def_td_rb_pg)} /><Stat label="to WRs" v={n1(r.def_td_wr_pg)} /><Stat label="to TEs" v={n1(r.def_td_te_pg)} />{r.matchup_note && <Stat label="Coverage" v={r.matchup_note} />}</div>
                <div className="block"><h3>The game <b className={'edge ' + ((r.c_environment ?? 0) >= 0 ? 'pos' : 'neg')}>{pts(r.c_environment)}</b></h3>
                  <Stat k="implied" v={r.implied.toFixed(1) + ' pts'} /><Stat label="Over / under" v={String(r.total)} /><Stat label="Spread" v={r.spread > 0 ? 'favored by ' + r.spread : 'underdog by ' + (-r.spread)} /></div>
              </div></td></tr>}
          </>) })}
        </tbody></table></div>
      <div className="tray">
        <h2>Picks <span className="meta">{picks.length ? picks.length + ' logged' : 'none yet'}</span></h2>
        <div className="list">{picks.length === 0 ? <span className="meta">Log a pick from any row. Picks and prices stay in this browser.</span> : picks.map(r => { const s = store[rid(r)]; const ip = implied(s.odds); return <span key={rid(r)} className="item">{r.player} ({r.team} v {r.opp}) {pct(r.p_model)}{s.odds ? ' @ ' + fmtOdds(Number(s.odds)) : ''}{ip != null ? ` · edge ${((r.p_model - ip) * 100).toFixed(1)}` : ''}</span> })}</div>
        <button onClick={copyPicks}>Copy picks CSV</button>
        <button onClick={() => { if (confirm('Clear all picks and prices for this week?')) setStore({}) }}>Clear</button>
      </div>
    </div>)
}
