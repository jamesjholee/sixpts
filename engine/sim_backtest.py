"""Backtest the simulator week by week with walk-forward inputs.

    python3 -m engine.sim_backtest --season 2026 --weeks 1-7 [--n 2000] [--pass-scale 1.0 --run-scale 1.0 --pace-scale 1.0]

For every game: sim mean total vs market total vs actual; sim P(home) vs market implied vs result; and for every
player on the board: sim P(TD) vs the gbm's P(TD) vs scored. Prints the calibration tables the docs quote.
Needs data/train.parquet (walk-forward features incl. the 2026 weeks) and play_by_play_{season-1,season}.parquet.
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys, time, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401
from engine import sim, model as M
from engine.features import make_features
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

def run(season: int, weeks: list[int], n: int, scales: dict, verbose: bool = True) -> dict:
    p_prior = M.prep_pbp(pd.read_parquet(D / f"play_by_play_{season - 1}.parquet"))
    p_cur = M.prep_pbp(pd.read_parquet(D / f"play_by_play_{season}.parquet"))
    feat_all = pd.read_parquet(D / "train.parquet"); feat_all = feat_all[feat_all.season == season]
    feat_all, _ = make_features(feat_all)
    names = {}; pfr2gsis = {}
    try:
        pl = pd.read_parquet(D / "players.parquet"); names = dict(zip(pl.gsis_id, pl.display_name)); pfr2gsis = dict(zip(pl.pfr_id, pl.gsis_id))
    except Exception: pass
    snaps = pd.read_parquet(D / f"snap_counts_{season}.parquet") if (D / f"snap_counts_{season}.parquet").exists() and scales.get("use_snaps", True) else None
    inj = pd.read_parquet(D / f"injuries_{season}.parquet") if (D / f"injuries_{season}.parquet").exists() and scales.get("use_inj", True) else None
    ros = pd.read_parquet(D / f"roster_weekly_{season}.parquet") if (D / f"roster_weekly_{season}.parquet").exists() and scales.get("use_inj", True) else None
    scales = {k: v for k, v in scales.items() if k not in ("use_snaps", "use_inj")}
    games, players = [], []
    for w in weeks:
        bf = D / f"board_w{w}_public.json"
        if not bf.exists(): print(f"no board for week {w}"); continue
        board = json.load(open(bf)); slate = board["slate"]; rows = board["board"]
        T = sim.team_inputs(p_prior, p_cur[p_cur.week < w]); recent = sim.recent_usage(p_cur, w, snaps=snaps, pfr2gsis=pfr2gsis); avail = sim.availability(inj, ros, w)
        feat = feat_all[feat_all.week == w]
        pw = p_cur[p_cur.week == w]
        act_car = pw[pw.rush_attempt == 1].groupby("rusher_player_id").size(); act_tgt = pw[pw.pass_attempt == 1].groupby("receiver_player_id").size()
        actual = pw.groupby("game_id").agg(h=("home_score", "max"), a=("away_score", "max")) if "home_score" in pw else None
        scored = set(zip(pw[pw.touchdown == 1].td_player_id, pw[pw.touchdown == 1].game_id))
        played = set(pw.receiver_player_id.dropna()) | set(pw.rusher_player_id.dropna()) | set(pw.passer_player_id.dropna())
        gbm = {(r["gsis_id"], r["game_id"]): r["p_model"] for r in rows}
        for g in slate:
            gid = g["game"]; parts = gid.split("_"); away, home = parts[2], parts[3]
            r_home = next((r for r in rows if r["game_id"] == gid and r["team"] == home), None)
            if r_home is None: continue
            spread = -float(r_home["spread"]); total = float(g["total"])   # board: + = favoured -> standard sign
            PH = sim.player_inputs(feat, home, names, recent, avail); PA = sim.player_inputs(feat, away, names, recent, avail)
            if not PH["ids"] or not PA["ids"]: continue
            out = sim.simulate(home, away, T, PH, PA, spread, total, n=n, seed=w * 100 + len(games), keep_log=False, **scales)
            s = sim.summarize(out, spread, total)
            ah = aa = np.nan
            if actual is not None and gid in actual.index: ah, aa = float(actual.loc[gid, "h"]), float(actual.loc[gid, "a"])
            mkt_home = M.implied(int(-110)) if False else None
            games.append(dict(week=w, game=gid, home=home, away=away, spread=spread, total=total, sim_total=s["mean_total"], sim_home=s["median_home"], sim_away=s["median_away"],
                              p_home=s["p_home"], p_cover=s["p_home_cover"], p_over=s["p_over"], act_home=ah, act_away=aa))
            for x in s["players"]:
                k = (x["gsis_id"], gid)
                if x["gsis_id"] not in played and actual is not None: continue
                players.append(dict(week=w, game=gid, player=x["player"], team=x["team"], pos=x["position"], p_sim=x["p_td"], p_gbm=gbm.get(k, np.nan), y=int(k in scored),
                                    car_sim=x["car"], tgt_sim=x.get("tgt", x["rec"]), car_act=float(act_car.get(x["gsis_id"], 0)), tgt_act=float(act_tgt.get(x["gsis_id"], 0))))
    G = pd.DataFrame(games); P = pd.DataFrame(players)
    rep = {}
    if len(G):
        G["act_total"] = G.act_home + G.act_away; G["act_margin"] = G.act_home - G.act_away; G["sim_margin"] = G.sim_home - G.sim_away; G["mkt_margin"] = -G.spread
        rep["games"] = len(G); rep["bias_total_vs_market"] = float((G.sim_total - G.total).mean()); rep["mae_total_vs_market"] = float((G.sim_total - G.total).abs().mean())
        g2 = G.dropna(subset=["act_total"])
        if len(g2):
            rep["mae_total_sim_vs_actual"] = float((g2.sim_total - g2.act_total).abs().mean()); rep["mae_total_market_vs_actual"] = float((g2.total - g2.act_total).abs().mean())
            rep["mae_margin_sim"] = float((g2.sim_margin - g2.act_margin).abs().mean()); rep["mae_margin_market"] = float((g2.mkt_margin - g2.act_margin).abs().mean())
            win = (g2.act_margin > 0).astype(float); rep["brier_home_sim"] = float(((g2.p_home - win) ** 2).mean())
            rep["sim_home_pct"] = float(g2.p_home.mean()); rep["actual_home_pct"] = float(win.mean())
    if len(P):
        P2 = P.dropna(subset=["p_gbm"]); y = P2.y.values
        from sklearn.metrics import roc_auc_score, log_loss
        rep["players"] = len(P2); rep["td_rate_actual"] = float(y.mean()); rep["td_rate_sim"] = float(P2.p_sim.mean()); rep["td_rate_gbm"] = float(P2.p_gbm.mean())
        rep["auc_sim"] = float(roc_auc_score(y, P2.p_sim)); rep["auc_gbm"] = float(roc_auc_score(y, P2.p_gbm)); rep["auc_blend"] = float(roc_auc_score(y, 0.5 * P2.p_sim + 0.5 * P2.p_gbm))
        rep["logloss_sim"] = float(log_loss(y, P2.p_sim.clip(1e-3, 1 - 1e-3))); rep["logloss_gbm"] = float(log_loss(y, P2.p_gbm.clip(1e-3, 1 - 1e-3))); rep["logloss_blend"] = float(log_loss(y, (0.5 * P2.p_sim + 0.5 * P2.p_gbm).clip(1e-3, 1 - 1e-3)))
        # usage: projected carries / targets vs actual, for players the sim gives at least one touch
        U = P[(P.car_sim + P.tgt_sim) >= 1.0]
        rep["usage_n"] = len(U); rep["usage_mae_car"] = float((U.car_sim - U.car_act).abs().mean()); rep["usage_mae_tgt"] = float((U.tgt_sim - U.tgt_act).abs().mean())
        rep["usage_corr_car"] = float(np.corrcoef(U.car_sim, U.car_act)[0, 1]); rep["usage_corr_tgt"] = float(np.corrcoef(U.tgt_sim, U.tgt_act)[0, 1])
        P2["band"] = pd.cut(P2.p_sim, [0, .05, .1, .2, .3, .45, .6, 1.0])
        rep["calibration"] = P2.groupby("band", observed=True).agg(n=("y", "size"), sim=("p_sim", "mean"), gbm=("p_gbm", "mean"), actual=("y", "mean")).round(3)
        rep["by_pos"] = P2.groupby("pos").agg(n=("y", "size"), sim=("p_sim", "mean"), gbm=("p_gbm", "mean"), actual=("y", "mean")).round(3)
    if verbose:
        print(f"\n== sim backtest · weeks {weeks} · n={n} · scales {scales}")
        for k, v in rep.items():
            if isinstance(v, (int, float)): print(f"  {k:<28} {v:.4f}" if isinstance(v, float) else f"  {k:<28} {v}")
        if "calibration" in rep: print("\n  P(TD) calibration by sim band:\n" + rep["calibration"].to_string()); print("\n  by position:\n" + rep["by_pos"].to_string())
        if len(G): print("\n  totals by week (sim / market / actual):"); print(G.groupby("week").agg(sim=("sim_total", "mean"), market=("total", "mean"), actual=("act_total", "mean")).round(1).to_string())
    rep["G"] = G; rep["P"] = P
    return rep

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--weeks", default="1-7"); ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--pass-scale", type=float, default=1.0); ap.add_argument("--run-scale", type=float, default=1.0); ap.add_argument("--pace-scale", type=float, default=1.0); ap.add_argument("--td-scale", type=float, default=1.0)
    a = ap.parse_args(); lo, hi = a.weeks.split("-") if "-" in a.weeks else (a.weeks, a.weeks)
    t0 = time.time(); run(a.season, list(range(int(lo), int(hi) + 1)), a.n, dict(pass_scale=a.pass_scale, run_scale=a.run_scale, pace_scale=a.pace_scale)); print(f"\n{time.time() - t0:.0f}s")
