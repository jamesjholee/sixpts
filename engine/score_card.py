"""Score a published board against what actually happened.

    python3 -m engine.score_card --season 2026 --week 1

Writes data/scorecard_w{week}.json (served publicly) and prints the summary.
This is the honest record of the model's predictions — separate from the picks record,
which only covers bets that were actually logged at a price.
"""
import argparse, json, os, pathlib, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import ingest, model as M
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

BUCKETS = [0, .10, .20, .30, .40, .50, 1.01]
LABELS = ["under 10%", "10–20%", "20–30%", "30–40%", "40–50%", "50%+"]

def main(season: int, week: int):
    f = D / f"board_w{week}_public.json"
    if not f.exists(): print(f"no board for week {week}"); return
    board = pd.DataFrame(json.load(open(f))["board"])
    p = M.prep_pbp(ingest.load_pbp(season, force=True)); p = p[p.week == week]
    if p.empty: print(f"week {week} hasn't been played yet"); return
    scored = p[p.touchdown == 1].groupby(["game_id", "td_player_id"]).size().rename("tds").reset_index().rename(columns={"td_player_id": "gsis_id"})
    played = set(p.receiver_player_id.dropna()) | set(p.rusher_player_id.dropna()) | set(p.passer_player_id.dropna())
    b = board.merge(scored, on=["gsis_id", "game_id"], how="left").fillna({"tds": 0})
    b["played"] = b.gsis_id.isin(played)
    b = b[b.played].copy()                      # inactive players aren't a prediction miss
    b["scored"] = (b.tds > 0).astype(int)

    n, act, pred = len(b), b.scored.mean(), b.p_model.mean()
    brier = float(((b.p_model - b.scored) ** 2).mean())
    base = float(((act - b.scored) ** 2).mean())
    skill = 1 - brier / base if base else 0.0
    b["bucket"] = pd.cut(b.p_model, BUCKETS, labels=LABELS, right=False)
    cal = b.groupby("bucket", observed=True).agg(n=("scored", "size"), predicted=("p_model", "mean"), actual=("scored", "mean")).reset_index()
    cal = [dict(bucket=str(r.bucket), n=int(r.n), predicted=round(float(r.predicted), 3), actual=round(float(r.actual), 3)) for r in cal.itertuples()]
    top = b.sort_values("p_model", ascending=False).head(20)
    top_rows = [dict(player=r.player, team=r.team, opp=r.opp, p_model=round(float(r.p_model), 3), tds=int(r.tds), scored=bool(r.scored)) for r in top.itertuples()]
    miss_low = b[(b.scored == 1)].sort_values("p_model").head(8)
    miss_high = b[(b.scored == 0)].sort_values("p_model", ascending=False).head(8)
    fmt = lambda df: [dict(player=r.player, team=r.team, opp=r.opp, p_model=round(float(r.p_model), 3), tds=int(r.tds)) for r in df.itertuples()]

    out = dict(season=season, week=week, n_players=int(n), actual_rate=round(float(act), 3), mean_prediction=round(float(pred), 3),
               brier=round(brier, 4), brier_skill_vs_base_rate=round(float(skill), 4),
               top20_hit_rate=round(float(top.scored.mean()), 3), top20_expected=round(float(top.p_model.mean()), 3),
               calibration=cal, top20=top_rows, scored_despite_low=fmt(miss_low), favored_but_blanked=fmt(miss_high))
    json.dump(out, open(D / f"scorecard_w{week}.json", "w"))

    print(f"Week {week}: {n} players who took a snap · actually scored {act:.1%} · model averaged {pred:.1%}")
    print(f"Brier {brier:.4f} (skill vs guessing the base rate: {skill:+.1%})")
    print("\nPredicted vs actual")
    for r in cal: print(f"  {r['bucket']:>9}  n={r['n']:>3}  said {r['predicted']:.0%}  actual {r['actual']:.0%}")
    print(f"\nTop 20 by P(TD): {top.scored.sum()}/20 scored (expected {top.p_model.sum():.1f})")
    for r in top_rows[:10]: print(f"  {'✓' if r['scored'] else '·'} {r['player']:<22} {r['team']} vs {r['opp']}  {r['p_model']:.0%}")
    print("\nScored despite a low number:"); [print(f"  {r['player']:<22} {r['p_model']:.0%} → {r['tds']} TD") for r in fmt(miss_low)]
    print("Highest numbers that blanked:"); [print(f"  {r['player']:<22} {r['p_model']:.0%}") for r in fmt(miss_high)]
    print(f"\nwrote {D / f'scorecard_w{week}.json'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); a = ap.parse_args(); main(a.season, a.week)
