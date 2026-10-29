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
  return { off, de, moves, ball, carrier, d, los, end, slot }
}

// ---- timeline: coin toss, kickoffs, plays, quarter cards on one clock (mirror of engine/replay.py PACE) ----
const PACE: Record<string, number> = { setup: .7, read: .55, complete: 1.55, incomplete: 1.25, run: 1.45, sack: 1.25, int: 2.1, fumble: 1.9, punt: 1.9, 'field goal': 1.9, kneel: .8, kickoff: 2.3, toss: 4.2, qcard: 2.4, td: 2.4, score: 1.5, turnover: 1.5 }
type Seg = { kind: 'toss' | 'kickoff' | 'play' | 'qcard'; i: number; t0: number; dur: number; setup: number; action: number; hold: number; q?: number }
function timeline(plays: Play[], speed: number) {
  const segs: Seg[] = []; let t = 0, q = 1; const sp = speed
  const add = (s: Omit<Seg, 't0'>) => { segs.push({ ...s, t0: t }); t += s.dur }
  if (plays.length) add({ kind: 'toss', i: 0, dur: PACE.toss / sp, setup: 0, action: 0, hold: 0 })
  plays.forEach((p, i) => {
    const prev = i ? plays[i - 1] : null
    if (p.q !== q) { add({ kind: 'qcard', i, dur: PACE.qcard / sp, setup: 0, action: 0, hold: 0, q }); q = p.q }
    const kicked = i === 0 || (prev && ['TD', 'FG good'].includes(prev.result) && prev.off !== p.off) || (prev && prev.q === 2 && p.q === 3)
    if (kicked) add({ kind: 'kickoff', i, dur: PACE.kickoff / sp, setup: 0, action: 0, hold: 0 })
    const act = (PACE[p.type] ?? 1.3) / sp, hold = (p.result === 'TD' ? PACE.td : p.result === 'FG good' ? PACE.score : ['turnover', 'FG miss', 'downs'].includes(p.result) ? PACE.turnover : PACE.read) / sp
    add({ kind: 'play', i, dur: PACE.setup / sp + act + hold, setup: PACE.setup / sp, action: act, hold })
  })
  return { segs, total: t }
}
function winProb(marginHome: number, secsLeft: number, spreadHome: number, possHome: boolean | null) {
  const f = Math.max(secsLeft / 3600, .004); const mu = -spreadHome * f + (possHome === null ? 0 : possHome ? 1.4 : -1.4) * f
  const z = (marginHome + mu) / (13.5 * Math.sqrt(f)); const erf = (x: number) => { const s = Math.sign(x); x = Math.abs(x); const t = 1 / (1 + .3275911 * x); const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - .284496736) * t + .254829592) * t * Math.exp(-x * x); return s * y }
  return .5 * (1 + erf(z / Math.SQRT2))
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
type Stats = { att: number; cmp: number; pyds: number; ptd: number; inter: number; rush: Record<string, { car: number; yds: number; td: number }>; recv: Record<string, { rec: number; yds: number; td: number }> }
const blankStats = (): Stats => ({ att: 0, cmp: 0, pyds: 0, ptd: 0, inter: 0, rush: {}, recv: {} })
function statsThrough(plays: Play[], n: number, home: string, away: string) {
  const S: Record<string, Stats> = { [home]: blankStats(), [away]: blankStats() }
  for (const p of plays.slice(0, n)) {
    const s = S[p.off || '']; if (!s) continue; const who = p.player || '—'; const td = p.result === 'TD' ? 1 : 0
    if (['complete', 'incomplete', 'int'].includes(p.type)) s.att++
    if (p.type === 'complete') { s.cmp++; s.pyds += p.gain; s.ptd += td; const r = s.recv[who] ||= { rec: 0, yds: 0, td: 0 }; r.rec++; r.yds += p.gain; r.td += td }
    if (p.type === 'int') s.inter++
    if (p.type === 'run' || p.type === 'fumble') { const r = s.rush[who] ||= { car: 0, yds: 0, td: 0 }; r.car++; r.yds += p.gain; r.td += td }
  }
  return S
}

function Replay({ game, gid }: { game: Game; gid: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const plays = useMemo(() => (game.log?.plays || []).filter(p => p.type !== 'final'), [game])
  const [speed, setSpeed] = useState(1)
  const [playing, setPlaying] = useState(true)
  const [pos, setPos] = useState(0)       // seconds on the timeline
  const [idx, setIdx] = useState(0)       // current play index
  const [done, setDone] = useState(0)     // plays fully finished (for the leader panels)
  const tl = useMemo(() => timeline(plays, speed), [plays, speed])
  const [ch, ca] = useMemo(() => teamColors(game.home, game.away), [game])
  const qb = useMemo(() => { const o: Record<string, string> = {}; for (const t of [game.home, game.away]) { const qs = game.players.filter(p => p.position === 'QB' && p.team === t); o[t] = qs.sort((a, b) => b.car - a.car)[0]?.player || 'QB' } return o }, [game])
  const nick = (t: string) => TEAMS[t]?.[2] || t
  const chor = useRef<{ i: number; c: ReturnType<typeof choreograph> } | null>(null)
  const last = useRef<Record<string, P>>({}); const trail = useRef<P[]>([]); const cam = useRef(60); const flash = useRef<{ side: 'home' | 'away'; at: number } | null>(null)
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const posRef = useRef(0); posRef.current = pos
  const logRef = useRef<HTMLOListElement>(null)
  useEffect(() => {  // keep the current play visible inside the log box only — never scroll the page
    const box = logRef.current; const el = box?.children[idx] as HTMLElement | undefined; if (!box || !el) return
    const top = el.offsetTop - box.offsetTop, bot = top + el.offsetHeight
    if (top < box.scrollTop) box.scrollTop = top; else if (bot > box.scrollTop + box.clientHeight) box.scrollTop = bot - box.clientHeight
  }, [idx])

  // clock
  useEffect(() => {
    if (!playing || reduced) return; let raf = 0, prev = performance.now()
    const step = (now: number) => { const dt = (now - prev) / 1000; prev = now; const nx = posRef.current + dt; if (nx >= tl.total) { setPos(tl.total); setPlaying(false); return } setPos(nx); raf = requestAnimationFrame(step) }
    raf = requestAnimationFrame(step); return () => cancelAnimationFrame(raf)
  }, [playing, tl.total, reduced])

  // current segment + derived state for the DOM parts
  const seg = tl.segs.reduce((a, s) => pos >= s.t0 ? s : a, tl.segs[0])
  const p = plays[seg?.i ?? 0]
  const local = pos - (seg?.t0 ?? 0)
  const inSetup = seg?.kind === 'play' && local < seg.setup
  const t = seg?.kind === 'play' ? Math.min(1, Math.max(0, (local - seg.setup) / seg.action)) : 0
  const finished = seg?.kind === 'play' && !inSetup && t >= 1
  const doneCount = seg ? (seg.kind === 'play' ? seg.i + (finished ? 1 : 0) : seg.i) : 0
  useEffect(() => { if (seg && seg.i !== idx) setIdx(seg.i) }, [seg?.i])
  useEffect(() => { if (doneCount !== done) setDone(doneCount) }, [doneCount])
  const stats = useMemo(() => statsThrough(plays, done, game.home, game.away), [plays, done, game])
  const score: [number, number] = !p ? [0, 0] : (seg.kind === 'play' && finished) ? [p.score_home, p.score_away] : seg.i ? [plays[seg.i - 1].score_home, plays[seg.i - 1].score_away] : [0, 0]
  const sit = p ? situation(p, game.home, game.away) : ''
  const [sitDown, sitSpot] = sit ? sit.split(' · ') : ['', '']

  // draw
  useEffect(() => {
    const cv = ref.current; if (!cv || !plays.length || !seg) return
    const dpr = Math.min(2, window.devicePixelRatio || 1); const W = cv.clientWidth, vertical = W < 640; const H = vertical ? Math.round(W * 1.45) : Math.round(W * 0.52)
    if (cv.width !== W * dpr || cv.height !== H * dpr) { cv.width = W * dpr; cv.height = H * dpr }
    const c = cv.getContext('2d')!; c.setTransform(dpr, 0, 0, dpr, 0, 0)
    const top = vertical ? 100 : 72, bottom = H - 10, margin = vertical ? 0 : 16
    const s = vertical ? (W - 2 * margin) / FW : Math.min((W - 2 * margin) / 120, (bottom - top) / FW); const span = vertical ? (bottom - top) / s : 120
    let flip = false
    const px = (x: number, y: number): P => vertical ? (flip ? [margin + (FW - y) * s, (top + bottom) / 2 + (x - cam.current) * s] : [margin + y * s, (top + bottom) / 2 - (x - cam.current) * s]) : [(W - 120 * s) / 2 + x * s, (top + bottom) / 2 - (y - FW / 2) * s]
    const clampCam = (x: number) => span < 120 ? Math.min(Math.max(x, span / 2), 120 - span / 2) : 60
    const scale = s
    // --- background + field
    c.fillStyle = '#070A0F'; c.fillRect(0, 0, W, H)
    const drawField = () => {
      c.save(); c.beginPath(); c.rect(0, top, W, bottom - top); c.clip()
      const rect = (xa: number, xb: number, fill: string) => { const a = px(xa, 0), b = px(xb, FW); c.fillStyle = fill; c.fillRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])) }
      rect(-3, 123, '#0B1511'); rect(0, 120, '#10201A'); for (let i = 10; i < 110; i += 10) if ((i / 10) % 2) rect(i, i + 10, 'rgba(255,255,255,.025)')
      rect(0, 10, `#${TEAMS[game.home]?.[0] || '1E2836'}4D`); rect(110, 120, `#${TEAMS[game.away]?.[0] || '1E2836'}4D`)
      const line = (x0: number, y0: number, x1: number, y1: number, col: string, w: number) => { const a = px(x0, y0), b = px(x1, y1); c.strokeStyle = col; c.lineWidth = w; c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke() }
      for (let yd = 10; yd <= 110; yd += 5) line(yd, 0, yd, FW, 'rgba(255,255,255,.4)', Math.max(1, .11 * s))
      for (let yd = 11; yd < 110; yd++) for (const hy of [.6, 23.6, 29, 52.7]) line(yd, hy, yd, hy + (hy < 26 ? .9 : -.9), 'rgba(255,255,255,.28)', 1)
      for (const yd of [10, 110]) line(yd, 0, yd, FW, 'rgba(255,255,255,.8)', Math.max(1.5, .18 * s))
      for (const yd of [0, 120]) line(yd, FW / 2 - 3.1, yd, FW / 2 + 3.1, '#FFB020', Math.max(2, .25 * s))
      c.fillStyle = 'rgba(255,255,255,.34)'; c.font = `800 ${2.6 * s}px Archivo, Inter, sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle'
      for (let yd = 20; yd <= 100; yd += 10) { const n = yd <= 60 ? yd - 10 : 110 - yd; for (const [hy, far] of [[41.3, false], [12, true]] as [number, boolean][]) { const [x, y] = px(yd, hy); c.save(); c.translate(x, y); c.rotate(vertical ? ((far ? Math.PI / 2 : -Math.PI / 2) * (flip ? -1 : 1)) : far ? Math.PI : 0); c.fillText(String(n), 0, 0); c.restore() } }
      c.font = `800 ${5 * s}px Archivo, Inter, sans-serif`; c.fillStyle = 'rgba(255,255,255,.45)'
      for (const [xa, team] of [[5, game.home], [115, game.away]] as [number, string][]) { const [x, y] = px(xa, FW / 2); c.save(); c.translate(x, y); if (!vertical) c.rotate(xa < 60 ? Math.PI / 2 : -Math.PI / 2); else if (flip) c.rotate(Math.PI); c.fillText(nick(team).toUpperCase(), 0, 0); c.restore() }
      const [mx, my] = px(60, FW / 2); c.strokeStyle = 'rgba(255,176,32,.18)'; c.lineWidth = Math.max(1, .3 * s); c.beginPath(); c.arc(mx, my, 6 * s, 0, 7); c.stroke()
      c.fillStyle = 'rgba(255,176,32,.22)'; c.font = `800 ${2.4 * s}px Archivo, Inter, sans-serif`; c.fillText('sixpts', mx, my)
      c.restore()
    }
    const dot = (x: number, y: number, col: string, offense: boolean, carrier = false, name: string | null = null) => {
      const [X, Y] = px(x, y); const r = .72 * scale * (vertical ? 1 : 1.1)
      c.fillStyle = 'rgba(7,10,15,.45)'; c.beginPath(); c.arc(X, Y + r * .3, r * 1.05, 0, 7); c.fill()
      if (offense) { c.fillStyle = `#${col}`; c.beginPath(); c.arc(X, Y, r, 0, 7); c.fill(); c.strokeStyle = 'rgba(255,255,255,.7)'; c.lineWidth = Math.max(1, r * .16); c.stroke() }
      else { c.fillStyle = 'rgba(7,10,15,.85)'; c.beginPath(); c.arc(X, Y, r, 0, 7); c.fill(); c.strokeStyle = `#${col}`; c.lineWidth = Math.max(2, r * .36); c.beginPath(); c.arc(X, Y, r * .78, 0, 7); c.stroke() }
      if (carrier) {
        c.strokeStyle = '#FFB020'; c.lineWidth = Math.max(1.5, r * .2); c.beginPath(); c.arc(X, Y, r * 1.75, 0, 7); c.stroke()
        if (name) { c.font = `700 ${Math.max(11, r * 1.05)}px Inter, sans-serif`; const w = c.measureText(name).width + r * 1.6; const rect = [X - w / 2, Y - r * 3.9, w, r * 1.9] as const; c.fillStyle = 'rgba(7,10,15,.86)'; c.beginPath(); c.roundRect(rect[0], rect[1], rect[2], rect[3], r * .5); c.fill(); c.strokeStyle = 'rgba(255,176,32,.9)'; c.lineWidth = 1.2; c.stroke(); c.fillStyle = '#EEF2F6'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(name, X, Y - r * 2.95) }
      }
    }
    const ball = (b: [number, number, number], path: P[] | null) => {
      const [x, y, h] = b; const [X, Y0] = px(x, y)
      if (path) { c.save(); c.setLineDash([5, 6]); c.strokeStyle = 'rgba(255,176,32,.55)'; c.lineWidth = 1.8; c.beginPath(); path.forEach((q, i) => i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1])); c.stroke(); c.restore() }
      if (h > 0) { c.fillStyle = 'rgba(7,10,15,.45)'; c.beginPath(); c.arc(X, Y0, .3 * scale, 0, 7); c.fill() }
      const Y = Y0 - h * scale * 1.7, r = .32 * scale * (1 + .5 * h)
      c.fillStyle = '#B5642B'; c.beginPath(); c.ellipse(X, Y, r * 1.25, r * .85, 0, 0, 7); c.fill(); c.strokeStyle = 'rgba(255,255,255,.85)'; c.lineWidth = Math.max(1, r * .22); c.stroke()
    }
    const flight = (bf: Ball, t0: number, t1: number): P[] => { const o: P[] = []; for (let k = 0; k <= 24; k++) { const tt = t0 + (t1 - t0) * k / 24; const [x, y, h] = bf(tt); const [X, Y] = px(x, y); o.push([X, Y - h * scale * 1.7]) } return o }
    const d0 = p ? (p.off === game.home ? 1 : -1) : 1
    // --- segments
    if (seg.kind === 'toss') {
      cam.current = 60; flip = false; drawField()
      const u = local / seg.dur; const [X, Y] = px(60, FW / 2); const r = 7 * s * (vertical ? 1 : 1.2); const spin = Math.min(1, u / .6)
      c.textAlign = 'center'; c.textBaseline = 'middle'
      if (spin < 1) { const w = Math.abs(Math.cos(spin * Math.PI * 9)) * r; const lift = Math.sin(spin * Math.PI) * 6 * s; c.fillStyle = '#FFB020'; c.beginPath(); c.ellipse(X, Y - lift, Math.max(1, w), r, 0, 0, 7); c.fill(); c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 20 : 24}px Archivo, Inter, sans-serif`; c.fillText('COIN TOSS', W / 2, Y - lift - r - 22) }
      else { const rcv = plays[0].off || game.home; c.fillStyle = '#FFB020'; c.beginPath(); c.arc(X, Y, r, 0, 7); c.fill(); c.fillStyle = '#0A0D12'; c.font = `800 ${r * .8}px Archivo, Inter, sans-serif`; c.fillText(rcv, X, Y); c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 22 : 26}px Archivo, Inter, sans-serif`; c.fillText(`${nick(rcv).toUpperCase()} WILL RECEIVE`, W / 2, Y + r + 30) }
    } else if (seg.kind === 'kickoff') {
      const rcv = p.off; const d = rcv === game.home ? 1 : -1; const kickX = d === 1 ? 75 : 45; const land = d === 1 ? 10 + p.from_yl : 110 - p.from_yl; const u = local / seg.dur
      flip = vertical && d === -1; cam.current += (clampCam(lerp(kickX, land, ease(u))) - cam.current) * .25; drawField()
      const tk = .18, tlnd = .72, cc = FW / 2
      const bf: Ball = tt => tt < tk ? [kickX, cc, 0] : tt < tlnd ? [lerp(kickX, land, easeOut((tt - tk) / (tlnd - tk)) ** .9), lerp(cc, cc + 3, (tt - tk) / (tlnd - tk)), Math.sin(Math.PI * Math.min(1, (tt - tk) / (tlnd - tk))) * 2.6] : [land, cc + 3, 0]
      const R = rngFor(seg.i * 131 + 7); const kcol = rcv === game.home ? ca : ch, rcol = rcv === game.home ? ch : ca
      c.save(); c.beginPath(); c.rect(0, top, W, bottom - top); c.clip()
      for (let i = 0; i < 11; i++) { const y0 = 4 + i * 4.6; const x0 = kickX - (i === 5 ? 3 : 1) * d; dot(lerp(x0, land - 4 * d + R.n(2), ease(Math.max(0, (u - tk) / (1 - tk)))), y0 + R.n(.3), kcol, false) }
      for (let i = 0; i < 10; i++) { const y0 = 6 + i * 4.8; const x0 = land - 12 * d + R.n(3); dot(lerp(x0, land - 6 * d, ease(Math.max(0, (u - .5) / .5))), y0, rcol, true) }
      dot(lerp(land - 2 * d, land, ease(Math.min(1, u / tlnd))), cc + 3, rcol, true, u > tlnd)
      ball(bf(u), u >= tk && u < tlnd ? flight(bf, tk, tlnd) : null); c.restore()
    } else if (seg.kind === 'qcard') {
      cam.current = 60; flip = false; drawField()
      c.fillStyle = 'rgba(7,10,15,.72)'; c.fillRect(0, top, W, bottom - top); c.textAlign = 'center'; c.textBaseline = 'middle'
      c.fillStyle = '#FFB020'; c.font = `800 ${vertical ? 34 : 44}px Archivo, Inter, sans-serif`; c.fillText(seg.q === 2 ? 'HALFTIME' : `END OF ${ordinal(seg.q!).toUpperCase()}`, W / 2, (top + bottom) / 2 - 20)
      c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 22 : 28}px Archivo, Inter, sans-serif`; c.fillText(`${game.away} ${score[1]} · ${game.home} ${score[0]}`, W / 2, (top + bottom) / 2 + 24)
    } else {
      if (!chor.current || chor.current.i !== seg.i) { chor.current = { i: seg.i, c: choreograph(p, plays[seg.i + 1], game.home, seg.i * 7919 + 17) }; trail.current = [] }
      const K = chor.current.c; const d = K.d
      const [bx, by, bh] = inSetup ? K.ball(0) : K.ball(t)
      flip = vertical && d === -1
      const tgt = clampCam(inSetup ? K.los + 10 * d : bx + 5 * d); cam.current = Math.abs(tgt - cam.current) < .3 ? tgt : cam.current + (tgt - cam.current) * .12
      drawField()
      c.save(); c.beginPath(); c.rect(0, top, W, bottom - top); c.clip()
      const line = (x: number, col: string) => { const a = px(x, 0), b = px(x, FW); c.strokeStyle = col; c.lineWidth = Math.max(1.5, .16 * s); c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke() }
      line(K.los, 'rgba(76,141,255,.8)'); if (p.down && p.dist < 100 - p.from_yl) line(K.los + p.dist * d, 'rgba(255,176,32,.85)')
      let path: P[] | null = null
      if (!inSetup && bh > 0) { let t0 = t, t1 = t; while (t0 > 0 && K.ball(t0 - .02)[2] > 0) t0 -= .02; while (t1 < 1 && K.ball(t1 + .02)[2] > 0) t1 += .02; path = flight(K.ball, Math.max(0, t0 - .02), Math.min(1, t1 + .02)) }
      if (K.carrier && !inSetup) { const cp = K.moves[K.carrier](t); if (t < 1) trail.current.push(cp); trail.current.slice(-16).forEach((q, j, arr) => { const [x, y] = px(q[0], q[1]); c.fillStyle = `rgba(255,176,32,${(j + 1) / arr.length * .5})`; c.beginPath(); c.arc(x, y, .32 * s, 0, 7); c.fill() }) }
      const form = { ...K.off, ...K.de }; const u = inSetup ? ease(local / seg.setup) : 1
      const order = [...OFF, ...DEF].sort((a, b) => vertical ? (flip ? form[a][0] - form[b][0] : form[b][0] - form[a][0]) : form[a][1] - form[b][1])
      for (const sl of order) {
        const target = inSetup ? form[sl] : K.moves[sl](t); const from = last.current[sl] || form[sl]
        const x = inSetup ? lerp(from[0], target[0], u) : target[0], y = inSetup ? lerp(from[1], target[1], u) : target[1]
        if (!inSetup) last.current[sl] = [x, y]
        const offSide = OFF.includes(sl) === (p.off === game.home); const isCar = !inSetup && sl === K.carrier && t > .12
        const name = isCar ? (sl === K.slot && p.player ? short(p.player) : sl === 'QB' ? short(qb[p.off || '']) : p.type === 'int' ? 'INT' : null) : null
        dot(x, y, offSide ? ch : ca, OFF.includes(sl), isCar, name)
      }
      if (!(K.carrier && bh === 0 && t >= 1 && !inSetup)) ball([bx, by, bh], path)
      c.restore()
      const prevScore: [number, number] = seg.i ? [plays[seg.i - 1].score_home, plays[seg.i - 1].score_away] : [0, 0]
      if (finished && (p.score_home !== prevScore[0] || p.score_away !== prevScore[1]) && (!flash.current || flash.current.at !== seg.t0)) flash.current = { side: p.score_home !== prevScore[0] ? 'home' : 'away', at: seg.t0 }
      if (finished && ['TD', 'FG good', 'turnover', 'FG miss'].includes(p.result)) {
        const hu = Math.max(0, (local - seg.setup - seg.action) / Math.max(seg.hold, .01)); const a = Math.min(1, hu * 6) * (hu < .8 ? 1 : Math.max(0, 1 - (hu - .8) / .2))
        const label = p.result === 'TD' ? 'TOUCHDOWN' : p.result === 'FG good' ? 'FIELD GOAL' : p.result === 'FG miss' ? 'NO GOOD' : 'TURNOVER'
        c.font = `800 ${vertical ? 40 : 54}px Archivo, Inter, sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle'; const w = c.measureText(label).width; const y = (top + bottom) / 2 - (vertical ? 60 : 0)
        c.fillStyle = `rgba(7,10,15,${.78 * a})`; c.fillRect(W / 2 - w / 2 - 18, y - 34, w + 36, 68); c.fillStyle = p.result === 'TD' || p.result === 'FG good' ? `rgba(255,176,32,${a})` : `rgba(242,85,90,${a})`; c.fillText(label, W / 2, y)
      }
    }
    // --- scoreboard + win probability
    const secsLeft = p ? ((p.q <= 4 ? (4 - Math.min(p.q, 4)) * 900 : 0) + p.clock) : 3600
    const wp = winProb(score[0] - score[1], secsLeft - (inSetup ? 0 : 20), game.spread, seg.kind === 'play' || seg.kind === 'kickoff' ? p.off === game.home : null)
    const sb = (x: number, team: string, sc: number, col: string, side: 'home' | 'away') => {
      c.textAlign = 'center'; c.textBaseline = 'alphabetic'; c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 16 : 16}px Archivo, Inter, sans-serif`; c.fillText(team, x, 22)
      c.fillStyle = '#8794A5'; c.font = `600 ${vertical ? 10 : 11}px Inter, sans-serif`; c.fillText(nick(team), x, 36)
      const fl = flash.current?.side === side && pos - flash.current.at < 2.2 && finished
      c.fillStyle = fl ? '#FFC247' : '#EEF2F6'; c.font = `800 ${vertical ? 34 : 32}px Archivo, Inter, sans-serif`; c.fillText(String(sc), x + (side === 'away' ? 70 : -70) * (vertical ? 1 : 1.4), 42)
      c.fillStyle = `#${col}`; c.fillRect(x - 24, 48, 48, 3)
      if (seg.kind === 'play' && p.off === team) { c.fillStyle = '#FFB020'; c.beginPath(); c.arc(x + (side === 'away' ? 28 : -28), 17, 4, 0, 7); c.fill() }
    }
    sb(vertical ? 46 : 150, game.away, score[1], ca, 'away'); sb(W - (vertical ? 46 : 150), game.home, score[0], ch, 'home')
    c.textAlign = 'center'; c.fillStyle = '#8794A5'; c.font = `600 11px Inter, sans-serif`; c.fillText(!p ? 'Q1' : p.q > 4 ? 'OT' : `Q${seg.kind === 'qcard' ? seg.q : p.q}`, W / 2, 16)
    c.fillStyle = '#EEF2F6'; c.font = `800 ${vertical ? 22 : 24}px Archivo, Inter, sans-serif`; c.fillText(seg.kind === 'toss' ? '15:00' : seg.kind === 'qcard' ? '0:00' : clock(p.clock), W / 2, 40)
    if (seg.kind === 'play') { c.fillStyle = '#FFB020'; c.font = `600 11px Inter, sans-serif`; c.fillText(sit, W / 2, 56) }
    const bw = Math.min(W - 160, 520), bx0 = (W - bw) / 2, by0 = vertical ? 76 : 60
    c.fillStyle = '#2B3949'; c.fillRect(bx0, by0, bw, 6); c.fillStyle = `#${ca}`; c.fillRect(bx0, by0, bw * (1 - wp), 6); c.fillStyle = `#${ch}`; c.fillRect(bx0 + bw * (1 - wp), by0, bw * wp, 6)
    const mk = bx0 + bw * (1 - wp); c.fillStyle = '#070A0F'; c.beginPath(); c.arc(mk, by0 + 3, 7, 0, 7); c.fill(); c.strokeStyle = '#FFB020'; c.lineWidth = 2.5; c.stroke()
    c.font = `800 13px Archivo, Inter, sans-serif`; c.textBaseline = 'middle'; c.fillStyle = `#${ca}`; c.textAlign = 'right'; c.fillText(`${Math.round((1 - wp) * 100)}%`, bx0 - 10, by0 + 3); c.fillStyle = `#${ch}`; c.textAlign = 'left'; c.fillText(`${Math.round(wp * 100)}%`, bx0 + bw + 10, by0 + 3)
  }, [pos, plays, tl, game, ch, ca, qb, seg, finished, score, inSetup, t, local, sit])

  if (!plays.length) return <p className="empty">No play log for this game.</p>
  const jump = (i: number) => { const sg = tl.segs.find(x => x.i === i && x.kind === 'play'); if (sg) { setPos(sg.t0); chor.current = null } }
  const lineText = seg.kind === 'toss' ? `${game.away} @ ${game.home} · ${game.n.toLocaleString()} simulated games · this is the median one` : seg.kind === 'kickoff' ? `${nick(p.off || '')} start at the ${p.from_yl <= 50 ? p.from_yl : 100 - p.from_yl}` : seg.kind === 'qcard' ? '' : (inSetup || t < .55) ? '…' : describe(p, qb)
  const lineColr = seg.kind === 'play' && !inSetup && t >= .55 ? (['TD', 'FG good'].includes(p.result) ? 'pos' : ['turnover', 'FG miss', 'downs'].includes(p.result) ? 'neg' : '') : 'muted'
  const upcoming = seg.kind !== 'play' || inSetup || t < .55
  const panel = (team: string, col: string) => { const S = stats[team]; const ru = Object.entries(S.rush).sort((a, b) => b[1].yds - a[1].yds)[0]; const re = Object.entries(S.recv).sort((a, b) => b[1].yds - a[1].yds)[0]
    return <div className="sim-panel" key={team} style={{ borderTopColor: `#${col}` }}>
      <b>{nick(team).toUpperCase()}</b>
      <div className="sim-row"><span className="sim-l">Passing</span><span>{short(qb[team])}</span><span className="muted">{S.cmp}/{S.att} · {S.pyds} yds{S.ptd ? ` · ${S.ptd} TD` : ''}{S.inter ? ` · ${S.inter} INT` : ''}</span></div>
      <div className="sim-row"><span className="sim-l">Rushing</span><span>{ru ? short(ru[0]) : '—'}</span><span className="muted">{ru ? `${ru[1].car} car · ${ru[1].yds} yds${ru[1].td ? ` · ${ru[1].td} TD` : ''}` : ''}</span></div>
      <div className="sim-row"><span className="sim-l">Receiving</span><span>{re ? short(re[0]) : '—'}</span><span className="muted">{re ? `${re[1].rec} rec · ${re[1].yds} yds${re[1].td ? ` · ${re[1].td} TD` : ''}` : ''}</span></div>
    </div> }
  return (<div className="sim-player">
    <canvas ref={ref} className="sim-canvas" style={{ width: '100%', display: 'block' }} aria-label={`Replay of the median simulated ${game.away} at ${game.home} game`} />
    <div className="sim-last">
      <span className="chip">{upcoming ? 'NEXT SNAP' : 'LAST PLAY'}</span>
      <span className="sim-down">{seg.kind === 'play' ? sitDown.toUpperCase() : seg.kind === 'kickoff' || seg.kind === 'toss' ? 'KICKOFF' : `END Q${seg.q}`}</span>
      <span className="muted">{seg.kind === 'play' ? sitSpot : seg.kind === 'toss' ? `${nick(plays[0].off || '')} will receive` : seg.kind === 'kickoff' ? `${nick(p.off || '')} receive` : ''}</span>
      <span className={'sim-line ' + lineColr}>{lineText}</span>
    </div>
    <div className="sim-controls">
      <button className="btn" onClick={() => { if (pos >= tl.total) { setPos(0); chor.current = null; last.current = {} } setPlaying(!playing) }} aria-label={playing ? 'Pause' : 'Play'}>{playing ? '❚❚' : pos >= tl.total ? '↺' : '▶'}</button>
      <input type="range" min={0} max={tl.total} step={0.05} value={pos} onChange={e => { setPos(+e.target.value); chor.current = null }} aria-label="Scrub the replay" />
      <div className="seg">{[1, 1.5, 2.5].map(x => <button key={x} className={speed === x ? 'on' : ''} onClick={() => setSpeed(x)}>{x}×</button>)}</div>
    </div>
    <div className="sim-panels">{panel(game.away, ca)}{panel(game.home, ch)}</div>
    <ol className="sim-log" aria-label="Play by play" ref={logRef}>
      {plays.map((q, i) => <li key={i} className={i === idx ? 'on' : ''} onClick={() => jump(i)} style={{ cursor: 'pointer' }}><span className="muted">{q.q > 4 ? 'OT' : 'Q' + q.q} {clock(q.clock)}</span> <span className={['TD', 'FG good'].includes(q.result) ? 'pos' : ['turnover', 'FG miss', 'downs'].includes(q.result) ? 'neg' : ''}>{describe(q, qb)}</span> <span className="muted">{q.score_away}–{q.score_home}</span></li>)}
    </ol>
    {reduced && <p className="muted" style={{ fontSize: 12 }}>Reduced motion is on: use the slider or click a play to step through.</p>}
  </div>)
}

export default function Sim({ week }: { week: number }) {
  const [list, setList] = useState<Record<string, Game> | null>(null); const [err, setErr] = useState(''); const [note, setNote] = useState(''); const [sheets, setSheets] = useState<string[]>([])
  const [sel, setSel] = useState<string>(() => new URLSearchParams(location.search).get('game') || '')
  const [full, setFull] = useState<Game | null>(null)
  useEffect(() => { setList(null); setErr(''); fetch(`${API}/api/sim/${week}`).then(async r => { if (!r.ok) throw new Error(`No simulations for week ${week} yet — they're built with the Tuesday board.`); return r.json() }).then(d => { setList(d.games); setNote(d.note || ''); setSheets(d.cheatsheets || []) }).catch(e => setErr(e.message)) }, [week])
  const gid = list ? (sel && list[sel] ? sel : Object.keys(list)[0]) : ''
  useEffect(() => { if (!gid) return; setFull(null); fetch(`${API}/api/sim/${week}/${gid}`).then(r => r.json()).then(d => setFull(d.game)).catch(() => {}) }, [week, gid])
  if (err) return <p className="empty">{err}</p>
  if (!list || !gid) return <div className="sk-rows" aria-busy="true" aria-label="Loading simulations">{Array.from({ length: 4 }).map((_, i) => <div className="sk-row" key={i}><div className="skeleton" style={{ width: '55%' }} /><div className="skeleton" /><div className="skeleton" style={{ width: '80%' }} /></div>)}</div>
  const g = list[gid]; const top = [...g.players].sort((a, b) => b.p_td - a.p_td).slice(0, 10)
  const fav = g.spread < 0 ? `${g.home} ${g.spread}` : g.spread > 0 ? `${g.away} −${g.spread}` : 'pick'
  return (<div>
    {sheets.length > 0 && <p className="muted" style={{ margin: '0 0 8px', fontSize: 13 }}>Week {week} cheat sheet (every offense's top-10 ranks, every defense's most/fewest allowed): {sheets.map((u, i) => <a key={u} href={u} target="_blank" rel="noreferrer" style={{ marginRight: 8 }}>page {i + 1} ↗</a>)}</p>}
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
    <div className="scroll-x"><table className="sim-table"><thead><tr><th className="l">Player</th><th title="Share of the 5,000 simulated games with at least one TD">Sim TD</th><th className="wide" title="Two or more">2+</th><th title="The model's calibrated P(TD) — the number the picks are built on">Model</th><th>Rec</th><th>Rec yds</th><th>Rush yds</th></tr></thead>
      <tbody>{top.map(p => <tr key={p.gsis_id}><td className="l">{p.player} <span className="muted">{p.team} {p.position}</span></td><td className="num"><b>{pc(p.p_td)}</b></td><td className="num wide">{pc(p.p_2td)}</td><td className="num">{pc(p.p_gbm)}</td><td className="num">{p.rec_q[1]}</td><td className="num">{p.rec_yds_q[1]} <span className="delta">{p.rec_yds_q[0]}–{p.rec_yds_q[2]}</span></td><td className="num">{p.rush_yds_q[1]} <span className="delta">{p.rush_yds_q[0]}–{p.rush_yds_q[2]}</span></td></tr>)}</tbody></table></div>
    <div className="scroll-x"><table className="sim-table" style={{ marginTop: 12 }}><thead><tr><th className="l">QB line</th><th>Att</th><th>Cmp</th><th>Yds</th><th>TD</th><th>INT</th></tr></thead>
      <tbody>{g.qb.map(q => <tr key={q.team}><td className="l">{q.team}</td><td className="num">{q.att.toFixed(0)}</td><td className="num">{q.cmp.toFixed(0)}</td><td className="num">{q.yds_q[1]} <span className="delta">{q.yds_q[0]}–{q.yds_q[2]}</span></td><td className="num">{q.td.toFixed(1)}</td><td className="num">{q.inter.toFixed(1)}</td></tr>)}</tbody></table></div>
    <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>{note} The picks on the Board use the model column, not the sim: in the 2026 backtest the model ranks scorers a little better (AUC .688 vs .668). The sim is here for the game-level picture and the replay.</p>
  </div>)
}
