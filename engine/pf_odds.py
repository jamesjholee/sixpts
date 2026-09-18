"""PropFinder props board -> `odds` table (source='pf', private only).

Usage:
  python3 -m engine.pf_odds --week 2 --file data/props.json          # from a saved JSON page (or list of pages)
  python3 -m engine.pf_odds --week 2 --live                            # paginated pull with PF_COOKIE/PF_BEARER in .env
Options: --category touchdowns|receivingReceptions|receivingYards|rushingYards|rushingAttempts

Matching: PropFinder player -> our gsis_id by normalized name + team (+ position as tiebreak) against the week's board.
Unmatched names are printed so you can add an alias to ALIASES.
"""
from __future__ import annotations
import argparse, json, os, re, sys, unicodedata, pathlib, requests, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401  (loads .env)
from engine import ingest
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

TEAM_FIX = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS"}
MARKET = {"touchdowns": "anytime_td", "receivingReceptions": "receptions", "receivingYards": "rec_yards", "rushingYards": "rush_yards", "rushingAttempts": "rush_att"}
ALIASES = {"odell beckham": "odell beckham jr", "bam knight": "zonovan knight"}   # pf name -> our display name (normalized)

def norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'’]", "", n); n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n); n = re.sub(r"\s+", " ", n).strip()
    return ALIASES.get(n, n)

def load_pages(path: str) -> list[dict]:
    raw = json.load(open(path)); pages = raw if isinstance(raw, list) else [raw]
    return [it for p in pages for it in p.get("items", [])]

def pull_live(category: str, books=("draftkings", "fanduel", "betmgm", "caesars"), max_pages: int = 6) -> list[dict]:
    items, cursor = [], None
    for _ in range(max_pages):
        params = [("sportsbooks", b) for b in books] + [("category", category), ("overUnder", "over"), ("includeAlternates", "false"), ("sortBy", "l10"), ("sortDir", "desc"), ("limit", 100)]
        if cursor: params.append(("cursor", cursor))
        r = requests.get("https://api.propfinder.app/nfl/props", params=params, headers=ingest.pf_headers(), timeout=60); r.raise_for_status(); j = r.json()
        items += j.get("items", []); cursor = j.get("nextCursor")
        if not cursor: break
    return items

def normalize(items: list[dict], board: pd.DataFrame, category: str) -> tuple[pd.DataFrame, list[str]]:
    market = MARKET.get(category, category)
    board = board.assign(key=board.player.map(norm), team_k=board.team)
    by_key = {(k, t): (g, gid, p) for k, t, g, gid, p in zip(board.key, board.team_k, board.game_id, board.gsis_id, board.position)}
    by_name = {}
    for k, g, gid, p, t in zip(board.key, board.game_id, board.gsis_id, board.position, board.team_k): by_name.setdefault(k, []).append((g, gid, p, t))
    rows, unmatched = [], []
    for it in items:
        if it.get("overUnder", "over") != "over": continue
        line = float(it.get("line", 0.5))
        mk = market if not (market == "anytime_td" and line >= 1.5) else "td_2plus"
        team = TEAM_FIX.get(it["teamCode"], it["teamCode"]); k = norm(it["name"])
        hit = by_key.get((k, team))
        if not hit:
            cands = [c for c in by_name.get(k, []) if c[2] == it.get("position")] or by_name.get(k, [])
            if len(cands) == 1: hit = (cands[0][0], cands[0][1], cands[0][2])
        if not hit: unmatched.append(f"{it['name']} ({it['teamCode']} {it.get('position')})"); continue
        game_id, gsis_id, _ = hit
        for m in it.get("markets", []):
            rows.append(dict(gsis_id=gsis_id, game_id=game_id, market=mk, line=line, book=m["sportsbook"].lower(), price=int(m["price"]), source="pf",
                             pf_injury=it.get("injuryStatus"), pf_rating=it.get("pfRating")))
    return pd.DataFrame(rows), unmatched

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--week", type=int, required=True); ap.add_argument("--file"); ap.add_argument("--live", action="store_true")
    ap.add_argument("--category", default="touchdowns"); a = ap.parse_args()
    board = pd.DataFrame(json.load(open(D / f"board_w{a.week}.json"))["board"])
    items = pull_live(a.category) if a.live else load_pages(a.file)
    df, unmatched = normalize(items, board, a.category)
    if df.empty: print("no rows parsed"); return
    ingest.write(df.drop(columns=["pf_injury", "pf_rating"]), "odds")
    # PF's own injury status as a cross-check file for the board (private)
    df[["gsis_id", "pf_injury", "pf_rating"]].drop_duplicates("gsis_id").to_json(D / f"pf_status_w{a.week}.json", orient="records")
    best = df[df.market == "anytime_td"].sort_values("price", ascending=False).drop_duplicates("gsis_id")
    print(f"{len(items)} items -> {len(df)} price rows for {df.gsis_id.nunique()} players ({df.market.value_counts().to_dict()})")
    print(f"books: {sorted(df.book.unique())}")
    if unmatched: print(f"\nUNMATCHED ({len(unmatched)}): " + "; ".join(unmatched[:20]) + (" ..." if len(unmatched) > 20 else ""))
    q = df[df.pf_injury.str.lower().eq("questionable")].gsis_id.unique()
    if len(q): print(f"\nPropFinder lists {len(q)} of these players as questionable: " + ", ".join(board[board.gsis_id.isin(q)].player.tolist()))

if __name__ == "__main__":
    main()
