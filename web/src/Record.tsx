import { API } from './api'
import { useEffect, useState } from 'react'
type Sum = { market: string; n: number; won: number; lost: number; pnl: number; clv: number | null; avg_p: number }
type Pick = { player: string; team: string; opp: string; market: string; line: number | null; price_taken: number; p_model: number; closing_price: number | null; result: string; pnl_units: number; clv: number | null; game_id: string }
const fmt = (o: number | null) => o == null ? '—' : (o > 0 ? '+' : '') + o
export default function Record() {
  const [d, setD] = useState<{ summary: Sum[]; recent: Pick[] } | null>(null); const [err, setErr] = useState('')
  useEffect(() => { fetch(`${API}/api/record/public`).then(r => r.json()).then(setD).catch(e => setErr(String(e))) }, [])
  if (err) return <p className="empty">{err}</p>
  if (!d) return <p className="empty">Loading record…</p>
  return (<div>
    <div className="bar"><h1>Record</h1><span className="muted">every graded pick, at the price taken. CLV = closing-line value: how much better than the closing price we bet, in probability points. Positive over 60+ picks is the real evidence of edge; win rate alone isn't.</span></div>
    {d.summary.length === 0 ? <p className="empty">No graded picks yet — first results post the Tuesday after Week 2. Every pick is graded at the price taken; nothing is deleted.</p> : <div className="tablewrap"><table ><thead><tr><th className="l">Market</th><th>Picks</th><th>Won</th><th>Lost</th><th>Hit %</th><th>Units</th><th>Avg CLV</th><th>Avg model P</th></tr></thead>
      <tbody>{d.summary.map(s => <tr key={s.market}><td className="l">{s.market}</td><td>{s.n}</td><td>{s.won}</td><td>{s.lost}</td><td>{Math.round(100 * s.won / Math.max(1, s.n))}%</td><td className={'num ' + (s.pnl >= 0 ? 'edge pos' : 'edge neg')}>{s.pnl >= 0 ? '+' : ''}{s.pnl}</td><td>{s.clv == null ? '—' : (s.clv >= 0 ? '+' : '') + (s.clv * 100).toFixed(1) + ' pts'}</td><td>{Math.round(s.avg_p * 100)}%</td></tr>)}</tbody></table></div>}
    {d.recent.length > 0 && <><h2 style={{ marginTop: 20, fontSize: 22 }}>Recent picks</h2><div className="tablewrap"><table ><thead><tr><th className="l">Player</th><th className="l">Game</th><th>Market</th><th>Line</th><th>Taken</th><th>Model</th><th>Close</th><th>Result</th><th>Units</th><th>CLV</th></tr></thead>
      <tbody>{d.recent.map((p, i) => <tr key={i}><td className="l">{p.player}</td><td className="l">{p.team} vs {p.opp}</td><td>{p.market}</td><td>{p.line ?? '—'}</td><td>{fmt(p.price_taken)}</td><td>{Math.round(p.p_model * 100)}%</td><td>{fmt(p.closing_price)}</td><td className={p.result === 'won' ? 'edge pos' : p.result === 'lost' ? 'edge neg' : 'meta'}>{p.result}</td><td>{p.pnl_units == null ? '—' : (p.pnl_units >= 0 ? '+' : '') + p.pnl_units.toFixed(2)}</td><td>{p.clv == null ? '—' : (p.clv >= 0 ? '+' : '') + (p.clv * 100).toFixed(1)}</td></tr>)}</tbody></table></div></>}
  </div>)
}
