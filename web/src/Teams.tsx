import { API } from './api'
import { useEffect, useState } from 'react'

type Off = { games: number; plays: number; pass_rate: number; neutral_pass_rate: number | null; pass_oe: number; rz_trips: number; rz_pass_rate: number | null; rz_td_pct: number | null; td_pg: number; xtd_pg: number }
type Def = { games: number; td_allowed: number; xtd_allowed: number; pass_td: number; rush_td: number; rz_trips: number; rz_td_pct: number | null; td_RB: number; td_WR: number; td_TE: number; td_over_x: number }
type Role = { gsis_id: string; display_name: string; position: string; targets: number; carries: number; rz_tgt: number; ez_tgt: number; i5_carry: number; xtd: number; td: number; rz_tgt_share: number | null; ez_tgt_share: number | null; i5_carry_share: number | null; xtd_share: number | null }
type Team = { callouts?: string[]; next: { opp: string; home: boolean; implied: number; total: number; spread: number } | null; offense: { prev: Off | null; cur: Off | null }; defense: { prev: Def | null; cur: Def | null }; roles: { prev: Role[]; cur: Role[] } }
type Data = { season: number; week: number; teams: Record<string, Team> }

const pc = (x?: number | null) => x == null ? '—' : Math.round(x * 100) + '%'
const f1 = (x?: number | null) => x == null ? '—' : x.toFixed(1)
const f2 = (x?: number | null) => x == null ? '—' : x.toFixed(2)
const sg = (x?: number | null) => x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(1)

function Delta({ cur, prev, fmt, invert = false }: { cur?: number | null; prev?: number | null; fmt: (x?: number | null) => string; invert?: boolean }) {
  if (cur == null || prev == null) return <span className="meta">{fmt(cur)}</span>
  const d = cur - prev; const good = invert ? d < 0 : d > 0
  return <span><b>{fmt(cur)}</b> <span className="meta">was {fmt(prev)}</span> <span className={'edge ' + (Math.abs(d) < 1e-9 ? '' : good ? 'pos' : 'neg')}>{d >= 0 ? '▲' : '▼'}</span></span>
}

export default function Teams({ week, season }: { week: number; season: number }) {
  const [data, setData] = useState<Data | null>(null); const [err, setErr] = useState(''); const [sel, setSel] = useState('')
  useEffect(() => { fetch(`${API}/api/teams/${week}`).then(r => { if (!r.ok) throw new Error(`No team profiles for week ${week}`); return r.json() }).then(d => { setData(d); setSel(Object.keys(d.teams)[0]) }).catch(e => setErr(e.message)) }, [week])
  if (err) return <p className="empty">{err}</p>
  if (!data) return <p className="empty">Loading team profiles…</p>
  const t = data.teams[sel]; if (!t) return null
  const o = t.offense, d = t.defense
  const row = (label: string, cur?: number | null, prev?: number | null, fmt = f1, invert = false, note?: string) => (
    <tr><td className="l">{label}{note && <small className="meta"> {note}</small>}</td><td><Delta cur={cur} prev={prev} fmt={fmt} invert={invert} /></td></tr>)
  return (
    <div>
      <div className="slate">{Object.keys(data.teams).map(k => <div key={k} className={'chip' + (sel === k ? ' on' : '')} onClick={() => setSel(k)}><b>{k}</b>{data.teams[k].next ? `${data.teams[k].next!.home ? 'vs' : '@'} ${data.teams[k].next!.opp}` : 'bye'}</div>)}</div>
      <header><h1>{sel} <small>{t.next ? `${t.next.home ? 'vs' : '@'} ${t.next.opp} · implied ${t.next.implied.toFixed(1)} · O/U ${t.next.total} · ${t.next.spread > 0 ? 'favored by ' + t.next.spread : 'dog by ' + (-t.next.spread)}` : 'bye week'}</small></h1>
        <span className="meta">{season} through Week {week - 1} ({o.cur?.games ?? 0} gm) vs {season - 1} full season. ▲▼ = direction of change; green = more touchdown-friendly for this team's players.</span></header>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: 18 }}>
        <div className="tablewrap"><table style={{ minWidth: 0 }}><thead><tr><th className="l" colSpan={2}>Offense · identity</th></tr></thead><tbody>
          {row('Plays / game', o.cur?.plays, o.prev?.plays)}
          {row('Pass rate', o.cur?.pass_rate, o.prev?.pass_rate, pc)}
          {row('Neutral-script pass rate', o.cur?.neutral_pass_rate, o.prev?.neutral_pass_rate, pc, false, 'win prob 20–80%')}
          {row('Pass rate over expected', o.cur?.pass_oe, o.prev?.pass_oe, sg)}
          {row('Red zone trips / game', o.cur?.rz_trips, o.prev?.rz_trips)}
          {row('Red zone pass rate', o.cur?.rz_pass_rate, o.prev?.rz_pass_rate, pc, false, 'the "who scores" lever')}
          {row('Red zone TD %', o.cur?.rz_td_pct, o.prev?.rz_td_pct, pc)}
          {row('TDs / game', o.cur?.td_pg, o.prev?.td_pg)}
          {row('Expected TDs / game', o.cur?.xtd_pg, o.prev?.xtd_pg, f2)}
        </tbody></table></div>
        <div className="tablewrap"><table style={{ minWidth: 0 }}><thead><tr><th className="l" colSpan={2}>Defense · what it gives up</th></tr></thead><tbody>
          {row('TDs allowed / game', d.cur?.td_allowed, d.prev?.td_allowed, f1)}
          {row('Expected TDs allowed / game', d.cur?.xtd_allowed, d.prev?.xtd_allowed, f2)}
          {row('TDs over expected', d.cur?.td_over_x, d.prev?.td_over_x, sg, false, 'regression flag')}
          {row('Red zone trips faced / game', d.cur?.rz_trips, d.prev?.rz_trips)}
          {row('Red zone TD % allowed', d.cur?.rz_td_pct, d.prev?.rz_td_pct, pc, false, 'top model feature')}
          {row('Pass TDs / game', d.cur?.pass_td, d.prev?.pass_td, f2)}
          {row('Rush TDs / game', d.cur?.rush_td, d.prev?.rush_td, f2)}
          {row('TDs to RB / WR / TE', undefined, undefined, () => `${f2(d.cur?.td_RB)} / ${f2(d.cur?.td_WR)} / ${f2(d.cur?.td_TE)}`)}
          <tr><td className="l"><small className="meta">{season - 1}: RB / WR / TE</small></td><td className="meta">{f2(d.prev?.td_RB)} / {f2(d.prev?.td_WR)} / {f2(d.prev?.td_TE)}</td></tr>
        </tbody></table></div>
      </div>
      {t.callouts && t.callouts.length > 0 && <><h2 style={{ marginTop: 22, fontSize: 24 }}>Call-outs</h2><div className="block">{t.callouts.map((s, i) => <div key={i} className={'co ' + (/rising|due|newly|opened/.test(s) ? 'pos' : /falling|risk|Out|Doubtful|wind|cold/.test(s) ? 'neg' : '')}>{s}</div>)}</div></>}
      <h2 style={{ marginTop: 22, fontSize: 24 }}>Who owns the touchdown work</h2>
      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(360px,1fr))', gap: 18 }}>
        {(['cur', 'prev'] as const).map(k => (
          <div key={k} className="tablewrap"><table style={{ minWidth: 0 }}>
            <thead><tr><th className="l">{k === 'cur' ? `${season} to date` : `${season - 1}`}</th><th title="Share of the team's expected touchdowns that came from this player's touches">Expected TD share</th><th title="Share of the team's targets inside the 20">Red zone target share</th><th title="Share of the team's throws into the end zone">End zone target share</th><th title="Share of the team's carries inside the 5">Goal-line carry share</th><th title="Touchdowns scored">TD</th><th title="Expected touchdowns from where his touches happened">xTD</th></tr></thead>
            <tbody>{t.roles[k].map(r => <tr key={r.gsis_id}><td className="l"><span className="player">{r.display_name}<small>{r.position}</small></span></td><td className="num">{pc(r.xtd_share)}</td><td>{pc(r.rz_tgt_share)}</td><td>{pc(r.ez_tgt_share)}</td><td>{pc(r.i5_carry_share)}</td><td>{r.td}</td><td>{f2(r.xtd)}</td></tr>)}</tbody>
          </table></div>))}
      </div>
    </div>)
}
