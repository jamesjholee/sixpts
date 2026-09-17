import { useEffect, useState } from 'react'
import { API } from './api'

type Off = { games: number; plays: number; pass_rate: number; neutral_pass_rate: number | null; pass_oe: number; rz_trips: number; rz_pass_rate: number | null; rz_td_pct: number | null; td_pg: number; xtd_pg: number }
type Def = { games: number; td_allowed: number; xtd_allowed: number; pass_td: number; rush_td: number; rz_trips: number; rz_td_pct: number | null; td_RB: number; td_WR: number; td_TE: number; td_over_x: number }
type Role = { gsis_id: string; display_name: string; position: string; targets: number; carries: number; rz_tgt: number; ez_tgt: number; i5_carry: number; xtd: number; td: number; rz_tgt_share: number | null; ez_tgt_share: number | null; i5_carry_share: number | null; xtd_share: number | null }
type Team = { callouts?: string[]; next: { opp: string; home: boolean; implied: number; total: number; spread: number } | null; offense: { prev: Off | null; cur: Off | null }; defense: { prev: Def | null; cur: Def | null }; roles: { prev: Role[]; cur: Role[] } }
type Data = { season: number; week: number; teams: Record<string, Team> }

const pc = (x?: number | null) => x == null ? '—' : Math.round(x * 100) + '%'
const f1 = (x?: number | null) => x == null ? '—' : x.toFixed(1)
const f2 = (x?: number | null) => x == null ? '—' : x.toFixed(2)
const sg = (x?: number | null) => x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1)

function D({ cur, prev, fmt, invert = false }: { cur?: number | null; prev?: number | null; fmt: (x?: number | null) => string; invert?: boolean }) {
  if (cur == null || prev == null) return <span className="muted">{fmt(cur)}</span>
  const d = cur - prev; const good = invert ? d < 0 : d > 0; const flat = Math.abs(d) < 1e-9
  return <span className="num"><b>{fmt(cur)}</b> <span className="delta">{prev != null && !flat ? <span className={good ? 'pos' : 'neg'}>{d > 0 ? '▲' : '▼'}</span> : null} was {fmt(prev)}</span></span>
}

export default function Games({ week, season }: { week: number; season: number }) {
  const [data, setData] = useState<Data | null>(null); const [err, setErr] = useState('')
  const [sel, setSel] = useState<string>(() => new URLSearchParams(location.search).get('game') || '')
  const [side, setSide] = useState<0 | 1>(0)
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth < 720)
  useEffect(() => { const f = () => setIsMobile(window.innerWidth < 720); window.addEventListener('resize', f); return () => window.removeEventListener('resize', f) }, [])
  useEffect(() => { fetch(`${API}/api/teams/${week}`).then(r => { if (!r.ok) throw new Error(`No team profiles for week ${week}`); return r.json() }).then(setData).catch(e => setErr(e.message)) }, [week])
  if (err) return <p className="empty">{err}</p>
  if (!data) return <p className="empty">Loading…</p>

  // build matchups from each team's `next`
  const seen = new Set<string>(); const games: { id: string; away: string; home: string; total: number; spread: number }[] = []
  for (const [tm, t] of Object.entries(data.teams)) {
    if (!t.next) continue; const home = t.next.home ? tm : t.next.opp; const away = t.next.home ? t.next.opp : tm; const id = `${away}@${home}`
    if (seen.has(id)) continue; seen.add(id); games.push({ id, away, home, total: t.next.total, spread: t.next.home ? t.next.spread : -t.next.spread })
  }
  const byes = Object.entries(data.teams).filter(([, t]) => !t.next).map(([k]) => k)
  const g = games.find(x => x.id === sel) || games[0]
  if (!g) return <p className="empty">No games this week.</p>
  const teams = [g.away, g.home]
  const spreadText = g.spread > 0 ? `${g.home} −${g.spread}` : g.spread < 0 ? `${g.away} −${-g.spread}` : 'pick'

  const card = (tm: string) => {
    const t = data.teams[tm]; const o = t.offense, d = t.defense
    const row = (label: string, cur?: number | null, prev?: number | null, fmt = f1, invert = false) => <tr><td className="l muted">{label}</td><td><D cur={cur} prev={prev} fmt={fmt} invert={invert} /></td></tr>
    return (<div className="team-card" key={tm}>
      <h2>{tm} <span className="muted">{t.next?.home ? 'home' : 'away'} · implied {t.next?.implied.toFixed(1)}</span></h2>
      <p className="muted" style={{ margin: '4px 0 10px' }}>{season} through Week {week - 1} ({o.cur?.games ?? 0} gm) vs {season - 1}</p>
      {t.callouts && t.callouts.length > 0 && <div style={{ marginBottom: 12 }}><h3>What changed</h3>{t.callouts.map((s, i) => <div key={i} className={'co ' + (/rising|due|newly|opened/.test(s) ? 'pos' : /falling|risk|Out|Doubtful|wind|cold/.test(s) ? 'neg' : '')}>{s}</div>)}</div>}
      <h3>Offense</h3>
      <table><tbody>
        {row('Red zone trips / game', o.cur?.rz_trips, o.prev?.rz_trips)}
        {row('Red zone pass rate', o.cur?.rz_pass_rate, o.prev?.rz_pass_rate, pc)}
        {row('Red zone TD %', o.cur?.rz_td_pct, o.prev?.rz_td_pct, pc)}
        {row('Pass rate over expected', o.cur?.pass_oe, o.prev?.pass_oe, sg)}
        {row('Neutral-script pass rate', o.cur?.neutral_pass_rate, o.prev?.neutral_pass_rate, pc)}
        {row('Expected TDs / game', o.cur?.xtd_pg, o.prev?.xtd_pg, f2)}
      </tbody></table>
      <h3 style={{ marginTop: 12 }}>Defense</h3>
      <table><tbody>
        {row('Red zone TD % allowed', d.cur?.rz_td_pct, d.prev?.rz_td_pct, pc)}
        {row('TDs over expected', d.cur?.td_over_x, d.prev?.td_over_x, sg)}
        {row('Pass TDs / game', d.cur?.pass_td, d.prev?.pass_td, f2)}
        {row('Rush TDs / game', d.cur?.rush_td, d.prev?.rush_td, f2)}
        <tr><td className="l muted">TDs / game to RB · WR · TE</td><td className="num">{f2(d.cur?.td_RB)} · {f2(d.cur?.td_WR)} · {f2(d.cur?.td_TE)} <span className="delta">was {f2(d.prev?.td_RB)} · {f2(d.prev?.td_WR)} · {f2(d.prev?.td_TE)}</span></td></tr>
      </tbody></table>
      <h3 style={{ marginTop: 12 }}>Who owns the touchdown work</h3>
      <table><thead><tr><th className="l">Player</th><th title="Share of the team's expected TDs">xTD</th><th title="Share of red zone targets">RZ tgt</th><th title="Share of carries inside the 5">GL car</th><th>TD</th></tr></thead>
        <tbody>{t.roles.cur.map(r => { const p = t.roles.prev.find(x => x.gsis_id === r.gsis_id); return <tr key={r.gsis_id}><td className="l">{r.display_name} <span className="muted">{r.position}</span></td>
          <td className="num">{pc(r.xtd_share)}{p && <span className="delta"> {pc(p.xtd_share)}</span>}</td><td className="num">{pc(r.rz_tgt_share)}{p && <span className="delta"> {pc(p.rz_tgt_share)}</span>}</td><td className="num">{pc(r.i5_carry_share)}{p && <span className="delta"> {pc(p.i5_carry_share)}</span>}</td><td className="num">{r.td}</td></tr> })}</tbody></table>
      <p className="muted" style={{ marginTop: 6, fontSize: 12 }}>Small grey number = last season's share.</p>
    </div>)
  }

  return (<div>
    <div className="games">{games.map(x => <div key={x.id} className={'game' + (g.id === x.id ? ' on' : '')} onClick={() => { setSel(x.id); setSide(0) }}><b>{x.away} @ {x.home}</b><small>O/U {x.total} · {x.spread > 0 ? `${x.home} −${x.spread}` : x.spread < 0 ? `${x.away} −${-x.spread}` : 'pick'}</small></div>)}</div>
    <div className="bar"><h1>{g.away} @ {g.home}</h1><span className="muted">O/U {g.total} · {spreadText}</span>
      {isMobile && <div className="seg" style={{ marginLeft: 'auto' }}>{teams.map((tm, i) => <button key={tm} className={side === i ? 'on' : ''} onClick={() => setSide(i as 0 | 1)}>{tm}</button>)}</div>}</div>
    {isMobile ? card(teams[side]) : <div className="two">{card(g.away)}{card(g.home)}</div>}
    {byes.length > 0 && <p className="muted" style={{ marginTop: 12 }}>Bye: {byes.join(', ')}</p>}
  </div>)
}
