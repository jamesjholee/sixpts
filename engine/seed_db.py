"""Seed the reference tables — teams, players, games — from nflverse.

    python3 -m engine.seed_db              # insert anything missing
    python3 -m engine.seed_db --refresh    # also refresh games (lines move, results land)

Postgres enforces the foreign keys that SQLite quietly ignores, so `odds`, `picks` and `scores`
can only hold players and games that exist here. Run this once after applying db/schema.sql,
and again whenever a new season's schedule appears.
"""
from __future__ import annotations
import argparse, os, sys, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401  (loads .env)
from engine import ingest
from sqlalchemy import text, inspect

TEAM_COL = ["abbr", "nfl_abbr"]

def _existing(conn, table: str, col: str) -> set:
    return {r[0] for r in conn.execute(text(f"select {col} from {table}")).all()}

def main(refresh: bool = False):
    eng = ingest.db()
    insp = inspect(eng)
    for t in ("teams", "players", "games"):
        if not insp.has_table(t):
            sys.exit(f"table '{t}' is missing — apply db/schema.sql first")

    players = pd.read_parquet(ingest.nflverse_file("players/players.parquet"))
    games = pd.read_parquet(ingest.nflverse_file("schedules/games.parquet", force=True))

    # ---- teams (from the schedule, so it always matches the game rows) ----
    abbrs = sorted(set(games.home_team.dropna()) | set(games.away_team.dropna()))
    with eng.begin() as c:
        have = _existing(c, "teams", "abbr")
        new = [{"abbr": a, "name": a} for a in abbrs if a not in have]
        if new: pd.DataFrame(new).to_sql("teams", c, if_exists="append", index=False)
    print(f"teams: {len(abbrs)} known, {len(new)} added")

    # ---- players ----
    p = players.rename(columns={"display_name": "name", "latest_team": "team"})
    keep = [c for c in ["gsis_id", "name", "position", "team"] if c in p.columns]
    p = p[keep].dropna(subset=["gsis_id"]).drop_duplicates("gsis_id")
    with eng.begin() as c:
        have = _existing(c, "players", "gsis_id")
        valid_teams = _existing(c, "teams", "abbr")
        add = p[~p.gsis_id.isin(have)].copy()
        if "team" in add: add.loc[~add.team.isin(valid_teams), "team"] = None   # FK on teams
        if len(add): add.to_sql("players", c, if_exists="append", index=False, chunksize=2000)
    print(f"players: {len(p)} known, {len(add)} added")

    # ---- games ----
    g = games.rename(columns={"home_team": "home", "away_team": "away", "gameday": "kickoff"})
    g = g[[c for c in ["game_id", "season", "week", "kickoff", "home", "away", "spread_line", "total_line", "result"] if c in g.columns]]
    g = g.dropna(subset=["game_id"]).drop_duplicates("game_id")
    g = g.astype(object).where(pd.notna(g), None)
    with eng.begin() as c:
        have = _existing(c, "games", "game_id")
        add_g = g[~g.game_id.isin(have)]
        if len(add_g): add_g.to_sql("games", c, if_exists="append", index=False, chunksize=2000)
        updated = 0
        if refresh:
            for r in g[g.game_id.isin(have)].itertuples(index=False):
                c.execute(text("update games set spread_line=:s, total_line=:t, result=:r where game_id=:g"),
                          {"s": r.spread_line, "t": r.total_line, "r": getattr(r, "result", None), "g": r.game_id})
                updated += 1
    print(f"games: {len(g)} known, {len(add_g)} added" + (f", {updated} refreshed" if refresh else ""))
    print("\nreference tables ready — odds, picks and scores can be written now")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--refresh", action="store_true"); a = ap.parse_args()
    main(a.refresh)
