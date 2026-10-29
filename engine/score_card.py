"""Score a published board against what actually happened.

    python3 -m engine.score_card --season 2026 --week 1

Writes data/scorecard_w{week}.json (served publicly) and prints the summary.
This is the honest record of the model's predictions — separate from the picks record,
which only covers bets that were actually logged at a price.
"""
import argparse, json, os, pathlib, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401  (loads .env)
from engine import ingest, model as M
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

BUCKETS = [0, .10, .20, .30, .40, .50, 1.01]
LABELS = ["under 10%", "10–20%", "20–30%", "30–40%", "40–50%", "50%+"]

def vs_market(b: pd.DataFrame, week: int) -> dict | None:
    """The benchmark that matters: did we beat the book? Uses the last stored price per (player, book) for the
    week — the Friday refresh, close to closing. Reports model vs market Brier/AUC on the same players, the hit
    rate of every edge bucket (model-vs-best-price, the old definition), and flat-bet ROI of the blended picks."""
    from sqlalchemy import create_engine, text
    from engine.market import market_for_week, blend, implied
    from engine.grade import _pg
    try:
        eng = create_engine(_pg(os.environ.get("DATABASE_URL", "sqlite:///data/sixpts.db")))
        with eng.begin() as c:
            rows = c.execute(text("select gsis_id, game_id, book, price, fetched_at from odds where market='anytime_td' and source<>'pf' and game_id like :w order by fetched_at desc"), {"w": f"%_{week:02d}_%"}).mappings().all()
    except Exception as e:
        print("no odds table / db:", e); return None
    if not rows: return None
    board = b.to_dict("records")
    mkt = market_for_week(rows, board)
    best = {}
    for r in rows:
        k = f"{r['gsis_id']}|{r['game_id']}"; kb = (k, r["book"])
        if kb in best: continue
        best[kb] = r["price"]
    bestp = {}
    for (k, _), pr in best.items(): bestp[k] = max(bestp.get(k, -1e9), pr)
    b = b.copy(); b["key"] = b.gsis_id + "|" + b.game_id
    b["p_market"] = b.key.map(mkt); b["best"] = b.key.map(bestp)
    m = b.dropna(subset=["p_market", "best"]).copy()
    if len(m) < 30: return None
    m["p_bet"] = [blend(p, q, c)[0] for p, q, c in zip(m.p_model, m.p_market, m.get("certainty_label", pd.Series(["medium"] * len(m), index=m.index)))]
    m["ip_best"] = m.best.map(implied)
    y = m.scored.values
    from sklearn.metrics import roc_auc_score
    def sc(p): return dict(brier=round(float(((p - y) ** 2).mean()), 4), auc=round(float(roc_auc_score(y, p)), 3)) if 0 < y.mean() < 1 else dict(brier=None, auc=None)
    edges = m.p_model - m.ip_best
    buckets = []
    for lo, hi, lab in [(-9, -.05, "book higher by 5+"), (-.05, -.02, "book higher 2–5"), (-.02, .02, "agree ±2"), (.02, .05, "we're higher 2–5"), (.05, .10, "we're higher 5–10"), (.10, 9, "we're higher 10+")]:
        s = m[(edges > lo) & (edges <= hi)]
        if len(s): buckets.append(dict(bucket=lab, n=int(len(s)), model=round(float(s.p_model.mean()), 3), market=round(float(s.ip_best.mean()), 3), actual=round(float(s.scored.mean()), 3)))
    dec = m.best.map(lambda o: 1 + (o / 100 if o > 0 else 100 / -o))
    pnl_raw = np.where(m.scored == 1, dec - 1, -1.0)
    from engine.select_picks import evaluate as _ev
    sel_old = (edges >= .03)
    # the shipped rule: every row through the real picks engine, Longshot tier only
    tiers = [_ev(dict(r), int(bp), "", p_market=pmk)["tier"] for r, bp, pmk in zip(m.to_dict("records"), m.best, m.p_market)]
    sel_new = pd.Series([t == "Longshot" for t in tiers], index=m.index)
    roi = lambda sel: dict(n=int(sel.sum()), units=round(float(pnl_raw[sel].sum()), 2), roi=round(float(pnl_raw[sel].mean()), 3) if sel.sum() else None, hit=round(float(m.scored[sel].mean()), 3) if sel.sum() else None, avg_price=int(m.best[sel].mean()) if sel.sum() else None)
    return dict(n=int(len(m)), model=sc(m.p_model.values), market=sc(m.p_market.values), blend=sc(m.p_bet.values),
                edge_buckets=buckets, flat_bet_model_edge_3=roi(sel_old), flat_bet_blend_edge_3=roi(sel_new), note="flat_bet_blend_edge_3 is the Longshot tier as shipped (engine.select_picks), flat 1u per pick")

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
    mk = vs_market(b, week)
    if mk: out["market"] = mk
    try:
        from engine import sim_grade
        sg = sim_grade.grade(season, week, p)
        if sg: out["sim"] = sg
    except Exception as e: print("sim grade skipped:", e)
    json.dump(out, open(D / f"scorecard_w{week}.json", "w"))

    print(f"Week {week}: {n} players who took a snap · actually scored {act:.1%} · model averaged {pred:.1%}")
    print(f"Brier {brier:.4f} (skill vs guessing the base rate: {skill:+.1%})")
    print("\nPredicted vs actual")
    for r in cal: print(f"  {r['bucket']:>9}  n={r['n']:>3}  said {r['predicted']:.0%}  actual {r['actual']:.0%}")
    print(f"\nTop 20 by P(TD): {top.scored.sum()}/20 scored (expected {top.p_model.sum():.1f})")
    for r in top_rows[:10]: print(f"  {'✓' if r['scored'] else '·'} {r['player']:<22} {r['team']} vs {r['opp']}  {r['p_model']:.0%}")
    print("\nScored despite a low number:"); [print(f"  {r['player']:<22} {r['p_model']:.0%} → {r['tds']} TD") for r in fmt(miss_low)]
    print("Highest numbers that blanked:"); [print(f"  {r['player']:<22} {r['p_model']:.0%}") for r in fmt(miss_high)]
    if mk:
        print(f"\nVs the market ({mk['n']} priced players): model AUC {mk['model']['auc']} · market AUC {mk['market']['auc']} · blend AUC {mk['blend']['auc']}")
        for e in mk["edge_buckets"]: print(f"  {e['bucket']:<20} n={e['n']:3d}  model {e['model']:.0%}  market {e['market']:.0%}  actual {e['actual']:.0%}")
        o, n2 = mk["flat_bet_model_edge_3"], mk["flat_bet_blend_edge_3"]
        print(f"  flat-bet 3+ pts edge, model-vs-price: {o['n']} bets {o['units']:+.1f}u · Longshot tier as shipped: {n2['n']} bets {n2['units']:+.1f}u (1u each)")
    print(f"\nwrote {D / f'scorecard_w{week}.json'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); a = ap.parse_args(); main(a.season, a.week)
