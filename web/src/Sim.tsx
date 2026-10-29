import { useEffect, useMemo, useRef, useState } from 'react'
import { API } from './api'

// The Sim view: 5,000 simulated games per matchup, shown as tiles, a top-scorers table, and a replay of the one
// game nearest the median score — animated live on a canvas from the play log (same choreography as the video
// renderer in engine/replay.py, so the site and the social clips tell the same story).

type Play = { q: number; clock: number; off: string | null; down: number; dist: number; from_yl: number; to_yl: number; type: string; player: string | null; pos: string | null; air: number; gain: number; result: string; fg_dist: number; score_home: number; score_away: number }
type Player = { gsis_id: string; player: string; team: string; position: string; p_td: number; p_2td: number; rec: number; rec_yds: number; car: number; rush_yds: number; rec_q: number[]; rec_yds_q: number[]; rush_yds_q: number[]; p_gbm: number | null }
type Game = { home: string; away: string; n: number; p_home: number; p_away: number; p_tie: number; median_home: number; median_away: number; mean_total: number; p_ot: number; q_home: number[]; q_away: number[]; p_home_cover: number; spread: number; p_over: number; total: number; qb: { team: string; att: number; cmp: number; yds: number; td: number; inter: number; yds_q: number[] }[]; kickoff: string; players: Player[]; replay?: Record<string, string> | null; log?: { plays: Play[] } }

const pc = (x?: number | null) => x == null ? '—' : Math.round(x * 100) + '%'
const TEAMS: Record<string, [string, string, string]> = { ARI: ['97233F', 'FFB612', 'Cardinals'], ATL: ['A71930', 'A5ACAF', 'Falcons'], BAL: ['241773', '9E7C0C', 'Ravens'], BUF: ['00338D', 'C60C30', 'Bills'], CAR: ['0085CA', 'BFC0BF', 'Panthers'], CHI: ['0B162A', 'C83803', 'Bears'], CIN: ['FB4F14', 'FFFFFF', 'Bengals'], CLE: ['FF3C00', '311D00', 'Browns'], DAL: ['003594', '869397', 'Cowboys'], DEN: ['FB4F14', '002244', 'Broncos'], DET: ['0076B6', 'B0B7BC', 'Lions'], GB: ['203731', 'FFB612', 'Packers'], HOU: ['03202F', 'A71930', 'Texans'], IND: ['002C5F', 'A2AAAD', 'Colts'], JAX: ['006778', 'D7A22A', 'Jaguars'], KC: ['E31837', 'FFB81C', 'Chiefs'], LV: ['A5ACAF', '000000', 'Raiders'], LAC: ['0080C6', 'FFC20E', 'Chargers'], LA: ['003594', 'FFA300', 'Rams'], MIA: ['008E97', 'FC4C02', 'Dolphins'], MIN: ['4F2683', 'FFC62F', 'Vikings'], NE: ['002244', 'C60C30', 'Patriots'], NO: ['D3BC8D', '101820', 'Saints'], NYG: ['0B2265', 'A71930', 'Giants'], NYJ: ['125740', 'FFFFFF', 'Jets'], PHI: ['004C54', 'A5ACAF', 'Eagles'], PIT: ['FFB612', '101820', 'Steelers'], SF: ['AA0000', 'B3995D', '49ers'], SEA: ['69BE28', '002244', 'Seahawks'], TB: ['D50A0A', 'FF7900', 'Buccaneers'], TEN: ['4B92DB', '0C2340', 'Titans'], WAS: ['5A1414', 'FFB612', 'Commanders'] }
const BULB = 'FFB020', SILVER = 'C9D1DB'
const rgb = (h: string) => [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16))
const lum = (h: string) => { const [r, g, b] = rgb(h).map(c => c / 255); return .2126 * r + .7152 * g + .0722 * b }
const dist = (a: string, b: string) => Math.hypot(...rgb(a).map((x, i) => x - rgb(b)[i]))
function teamColors(home: string, away: string): [string, string] {
  const cands = (t: string) => [...(TEAMS[t] ? [TEAMS[t][0], TEAMS[t][1]] : ['8794A5']).filter(c => lum(c) > .16), SILVER]
  let best: [string, string] = [SILVER, SILVER], score = -1
  for (const h of cands(home)) for (const a of cands(away)) { const d = Math.min(dist(h, a), dist(h, BULB), dist(a, BULB)) - (h === SILVER || a === SILVER ? 40 : 0) - (h === a ? 80 : 0); if (d > score) { best = [h, a]; score = d } }
  return best
}

// ---- choreography (mirror of engine/replay.py) ----
const FW = 53.3
const ease = (t: number) => { t = Math.min(Math.max(t, 0), 1); return t * t * (3 - 2 * t) }
const easeOut = (t: number) => { t = Math.min(Math.max(t, 0), 1); return 1 - (1 - t) ** 3 }
const lerp = (a: number, b: number, t: number) => a + (b - a) * t
function rngFor(seed: number) { let a = seed >>> 0; const u = () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296 }; const n = (sd = 1) => sd * Math.sqrt(-2 * Math.log(1 - u() + 1e-12)) * Math.cos(2 * Math.PI * u()); return { u, n, pick: <T,>(xs: T[]) => xs[Math.floor(u() * xs.length)], range: (a: number, b: number) => a + (b - a) * u() } }
const OFF = ['QB', 'RB', 'TE', 'WR1', 'WR2', 'WR3', 'OL1', 'OL2', 'OL3', 'OL4', 'OL5'], DEF = ['DL1', 'DL2', 'DL3', 'DL4', 'LB1', 'LB2', 'LB3', 'CB1', 'CB2', 'S1', 'S2']
type P = [number, number]; type Move = (t: number) => P; type Ball = (t: number) => [number, number, number]
const ACTION: Record<string, number> = { complete: .8, incomplete: .6, run: .72, sack: .7, int: 1.1, fumble: 1.05, punt: .85, 'field goal': .95, kneel: .35 }
const SETUP = .28, HOLD: Record<string, number> = { TD: 1.5, 'FG good': .8, turnover: .6, 'FG miss': .6, downs: .6 }

function choreograph(play: Play, nxt: Play | undefined, home: string, seed: number) {
  const R = rngFor(seed); const d = play.off === home ? 1 : -1
  const los = d === 1 ? 10 + play.from_yl : 110 - play.from_yl; const end = los + play.gain * d; const c = FW / 2
  const air = play.air || 0, typ = play.type
  const shotgun = R.u() < .55; const j = (s = .6) => R.n(s)
  const off: Record<string, P> = { QB: [los - (shotgun ? 5 : 1.4) * d, c + j(.3)], RB: [los - (shotgun ? 5.5 : 7) * d, c + (shotgun ? 1.5 : .5) + j()], TE: [los - .6 * d, c + 6.5 + j()], WR1: [los - .8 * d, c - 21 + j(1.2)], WR2: [los - .8 * d, c + 22 + j(1.2)], WR3: [los - 1.6 * d, c - 11 + j(1.2)] }
  for (let i = 0; i < 5; i++) off[`OL${i + 1}`] = [los - .7 * d, c - 4.8 + i * 2.4]
  const de: Record<string, P> = { DL1: [los + 1.2 * d, c - 5.5], DL2: [los + 1.2 * d, c - 1.8], DL3: [los + 1.2 * d, c + 1.8], DL4: [los + 1.2 * d, c + 5.5], LB1: [los + 4.5 * d, c - 6 + j()], LB2: [los + 4.8 * d, c + j()], LB3: [los + 4.5 * d, c + 6 + j()], CB1: [los + 6.5 * d, c - 20 + j()], CB2: [los + 6.5 * d, c + 21 + j()], S1: [los + 12 * d, c - 8 + j(1.5)], S2: [los + 12 * d, c + 8 + j(1.5)] }
  const hash = (s: string) => { let h = 0; for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h }
  const slot = ({ RB: 'RB', FB: 'RB', TE: 'TE', QB: 'QB' } as Record<string, string>)[play.pos || ''] || ['WR1', 'WR2', 'WR3'][hash(play.player || '') % 3]
  const moves: Record<string, Move> = {}
  const path = (pts: [number, number, number][]): Move => t => { for (let i = 0; i + 1 < pts.length; i++) { const [t0, x0, y0] = pts[i], [t1, x1, y1] = pts[i + 1]; if (t <= t1) { const u = t1 > t0 ? ease((t - t0) / (t1 - t0)) : 1; return [lerp(x0, x1, u), lerp(y0, y1, u)] } } const l = pts[pts.length - 1]; return [l[1], l[2]] }
  const weave = (f: Move, amp: number, k: number): Move => t => { const [x, y] = f(t); return [x, y + amp * Math.sin(k * t * Math.PI)] }
  const clampY = (y: number, m = 2) => Math.min(Math.max(y, m), FW - m)
  const qb = off.QB; const drop: P = [qb[0] - 2.5 * d, qb[1]]
  const catchY = clampY(off[slot][1] + (slot.startsWith('WR') ? R.n(3) : R.n(4)))
  const endY = clampY(lerp(catchY, c, .35) + R.n(4), 1.5)
  let ball: Ball = () => [los, c, 0]; let carrier: string | null = null
  const fly = (from: P, to: P, t0: number, t1: number, t: number, h: number): [number, number, number] => { const u = (t - t0) / (t1 - t0); return [lerp(from[0], to[0], u), lerp(from[1], to[1], u), Math.sin(Math.PI * u) * h] }
  if (typ === 'complete' || typ === 'incomplete' || typ === 'int') {
    const catchX = los + air * d, tThrow = .32, tCatch = .56, h = .4 + air / 25
    moves.QB = path([[0, ...qb], [.3, ...drop], [1, ...drop]])
    if (typ === 'complete') {
      moves[slot] = path([[0, ...off[slot]], [tCatch, catchX, catchY], [.95, end, endY], [1, end, endY]])
      ball = t => t < tThrow ? [...moves.QB(t), 0] : t < tCatch ? fly(drop, [catchX, catchY], tThrow, tCatch, t, h) : [...moves[slot](t), 0]; carrier = slot
    } else if (typ === 'incomplete') {
      const miss: P = [catchX + 1.5 * d, catchY + (R.u() < .5 ? 3 : -3)]
      moves[slot] = path([[0, ...off[slot]], [tCatch, catchX, catchY], [.8, catchX + 1.2 * d, catchY], [1, catchX + 1.2 * d, catchY]])
      ball = t => t < tThrow ? [...moves.QB(t), 0] : t < tCatch + .08 ? fly(drop, miss, tThrow, tCatch + .08, t, h) : [miss[0], miss[1], 0]
    } else {
      const picker = ['CB1', 'CB2', 'S1', 'S2'].reduce((a, b) => Math.abs(de[b][1] - catchY) < Math.abs(de[a][1] - catchY) ? b : a)
      moves[slot] = path([[0, ...off[slot]], [tCatch, catchX - 1.5 * d, catchY + 1.5], [1, catchX - 2 * d, catchY + 2]])
      moves[picker] = path([[0, ...de[picker]], [tCatch, catchX, catchY], [.95, end, endY], [1, end, endY]])
      ball = t => t < tThrow ? [...moves.QB(t), 0] : t < tCatch ? fly(drop, [catchX, catchY], tThrow, tCatch, t, h) : [...moves[picker](t), 0]; carrier = picker
    }
    for (const s of ['WR1', 'WR2', 'WR3', 'TE', 'RB']) if (!moves[s]) { const [x0, y0] = off[s]; const depth = s !== 'RB' ? R.range(6, 18) : R.range(2, 6); moves[s] = path([[0, x0, y0], [.6, x0 + depth * d, clampY(y0 + R.n(5))], [1, x0 + (depth + 3) * d, y0]]) }
  } else if (typ === 'run' || typ === 'fumble' || typ === 'kneel') {
    const tHand = .18
    moves.QB = typ !== 'kneel' ? path([[0, ...qb], [tHand, qb[0] - d, qb[1]], [1, qb[0] - 1.5 * d, qb[1] + 1]]) : path([[0, ...qb], [.5, qb[0] - 1.2 * d, qb[1]], [1, qb[0] - 1.2 * d, qb[1]]])
    if (typ === 'kneel') { ball = t => [...moves.QB(t), 0]; carrier = 'QB' }
    else {
      const rs = slot === 'QB' ? 'QB' : slot; const [x0, y0] = off[rs]; const holeY = c + R.pick([-7, -3, 0, 3, 7]) + R.n(1.5)
      const mid: P = [lerp(los, end, .45), lerp(holeY, endY, .3)]
      const pts: [number, number, number][] = rs !== 'QB' ? [[0, x0, y0], [tHand, qb[0] - .5 * d, lerp(y0, holeY, .3)], [.5, ...mid], [.93, end, endY], [1, end, endY]] : [[0, x0, y0], [.5, ...mid], [.93, end, endY], [1, end, endY]]
      moves[rs] = weave(path(pts), .8, 5)
      const base: Ball = rs !== 'QB' ? t => t < tHand ? [...moves.QB(t), 0] : [...moves[rs](t), 0] : t => [...moves[rs](t), 0]
      ball = base; carrier = rs
      if (typ === 'fumble') {
        const sc = ['LB1', 'LB2', 'LB3', 'S1', 'S2'].reduce((a, b) => Math.abs(de[b][1] - endY) < Math.abs(de[a][1] - endY) ? b : a)
        moves[sc] = path([[0, ...de[sc]], [.9, end + 1.5 * d, endY + 1], [1, end + 1.5 * d, endY + 1]])
        ball = t => t < .85 ? base(t) : [end + 1.5 * d * ease((t - .85) / .1), endY + ease((t - .85) / .1), .15 * Math.sin(Math.PI * Math.min(1, (t - .85) / .1))]
      }
    }
    for (const s of ['WR1', 'WR2', 'WR3', 'TE']) if (!moves[s]) { const [x0, y0] = off[s]; moves[s] = path([[0, x0, y0], [1, x0 + R.range(3, 8) * d, y0 + R.n(2)]]) }
  } else if (typ === 'sack') {
    const rusher = R.pick(['DL1', 'DL4', 'LB1', 'LB3'])
    moves.QB = path([[0, ...qb], [.35, qb[0] - 3 * d, qb[1] + R.n(1)], [.8, end, qb[1] + R.n(1.5)], [1, end, qb[1]]])
    moves[rusher] = path([[0, ...de[rusher]], [.8, end + .8 * d, qb[1] + .5], [1, end + .8 * d, qb[1] + .5]])
    ball = t => [...moves.QB(t), 0]; carrier = 'QB'
    for (const s of ['WR1', 'WR2', 'WR3', 'TE']) { const [x0, y0] = off[s]; moves[s] = path([[0, x0, y0], [1, x0 + R.range(8, 16) * d, y0 + R.n(3)]]) }
  } else if (typ === 'punt' || typ === 'field goal') {
    const kickX = los - (typ === 'punt' ? 13 : 7) * d; let land: number, landY: number
    if (typ === 'punt') { land = nxt && nxt.off && nxt.off !== play.off ? (nxt.off === home ? 10 + nxt.from_yl : 110 - nxt.from_yl) : Math.min(Math.max(los + 42 * d, 11), 109); landY = c + R.n(8) }
    else { land = d === 1 ? 120 : 0; const good = play.result === 'FG good'; landY = c + (good ? R.n(1.5) : R.pick([-1, 1]) * R.range(5, 9)) }
    moves.QB = path([[0, kickX - .5 * d, c], [.25, kickX + .5 * d, c], [1, kickX + d, c]])
    const tKick = .25, tLand = typ === 'punt' ? .9 : .8, h = typ === 'punt' ? 2 : 1.4
    ball = t => t < tKick ? [kickX, c, 0] : t < tLand ? [lerp(kickX, land, easeOut((t - tKick) / (tLand - tKick)) ** .9), lerp(c, landY, (t - tKick) / (tLand - tKick)), Math.sin(Math.PI * Math.min(1, (t - tKick) / (tLand - tKick))) * h] : [land, landY, 0]
    if (typ === 'punt') {
      const ret = landY < c ? 'S1' : 'S2'; moves[ret] = path([[0, land - 3 * d, c + R.n(3)], [tLand, land, landY], [1, land - 2 * d, landY]]); de[ret] = [land - 3 * d, c]
      for (const s of OFF) if (s !== 'QB') { const [x0, y0] = off[s]; moves[s] = path([[0, x0, y0], [1, land - 6 * d + R.n(3), lerp(y0, landY, .6)]]) }
    }
  }
  for (const s of DEF) {
    if (moves[s]) continue; const [x0, y0] = de[s]; const lag = R.range(.15, .4), gap = R.range(.8, 2.5)
    if (typ === 'punt' || typ === 'field goal') { const dx = R.range(-2, 1); moves[s] = path([[0, x0, y0], [1, x0 + dx * d, y0]]); continue }
    moves[s] = t => { if (!carrier) return [x0 + (s.startsWith('DL') ? 1.5 * d : 0) * ease(t), y0]; const [bx, by] = ball(Math.max(0, t - lag)); const u = ease(t / (1 - lag * .5)); return [lerp(x0, bx + gap * d, u), lerp(y0, by, u)] }
  }
  for (const s of OFF) if (!moves[s]) { const [x0, y0] = off[s]; moves[s] = path([[0, x0, y0], [1, x0 + (typ !== 'sack' ? 1 : -1.5) * d, y0]]) }
  return { off, de, moves, ball, carrier, d, los, end }
}

// ---- timeline: every play becomes [setup, action, hold] segments on one clock ----
type Seg = { i: number; t0: number; setup: number; action: number; hold: number; qcard?: boolean }
function timeline(plays: Play[], speed: number) {
  const segs: Seg[] = []; let t = 0, q = 1
  plays.forEach((p, i) => {
    if (p.q !== q) { segs.push({ i, t0: t, setup: 0, action: 0, hold: 1.1, qcard: true }); t += 1.1; q = p.q }
    const setup = SETUP / speed, action = (ACTION[p.type] ?? .7) / speed, hold = (HOLD[p.result] ?? .05) / Math.sqrt(speed)
    segs.push({ i, t0: t, setup, action, hold }); t += setup + action + hold
  })
  return { segs, total: t }
}

const ordinal = (n: number) => `${n}${['th', 'st', 'nd', 'rd'][(n % 100 > 10 && n % 100 < 14) ? 0 : Math.min(n % 10, 4) % 4] || 'th'}`
const clock = (s: number) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.max(0, s) % 60).padStart(2, '0')}`
const short = (n?: string | null) => { if (!n) return ''; const p = n.split(' '); return p.length < 2 ? n : `${p[0][0]}. ${p.slice(1).join(' ')}` }
function describe(p: Play, qb: Record<string, string>) {
  const who = short(p.player), g = p.gain, r = p.result, q = short(qb[p.off || ''] || 'QB')
  const yds = (n: number) => n === 0 ? 'no gain' : n > 0 ? `${n} yds` : `a loss of ${-n}`
  let s = p.type
  if (p.type === 'complete') s = r === 'TD' ? `${q} → ${who} · TOUCHDOWN` : `${q} → ${who} for ${yds(g)}`
  else if (p.type === 'incomplete') s = `Incomplete, intended for ${who}`
  else if (p.type === 'run') s = r === 'TD' ? `${who} runs it in · TOUCHDOWN` : `${who} runs for ${yds(g)}`
  else if (p.type === 'sack') s = `${q} sacked for ${yds(g)}`
  else if (p.type === 'int') s = g < 0 ? `INTERCEPTED · ${q} picked off, returned ${-g} yds` : `INTERCEPTED · ${q} picked off`
  else if (p.type === 'fumble') s = `${who} FUMBLES · ${p.off} turns it over`
  else if (p.type === 'punt') s = `${p.off} punts`
  else if (p.type === 'field goal') s = `${p.fg_dist}-yd field goal is ${r === 'FG good' ? 'GOOD' : 'NO GOOD'}`
  else if (p.type === 'kneel') s = `${q} kneels`
  if (r === 'downs') s += ' · turnover on downs'
  return s
}
const situation = (p: Play, home: string, away: string) => { if (!p.down) return ''; const yl = p.from_yl; const side = yl <= 50 ? p.off : (p.off === away ? home : away); const mark = yl <= 50 ? yl : 100 - yl; return `${ordinal(p.down)} & ${p.dist >= 100 - yl ? 'Goal' : p.dist} · ${side} ${mark}` }

// ---- the canvas player ----
function Replay({ game, gid }: { game: Game; gid: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const plays = useMemo(() => (game.log?.plays || []).filter(p => p.type !== 'final'), [game])
  const [speed, setSpeed] = useState(1.6)
  const [playing, setPlaying] = useState(true)
  const [pos, setPos] = useState(0)       // seconds on the timeline
  const [idx, setIdx] = useState(0)
  const tl = useMemo(() => timeline(plays, speed), [plays, speed])
  const [ch, ca] = useMemo(() => teamColors(game.home, game.away), [game])
  const qb = useMemo(() => { const o: Record<string, string> = {}; for (const t of [game.home, game.away]) { const qs = game.players.filter(p => p.position === 'QB' && p.team === t); o[t] = qs.sort((a, b) => b.car - a.car)[0]?.player || 'QB' } return o }, [game])
  const chor = useRef<{ i: number; c: ReturnType<typeof choreograph> } | null>(null)
  const last = useRef<Record<string, P>>({}); const trail = useRef<P[]>([]); const cam = useRef(60); const flash = useRef<{ side: 'home' | 'away'; at: number } | null>(null)
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const posRef = useRef(0); posRef.current = pos
  const logRef = useRef<HTMLOListElement>(null)
  useEffect(() => { const el = logRef.current?.children[idx] as HTMLElement | undefined; el?.scrollIntoView({ block: 'nearest' }) }, [idx])

  // clock
  useEffect(() => {
    if (!playing || reduced) return; let raf = 0, prev = performance.now()
    const step = (now: number) => { const dt = (now - prev) / 1000; prev = now; const nx = posRef.current + dt; if (nx >= tl.total) { setPos(tl.total); setPlaying(false); return } setPos(nx); raf = requestAnimationFrame(step) }
    raf = requestAnimationFrame(step); return () => cancelAnimationFrame(raf)
  }, [playing, tl.total, reduced])

  // draw
  useEffect(() => {
    const cv = ref.current; if (!cv || !plays.length) return
    const dpr = Math.min(2, window.devicePixelRatio || 1); const W = cv.clientWidth, vertical = W < 640; const H = vertical ? Math.round(W * 1.5) : Math.round(W * 0.5)
    if (cv.width !== W * dpr || cv.height !== H * dpr) { cv.width = W * dpr; cv.height = H * dpr }
    const c = cv.getContext('2d')!; c.setTransform(dpr, 0, 0, dpr, 0, 0)
    const seg = tl.segs.reduce((a, s) => pos >= s.t0 ? s : a, tl.segs[0]); const p = plays[seg.i]; if (seg.i !== idx) setIdx(seg.i)
    const prevScore: [number, number] = seg.i ? [plays[seg.i - 1].score_home, plays[seg.i - 1].score_away] : [0, 0]
    // layout
    const top = vertical ? 92 : 64, bottom = H - (vertical ? 74 : 56), margin = vertical ? 10 : 20
    const s = vertical ? (W - 2 * margin) / FW : Math.min((W - 2 * margin) / 120, (bottom - top) / FW); const span = vertical ? (bottom - top) / s : 120
    const px = (x: number, y: number): P => vertical ? [margin + y * s, (top + bottom) / 2 - (x - cam.current) * s] : [(W - 120 * s) / 2 + x * s, (top + bottom) / 2 - (y - FW / 2) * s]
    const clampCam = (x: number) => span < 120 ? Math.min(Math.max(x, span / 2), 120 - span / 2) : 60
    if (!chor.current || chor.current.i !== seg.i) { chor.current = { i: seg.i, c: choreograph(p, plays[seg.i + 1], game.home, seg.i * 7919 + 17) }; trail.current = [] }
    const K = chor.current.c; const local = pos - seg.t0
    const inSetup = seg.qcard ? false : local < seg.setup; const t = seg.qcard ? 0 : Math.min(1, Math.max(0, (local - seg.setup) / seg.action)); const holdU = seg.qcard ? 0 : Math.max(0, (local - seg.setup - seg.action) / Math.max(seg.hold, .01))
    const [bx, by, bh] = inSetup ? K.ball(0) : K.ball(t)
    cam.current += (clampCam(inSetup ? K.los + 12 * K.d : bx + 6 * K.d) - cam.current) * 0.12
    // background
    c.fillStyle = '#070A0F'; c.fillRect(0, 0, W, H)
    c.save(); c.beginPath(); c.rect(0, top, W, bottom - top); c.clip()
    const rect = (xa: number, xb: number, fill: string) => { const a = px(xa, 0), b = px(xb, FW); c.fillStyle = fill; c.fillRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])) }
    rect(0, 120, '#10201A'); for (let i = 10; i < 110; i += 10) if ((i / 10) % 2) rect(i, i + 10, 'rgba(255,255,255,.022)')
    rect(0, 10, `#${TEAMS[game.home]?.[0] || '1E2836'}42`); rect(110, 120, `#${TEAMS[game.away]?.[0] || '1E2836'}42`)
    c.lineWidth = Math.max(1, .12 * s); c.strokeStyle = 'rgba(255,255,255,.42)'
    for (let yd = 10; yd <= 110; yd += 5) { const a = px(yd, 0), b = px(yd, FW); c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke() }
    c.strokeStyle = 'rgba(255,255,255,.75)'; c.lineWidth = Math.max(1.5, .18 * s)
    for (const yd of [10, 110]) { const a = px(yd, 0), b = px(yd, FW); c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke() }
    c.fillStyle = 'rgba(255,255,255,.34)'; c.font = `800 ${2.6 * s}px Archivo, Inter, sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle'
    for (let yd = 20; yd <= 100; yd += 10) { const n = yd <= 60 ? yd - 10 : 110 - yd; for (const [hy, flip] of [[12, false], [41.3, true]] as [number, boolean][]) { const [x, y] = px(yd, hy); c.save(); c.translate(x, y); c.rotate(vertical ? (flip ? Math.PI / 2 : -Math.PI / 2) : flip ? Math.PI : 0); c.fillText(String(n), 0, 0); c.restore() } }
    c.font = `800 ${5 * s}px Archivo, Inter, sans-serif`; c.fillStyle = 'rgba(255,255,255,.5)'
    for (const [xa, team] of [[5, game.home], [115, game.away]] as [number, string][]) { const [x, y] = px(xa, FW / 2); c.save(); c.translate(x, y); if (!vertical) c.rotate(xa < 60 ? Math.PI / 2 : -Math.PI / 2); c.fillText((TEAMS[team]?.[2] || team).toUpperCase(), 0, 0); c.restore() }
    c.strokeStyle = '#FFB020'; c.lineWidth = Math.max(2, .22 * s); for (const yd of [0, 120]) { const a = px(yd, FW / 2 - 3.1), b = px(yd, FW / 2 + 3.1); c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke() }
    // LOS / first down
    const line = (x: number, col: string) => { const a = px(x, 0), b = px(x, FW); c.strokeStyle = col; c.lineWidth = Math.max(1.5, .16 * s); c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke() }
    line(K.los, 'rgba(76,141,255,.75)'); if (p.down && p.dist < 100 - p.from_yl) line(K.los + p.dist * K.d, 'rgba(255,176,32,.8)')
    // trail
    if (K.carrier && !inSetup) { const cp = K.moves[K.carrier](t); if (t < 1) trail.current.push(cp); trail.current.slice(-14).forEach((q, j, arr) => { const [x, y] = px(q[0], q[1]); c.fillStyle = `rgba(255,176,32,${(j + 1) / arr.length * .45})`; c.beginPath(); c.arc(x, y, .4 * s, 0, 7); c.fill() }) }
    // players
    const form = { ...K.off, ...K.de }; const u = inSetup ? ease(local / seg.setup) : 1
    for (const sl of [...OFF, ...DEF]) {
      const target = inSetup ? form[sl] : K.moves[sl](t); const from = last.current[sl] || form[sl]
      const x = inSetup ? lerp(from[0], target[0], u) : target[0], y = inSetup ? lerp(from[1], target[1], u) : target[1]
      if (!inSetup) last.current[sl] = [x, y]
      const offSide = OFF.includes(sl) === (p.off === game.home); const r = (sl.startsWith('OL') || sl.startsWith('DL') ? .66 : .78) * s * (vertical ? 1 : 1.15)
      const [X, Y] = px(x, y); c.fillStyle = 'rgba(7,10,15,.45)'; c.beginPath(); c.arc(X, Y + r * .25, r, 0, 7); c.fill()
      c.fillStyle = `#${offSide ? ch : ca}`; c.beginPath(); c.arc(X, Y, r, 0, 7); c.fill(); c.strokeStyle = 'rgba(255,255,255,.55)'; c.lineWidth = Math.max(1, r * .14); c.stroke()
      if (!inSetup && sl === K.carrier && t > .12) { c.strokeStyle = '#FFB020'; c.lineWidth = Math.max(1.5, r * .22); c.beginPath(); c.arc(X, Y, r * 1.9, 0, 7); c.stroke() }
    }
    // ball
    if (!(K.carrier && bh === 0 && t >= 1 && !inSetup)) { const [X, Y0] = px(bx, by); const Y = Y0 - bh * s * 1.6, r = .3 * s * (1 + .6 * bh); if (bh > 0) { c.fillStyle = 'rgba(7,10,15,.4)'; c.beginPath(); c.arc(X, Y0, .28 * s, 0, 7); c.fill() } c.fillStyle = '#B5642B'; c.beginPath(); c.arc(X, Y, r, 0, 7); c.fill(); c.strokeStyle = 'rgba(255,255,255,.8)'; c.lineWidth = Math.max(1, r * .2); c.stroke() }
    // banner
    const scoreNow: [number, number] = (t >= 1 && !inSetup) ? [p.score_home, p.score_away] : prevScore
    if (t >= 1 && !inSetup && ['TD', 'FG good', 'turnover', 'FG miss'].includes(p.result)) {
      const a = Math.min(1, holdU * 6) * (holdU < .8 ? 1 : Math.max(0, 1 - (holdU - .8) / .2)); const label = p.result === 'TD' ? 'TOUCHDOWN' : p.result === 'FG good' ? 'FIELD GOAL' : p.result === 'FG miss' ? 'NO GOOD' : 'TURNOVER'
      c.font = `800 ${vertical ? 44 : 56}px Archivo, Inter, sans-serif`; const w = c.measureText(label).width; const y = (top + bottom) / 2 - (vertical ? 60 : 0)
      c.fillStyle = `rgba(7,10,15,${.78 * a})`; c.fillRect(W / 2 - w / 2 - 18, y - 36, w + 36, 64); c.fillStyle = p.result === 'TD' || p.result === 'FG good' ? `rgba(255,176,32,${a})` : `rgba(242,85,90,${a})`; c.fillText(label, W / 2, y - 2)
    }
    if (seg.qcard) { c.fillStyle = 'rgba(7,10,15,.72)'; c.fillRect(0, top, W, bottom - top); c.fillStyle = '#FFB020'; c.font = `800 ${vertical ? 36 : 44}px Archivo, Inter, sans-serif`; c.fillText(plays[seg.i - 1].q === 2 ? 'HALFTIME' : `END OF ${ordinal(plays[seg.i - 1].q).toUpperCase()}`, W / 2, (top + bottom) / 2 - 20); c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 24 : 28}px Archivo, Inter, sans-serif`; c.fillText(`${game.away} ${prevScore[1]} · ${game.home} ${prevScore[0]}`, W / 2, (top + bottom) / 2 + 24) }
    c.restore()
    // scoreboard
    if (t >= 1 && !inSetup && (p.score_home !== prevScore[0] || p.score_away !== prevScore[1]) && (!flash.current || flash.current.at !== seg.t0)) flash.current = { side: p.score_home !== prevScore[0] ? 'home' : 'away', at: seg.t0 }
    const sb = (x: number, team: string, sc: number, col: string, side: 'home' | 'away') => {
      c.textAlign = 'center'; c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 16 : 15}px Archivo, Inter, sans-serif`; c.fillText(team, x, vertical ? 24 : 18)
      const fl = flash.current?.side === side && pos - flash.current.at < 2.2 && pos - seg.t0 > seg.setup + seg.action
      c.fillStyle = fl ? '#FFC247' : '#EEF2F6'; c.font = `800 ${vertical ? 38 : 30}px Archivo, Inter, sans-serif`; c.fillText(String(sc), x, vertical ? 62 : 46)
      c.fillStyle = `#${col}`; c.fillRect(x - 22, vertical ? 80 : 58, 44, 3)
      if (p.off === team && !seg.qcard) { c.fillStyle = '#FFB020'; c.beginPath(); c.arc(x + (side === 'away' ? -40 : 40), vertical ? 58 : 42, 4, 0, 7); c.fill() }
    }
    sb(vertical ? 70 : 110, game.away, scoreNow[1], ca, 'away'); sb(W - (vertical ? 70 : 110), game.home, scoreNow[0], ch, 'home')
    c.fillStyle = '#8794A5'; c.font = `600 ${vertical ? 12 : 12}px Inter, sans-serif`; c.fillText(p.q > 4 ? 'OT' : `${ordinal(p.q)} QTR`, W / 2, vertical ? 20 : 16)
    c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 24 : 22}px Archivo, Inter, sans-serif`; c.fillText(clock(p.clock), W / 2, vertical ? 46 : 38)
    c.fillStyle = '#FFB020'; c.font = `600 ${vertical ? 12 : 12}px Inter, sans-serif`; c.fillText(situation(p, game.home, game.away), W / 2, vertical ? 70 : 56)
    // caption
    const done = t > .5 && !inSetup; const line1 = done ? describe(p, qb) : situation(p, game.home, game.away); const colr = done ? (['TD', 'FG good'].includes(p.result) ? '#FFB020' : ['turnover', 'FG miss', 'downs'].includes(p.result) ? '#F2555A' : '#EEF2F6') : '#8794A5'
    c.textAlign = 'left'; c.fillStyle = colr; c.font = `800 ${vertical ? 17 : 20}px Archivo, Inter, sans-serif`; c.fillText(line1, margin + 4, bottom + (vertical ? 24 : 24), W - 2 * margin - 8)
    c.fillStyle = '#8794A5'; c.font = `500 ${vertical ? 12 : 13}px Inter, sans-serif`; c.fillText(`${p.q > 4 ? 'OT' : 'Q' + p.q} ${clock(p.clock)} · play ${seg.i + 1} of ${plays.length}`, margin + 4, bottom + (vertical ? 46 : 46))
  }, [pos, plays, tl, game, ch, ca, qb, idx])

  if (!plays.length) return <p className="empty">No play log for this game.</p>
  const jump = (i: number) => { const sg = tl.segs.find(x => x.i === i && !x.qcard); if (sg) { setPos(sg.t0); chor.current = null } }
  return (<div className="sim-player">
    <canvas ref={ref} style={{ width: '100%', display: 'block', borderRadius: 6, border: '1px solid var(--line)' }} aria-label={`Replay of the median simulated ${game.away} at ${game.home} game`} />
    <div className="sim-controls">
      <button className="btn" onClick={() => { if (pos >= tl.total) { setPos(0); chor.current = null; last.current = {} } setPlaying(!playing) }} aria-label={playing ? 'Pause' : 'Play'}>{playing ? '❚❚' : pos >= tl.total ? '↺' : '▶'}</button>
      <input type="range" min={0} max={tl.total} step={0.05} value={pos} onChange={e => { setPos(+e.target.value); chor.current = null }} aria-label="Scrub the replay" />
      <div className="seg">{[1, 1.6, 2.5].map(x => <button key={x} className={speed === x ? 'on' : ''} onClick={() => setSpeed(x)}>{x}×</button>)}</div>
    </div>
    <ol className="sim-log" aria-label="Play by play" ref={logRef}>
      {plays.map((q, i) => <li key={i} className={i === idx ? 'on' : ''} onClick={() => jump(i)} style={{ cursor: 'pointer' }}><span className="muted">{q.q > 4 ? 'OT' : 'Q' + q.q} {clock(q.clock)}</span> <span className={['TD', 'FG good'].includes(q.result) ? 'pos' : ['turnover', 'FG miss', 'downs'].includes(q.result) ? 'neg' : ''}>{describe(q, qb)}</span> <span className="muted">{q.score_away}–{q.score_home}</span></li>)}
    </ol>
    {reduced && <p className="muted" style={{ fontSize: 12 }}>Reduced motion is on: use the slider or click a play to step through.</p>}
  </div>)
}

export default function Sim({ week }: { week: number }) {
  const [list, setList] = useState<Record<string, Game> | null>(null); const [err, setErr] = useState(''); const [note, setNote] = useState('')
  const [sel, setSel] = useState<string>(() => new URLSearchParams(location.search).get('game') || '')
  const [full, setFull] = useState<Game | null>(null)
  useEffect(() => { setList(null); setErr(''); fetch(`${API}/api/sim/${week}`).then(async r => { if (!r.ok) throw new Error(`No simulations for week ${week} yet — they're built with the Tuesday board.`); return r.json() }).then(d => { setList(d.games); setNote(d.note || '') }).catch(e => setErr(e.message)) }, [week])
  const gid = list ? (sel && list[sel] ? sel : Object.keys(list)[0]) : ''
  useEffect(() => { if (!gid) return; setFull(null); fetch(`${API}/api/sim/${week}/${gid}`).then(r => r.json()).then(d => setFull(d.game)).catch(() => {}) }, [week, gid])
  if (err) return <p className="empty">{err}</p>
  if (!list || !gid) return <div className="sk-rows" aria-busy="true" aria-label="Loading simulations">{Array.from({ length: 4 }).map((_, i) => <div className="sk-row" key={i}><div className="skeleton" style={{ width: '55%' }} /><div className="skeleton" /><div className="skeleton" style={{ width: '80%' }} /></div>)}</div>
  const g = list[gid]; const top = [...g.players].sort((a, b) => b.p_td - a.p_td).slice(0, 10)
  const fav = g.spread < 0 ? `${g.home} ${g.spread}` : g.spread > 0 ? `${g.away} −${g.spread}` : 'pick'
  return (<div>
    <div className="games">{Object.entries(list).map(([id, x]) => <button key={id} className={'game' + (gid === id ? ' on' : '')} aria-pressed={gid === id} onClick={() => setSel(id)}><b>{x.away} @ {x.home}</b><small>{x.home} {pc(x.p_home)} · {Math.round(x.median_away)}–{Math.round(x.median_home)}</small></button>)}</div>
    <div className="bar"><h1>{g.away} @ {g.home}</h1><span className="muted">{fav} · O/U {g.total}</span>
      {g.replay?.['9x16'] && <a className="btn" style={{ marginLeft: 'auto' }} href={g.replay['9x16']} download>Download for social ↓</a>}</div>
    <div className="tiles">
      <div className="tile"><span className="tile-l">{g.home} wins</span><b>{pc(g.p_home)}</b><small>{g.away} {pc(g.p_away)} · tie {pc(g.p_tie)}</small></div>
      <div className="tile"><span className="tile-l">Median score</span><b>{g.away} {Math.round(g.median_away)}–{g.home} {Math.round(g.median_home)}</b><small>middle half {g.q_away[1]}–{g.q_away[3]} · {g.q_home[1]}–{g.q_home[3]}</small></div>
      <div className="tile"><span className="tile-l">Over {g.total}</span><b>{pc(g.p_over)}</b><small>sim total {g.mean_total.toFixed(1)}</small></div>
      <div className="tile"><span className="tile-l">{g.home} covers {g.spread > 0 ? '+' : ''}{g.spread}</span><b>{pc(g.p_home_cover)}</b><small>OT in {pc(g.p_ot)}</small></div>
    </div>
    <h2 style={{ margin: '18px 0 8px' }}>The median game <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>one of the {g.n.toLocaleString()} sims — the one that landed nearest the median score</span></h2>
    {full ? <Replay game={full} gid={gid} /> : <div className="skeleton" style={{ height: 320 }} />}
    <h2 style={{ margin: '22px 0 8px' }}>Who scores</h2>
    <table><thead><tr><th className="l">Player</th><th title="Share of the 5,000 simulated games with at least one TD">Sim TD</th><th title="Two or more">2+</th><th title="The model's calibrated P(TD) — the number the picks are built on">Model</th><th>Rec</th><th>Rec yds</th><th>Rush yds</th></tr></thead>
      <tbody>{top.map(p => <tr key={p.gsis_id}><td className="l">{p.player} <span className="muted">{p.team} {p.position}</span></td><td className="num"><b>{pc(p.p_td)}</b></td><td className="num">{pc(p.p_2td)}</td><td className="num">{pc(p.p_gbm)}</td><td className="num">{p.rec_q[1]}</td><td className="num">{p.rec_yds_q[1]} <span className="delta">{p.rec_yds_q[0]}–{p.rec_yds_q[2]}</span></td><td className="num">{p.rush_yds_q[1]} <span className="delta">{p.rush_yds_q[0]}–{p.rush_yds_q[2]}</span></td></tr>)}</tbody></table>
    <table style={{ marginTop: 12 }}><thead><tr><th className="l">QB line</th><th>Att</th><th>Cmp</th><th>Yds</th><th>TD</th><th>INT</th></tr></thead>
      <tbody>{g.qb.map(q => <tr key={q.team}><td className="l">{q.team}</td><td className="num">{q.att.toFixed(0)}</td><td className="num">{q.cmp.toFixed(0)}</td><td className="num">{q.yds_q[1]} <span className="delta">{q.yds_q[0]}–{q.yds_q[2]}</span></td><td className="num">{q.td.toFixed(1)}</td><td className="num">{q.inter.toFixed(1)}</td></tr>)}</tbody></table>
    <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>{note} The picks on the Board use the model column, not the sim: in the 2026 backtest the model ranks scorers a little better (AUC .688 vs .668). The sim is here for the game-level picture and the replay.</p>
  </div>)
}
