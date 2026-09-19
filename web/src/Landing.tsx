import { useEffect, useRef, useState } from 'react'
import { API } from './api'

/** Measured on 2021–25 play-by-play: the chance a touch becomes a touchdown, by where it happens. */
const RUSH = [
  { yard: '20', p: 0.045 }, { yard: '10', p: 0.136 }, { yard: '5', p: 0.260 },
  { yard: '2', p: 0.383 }, { yard: '1', p: 0.533 },
]
const PASS = [
  { where: 'Caught 20+ yards out', p: 0.013 }, { where: 'Caught 11–20 out', p: 0.051 },
  { where: 'Caught 6–10 out', p: 0.115 }, { where: 'Caught 1–5 out', p: 0.281 },
  { where: 'Caught in the end zone', p: 0.405 },
]

type Card = { week: number; n_players: number; actual_rate: number; mean_prediction: number; top20_hit_rate: number; calibration: { bucket: string; n: number; predicted: number; actual: number }[] }

export default function Landing({ onEnter, entered = false }: { onEnter: () => void; entered?: boolean }) {
  const [card, setCard] = useState<Card | null>(null)
  const [drawn, setDrawn] = useState(false)
  const field = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch(`${API}/api/scorecards`).then(r => r.json())
      .then(d => { const w = (d.weeks || []).slice(-1)[0]; if (w) return fetch(`${API}/api/scorecard/${w}`).then(r => r.json()).then(setCard) })
      .catch(() => {})
  }, [])
  useEffect(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce) return setDrawn(true)
    const t = setTimeout(() => setDrawn(true), 120); return () => clearTimeout(t)
  }, [])

  const pct = (x: number) => Math.round(x * 100) + '%'

  return (
    <div className="lp">
      <header className="lp-top">
        <div className="lp-mark">SixPts</div>
        <button className="lp-enter" onClick={onEnter}>{entered ? 'Back to the board' : 'Open the board'}</button>
      </header>

      <section className="lp-hero">
        <h1>A touchdown is mostly a question<br />of where the ball already is.</h1>
        <div className="lp-field" ref={field} aria-label="Chance a carry becomes a touchdown, by yard line">
          <div className="lp-bars">
            {RUSH.map((r, i) => (
              <div className="lp-bar" key={r.yard}>
                <span className="lp-val">{pct(r.p)}</span>
                <i style={{ height: drawn ? `${r.p * 150}px` : 0, transitionDelay: `${i * 90}ms` }} />
                <span className="lp-yard">{r.yard}</span>
              </div>
            ))}
            <div className="lp-bar lp-goal"><span className="lp-yard">goal</span></div>
          </div>
          <p className="lp-caption">A handoff from the 1 scores more than half the time. The same handoff from the 20 scores 4.5% of the time. Every model that ignores this is guessing.</p>
        </div>
        <p className="lp-lede">
          SixPts gives every skill player a touchdown probability built from where his touches actually happen,
          blended with last season and adjusted for his offense, the defense he's facing, and the game itself.
          Then it puts that number next to the price your sportsbook is offering.
        </p>
      </section>

      <section className="lp-proof">
        <h2>We publish what we said, then what happened.</h2>
        <p className="lp-body">
          Every tool in this category shows you hit rates. Almost none show you what they predicted beforehand,
          because that record is uncomfortable. Here is ours{card ? `, from week ${card.week}` : ''}.
        </p>
        {card ? (
          <>
            <table className="lp-table">
              <thead><tr><th>We said</th><th>Players</th><th>They actually scored</th></tr></thead>
              <tbody>
                {card.calibration.map(b => {
                  const off = Math.abs(b.actual - b.predicted) > 0.07
                  return (
                    <tr key={b.bucket}>
                      <td>{b.bucket}</td>
                      <td className="lp-num">{b.n}</td>
                      <td className={'lp-num' + (off ? ' lp-off' : '')}>{pct(b.actual)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <p className="lp-body lp-admit">
              Read the third row. We said 24% for that group and 40% of them scored — we were too cautious,
              and we've left it visible rather than quietly retuning. Of {card.n_players} players who took a snap,
              {' '}{pct(card.actual_rate)} found the end zone; our twenty highest numbers produced{' '}
              {Math.round(card.top20_hit_rate * 20)} scorers.
            </p>
          </>
        ) : (
          <p className="lp-body">The week-by-week record loads from the live board.</p>
        )}
      </section>

      <section className="lp-how">
        <h2>Three questions, in this order.</h2>
        <div className="lp-cols">
          <div>
            <h3>Is the ball going to him near the goal line?</h3>
            <p>Red zone targets, carries inside the five, end zone looks, snap share. A player with one lucky target does not get treated like a starter — the estimate is weighted by how many touches it rests on.</p>
          </div>
          <div>
            <h3>Does this defense give those up?</h3>
            <p>What share of red zone trips against them end in a touchdown, and whether they leak to backs, receivers or tight ends. Measured from play-by-play, not reputation.</p>
          </div>
          <div>
            <h3>Is the price better than the number?</h3>
            <p>Prices from DraftKings, FanDuel, BetMGM and Caesars sit on every row. When four books disagree sharply with us on a player with thin usage, we say so and pass instead of calling it an edge.</p>
          </div>
        </div>
        <div className="lp-passes">
          {PASS.map(p => (
            <div key={p.where} className="lp-pass">
              <span className="lp-pass-p">{pct(p.p)}</span>
              <span className="lp-pass-w">{p.where}</span>
            </div>
          ))}
          <p className="lp-caption">Chance a target becomes a touchdown, by where it's caught.</p>
        </div>
      </section>

      <section className="lp-cta">
        <h2>Free, and it argues with you.</h2>
        <p className="lp-body">
          The board, the team pages and the record are open. Type in the price you're being offered and the model
          will tell you to bet it, lean it, or pass — and why.{entered ? '' : ' You must be 21 or older to continue.'}
        </p>
        <button className="lp-enter lp-big" onClick={onEnter}>{entered ? 'Back to the board' : 'Open the board'}</button>
        <p className="lp-fine">
          SixPts is a research tool. It doesn't take bets and it isn't advice. Gambling problem? Call 1-800-GAMBLER.
          Data from nflverse and The Odds API.
        </p>
      </section>
    </div>
  )
}
