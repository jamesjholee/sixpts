import { API } from './api'
import { useEffect, useState } from 'react'
type Sum = { market: string; n: number; won: number; lost: number; pnl: number; clv: number | null; avg_p: number }
type Pick = { player: string; team: string; opp: string; market: string; line: number | null; price_taken: number; p_model: number; closing_price: number | null; result: string; pnl_units: number; clv: number | null; game_id: string }
const fmt = (o: number | null) => o == null ? '—' : (o > 0 ? '+' : '') + o
export default function Record() {
  const [d, setD] = useState<{ summary: Sum[]; recent: Pick[] } | null>(null); const [err, setErr] = useState('')
  useEffect(() => { fetch(`${API}/api/record/public`).then(r => r.json()).then(setD).catch(e => setErr(String(e))) }, [])
  const [cards, setCards] = useState<any[]>([])
  useEffect(() => { fetch(`${API}/api/scorecards`).then(r => r.json()).then(async (d) => {
    const all = await Promise.all((d.weeks || []).map((w: number) => fetch(`${API}/api/scorecard/${w}`).then(r => r.ok ? r.json() : null)))
    setCards(all.filter(Boolean).reverse()) }).catch(() => {}) }, [])
  if (err) return <p className="empty">{err}</p>
  if (!d) return <p className="empty">Loading record…</p>
  return (<div>
    <div className="bar"><h1>Record</h1><span className="muted">every graded pick, at the price taken. CLV = closing-line value: how much better than the closing price we bet, in probability points. Positive over 60+ picks is the real evidence of edge; win rate alone isn't.</span></div>
    {cards.length > 0 && <div className="panel"><div className="head"><h2>How the model did</h2><span className="muted">every player on the published board, graded against what happened</span></div>
      <div className="body">{cards.map(c => <div key={c.week} style={{ padding: '12px 0', borderBottom: '1px solid var(--line)' }}>
        <h3 style={{ marginBottom: 8 }}>Week {c.week}</h3>
        <p className="muted" style={{ margin: '0 0 8px' }}>{c.n_players} players who took a snap · {Math.round(c.actual_rate * 100)}% actually scored · model averaged {Math.round(c.mean_prediction * 100)}% · top 20 by P(TD): <b>{Math.round(c.top20_hit_rate * 20)}/20</b> scored (expected {c.top20_expected ? (c.top20_expected * 20).toFixed(1) : '—'})</p>
        <table><thead><tr><th className="l">We said</th><th>Players</th><th>Predicted</th><th>Actually scored</th></tr></thead>
          <tbody>{c.calibration.map((b: any) => <tr key={b.bucket}><td className="l">{b.bucket}</td><td className="num">{b.n}</td><td className="num">{Math.round(b.predicted * 100)}%</td><td className="num"><b className={Math.abs(b.actual - b.predicted) <= .07 ? 'pos' : ''}>{Math.round(b.actual * 100)}%</b></td></tr>)}</tbody></table>
        <div className="two" style={{ marginTop: 10 }}>
          <div><h3>Top 20 by P(TD)</h3>{c.top20.slice(0, 10).map((r: any, i: number) => <div key={i} className="co">{r.scored ? '✓' : '·'} {r.player} <span className="muted">{r.team} vs {r.opp} · {Math.round(r.p_model * 100)}%{r.tds > 1 ? ` · ${r.tds} TD` : ''}</span></div>)}</div>
          <div><h3>Scored anyway (the tail)</h3>{c.scored_despite_low.slice(0, 6).map((r: any, i: number) => <div key={i} className="co neg">{r.player} <span className="muted">{r.team} · we said {Math.round(r.p_model * 100)}% · {r.tds} TD</span></div>)}
            <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>Backups and tight ends scoring on one or two touches are the part no role-based model catches. It's why long shots stay long shots.</p></div>
        </div></div>)}</div></div>}
    {d.summary.length === 0 ? <p className="empty">No graded picks yet — first results post the Tuesday after Week 2. Every pick is graded at the price taken; nothing is deleted.</p> : <div className="tablewrap"><table ><thead><tr><th className="l">Market</th><th>Picks</th><th>Won</th><th>Lost</th><th>Hit %</th><th>Units</th><th>Avg CLV</th><th>Avg model P</th></tr></thead>
      <tbody>{d.summary.map(s => <tr key={s.market}><td className="l">{s.market}</td><td>{s.n}</td><td>{s.won}</td><td>{s.lost}</td><td>{Math.round(100 * s.won / Math.max(1, s.n))}%</td><td className={'num ' + (s.pnl >= 0 ? 'edge pos' : 'edge neg')}>{s.pnl >= 0 ? '+' : ''}{s.pnl}</td><td>{s.clv == null ? '—' : (s.clv >= 0 ? '+' : '') + (s.clv * 100).toFixed(1) + ' pts'}</td><td>{Math.round(s.avg_p * 100)}%</td></tr>)}</tbody></table></div>}
    {d.recent.length > 0 && <><h2 style={{ marginTop: 20, fontSize: 22 }}>Recent picks</h2><div className="tablewrap"><table ><thead><tr><th className="l">Player</th><th className="l">Game</th><th>Market</th><th>Line</th><th>Taken</th><th>Model</th><th>Close</th><th>Result</th><th>Units</th><th>CLV</th></tr></thead>
      <tbody>{d.recent.map((p, i) => <tr key={i}><td className="l">{p.player}</td><td className="l">{p.team} vs {p.opp}</td><td>{p.market}</td><td>{p.line ?? '—'}</td><td>{fmt(p.price_taken)}</td><td>{Math.round(p.p_model * 100)}%</td><td>{fmt(p.closing_price)}</td><td className={p.result === 'won' ? 'edge pos' : p.result === 'lost' ? 'edge neg' : 'meta'}>{p.result}</td><td>{p.pnl_units == null ? '—' : (p.pnl_units >= 0 ? '+' : '') + p.pnl_units.toFixed(2)}</td><td>{p.clv == null ? '—' : (p.clv >= 0 ? '+' : '') + (p.clv * 100).toFixed(1)}</td></tr>)}</tbody></table></div></>}
  </div>)
}
