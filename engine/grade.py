"""Grade picks after games. Tuesday: python3 -m engine.grade --season 2026 --week 2
Marks won/lost, P&L in units at the price taken, and CLV vs closing price if you recorded one (PATCH /api/picks/{id}/close).
"""
import argparse, os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401  (loads .env)
from engine import ingest, model as M
from sqlalchemy import create_engine, text

def _pg(u: str) -> str:
    """Supabase hands out postgres:// or postgresql:// — pin the psycopg3 driver we install."""
    if u.startswith("postgres://"): return "postgresql+psycopg://" + u[len("postgres://"):]
    if u.startswith("postgresql://"): return "postgresql+psycopg://" + u[len("postgresql://"):]
    return u

def payout(price, won, stake=1.0):
    if not won: return -stake
    return stake * (price / 100 if price > 0 else 100 / -price)

def main(season, week):
    eng = create_engine(_pg(os.environ.get("DATABASE_URL", "sqlite:///data/sixpts.db")))
    p = M.prep_pbp(ingest.load_pbp(season, force=True)); p = p[p.week == week]
    if p.empty: print("no plays yet for that week"); return
    # actuals per player-game
    tds = p[p.touchdown == 1].groupby(["game_id", "td_player_id"]).size().rename("tds")
    rec = p[p.is_target & (p.complete_pass == 1)].groupby(["game_id", "receiver_player_id"]).size().rename("receptions")
    ryd = p[p.is_target].groupby(["game_id", "receiver_player_id"]).yards_gained.sum().rename("rec_yards")
    att = p[p.is_carry].groupby(["game_id", "rusher_player_id"]).size().rename("rush_att")
    rud = p[p.is_carry].groupby(["game_id", "rusher_player_id"]).yards_gained.sum().rename("rush_yards")
    with eng.begin() as c:
        picks = pd.DataFrame(c.execute(text("select * from picks where result is null and game_id like :w"), {"w": f"{season}_{week:02d}_%"}).mappings().all())
    if picks.empty: print("no ungraded picks for that week"); return
    for _, r in picks.iterrows():
        key = (r.game_id, r.gsis_id)
        if r.market == "anytime_td": actual = float(tds.get(key, 0)); won = actual >= 1
        elif r.market == "receptions": actual = float(rec.get(key, 0)); won = actual > r.line
        elif r.market == "rec_yards": actual = float(ryd.get(key, 0)); won = actual > r.line
        elif r.market == "rush_att": actual = float(att.get(key, 0)); won = actual > r.line
        elif r.market == "rush_yards": actual = float(rud.get(key, 0)); won = actual > r.line
        else: continue
        played = key in tds.index or key in rec.index or key in att.index or r.gsis_id in set(p.receiver_player_id.dropna()) | set(p.rusher_player_id.dropna())
        result = "void" if not played else ("won" if won else "lost")
        pnl = 0.0 if result == "void" else payout(r.price_taken, won, r.stake_units or 1.0)
        clv = None
        if r.closing_price is not None and pd.notna(r.closing_price):
            clv = round(M.implied(int(r.closing_price)) - M.implied(int(r.price_taken)), 4)  # positive = you beat the close
        with eng.begin() as c:
            c.execute(text("update picks set result=:res, actual=:a, pnl_units=:p, clv=:clv where id=:i"), {"res": result, "a": actual, "p": pnl, "clv": clv, "i": int(r.id)})
        print(f"{r.player:22s} {r.market:12s} {r.price_taken:+5d}  -> {result:5s} actual {actual:g}  pnl {pnl:+.2f}u" + (f"  clv {clv:+.3f}" if clv is not None else ""))
    with eng.begin() as c:
        tot = c.execute(text("select count(*) n, sum(pnl_units) pnl, avg(clv) clv from picks where result in ('won','lost')")).mappings().one()
    print(f"\nrecord to date: {tot['n']} graded, {tot['pnl']:+.2f} units, avg CLV {tot['clv'] if tot['clv'] is not None else 'n/a'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); a = ap.parse_args(); main(a.season, a.week)
