"""The Odds API loader — licensed odds, safe to show publicly.

    python3 -m engine.odds_api --week 3                       # anytime TD, US books
    python3 -m engine.odds_api --week 3 --market receptions
    python3 -m engine.odds_api --week 3 --replay data/oddsapi_sample.json   # offline parse test
    python3 -m engine.odds_api --usage                        # credits left this month

Set ODDS_API_KEY in .env (free tier = 500 credits/month).
Billing: one credit per event × market × region, so a full NFL slate is ~16 credits per refresh.

Rows are written to `odds` with source='oddsapi'. Unlike PropFinder rows, these are licensed
for display, so the public board and the public picks engine can use them.
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, sys, time, unicodedata, requests, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401  (loads .env)
from engine import ingest
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))
BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"

MARKETS = {                       # our market name -> The Odds API market key
    "anytime_td": "player_anytime_td",
    "td_2plus": "player_1st_td",          # note: 2+ isn't offered; kept for shape parity
    "receptions": "player_receptions",
    "rec_yards": "player_reception_yds",
    "rush_yards": "player_rush_yds",
    "rush_att": "player_rush_attempts",
}
TEAM = {"Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS"}
ALIASES = {"kenneth gainwell": "kenny gainwell", "odell beckham": "odell beckham jr", "bam knight": "zonovan knight"}

def norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'’]", "", n); n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n); n = re.sub(r"\s+", " ", n).strip()
    return ALIASES.get(n, n)

def key() -> str:
    k = os.environ.get("ODDS_API_KEY")
    if not k: sys.exit("ODDS_API_KEY not set — get a free key at the-odds-api.com and put it in .env")
    return k

def _get(url: str, params: dict) -> tuple[object, dict]:
    r = requests.get(url, params={**params, "apiKey": key()}, timeout=25)
    if r.status_code == 401: sys.exit("401 — bad ODDS_API_KEY")
    if r.status_code == 422: sys.exit(f"422 — {r.text[:200]}")
    r.raise_for_status()
    return r.json(), {"used": r.headers.get("x-requests-used"), "left": r.headers.get("x-requests-remaining")}

def fetch_events() -> list[dict]:
    j, _ = _get(f"{BASE}/events", {}); return j

def fetch_props(event_id: str, market_key: str, regions: str, books: str | None) -> dict:
    p = {"regions": regions, "markets": market_key, "oddsFormat": "american"}
    if books: p["bookmakers"] = books
    j, credits = fetch_props.last_credits = _get(f"{BASE}/events/{event_id}/odds", p)
    return j
fetch_props.last_credits = (None, {})

# ---------------- normalize ----------------
def normalize(events: list[dict], board: pd.DataFrame, market: str) -> tuple[pd.DataFrame, list[str]]:
    """events: list of per-event odds payloads. board: the week's board (for gsis_id + game_id)."""
    idx = {}
    for r in board.itertuples():
        idx[(norm(r.player), r.team)] = (r.gsis_id, r.game_id)
        idx.setdefault(norm(r.player), (r.gsis_id, r.game_id))          # fallback on name alone
    rows, unmatched = [], []
    for ev in events:
        home, away = TEAM.get(ev.get("home_team", ""), ""), TEAM.get(ev.get("away_team", ""), "")
        for bk in ev.get("bookmakers", []) or []:
            book = bk.get("key", "")
            for mk in bk.get("markets", []) or []:
                for o in mk.get("outcomes", []) or []:
                    # anytime TD: {"name":"Yes","description":"Jahmyr Gibbs","price":-120}
                    # over/under: {"name":"Over","description":"Jahmyr Gibbs","price":-115,"point":4.5}
                    side = (o.get("name") or "").lower()
                    if side in ("no", "under"): continue
                    player = o.get("description") or o.get("name") or ""
                    hit = idx.get((norm(player), home)) or idx.get((norm(player), away)) or idx.get(norm(player))
                    if not hit: unmatched.append(f"{player} ({away}@{home})"); continue
                    gsis_id, game_id = hit
                    rows.append(dict(gsis_id=gsis_id, game_id=game_id, market=market, line=o.get("point", 0.5),
                                     book=book, price=int(o["price"]), source="oddsapi"))
    return pd.DataFrame(rows), sorted(set(unmatched))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int); ap.add_argument("--market", default="anytime_td", choices=list(MARKETS))
    ap.add_argument("--regions", default="us"); ap.add_argument("--books", default="draftkings,fanduel,betmgm,caesars")
    ap.add_argument("--replay"); ap.add_argument("--usage", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.usage:
        _, c = _get(f"{BASE}/events", {}); print(f"credits used {c['used']} · remaining {c['left']}"); return
    if not a.week: sys.exit("--week is required")

    bf = D / f"board_w{a.week}_public.json"
    if not bf.exists(): sys.exit(f"no board for week {a.week} — run score_week first")
    board = pd.DataFrame(json.load(open(bf))["board"])

    if a.replay:
        payload = json.load(open(a.replay)); events = payload if isinstance(payload, list) else [payload]
        credits = {"used": "-", "left": "-"}
    else:
        evs = fetch_events()
        wanted = set(board.game_id)
        events = []
        for ev in evs:
            home, away = TEAM.get(ev.get("home_team", ""), ""), TEAM.get(ev.get("away_team", ""), "")
            if not any(g.endswith(f"_{away}_{home}") for g in wanted): continue     # only this week's slate
            events.append(fetch_props(ev["id"], MARKETS[a.market], a.regions, a.books))
            time.sleep(0.25)
        credits = fetch_props.last_credits[1] if events else {"used": "?", "left": "?"}
        json.dump(events, open(D / f"oddsapi_raw_w{a.week}_{a.market}.json", "w"))

    df, unmatched = normalize(events, board, a.market)
    if df.empty: print("no rows parsed — check the market key and that books are posting yet"); return
    df = df.sort_values("price").drop_duplicates(["gsis_id", "game_id", "market", "line", "book"], keep="last")
    if not a.dry_run: ingest.write(df, "odds")
    best = df.sort_values("price", ascending=False).drop_duplicates("gsis_id")
    print(f"{len(events)} games · {len(df)} prices · {df.gsis_id.nunique()} players · books: {sorted(df.book.unique())}")
    print(f"credits used {credits.get('used')} · remaining {credits.get('left')}")
    if unmatched: print(f"unmatched ({len(unmatched)}): " + "; ".join(unmatched[:12]) + (" …" if len(unmatched) > 12 else ""))
    show = best.merge(board[["gsis_id", "player", "team", "opp", "p_model", "fair_odds"]], on="gsis_id").head(8)
    print("\nbest price vs our number:")
    for r in show.itertuples():
        ip = (-r.price) / (-r.price + 100) if r.price < 0 else 100 / (r.price + 100)
        print(f"  {r.player:<22} {r.team} vs {r.opp}  book {r.price:+5d} ({r.book})  model {r.p_model:.0%}  edge {100*(r.p_model-ip):+.1f}")

if __name__ == "__main__":
    main()
