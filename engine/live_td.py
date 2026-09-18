"""Live touchdown watcher — pushes a notification the moment anyone scores.

    python3 -m engine.live_td --week 3                 # watch today's games
    python3 -m engine.live_td --week 3 --probe         # print what the feed looks like right now, then exit
    python3 -m engine.live_td --week 2 --replay data/espn_sample.json   # test the parser offline

Polling is cheap and one-sided: one scoreboard call every --interval seconds, and a game's
detail call only when that game's score actually moves. Between games it sleeps for minutes.

Notifications (set any of these in .env; all optional, all free except where noted):
  NTFY_TOPIC      a topic name you pick, e.g. sixpts-james-4821 — install the ntfy app, subscribe, done
  TELEGRAM_TOKEN  + TELEGRAM_CHAT_ID from @BotFather
  DISCORD_WEBHOOK a channel webhook URL
  (macOS always also fires a local Notification Center banner when run on a Mac)

Every touchdown is also appended to data/live_td_w{week}.json, which the site reads.
"""
from __future__ import annotations
import argparse, json, os, pathlib, platform, re, subprocess, sys, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))
SB = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUM = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={}"
UA = {"User-Agent": "sixpts/1.0 (personal research)"}

# ---------------- parsing (defensive: ESPN is undocumented and can change) ----------------
def get(d, *path, default=None):
    for k in path:
        if d is None: return default
        d = d.get(k) if isinstance(d, dict) else None
    return default if d is None else d

def games_from_scoreboard(j) -> list[dict]:
    out = []
    for e in j.get("events", []) or []:
        comp = (e.get("competitions") or [{}])[0]
        state = get(e, "status", "type", "state", default="pre")
        teams, score = {}, {}
        for c in comp.get("competitors", []) or []:
            ab = get(c, "team", "abbreviation", default="?")
            teams[c.get("homeAway", "?")] = ab
            try: score[ab] = int(c.get("score") or 0)
            except (TypeError, ValueError): score[ab] = 0
        out.append(dict(id=str(e.get("id")), name=e.get("shortName") or e.get("name") or "", state=state,
                        detail=get(e, "status", "type", "shortDetail", default=""), teams=teams, score=score,
                        total=sum(score.values())))
    return out

TD_RE = re.compile(r"^(?P<who>.+?)\s+(?P<yds>\d+)\s*(?:Yd|Yard)s?\s+(?P<how>pass from .+|run|rush|reception|interception return|fumble return|kickoff return|punt return)", re.I)

def touchdowns_from_summary(j, game_id: str, game_name: str) -> list[dict]:
    out = []
    for p in j.get("scoringPlays", []) or []:
        kind = (get(p, "scoringType", "name", default="") or get(p, "type", "abbreviation", default="")).lower()
        if "touchdown" not in kind and kind != "td": continue
        text = p.get("text", "") or ""
        m = TD_RE.match(text)
        scorer = (m.group("who").strip() if m else text.split(",")[0].strip())
        out.append(dict(
            play_id=str(p.get("id") or f"{game_id}:{p.get('period', {}).get('number')}:{get(p, 'clock', 'displayValue', default='')}:{text[:40]}"),
            game_id=game_id, game=game_name, scorer=scorer, text=text,
            team=get(p, "team", "abbreviation", default=""),
            how=(m.group("how").split(" from ")[0].lower() if m else ""), yards=int(m.group("yds")) if m else None,
            quarter=get(p, "period", "number", default=None), clock=get(p, "clock", "displayValue", default=""),
            away_score=p.get("awayScore"), home_score=p.get("homeScore"), at=time.strftime("%H:%M:%S")))
    return out

# ---------------- our pre-game number ----------------
def load_board(week: int) -> dict:
    f = D / f"board_w{week}_public.json"
    if not f.exists(): return {}
    idx = {}
    for r in json.load(open(f))["board"]:
        idx[norm(r["player"])] = r
        parts = r["player"].split()
        if len(parts) >= 2: idx[norm(f"{parts[0][0]}.{parts[-1]}")] = r     # J.Gibbs
    return idx

def norm(s: str) -> str:
    s = re.sub(r"[.'’]", "", s.lower()); s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s); return re.sub(r"\s+", " ", s).strip()

def line_for(td: dict, board: dict) -> tuple[str, str]:
    r = board.get(norm(td["scorer"]))
    head = f"{td['scorer']} TD" + (f" ({td['yards']} yd {td['how']})" if td["yards"] else "")
    bits = [f"{td['game']} · Q{td['quarter']} {td['clock']}"]
    if r:
        p = round(r["p_model"] * 100)
        bits.append(f"we said {p}% (fair {'+' if r['fair_odds'] > 0 else ''}{int(r['fair_odds'])})")
        if r.get("certainty_label"): bits.append(f"{r['certainty_label']} certainty")
    else:
        bits.append("not on our board")
    return head, " · ".join(bits)

# ---------------- notifiers ----------------
def notify(title: str, body: str, dry: bool = False):
    if dry: print(f"  [would notify] {title} — {body}"); return
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try: requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                           headers={"Title": title.encode("utf-8"), "Tags": "football", "Priority": "default"}, timeout=8)
        except Exception as e: print("ntfy failed:", e)
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try: requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": f"{title}\n{body}"}, timeout=8)
        except Exception as e: print("telegram failed:", e)
    hook = os.environ.get("DISCORD_WEBHOOK")
    if hook:
        try: requests.post(hook, json={"content": f"**{title}**\n{body}"}, timeout=8)
        except Exception as e: print("discord failed:", e)
    if platform.system() == "Darwin":
        try: subprocess.run(["osascript", "-e", f'display notification {json.dumps(body)} with title {json.dumps(title)} sound name "Glass"'], check=False)
        except Exception: pass

# ---------------- main loop ----------------
def fetch(url):
    r = requests.get(url, headers=UA, timeout=15); r.raise_for_status(); return r.json()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, required=True); ap.add_argument("--interval", type=int, default=25)
    ap.add_argument("--probe", action="store_true", help="print the current feed and exit")
    ap.add_argument("--replay", help="parse a saved scoreboard/summary JSON instead of calling ESPN")
    ap.add_argument("--dry-run", action="store_true", help="print notifications instead of sending them")
    a = ap.parse_args()
    board = load_board(a.week)
    feed_path = D / f"live_td_w{a.week}.json"
    seen: set[str] = set()
    feed: list[dict] = []
    if feed_path.exists():
        feed = json.load(open(feed_path)); seen = {t["play_id"] for t in feed}

    if a.replay:
        j = json.load(open(a.replay))
        tds = touchdowns_from_summary(j, "replay", j.get("_game", "REPLAY"))
        print(f"parsed {len(tds)} touchdowns from {a.replay}")
        for td in tds:
            title, body = line_for(td, board); print(f"  {title}\n    {body}"); notify(title, body, dry=True)
        return

    if a.probe:
        sb = fetch(SB); gs = games_from_scoreboard(sb)
        print(f"{len(gs)} games on the scoreboard right now:")
        for g in gs: print(f"  {g['name']:<12} {g['state']:<5} {g['detail']:<18} {g['score']}")
        live = [g for g in gs if g["state"] == "in"]
        if live:
            g = live[0]; s = fetch(SUM.format(g["id"])); tds = touchdowns_from_summary(s, g["id"], g["name"])
            print(f"\n{g['name']} has {len(tds)} touchdowns so far:")
            for td in tds: print("  ", line_for(td, board)[0], "|", line_for(td, board)[1])
        else:
            print("\nNo game in progress — run this again during a game to confirm the touchdown parse.")
        return

    print(f"watching week {a.week} · polling every {a.interval}s · {len(board)} players on the board")
    print("notifiers:", ", ".join(k for k, v in [("ntfy", os.environ.get("NTFY_TOPIC")), ("telegram", os.environ.get("TELEGRAM_TOKEN")), ("discord", os.environ.get("DISCORD_WEBHOOK"))] if v) or "local only")
    last_total: dict[str, int] = {}
    while True:
        try:
            gs = games_from_scoreboard(fetch(SB))
            live = [g for g in gs if g["state"] == "in"]
            if not live:
                nxt = [g for g in gs if g["state"] == "pre"]
                print(f"no games in progress{' · next: ' + nxt[0]['name'] + ' ' + nxt[0]['detail'] if nxt else ''} — sleeping 5 min")
                time.sleep(300); continue
            for g in live:
                if last_total.get(g["id"]) == g["total"]: continue      # score hasn't moved; no detail call
                last_total[g["id"]] = g["total"]
                for td in touchdowns_from_summary(fetch(SUM.format(g["id"])), g["id"], g["name"]):
                    if td["play_id"] in seen: continue
                    seen.add(td["play_id"]); feed.append(td)
                    title, body = line_for(td, board)
                    print(f"{time.strftime('%H:%M:%S')}  {title} | {body}")
                    notify(title, body, dry=a.dry_run)
                    json.dump(feed, open(feed_path, "w"))
            time.sleep(a.interval)
        except KeyboardInterrupt:
            print(f"\nstopped · {len(feed)} touchdowns recorded in {feed_path}"); return
        except Exception as e:
            print("poll failed:", e, "— retrying in 30s"); time.sleep(30)

if __name__ == "__main__":
    main()
