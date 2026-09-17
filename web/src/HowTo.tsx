import { useState } from 'react'
export default function HowTo() {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('sixpts_howto') !== '0' } catch { return true } })
  const toggle = () => setOpen(o => { try { localStorage.setItem('sixpts_howto', o ? '0' : '1') } catch {} ; return !o })
  return (<div className="panel">
    <div className="head" onClick={toggle}><h2>How to read this</h2><span className="caret">{open ? '▾' : '▸'}</span></div>
    {open && <div className="body howto">
      <p><b>P(TD)</b> is our probability that the player scores a touchdown, from where his touches happen, blended with last season, adjusted for the game and the defense. <b>Fair</b> is that probability as odds with no vig.</p>
      <p><b>Book</b> — type the price your sportsbook is offering. <b>Edge</b> is our probability minus the book's; 3+ points is flagged. The SixPts picks panel then reasons over every price you've entered: <b>Bet</b> needs 5+ points of edge and nearly every signal green, <b>Lean</b> has edge but mixed signals, <b>Pass</b> says exactly why.</p>
      <p>Tap any row for the four things behind the number: his role, his offense, the defense, the game. <b>Notes</b> call out role changes, injuries, and hot streaks that won't last (a 5-of-5 on 2 expected is a streak, not a role).</p>
      <p className="muted">This is research, not advice. Numbers are early-season estimates that get sharper every week; the Record tab shows how they've actually done. 21+. If gambling is a problem for you or someone you know, call 1-800-GAMBLER.</p>
    </div>}
  </div>)
}
