import { useEffect, useMemo, useState } from 'react'

type Row = {
  gsis_id: string; player: string; position: string; team: string; opp: string; game_id: string; kickoff: string
  implied: number; total: number; spread: number; new_team: boolean; games: number
  targets: number; rz_tgt: number; ez_tgt: number; carries: number; rz_carry: number; i5_carry: number; td: number; xtd: number
  xtd_pg_2026: number; xtd_pg_prior: number; xtd_pg_shrunk: number; env_mult: number; matchup_mult: number
  matchup_note: string | null; xtd_proj: number; p_model: number; fair_odds: number
  opp_score: number; gravity_score: number; matchup_score: number; env_score: number; score: number
}
type Board = { week: number; season: number; board: Row[]; slate: { game: string; kickoff: string; total: number }[] }
type Store = Record<string, { odds?: string; picked?: boolean; pickedAt?: string }>

const implied = (o: string | undefined) => { const n = Number(o); if (!n) return null; return n < 0 ? -n / (-n + 100) : 100 / (n + 100) }
const fmtOdds = (o: number) => (o > 0 ? '+' : '') + Math.round(o)
const heat = (p: number) => p >= .6 ? 'h5' : p >= .45 ? 'h4' : p >= .3 ? 'h3' : p >= .18 ? 'h2' : 'h1'
const pct = (x: number) => Math.round(x * 100) + '%'
const rid = (r: Row) => r.gsis_id + '|' + r.game_id

export default function App() {
  const params = new URLSearchParams(location.search)
  const week = Number(params.get('week') || 2)
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
    <th className={(left ? 'l ' : '') + (sort.k === k ? 'sorted' : '')} title={title} onClick={() => setSort(s => ({ k, dir: s.k === k ? (s.dir * -1 as 1 | -1) : (left ? 1 : -1) }))}>{label}</th>)

  const picks = data ? data.board.filter(r => store[rid(r)]?.picked) : []
  const copyPicks = () => {
    const head = 'week,game,team,opp,player,position,p_model,fair_odds,book_odds,implied,edge,xtd_proj,picked_at\n'
    const body = picks.map(r => { const s = store[rid(r)]; const ip = implied(s.odds); return [week, r.game_id, r.team, r.opp, r.player, r.position, r.p_model.toFixed(3), fmtOdds(r.fair_odds), s.odds || '', ip == null ? '' : ip.toFixed(3), ip == null ? '' : (r.p_model - ip).toFixed(3), r.xtd_proj.toFixed(3), s.pickedAt || ''].join(',') }).join('\n')
    navigator.clipboard?.writeText(head + body).catch(() => prompt('Copy:', head + body))
  }

  if (err) return <div className="wrap"><h1>SixPts</h1><p className="empty">{err}</p></div>
  if (!data) return <div className="wrap"><h1>SixPts</h1><p className="empty">Loading week {week}…</p></div>

  return (
    <div className="wrap">
      <header>
        <h1>SixPts <small>Week {data.week} · anytime TD</small></h1>
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
        <thead><tr>{th('player', 'Player', true)}{th('team', 'Game', true)}{th('p_model', 'P(TD)')}{th('fair_odds', 'Fair')}<th title="Your book price">Book</th>{th('edge', 'Edge')}{th('xtd_proj', 'xTD')}{th('td', 'TD / xTD')}{th('rz_tgt', 'RZ tgt')}{th('ez_tgt', 'EZ')}{th('rz_carry', 'RZ car')}{th('i5_carry', '≤5')}{th('implied', 'Impl')}{th('score', 'Score')}<th /></tr></thead>
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
              <td className="num">{r.xtd_proj.toFixed(2)}</td><td>{r.td} / {r.xtd.toFixed(2)}</td>
              <td>{r.rz_tgt}</td><td>{r.ez_tgt}</td><td>{r.rz_carry}</td><td>{r.i5_carry}</td>
              <td>{r.implied.toFixed(1)}</td><td className="num">{r.score}</td>
              <td><button className={'pick ' + (s.picked ? 'on' : '')} onClick={e => { e.stopPropagation(); setStore(st => ({ ...st, [id]: { ...st[id], picked: !st[id]?.picked, pickedAt: new Date().toISOString() } })) }}>{s.picked ? 'Picked' : 'Log pick'}</button></td>
            </tr>
            {open === id && <tr key={id + 'd'} className="detail"><td colSpan={15}><div className="grid">
              <div><h3>Opportunity</h3><div className="big">{r.opp_score}</div><div className="bar"><i style={{ width: r.opp_score + '%' }} /></div><div className="kv">{r.targets} tgt · {r.carries} car in {r.games} gm</div></div>
              <div><h3>TD gravity</h3><div className="big">{r.gravity_score}</div><div className="bar"><i style={{ width: r.gravity_score + '%' }} /></div><div className="kv">{data.season} <b>{r.xtd_pg_2026}</b>/gm · {data.season - 1} <b>{r.xtd_pg_prior}</b>/gm → shrunk <b>{r.xtd_pg_shrunk.toFixed(2)}</b></div></div>
              <div><h3>Matchup vs {r.opp}</h3><div className="big">{r.matchup_score}</div><div className="bar"><i style={{ width: r.matchup_score + '%' }} /></div><div className="kv">×{r.matchup_mult.toFixed(2)} · {r.matchup_note || 'opponent adjustment from our own model'}</div></div>
              <div><h3>Environment</h3><div className="big">{r.env_score}</div><div className="bar"><i style={{ width: r.env_score + '%' }} /></div><div className="kv">Implied <b>{r.implied.toFixed(1)}</b> pts · O/U {r.total} · ×{r.env_mult.toFixed(2)}</div></div>
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
