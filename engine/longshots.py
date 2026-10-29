"""The Longshot list for a week, exactly as the picks engine sees it.

    python3 -m engine.longshots --week 8            # before kickoff: the bet list, with fresh/stale and roster status
    python3 -m engine.longshots --week 7 --graded   # after the week: HIT / played / DNP per pick, units at best price

Fresh = the player still had a price in the latest odds refresh (after the post-inactives fetch, stale means the
books pulled his market and he is almost certainly inactive). Before Sunday's fetch everyone is fresh, so treat the
Tue-Sat list as research; the list after the 12:xx ET Sunday refresh is the one to bet.
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401
from engine.market import market_for_week, implied
from engine.select_picks import evaluate, LONGSHOT_STAKE
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

def _pg(u: str) -> str:
    if u.startswith("postgres://"): return "postgresql+psycopg://" + u[len("postgres://"):]
    if u.startswith("postgresql://"): return "postgresql+psycopg://" + u[len("postgresql://"):]
    return u

def _ts(x):
    if isinstance(x, str): x = datetime.fromisoformat(x.replace("Z", "+00:00"))
    return x if x.tzinfo else x.replace(tzinfo=timezone.utc)

def main(season: int, week: int, graded: bool):
    from sqlalchemy import create_engine, text
    board = json.load(open(D / f"board_w{week}_public.json"))["board"]
    eng = create_engine(_pg(os.environ.get("DATABASE_URL", "sqlite:///data/sixpts.db")))
    with eng.begin() as c:
        rows = c.execute(text("select gsis_id, game_id, book, price, fetched_at from odds where market='anytime_td' and source<>'pf' and game_id like :w order by fetched_at desc"), {"w": f"%_{week:02d}_%"}).mappings().all()
    if not rows: print(f"no odds stored for week {week}"); return
    mkt = market_for_week(rows, board)
    best, last = {}, {}
    for r in rows:
        k = (r["gsis_id"], r["game_id"])
        if (k, r["book"]) not in best: best[(k, r["book"])] = r["price"]
        last[k] = max(last.get(k, _ts(r["fetched_at"])), _ts(r["fetched_at"]))
    bp = {}
    for (k, _), p in best.items(): bp[k] = max(bp.get(k, -1e9), p)
    newest = max(last.values())
    print(f"Week {week} · latest odds refresh {newest.astimezone():%a %b %d %H:%M %Z} · {len(bp)} priced players")

    scored, played = set(), set()
    if graded:
        from engine import ingest, model as M
        p = M.prep_pbp(ingest.load_pbp(season, force=True)); p = p[p.week == week]
        if p.empty: print("week not played yet; showing the pre-game list"); graded = False
        else:
            scored = set(zip(p[p.touchdown == 1].td_player_id, p[p.touchdown == 1].game_id))
            played = set(p.receiver_player_id.dropna()) | set(p.rusher_player_id.dropna()) | set(p.passer_player_id.dropna())

    picks = []
    for r in board:
        k = (r["gsis_id"], r["game_id"])
        if k not in bp: continue
        fresh = (newest - last[k]).total_seconds() < 3 * 3600
        c = evaluate(r, int(bp[k]), "", p_market=mkt.get(f"{k[0]}|{k[1]}"), fresh=fresh)
        if c["tier"] != "Longshot" and not (c["tier"] == "Pass" and not fresh and "pulled his market" in c["headline"]): continue
        st = ""
        if graded: st = "HIT" if k in scored else ("played" if r["gsis_id"] in played else "DNP")
        picks.append(dict(player=r["player"], pos=r["position"], team=r["team"], opp=r["opp"], price=int(bp[k]), p_model=c["p_model"], p_market=c["p_market"], p_bet=c["p_bet"], edge=c["edge"] or 0, fresh=fresh, status=st, key=k))
    picks.sort(key=lambda x: -x["edge"])
    for x in picks:
        tag = ("FRESH" if x["fresh"] else "stale→Pass") if not graded else x["status"]
        print(f"{x['player']:<22} {x['pos']} {x['team']:<3} vs {x['opp']:<3} {x['price']:+6d}  model {x['p_model']:.0%} market {x['p_market']:.0%} bet {x['p_bet']:.0%}  +{x['edge']*100:.1f}  {tag}")
    live = [x for x in picks if x["fresh"]]
    print(f"\n{len(live)} longshots in the current list" + (f" ({len(picks) - len(live)} stale, passed)" if len(picks) != len(live) else "") + f" · flat {LONGSHOT_STAKE:.2f}u each = {LONGSHOT_STAKE * len(live):.2f}u")
    if graded:
        g = [x for x in picks if x["status"] != "DNP"]
        units = sum(((x["price"] / 100) if x["status"] == "HIT" else -1.0) for x in g)
        print(f"graded: {len(g)} dressed, {sum(x['status']=='HIT' for x in g)} hit, {units:+.1f}u at 1u ({units * LONGSHOT_STAKE:+.1f}u at {LONGSHOT_STAKE:.2f}u) · {len(picks) - len(g)} did not play (void at most books)")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); ap.add_argument("--graded", action="store_true")
    a = ap.parse_args(); main(a.season, a.week, a.graded)
