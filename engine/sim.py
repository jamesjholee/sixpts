"""Snap-by-snap game simulator, vectorised across N games.

    from engine import sim
    T = sim.team_inputs(pbp_prior, pbp_cur_through_prev_week)       # per-team pace / pass rate / efficiency, walk-forward
    P = sim.player_inputs(features_df, team)                          # per-player target / carry / red-zone shares (make_features output)
    out = sim.simulate(home, away, T, P_home, P_away, spread, total, n=5000, seed=1)

Every number the product shows is a count over these games: P(home win) = games won / N, P(player TD) = games
with a TD / N, and so on. The engine models play calling by down, distance, score and clock; pass vs run
efficiency as offense x defense; sacks, interceptions and fumbles; fourth-down decisions, field goals and punts;
two-point tries; the two-minute drill; and a sudden-death overtime. Not modelled: penalties, timeouts, kick
returns, in-game injuries, weather.

All N games advance one snap at a time in lockstep as numpy arrays, so a slate of 14 games x 5,000 sims runs in
seconds. The per-step arrays are kept, so the play-by-play of any one game (we keep the one nearest the median
score) comes out for free as a renderer-ready log: down, distance, yard line, play type, carrier, air yards,
result, clock and score on every snap.

Status: display-only. The sim's P(TD) is shown next to the model's and the market's; it does not drive picks
until it has graded weeks behind it (docs/model-notes.md).
"""
from __future__ import annotations
import numpy as np, pandas as pd

# ---------------- league-average play distributions (2021-25 nflfastR; tuned in calibrate()) ----------------
PACE_PLAYS = 63.0           # offensive snaps per team per game (pass + rush, incl. sacks)
COMP = 0.655; YPC_PASS = 11.3; SACK = 0.066; INT = 0.023; SACK_YDS = 6.5
YPC_RUSH = 4.35; FUMBLE_RUSH = 0.009; FUMBLE_REC = 0.012; FUMBLE_SACK = 0.09
XP = 0.945; TWO_PT = 0.48
FG = [(30, .985), (40, .935), (50, .80), (56, .64), (62, .35)]  # (max distance, make prob)
PUNT_NET = 41.0
HFA = 0.02                  # home field: +-2% efficiency, about 1.5 points
SEC_PASS_C, SEC_PASS_I, SEC_RUN, SEC_SACK = 33, 6, 35, 33      # seconds off the clock incl. the following huddle
SEC_KICK = 5
POS_TGT_FALLBACK = {"WR": .55, "TE": .20, "RB": .20, "QB": .0, "FB": .05}
# share shaping (fitted on 2026 weeks 1-6 in sim_backtest --grid; week 7 held out)
RZ_BLEND = 0.5              # weight on red-zone shares vs overall shares inside the 20
UNIFORM_MIX = 0.20          # share of looks spread evenly over active skill players (TDs are flatter than usage)
TE_RZ_MULT = 1.3            # tight ends convert red-zone targets at a higher rate than their share implies
QB_SNEAK = 0.15             # inside the 2, share of runs that are the QB's

def _season_rates(p: pd.DataFrame) -> pd.DataFrame:
    """Per-team offense and defense rates from prepped play-by-play (engine.model.prep_pbp output)."""
    p = p[p.play_type.isin(["pass", "run"])].copy()
    is_pass = p.play_type.eq("pass"); drop = is_pass | p.sack.eq(1) if "sack" in p else is_pass
    p["db"] = drop.astype(int); p["sk"] = (p.sack == 1).astype(int) if "sack" in p else 0
    p["cmp"] = (p.complete_pass == 1).astype(int) if "complete_pass" in p else 0
    p["inter"] = (p.interception == 1).astype(int) if "interception" in p else 0
    p["ay"] = p.air_yards.where(is_pass)
    p["pyd"] = p.yards_gained.where(is_pass & (p.cmp == 1))
    p["ryd"] = p.yards_gained.where(~is_pass)
    p["neutral"] = p.wp.between(0.2, 0.8) & (p.game_seconds_remaining > 300) if "wp" in p else True
    p["np"] = np.where(p.neutral, is_pass, np.nan)
    p["fourth_go"] = np.where((p.down == 4) & (p.ydstogo <= 2) & (p.yardline_100 <= 60), 1.0, np.nan)
    p["expl"] = (p.ryd >= 12).astype(float).where(~is_pass)
    g = lambda col, team: p.groupby(team).agg(games=("game_id", "nunique"), plays=("play_id", "count"), db=("db", "sum"), sk=("sk", "sum"),
        cmp=("cmp", "sum"), inter=("inter", "sum"), ay=("ay", "mean"), pyd=("pyd", "mean"), ryd=("ryd", "mean"), np_=("np", "mean"), go4=("fourth_go", "mean"), expl=("expl", "mean"))
    off = g(None, "posteam"); dfn = g(None, "defteam")
    for d in (off, dfn):
        att = (d.db - d.sk).replace(0, np.nan)   # completions and picks are per attempt; sacks per dropback
        d["plays_pg"] = d.plays / d.games; d["comp"] = d.cmp / att; d["sack"] = d.sk / d.db.replace(0, np.nan); d["int"] = d.inter / att
    return off, dfn

def team_inputs(p_prior: pd.DataFrame, p_cur: pd.DataFrame) -> dict:
    """Blend this season's rates toward last season's by games played (w = g/(g+4)). Returns {team: {...}}."""
    op, dp = _season_rates(p_prior); oc, dc = _season_rates(p_cur) if len(p_cur) else (op.iloc[0:0], dp.iloc[0:0])
    teams = sorted(set(op.index) | set(oc.index))
    # league baselines come from last season's data, so offense x defense adjustments are centred on 1
    lg = {c: float(op[c].mean()) for c in ["plays_pg", "comp", "sack", "int", "ay", "pyd", "ryd", "np_", "go4", "expl"]}
    lg["go4"] = float(np.clip(lg["go4"], 0.3, 0.8))
    out = {}
    def blend(a: pd.DataFrame, b: pd.DataFrame, t, col):
        pv = a[col].get(t, np.nan) if t in a.index else np.nan; cv = b[col].get(t, np.nan) if t in b.index else np.nan
        g = float(b.games.get(t, 0)) if t in b.index else 0.0; w = g / (g + 4.0)
        pv = lg[col] if pd.isna(pv) else pv; cv = pv if pd.isna(cv) else cv
        return w * cv + (1 - w) * pv
    for t in teams:
        o = {c: blend(op, oc, t, c) for c in lg}; d = {c: blend(dp, dc, t, c) for c in ["comp", "sack", "int", "pyd", "ryd", "expl", "plays_pg"]}
        o["go4"] = float(np.clip(o["go4"], 0.3, 0.8)) if not pd.isna(o["go4"]) else lg["go4"]
        out[t] = dict(off=o, defn=d)
    out["_league"] = lg
    return out

def player_inputs(feat: pd.DataFrame, team: str, names: dict | None = None) -> dict:
    """Target / carry / red-zone shares for one team from the make_features frame (walk-forward). Normalised within team."""
    f = feat[feat.posteam.eq(team)].copy()
    if f.empty: return dict(ids=[], names=[], pos=[], tgt=np.zeros(0), car=np.zeros(0), rz_tgt=np.zeros(0), rz_car=np.zeros(0), qb=-1)
    g = f.g_std.fillna(0).clip(lower=0); w = g / (g + 2.0)                     # two games of this season = half weight
    def share(cur, prev, filler):
        cur = f[cur]; prev = f[prev] if prev in f else pd.Series(np.nan, index=f.index)
        both = cur.isna() & prev.isna()
        v = w * cur.fillna(prev).fillna(0) + (1 - w) * prev.fillna(cur).fillna(0)
        return v.where(~both, filler).clip(lower=0)
    f["tgt"] = share("tgt_share_std", "tgt_share_prev", 0.004); f["car"] = share("car_share_std", "car_share_prev", 0.003)
    f["rz_tgt"] = share("rz_tgt_share_std", "rz_tgt_share_prev", 0.004); f["rz_car"] = share("rz_car_share_std", "rz_car_share_prev", 0.003)
    f["is_qb"] = f.position.eq("QB")
    f.loc[f.is_qb, ["tgt", "rz_tgt"]] = 0.0                                   # quarterbacks are not targets
    f.loc[~f.position.isin(["RB", "WR", "TE", "QB", "FB"]), ["tgt", "car", "rz_tgt", "rz_car"]] = 0.0
    # red zone shares are noisy and real TD distributions are flatter than usage (injuries, garbage time, trick plays):
    # lean red zone shares back toward overall shares, then mix 12% uniform over the active skill players
    f["rz_tgt"] = RZ_BLEND * f.rz_tgt + (1 - RZ_BLEND) * f.tgt; f["rz_car"] = RZ_BLEND * f.rz_car + (1 - RZ_BLEND) * f.car
    f.loc[f.position.eq("TE"), "rz_tgt"] *= TE_RZ_MULT
    for c in ["tgt", "car", "rz_tgt", "rz_car"]:
        s_ = f[c].sum()
        if s_ > 0:
            active = (f[c] > 0.01) & ~f.is_qb if c.endswith("tgt") else (f[c] > 0.01)
            uni = active / max(1, active.sum())
            f[c] = (1 - UNIFORM_MIX) * f[c] / s_ + UNIFORM_MIX * uni
    if f.tgt.sum() <= 0: f["tgt"] = f.position.map(POS_TGT_FALLBACK).fillna(0); f["rz_tgt"] = f.tgt
    if f.car.sum() <= 0: f["car"] = np.where(f.position.eq("RB"), 1.0, np.where(f.position.eq("QB"), .15, 0.0)); f["rz_car"] = f.car
    for c in ["tgt", "car", "rz_tgt", "rz_car"]:
        s = f[c].sum(); f[c] = f[c] / s if s > 0 else f[c]
    nm = names or {}
    return dict(ids=f.gsis_id.tolist(), names=[nm.get(g, g) for g in f.gsis_id], pos=f.position.tolist(),
                tgt=f.tgt.values, car=f.car.values, rz_tgt=f.rz_tgt.values, rz_car=f.rz_car.values, qb=int(np.argmax(f.is_qb.values)) if f.is_qb.any() else -1)

# ---------------- the engine ----------------
def _pick(rng, cum, n):
    """Vectorised categorical draw: cum is the cumulative share vector (sums to 1)."""
    u = rng.random(n); return np.searchsorted(cum, u).clip(0, len(cum) - 1)

def _fg_prob(dist):
    out = np.full(dist.shape, 0.2)
    for mx, pr in reversed(FG): out = np.where(dist <= mx, pr, out)
    return out

def simulate(home: str, away: str, T: dict, PH: dict, PA: dict, spread: float, total: float, n: int = 5000, seed: int = 1, keep_log: bool = True,
             pass_scale: float = 1.0, run_scale: float = 1.0, pace_scale: float = 1.0) -> dict:
    """spread: home spread (negative = home favoured). total: market total. Both only nudge pace/strength a little
    toward the market (the sim is mostly usage-driven); the backtest decides how much."""
    rng = np.random.default_rng(seed)
    th, ta, lg = T[home], T[away], T["_league"]
    teams = [th, ta]; players = [PH, PA]
    # strength: offense rate vs league x defense rate vs league
    def eff(o, d, col): return (o["off"][col] / lg[col]) * (d["defn"][col] / lg[col])
    comp = [min(.80, max(.50, lg["comp"] * eff(th, ta, "comp"))), min(.80, max(.50, lg["comp"] * eff(ta, th, "comp")))]
    pyd = [lg["pyd"] * eff(th, ta, "pyd"), lg["pyd"] * eff(ta, th, "pyd")]
    ryd = [lg["ryd"] * eff(th, ta, "ryd"), lg["ryd"] * eff(ta, th, "ryd")]
    sack = [lg["sack"] * eff(th, ta, "sack"), lg["sack"] * eff(ta, th, "sack")]
    intr = [0.85 * lg["int"] * eff(th, ta, "int"), 0.85 * lg["int"] * eff(ta, th, "int")]   # x0.85: the depth/3rd-and-long modifiers below average ~1.18
    expl = [th["off"]["expl"] * (ta["defn"]["expl"] / lg["expl"]), ta["off"]["expl"] * (th["defn"]["expl"] / lg["expl"])]
    npr = [th["off"]["np_"], ta["off"]["np_"]]; go4 = [th["off"]["go4"], ta["off"]["go4"]]
    # pace: seconds per snap so each team lands near its snaps/game; the market total nudges pace +-5%
    pace = (th["off"]["plays_pg"] + ta["off"]["plays_pg"]) / 2.0
    tempo = (3600.0 / (2 * pace)) / (3600.0 / (2 * lg["plays_pg"]))   # 1.0 = league average seconds per snap
    tempo *= np.clip(1 - 0.05 * (total - 45.0) / 10.0, 0.9, 1.1)
    # market spread nudges efficiency: each point of spread ~ 1% yards/play
    adj = np.clip(-spread * 0.01, -0.12, 0.12); pyd[0] *= 1 + adj; ryd[0] *= 1 + adj; pyd[1] *= 1 - adj; ryd[1] *= 1 - adj
    pyd[0] *= 1 + HFA; ryd[0] *= 1 + HFA; pyd[1] *= 1 - HFA; ryd[1] *= 1 - HFA                 # home field
    pyd = [x * pass_scale for x in pyd]; ryd = [x * run_scale for x in ryd]; tempo *= pace_scale
    cum = [dict(tgt=np.cumsum(P["tgt"]), car=np.cumsum(P["car"]), rz_tgt=np.cumsum(P["rz_tgt"]), rz_car=np.cumsum(P["rz_car"])) for P in players]
    npl = [len(P["ids"]) for P in players]
    stats = [dict(td=np.zeros((n, k), np.int16), rec=np.zeros((n, k), np.int16), rec_yds=np.zeros((n, k), np.int16), car=np.zeros((n, k), np.int16), rush_yds=np.zeros((n, k), np.int16), tgt=np.zeros((n, k), np.int16)) for k in npl]
    qb_stats = [dict(att=np.zeros(n, np.int16), cmp=np.zeros(n, np.int16), yds=np.zeros(n, np.int16), td=np.zeros(n, np.int16), inter=np.zeros(n, np.int16)) for _ in range(2)]

    # state
    q = np.ones(n, np.int8); clock = np.full(n, 900.0); score = np.zeros((n, 2), np.int16)
    off = (rng.random(n) < 0.5).astype(np.int8); second_half = 1 - off
    ball = np.full(n, 25.0); down = np.ones(n, np.int8); dist = np.full(n, 10.0); done = np.zeros(n, bool); ot = np.zeros(n, bool); ot_drives = np.zeros(n, np.int8)
    L = [] if keep_log else None
    MAX_STEPS = 320
    for step in range(MAX_STEPS):
        act = ~done
        if not act.any(): break
        i = np.where(act)[0]; m = len(i)
        o = off[i]; d_ = 1 - o
        diff = score[i, o] - score[i, d_]
        rem = clock[i] + np.where(q[i] < 4, (4 - q[i]) * 900.0, 0.0)
        late = (q[i] >= 4) & (clock[i] < 240)
        trailing = diff < 0
        # ---- play call
        base = np.where(o == 0, npr[0], npr[1])
        ppass = base - np.where(down[i] == 1, 0.08, 0.0)      # first down leans run; the situational bumps below skew pass
        ppass = np.where(down[i] == 3, np.where(dist[i] >= 7, .88, np.where(dist[i] >= 3, .64, .42)), ppass)
        ppass = np.where((down[i] == 2) & (dist[i] >= 8), ppass + .15, ppass)
        ppass = np.where(dist[i] <= 2, ppass - np.where(ball[i] >= 90, .10, .22), ppass)   # short yardage leans run, but goal-to-go stays near 50/50
        ppass = ppass + np.where(late & trailing, .25, 0.0) - np.where(late & (diff > 8), .30, 0.0) + np.clip(-diff, -14, 14) * 0.006
        # kneel: leading, under 2 min, opponent can't stop the clock (approx), not in own red zone
        kneel = (q[i] == 4) & (clock[i] < 110) & (diff > 0) & (down[i] <= 3) & (ball[i] < 90)
        ppass = np.clip(ppass, .08, .95)
        # fourth down: go for it, kick a field goal, or punt — decided before the snap
        fourth = down[i] >= 4
        fgd = (100 - ball[i]) + 17
        go = fourth & ((((dist[i] <= 2) & (ball[i] > 45)) & (rng.random(m) < np.where(o == 0, go4[0], go4[1]))) | (late & trailing & ((diff < -3) | (fgd > 52))) | ((q[i] == 4) & (clock[i] < 600) & (diff < -8) & (fgd > 45)) | ((dist[i] <= 1) & (ball[i] > 35) & (rng.random(m) < 0.5)))
        try_fg = fourth & ~go & (fgd <= 58) & ~(late & trailing & (diff < -3))
        punt = fourth & ~go & ~try_fg
        kick = try_fg | punt
        is_pass = (rng.random(m) < ppass) & ~kneel & ~kick
        is_run = ~is_pass & ~kick
        gain = np.zeros(m); t_off = np.zeros(m); turnover = np.zeros(m, bool); td = np.zeros(m, bool)
        ptype = np.zeros(m, np.int8)  # 0 run 1 complete 2 incomplete 3 sack 4 int 5 kneel 6 fumble
        carrier = np.full(m, -1, np.int16); air = np.zeros(m)
        # ---- passes
        ip = np.where(is_pass)[0]
        if len(ip):
            o_p = o[ip]; sk = rng.random(len(ip)) < np.where(o_p == 0, sack[0], sack[1])
            # target choice: red zone shares inside the 20
            tgt = np.empty(len(ip), np.int16)
            for side in (0, 1):
                sel = o_p == side
                if sel.any():
                    rz = ball[i][ip][sel] >= 80
                    u = rng.random(sel.sum())
                    a = np.searchsorted(cum[side]["tgt"], u).clip(0, npl[side] - 1); b = np.searchsorted(cum[side]["rz_tgt"], u).clip(0, npl[side] - 1)
                    tgt[sel] = np.where(rz, b, a)
            carrier[ip] = tgt
            dd = dist[i][ip]
            ay = np.maximum(-3, rng.normal(np.where(dd > 10, 9.5, np.where(dd > 4, 7.0, 4.5)), 7.0, len(ip)))
            air[ip] = ay
            comp_p = np.where(o_p == 0, comp[0], comp[1]) * np.clip(1 - (ay - 7) * 0.025, 0.55, 1.15)
            int_p = np.where(o_p == 0, intr[0], intr[1]) * np.where(dd > 8, 1.35, 1.0) * np.clip(1 + (ay - 7) * 0.05, 0.6, 2.0)
            r = rng.random(len(ip))
            is_int = ~sk & (r < int_p); is_cmp = ~sk & ~is_int & (r < int_p + comp_p)
            # yards per completion is anchored to the team's rate: air yards (90% of throw depth) plus YAC drawn so the
            # expected completion equals pyd. E[max(air,0)] for the three depth regimes is precomputed (normal, sd 7).
            e_air = np.where(dd > 10, 9.8, np.where(dd > 4, 7.6, 5.6))
            mu_yac = np.clip(np.where(o_p == 0, pyd[0], pyd[1]) - 0.9 * 0.82 * e_air, 1.0, 14.0)   # x0.82: completions skew short
            yac = rng.lognormal(np.log(mu_yac) - 0.405, 0.9, len(ip))
            cy = np.round(np.maximum(ay, 0.0) * 0.9 + yac)
            cy = np.minimum(cy, 100 - ball[i][ip])
            gain[ip] = np.where(sk, -np.round(rng.normal(SACK_YDS, 2.5, len(ip)).clip(1, 14)), np.where(is_cmp, cy, np.where(is_int, -np.round(rng.random(len(ip)) * 12), 0.0)))
            fum = (sk & (rng.random(len(ip)) < FUMBLE_SACK)) | (is_cmp & (rng.random(len(ip)) < FUMBLE_REC))
            turnover[ip] = is_int | fum
            ptype[ip] = np.where(sk, 3, np.where(is_int, 4, np.where(is_cmp, 1, 2))); ptype[ip] = np.where(fum, 6, ptype[ip])
            t_off[ip] = np.where(sk, SEC_SACK, np.where(is_cmp, SEC_PASS_C, SEC_PASS_I))
            td[ip] = is_cmp & ~fum & (ball[i][ip] + gain[ip] >= 100)
            # stats
            for side in (0, 1):
                sel = o_p == side
                if sel.any():
                    rows = i[ip][sel]; cols = tgt[sel]
                    np.add.at(stats[side]["tgt"], (rows, cols), (~sk[sel]).astype(np.int16))
                    np.add.at(stats[side]["rec"], (rows, cols), is_cmp[sel].astype(np.int16))
                    np.add.at(stats[side]["rec_yds"], (rows, cols), (gain[ip][sel] * is_cmp[sel]).astype(np.int16))
                    np.add.at(stats[side]["td"], (rows, cols), td[ip][sel].astype(np.int16))
                    qb_stats[side]["att"][rows] += (~sk[sel]); qb_stats[side]["cmp"][rows] += is_cmp[sel]; qb_stats[side]["yds"][rows] += (gain[ip][sel] * is_cmp[sel]).astype(np.int16)
                    qb_stats[side]["td"][rows] += td[ip][sel]; qb_stats[side]["inter"][rows] += is_int[sel]
        # ---- runs (and kneels)
        ir = np.where(is_run)[0]
        if len(ir):
            o_r = o[ir]; kn = kneel[ir]
            car = np.empty(len(ir), np.int16)
            for side in (0, 1):
                sel = o_r == side
                if sel.any():
                    rz = ball[i][ir][sel] >= 80; u = rng.random(sel.sum())
                    a = np.searchsorted(cum[side]["car"], u).clip(0, npl[side] - 1); b = np.searchsorted(cum[side]["rz_car"], u).clip(0, npl[side] - 1)
                    car[sel] = np.where(rz, b, a)
                    # quarterback sneaks: inside the 2, a quarter of runs are the QB's
                    qbi = players[side]["qb"]
                    if qbi >= 0:
                        sneak = (ball[i][ir][sel] >= 98) & (rng.random(sel.sum()) < QB_SNEAK)
                        car[sel] = np.where(sneak, qbi, car[sel])
            carrier[ir] = car
            # explosive runs (12+) are part of the team's yards-per-carry, not on top of it: the ordinary carry's mean is
            # solved so the mixture equals ryd
            ex_p = np.where(o_r == 0, expl[0], expl[1]); ypc = np.where(o_r == 0, ryd[0], ryd[1])
            ex = rng.random(len(ir)) < ex_p
            mu_run = np.clip((ypc - ex_p * 22.0) / (1 - ex_p), 1.5, 5.0)
            ry = np.where(ex, np.round(12 + rng.exponential(10, len(ir))), np.round(rng.normal(mu_run, 2.9, len(ir))))
            ry = np.where(kn, -1.0, np.clip(ry, -5, 100 - ball[i][ir]))
            gain[ir] = ry
            fum = ~kn & (rng.random(len(ir)) < FUMBLE_RUSH)
            turnover[ir] = fum; ptype[ir] = np.where(kn, 5, np.where(fum, 6, 0)); t_off[ir] = SEC_RUN
            td[ir] = ~fum & ~kn & (ball[i][ir] + gain[ir] >= 100)
            for side in (0, 1):
                sel = (o_r == side) & ~kn
                if sel.any():
                    rows = i[ir][sel]; cols = car[sel]
                    np.add.at(stats[side]["car"], (rows, cols), 1); np.add.at(stats[side]["rush_yds"], (rows, cols), gain[ir][sel].astype(np.int16)); np.add.at(stats[side]["td"], (rows, cols), td[ir][sel].astype(np.int16))
        # ---- clock
        hurry = late & trailing
        t_off = t_off * tempo * np.where(hurry, 0.55, 1.0) * np.where((q[i] == 4) & (diff > 0) & (clock[i] < 300), 1.08, 1.0)
        t_off = np.where((q[i] <= 2) & (clock[i] < 60) | (q[i] >= 4) & (clock[i] < 120), np.minimum(t_off, 25), t_off)
        t_off = np.where(kick, SEC_KICK, t_off)
        new_clock = clock[i] - t_off
        from_ = ball[i].copy(); to_ = np.clip(ball[i] + gain, 0, 100)
        res = np.zeros(m, np.int8)  # 0 play 1 TD 2 turnover 3 FG good 4 FG miss 5 punt 6 downs 7 safety 8 XP/2pt
        # ---- resolve
        nb = ball[i] + gain; nd = dist[i] - gain
        # touchdown
        tdi = td
        score[i[tdi], o[tdi]] += 6
        res[tdi] = 1
        # XP / 2pt (decided immediately; logged inside the TD row)
        if tdi.any():
            dd2 = score[i[tdi], o[tdi]] - score[i[tdi], 1 - o[tdi]]
            go2 = (q[i][tdi] >= 4) & np.isin(dd2, [-5, -2, 1, 2, 5]) & (new_clock[tdi] < 600)
            got = np.where(go2, rng.random(go2.size) < TWO_PT, rng.random(go2.size) < XP)
            score[i[tdi], o[tdi]] += np.where(go2, 2, 1) * got
        # safety
        saf = ~tdi & ~turnover & (nb <= 0)
        score[i[saf], 1 - o[saf]] += 2; res[saf] = 7
        # turnovers
        res[turnover & ~tdi] = 2
        # drive continues? first down / kicks
        live = ~tdi & ~turnover & ~saf & ~kick
        first = live & (nd <= 0)
        fg_ok = try_fg & (rng.random(m) < _fg_prob(fgd))
        score[i[fg_ok], o[fg_ok]] += 3; res[fg_ok] = 3; res[try_fg & ~fg_ok] = 4; res[punt] = 5
        ptype[try_fg] = 7; ptype[punt] = 8
        # ---- log this step (before state changes), compact arrays
        if keep_log:
            L.append(dict(i=i.copy(), q=q[i].copy(), clock=clock[i].copy(), off=o.copy(), down=down[i].copy(), dist=dist[i].copy(), frm=from_, to=to_, ptype=ptype.copy(), carrier=carrier.copy(), air=air.copy(), gain=gain.copy(), res=res.copy(), sh=score[i, 0].copy(), sa=score[i, 1].copy(), fgd=fgd.copy()))
        # ---- next state
        switch = tdi | turnover | saf | fg_ok | (try_fg & ~fg_ok) | punt
        # new ball spot for the defense after each kind of change
        spot = np.where(tdi | fg_ok | saf, 25.0, np.where(turnover, np.clip(100 - nb, 1, 99), np.where(try_fg & ~fg_ok, np.clip(100 - np.maximum(ball[i], 20) + 7, 1, 99), np.where(punt, np.clip(100 - (ball[i] + PUNT_NET + rng.normal(0, 7, m)), 20, 99), 0.0))))
        spot = np.where(punt & (ball[i] + PUNT_NET >= 100), 20.0, spot)
        ball[i] = np.where(switch, spot, np.clip(nb, 1, 99))
        off[i] = np.where(switch, 1 - o, o)
        down[i] = np.where(switch | first, 1, down[i] + 1)
        dist[i] = np.where(switch | first, np.minimum(10, 100 - ball[i]), nd)
        # turnover on downs: a failed 4th-down attempt shows up here as down 5
        tod = (down[i] >= 5)
        if tod.any():
            j = i[tod]; off[j] = 1 - off[j]; ball[j] = np.clip(100 - ball[j], 1, 99); down[j] = 1; dist[j] = np.minimum(10, 100 - ball[j])
            if keep_log: L[-1]["res"][tod] = 6
        clock[i] = new_clock
        # ---- quarter / game end
        over_q = clock[i] <= 0
        if over_q.any():
            j = i[over_q]
            end_reg = (q[j] == 4); end_ot = ot[j]
            tie = score[j, 0] == score[j, 1]
            # OT: sudden death-ish, 10 minutes; tie after OT -> coin flip winner by a FG (rare)
            start_ot = end_reg & tie & ~end_ot
            fin = (end_reg & ~tie) | end_ot
            done[j[fin]] = True
            if start_ot.any():
                k = j[start_ot]; ot[k] = True; q[k] = 5; clock[k] = 600.0; off[k] = (rng.random(len(k)) < 0.5).astype(np.int8); ball[k] = 25.0; down[k] = 1; dist[k] = 10.0
            nq = ~end_reg & ~end_ot
            if nq.any():
                k = j[nq]; q[k] += 1; clock[k] = 900.0
                half = q[k] == 3
                if half.any(): kk = k[half]; off[kk] = second_half[kk]; ball[kk] = 25.0; down[kk] = 1; dist[kk] = 10.0
        # OT ends on any score
        ot_score = ot[i] & np.isin(res, [1, 3, 7])
        done[i[ot_score]] = True
    # anything still running after MAX_STEPS: call it
    still = ~done
    if still.any():
        tie = score[still, 0] == score[still, 1]; k = np.where(still)[0][tie]; score[k, (rng.random(len(k)) < 0.5).astype(int)] += 3
    out = dict(home=home, away=away, n=n, score_home=score[:, 0], score_away=score[:, 1], ot=ot,
               players=[dict(ids=P["ids"], names=P["names"], pos=P["pos"], **{k: v for k, v in stats[s].items()}) for s, P in enumerate(players)],
               qb=qb_stats)
    if keep_log: out["log"] = _extract_log(L, score, out)
    return out

def _extract_log(L, score, out):
    """The game nearest the median score (home and away), as a list of renderer-ready plays."""
    mh, ma = np.median(score[:, 0]), np.median(score[:, 1])
    g = int(np.argmin(np.abs(score[:, 0] - mh) + np.abs(score[:, 1] - ma) + 0.3 * np.abs((score[:, 0] + score[:, 1]) - (mh + ma))))
    plays = []
    for st in L:
        pos = np.where(st["i"] == g)[0]
        if not len(pos): continue
        k = int(pos[0]); side = int(st["off"][k]); P = out["players"][side]; c = int(st["carrier"][k])
        who = P["names"][c] if 0 <= c < len(P["names"]) else None
        plays.append(dict(q=int(st["q"][k]), clock=int(max(0, st["clock"][k])), off=out["home"] if side == 0 else out["away"], down=int(st["down"][k]), dist=int(st["dist"][k]),
                          from_yl=int(st["frm"][k]), to_yl=int(st["to"][k]), type=["run", "complete", "incomplete", "sack", "int", "kneel", "fumble", "field goal", "punt"][int(st["ptype"][k])],
                          player=who, pos=P["pos"][c] if who else None, air=int(round(st["air"][k])), gain=int(st["gain"][k]),
                          result=["", "TD", "turnover", "FG good", "FG miss", "punt", "downs", "safety", ""][int(st["res"][k])], fg_dist=int(st["fgd"][k]),
                          score_home=int(st["sh"][k]), score_away=int(st["sa"][k])))
    # scores in the log are pre-play; append final
    plays.append(dict(q=plays[-1]["q"] if plays else 4, clock=0, off=None, down=0, dist=0, from_yl=0, to_yl=0, type="final", player=None, pos=None, air=0, gain=0, result="final", fg_dist=0, score_home=int(score[g, 0]), score_away=int(score[g, 1])))
    return dict(sim_index=g, plays=plays)

# ---------------- summaries ----------------
def summarize(out: dict, spread: float | None = None, total: float | None = None) -> dict:
    h, a = out["score_home"].astype(float), out["score_away"].astype(float); n = out["n"]
    s = dict(home=out["home"], away=out["away"], n=n, p_home=float((h > a).mean()), p_away=float((a > h).mean()), p_tie=float((h == a).mean()),
             median_home=float(np.median(h)), median_away=float(np.median(a)), mean_total=float((h + a).mean()), p_ot=float(out["ot"].mean()),
             q_home=[float(x) for x in np.percentile(h, [10, 25, 50, 75, 90])], q_away=[float(x) for x in np.percentile(a, [10, 25, 50, 75, 90])])
    if spread is not None: s["p_home_cover"] = float((h + spread > a).mean()); s["spread"] = spread
    if total is not None: s["p_over"] = float((h + a > total).mean()); s["total"] = total
    s["players"] = []
    for side, P in enumerate(out["players"]):
        for j, gid in enumerate(P["ids"]):
            td = P["td"][:, j]
            if td.mean() < 0.005 and P["rec"][:, j].mean() < 0.2 and P["car"][:, j].mean() < 0.2: continue
            s["players"].append(dict(gsis_id=gid, player=P["names"][j], team=out["home"] if side == 0 else out["away"], position=P["pos"][j],
                p_td=float((td >= 1).mean()), p_2td=float((td >= 2).mean()), rec=float(P["rec"][:, j].mean()), rec_yds=float(P["rec_yds"][:, j].mean()),
                car=float(P["car"][:, j].mean()), rush_yds=float(P["rush_yds"][:, j].mean()),
                rec_q=[float(x) for x in np.percentile(P["rec"][:, j], [25, 50, 75])], rec_yds_q=[float(x) for x in np.percentile(P["rec_yds"][:, j], [25, 50, 75])], rush_yds_q=[float(x) for x in np.percentile(P["rush_yds"][:, j], [25, 50, 75])]))
    s["qb"] = [dict(team=out["home"] if k == 0 else out["away"], att=float(qb["att"].mean()), cmp=float(qb["cmp"].mean()), yds=float(qb["yds"].mean()), td=float(qb["td"].mean()), inter=float(qb["inter"].mean()), yds_q=[float(x) for x in np.percentile(qb["yds"], [25, 50, 75])]) for k, qb in enumerate(out["qb"])]
    return s
