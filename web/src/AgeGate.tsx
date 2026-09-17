import { useState } from 'react'
export default function AgeGate({ children }: { children: React.ReactNode }) {
  const [ok, setOk] = useState(() => { try { return localStorage.getItem('sixpts_21') === '1' } catch { return false } })
  if (ok) return <>{children}</>
  return (<div className="gate">
    <div className="gate-box">
      <div className="wordmark"><i />SixPts</div>
      <h1 style={{ marginTop: 12 }}>Touchdown research, priced against the book.</h1>
      <p className="dim">SixPts is a research tool for NFL player props. It doesn't take bets and it isn't advice. You must be 21 or older (18+ where that's the legal age) to use it.</p>
      <button className="btn primary" onClick={() => { try { localStorage.setItem('sixpts_21', '1') } catch {} ; setOk(true) }}>I'm 21 or older — continue</button>
      <p className="muted" style={{ marginTop: 14, fontSize: 12 }}>Gambling problem? Call 1-800-GAMBLER (US). Play responsibly.</p>
    </div>
  </div>)
}
