"""Simulate every game on a week's slate and write data/sim_w{week}.json (public: nflverse inputs only).

    python3 -m engine.sim_week --season 2026 --week 8 [--n 5000]

Needs data/upcoming_w{week}.parquet (build_training --upcoming), the two play-by-play files, players.parquet and
the week's public board (slate, spreads, totals). Output per game: outcome probabilities and quantiles, per-player
TD / reception / yardage distributions, QB lines, and the play-by-play of the game nearest the median score for
the replay renderer. Runs in well under a minute for a full slate.
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys, time, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401
from engine import sim, model as M, ingest
from engine.features import make_features
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

def main(season: int, week: int, n: int):
    t0 = time.time()
    board = json.load(open(D / f"board_w{week}_public.json")); rows, slate = board["board"], board["slate"]
    p_prior = M.prep_pbp(ingest.load_pbp(season - 1)); p_cur = M.prep_pbp(ingest.load_pbp(season, force=True))
    T = sim.team_inputs(p_prior, p_cur[p_cur.week < week])
    pl = None
    try: pl = pd.read_parquet(ingest.nflverse_file("players/players.parquet"))
    except Exception: pass
    pfr2gsis = dict(zip(pl.pfr_id, pl.gsis_id)) if pl is not None else {}
    snaps = inj = ros = None
    try: snaps = ingest.load_snaps(season)
    except Exception as e: print("snap counts unavailable:", e)
    try: inj = pd.read_parquet(ingest.nflverse_file(f"injuries/injuries_{season}.parquet", force=True))
    except Exception as e: print("injuries unavailable:", e)
    try: ros = pd.read_parquet(ingest.nflverse_file(f"weekly_rosters/roster_weekly_{season}.parquet", force=True))
    except Exception as e: print("rosters unavailable:", e)
    dc = None
    try: dc = pd.read_parquet(ingest.nflverse_file(f"depth_charts/depth_charts_{season}.parquet", force=True))
    except Exception as e: print("depth charts unavailable:", e)
    recent = sim.recent_usage(p_cur, week, snaps=snaps, pfr2gsis=pfr2gsis); avail = sim.availability(inj, ros, week, sim.played_last(p_cur, week)); depth = sim.depth_prior(dc)
    n_out = sum(1 for v in avail.values() if v == 0); n_q = sum(1 for v in avail.values() if 0 < v < 1)
    print(f"availability: {n_out} players out/inactive, {n_q} reduced (questionable / did not practice)")
    feat = pd.read_parquet(D / f"upcoming_w{week}.parquet"); feat, _ = make_features(feat); feat = feat[(feat.season == season) & (feat.week == week)]
    names = dict(zip(pl.gsis_id, pl.display_name)) if pl is not None else {}
    names.update({r["gsis_id"]: r["player"] for r in rows})
    gbm = {(r["gsis_id"], r["game_id"]): r["p_model"] for r in rows}
    games = {}
    for i, g in enumerate(slate):
        gid = g["game"]; away, home = gid.split("_")[2], gid.split("_")[3]
        r_home = next((r for r in rows if r["game_id"] == gid and r["team"] == home), None)
        if r_home is None: continue
        spread, total = -float(r_home["spread"]), float(g["total"])   # board stores + = favoured; the sim takes the standard sign (- = home favoured)
        PH = sim.player_inputs(feat, home, names, recent, avail, depth); PA = sim.player_inputs(feat, away, names, recent, avail, depth)
        if not PH["ids"] or not PA["ids"]: print(f"skip {gid}: no players"); continue
        out = sim.simulate(home, away, T, PH, PA, spread, total, n=n, seed=season * 1000 + week * 50 + i, keep_log=True)
        s = sim.summarize(out, spread, total)
        for x in s["players"]: x["p_gbm"] = gbm.get((x["gsis_id"], gid))
        s["kickoff"] = g["kickoff"]; s["log"] = out["log"]
        games[gid] = s
        print(f"{away} @ {home}: home {s['p_home']:.0%} · median {s['median_away']:.0f}-{s['median_home']:.0f} · total {s['mean_total']:.1f} (mkt {total}) · over {s['p_over']:.0%} · {len(out['log']['plays'])} plays in the median game")
    out = dict(season=season, week=week, n=n, built=pd.Timestamp.utcnow().isoformat(), model="sim_v1",
               note="Simulated games, not predictions. Every number is a count over the simulated games; inputs are nflverse usage and team rates through the previous week plus the market spread and total.",
               games=games)
    json.dump(out, open(D / f"sim_w{week}.json", "w"))
    print(f"\n{len(games)} games x {n} sims in {time.time() - t0:.0f}s -> {D / f'sim_w{week}.json'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); ap.add_argument("--n", type=int, default=5000)
    a = ap.parse_args(); main(a.season, a.week, a.n)
