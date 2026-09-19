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

def _insert(conn, sql: str, rows: list[dict], chunk: int = 1000):
    """Explicit parameterised insert. pandas' to_sql infers every object column as text, which
    makes Postgres reject a date column; binding real Python values avoids the cast entirely."""
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        if batch: conn.execute(text(sql), batch)

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
        _insert(c, "insert into teams (abbr, name) values (:abbr, :name)", new)
    print(f"teams: {len(abbrs)} known, {len(new)} added")

    # ---- players ----
    p = players.rename(columns={"display_name": "name", "latest_team": "team"})
    keep = [c for c in ["gsis_id", "name", "position", "team"] if c in p.columns]
    p = p[keep].dropna(subset=["gsis_id"]).drop_duplicates("gsis_id")
    with eng.begin() as c:
        have = _existing(c, "players", "gsis_id")
        valid_teams = _existing(c, "teams", "abbr")
        add = p[~p.gsis_id.isin(have)]
        recs = []
        for r in add.itertuples(index=False):
            d = {k: (None if pd.isna(getattr(r, k, None)) else getattr(r, k)) for k in keep}
            if d.get("team") not in valid_teams: d["team"] = None
            recs.append({"gsis_id": d["gsis_id"], "name": d.get("name"), "position": d.get("position"), "team": d.get("team")})
        _insert(c, "insert into players (gsis_id, name, position, team) values (:gsis_id, :name, :position, :team)", recs)
    print(f"players: {len(p)} known, {len(recs)} added")

    # ---- games ----
    g = games.rename(columns={"home_team": "home", "away_team": "away", "gameday": "kickoff"})
    cols = [c for c in ["game_id", "season", "week", "kickoff", "home", "away", "spread_line", "total_line", "result"] if c in g.columns]
    g = g[cols].dropna(subset=["game_id"]).drop_duplicates("game_id")
    g["kickoff"] = pd.to_datetime(g.kickoff, errors="coerce").dt.date        # real date objects, not strings
    with eng.begin() as c:
        have = _existing(c, "games", "game_id")
        valid = _existing(c, "teams", "abbr")
        keepable = g.home.isin(valid) & g.away.isin(valid)
        skipped = int((~keepable).sum())                                     # relocated franchises (SD, STL, OAK)
        g = g[keepable]
        add_g = g[~g.game_id.isin(have)]
        recs_g = []
        for r in add_g.itertuples(index=False):
            recs_g.append(dict(
                game_id=r.game_id,
                season=None if pd.isna(r.season) else int(r.season),
                week=None if pd.isna(r.week) else int(r.week),
                kickoff=None if pd.isna(r.kickoff) else r.kickoff,           # datetime.date -> DATE
                home=r.home, away=r.away,
                spread_line=None if pd.isna(getattr(r, "spread_line", None)) else float(r.spread_line),
                total_line=None if pd.isna(getattr(r, "total_line", None)) else float(r.total_line),
                result=None if pd.isna(getattr(r, "result", None)) else float(r.result)))
        _insert(c, """insert into games (game_id, season, week, kickoff, home, away, spread_line, total_line, result)
                      values (:game_id, :season, :week, :kickoff, :home, :away, :spread_line, :total_line, :result)""", recs_g)
        updated = 0
        if refresh:
            upd = [dict(g=r.game_id, s=None if pd.isna(getattr(r, "spread_line", None)) else float(r.spread_line),
                        t=None if pd.isna(getattr(r, "total_line", None)) else float(r.total_line),
                        r=None if pd.isna(getattr(r, "result", None)) else float(r.result))
                   for r in g[g.game_id.isin(have)].itertuples(index=False)]
            _insert(c, "update games set spread_line=:s, total_line=:t, result=:r where game_id=:g", upd)
            updated = len(upd)
    print(f"games: {len(g)} known, {len(recs_g)} added" + (f", {updated} refreshed" if refresh else "")
          + (f" ({skipped} skipped — teams no longer in the league)" if skipped else ""))

    print("\nreference tables ready — odds, picks and scores can be written now")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--refresh", action="store_true"); a = ap.parse_args()
    main(a.refresh)
