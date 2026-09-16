"""Data ingestion. Every write carries a `source` tag.

nflverse (public, CC-BY):  play-by-play, schedules (+Vegas lines), snap counts, weekly stats
PropFinder (private, needs your session): team-defense, td-matchups, coverage, alignment, props (odds)
"""
from __future__ import annotations
import os, json, time, pathlib, requests, pandas as pd
from sqlalchemy import create_engine, text

DATA = pathlib.Path(os.environ.get("SIXPTS_DATA", "data")); DATA.mkdir(exist_ok=True)
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
PF = "https://api.propfinder.app"

def db():
    return create_engine(os.environ.get("DATABASE_URL", "sqlite:///data/sixpts.db"))

# ---------------- nflverse ----------------
def nflverse_file(path: str, force: bool = False) -> pathlib.Path:
    out = DATA / pathlib.Path(path).name
    if out.exists() and not force and time.time() - out.stat().st_mtime < 6 * 3600:
        return out
    r = requests.get(f"{NFLVERSE}/{path}", timeout=120); r.raise_for_status(); out.write_bytes(r.content); return out

def load_pbp(season: int, force: bool = False) -> pd.DataFrame:
    return pd.read_parquet(nflverse_file(f"pbp/play_by_play_{season}.parquet", force))

def load_games() -> pd.DataFrame:
    return pd.read_parquet(nflverse_file("schedules/games.parquet", force=True))

def load_snaps(season: int) -> pd.DataFrame:
    return pd.read_parquet(nflverse_file(f"snap_counts/snap_counts_{season}.parquet", force=True))

def load_weekly_stats(season: int) -> pd.DataFrame:
    return pd.read_parquet(nflverse_file(f"stats_player/stats_player_week_{season}.parquet", force=True))

# ---------------- PropFinder (private) ----------------
def pf_headers() -> dict:
    """Session comes from env only. Never commit it. Get it from the browser's request headers (Cookie / Authorization)."""
    h = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Origin": "https://propfinder.app", "Referer": "https://propfinder.app/"}
    if os.environ.get("PF_COOKIE"): h["Cookie"] = os.environ["PF_COOKIE"]
    if os.environ.get("PF_BEARER"): h["Authorization"] = f"Bearer {os.environ['PF_BEARER']}"
    return h

def pf_get(endpoint: str, params: dict | None = None) -> dict | list:
    """One call, stored raw. Keep total daily volume at page-load scale (~10-15 calls)."""
    r = requests.get(f"{PF}{endpoint}", params=params, headers=pf_headers(), timeout=60); r.raise_for_status()
    payload = r.json()
    with db().begin() as c:
        c.execute(text("insert into raw_pulls(source, endpoint, params, payload) values ('pf', :e, :p, :j)"),
                  {"e": endpoint, "p": json.dumps(params or {}), "j": json.dumps(payload)})
    return payload

def pf_team_defense(year: int = 2025, position: str = "All", last_n: int = 0) -> pd.DataFrame:
    rows = pf_get("/NFL/team-defense", {"year": year, "type": "Opponent", "position": position, "lastNGames": last_n})["rows"]
    out = []
    for r in rows:
        s = r["stats"]
        out.append(dict(team=r["code"], season=s["year"], window=f"last{last_n}" if last_n else "season", as_of=s.get("lastUpdated", "")[:10],
            man_rate=s.get("manCoverageRate"), zone_rate=s.get("zoneCoverageRate"), one_high=s.get("oneHighCoverageRate"), two_high=s.get("twoHighCoverageRate"),
            cover0=s.get("cover0Rate"), cover1=s.get("cover1Rate"), cover2=s.get("cover2Rate"), cover2man=s.get("cover2ManRate"), cover3=s.get("cover3Rate"),
            cover4=s.get("cover4Rate"), cover6=s.get("cover6Rate"), blitzes=s.get("defenseBlitzes"), dropbacks=s.get("dropbacks"),
            rz_td_pct=s.get("efficiencyRedzonePct"), g2g_td_pct=s.get("efficiencyGoaltogoPct"), rz_tgt_allowed=s.get("receivingRedzoneTargets"),
            rz_rush_allowed=s.get("rushingRedzoneAttempts"), pass_td_allowed=s.get("touchdownsPass"), rush_td_allowed=s.get("touchdownsRush"),
            rz_trips=s.get("efficiencyRedzoneAttempts"), source="pf"))
    return pd.DataFrame(out)

def pf_team_defense_from_json(path: str) -> pd.DataFrame:
    """Fallback: a JSON you saved from the browser (same shape as the endpoint)."""
    raw = json.load(open(path))
    if "rows" in raw:  # full endpoint dump
        rows = raw["rows"]; out = []
        for r in rows:
            s = r["stats"]; out.append(dict(team=r["code"], season=s["year"], window="season", as_of=s.get("lastUpdated", "")[:10],
                man_rate=s["manCoverageRate"], zone_rate=s["zoneCoverageRate"], one_high=s["oneHighCoverageRate"], two_high=s["twoHighCoverageRate"],
                cover0=s["cover0Rate"], cover1=s["cover1Rate"], cover2=s["cover2Rate"], cover2man=s["cover2ManRate"], cover3=s["cover3Rate"], cover4=s["cover4Rate"], cover6=s["cover6Rate"],
                blitzes=s["defenseBlitzes"], dropbacks=s["dropbacks"], rz_td_pct=s["efficiencyRedzonePct"], g2g_td_pct=s["efficiencyGoaltogoPct"],
                rz_tgt_allowed=s["receivingRedzoneTargets"], rz_rush_allowed=s["rushingRedzoneAttempts"], pass_td_allowed=s["touchdownsPass"], rush_td_allowed=s["touchdownsRush"],
                rz_trips=s["efficiencyRedzoneAttempts"], source="pf"))
        return pd.DataFrame(out)
    # compact hand-transcribed shape {TEAM: {man, zone, ...}}
    out = [dict(team=k, season=2025, window="season", as_of="", man_rate=v["man"], zone_rate=v["zone"], one_high=v["one_high"], two_high=v["two_high"],
                cover1=v.get("c1"), cover3=v.get("c3"), blitzes=v.get("blitzes"), dropbacks=v.get("dropbacks"), rz_td_pct=v["rz_td_pct"], g2g_td_pct=v["g2g_td_pct"],
                rz_tgt_allowed=v["rz_tgt_allowed"], rz_rush_allowed=v["rz_rush_allowed"], pass_td_allowed=v["pass_td_allowed"], rush_td_allowed=v["rush_td_allowed"],
                rz_trips=v["rz_trips"], source="pf") for k, v in raw.items() if not k.startswith("_")]
    return pd.DataFrame(out)

def pf_props(category: str = "touchdowns", books=("draftkings", "fanduel", "betmgm")) -> pd.DataFrame:
    rows, cursor = [], None
    for _ in range(10):
        params = [("sportsbooks", b) for b in books] + [("category", category), ("overUnder", "over"), ("includeAlternates", "false"), ("sortBy", "l10"), ("sortDir", "desc"), ("limit", 100)]
        if cursor: params.append(("cursor", cursor))
        r = requests.get(f"{PF}/nfl/props", params=params, headers=pf_headers(), timeout=60); r.raise_for_status(); j = r.json()
        rows += j.get("items", j.get("rows", [])); cursor = j.get("nextCursor") or j.get("cursor")
        if not cursor: break
    return pd.DataFrame(rows)  # shape TBD — inspect once, then normalise into `odds` with source='pf'

# ---------------- write ----------------
def write(df: pd.DataFrame, table: str, replace_where: str | None = None):
    eng = db()
    with eng.begin() as c:
        if replace_where: c.execute(text(f"delete from {table} where {replace_where}"))
        df.to_sql(table, c, if_exists="append", index=False)
