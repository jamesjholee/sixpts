"""Weekly cheat sheet: every offense's top-10 ranks and every defense's top-10 most / fewest allowed, one card per
game, four games a page, in the sixpts look. PNGs for posting.

    python3 -m engine.cheatsheet --season 2026 --week 8            -> data/replays/w8/cheatsheet_w8_p{1..4}.png

Numbers are per game from nflverse play-by-play through the previous week (regular season). Fantasy points are
PPR: pass yds/25, 4 per pass TD, -2 per INT, rush/rec yds/10, 6 per TD, 1 per reception, -2 per fumble lost;
position comes from the nflverse players file. Defense "allows" is the same stat for the opponents a team faced.
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401
from engine import ingest
try:
    import skia
except ImportError:  # pragma: no cover
    sys.exit("pip install skia-python")
from engine.replay import TEAMS, Fonts, paint, text, col, INK, SURFACE, RAISE, RAISE2, LINE, LINE_S, PAINT, DIM, FAINT, BULB, BULB_HI, GAIN, LOSS, ordinal, _lum, SILVER

D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))
STATS = [("pts", "Points"), ("tot_yds", "Total Yds"), ("pass_yds", "Pass Yds"), ("rush_yds", "Rush Yds"), ("pass_td", "Pass TD"), ("rush_td", "Rush TD"),
         ("fp_QB", "QB Fantasy Pts"), ("fp_RB", "RB Fantasy Pts"), ("fp_WR", "WR Fantasy Pts"), ("fp_TE", "TE Fantasy Pts")]
TOP = 10

# ---- numbers --------------------------------------------------------------------------------------------------
def team_games(pbp: pd.DataFrame, positions: dict) -> pd.DataFrame:
    """One row per (team, game): the team's own offensive output. Defense allowed = the opponent's row."""
    p = pbp[(pbp.season_type == "REG") & pbp.posteam.notna() & (pbp.play_type.isin(["pass", "run", "qb_kneel", "qb_spike"]))].copy()
    g = p.groupby(["game_id", "posteam"])
    out = pd.DataFrame({
        "tot_yds": g.yards_gained.sum(), "pass_yds": g.passing_yards.sum(), "rush_yds": g.rushing_yards.sum(),
        "pass_td": g.pass_touchdown.sum(), "rush_td": g.rush_touchdown.sum()})
    # points: final score of the game for the team
    sc = pbp.groupby("game_id").agg(home=("home_team", "first"), away=("away_team", "first"), hs=("home_score", "max"), as_=("away_score", "max"))
    pts = {}
    for gid, r in sc.iterrows(): pts[(gid, r.home)] = r.hs; pts[(gid, r.away)] = r.as_
    out["pts"] = [pts.get(k, np.nan) for k in out.index]
    # fantasy points by position (PPR)
    fp = []
    for col_id, col_yds, col_td, kind in (("passer_player_id", "passing_yards", "pass_touchdown", "pass"), ("rusher_player_id", "rushing_yards", "rush_touchdown", "rush"), ("receiver_player_id", "receiving_yards", "pass_touchdown", "rec")):
        q = p[p[col_id].notna()]
        if kind == "pass": v = q[col_yds].fillna(0) / 25 + 4 * q[col_td].fillna(0) - 2 * q.interception.fillna(0)
        elif kind == "rush": v = q[col_yds].fillna(0) / 10 + 6 * q[col_td].fillna(0)
        else: v = q[col_yds].fillna(0) / 10 + 6 * q[col_td].fillna(0) + q.complete_pass.fillna(0)
        fp.append(pd.DataFrame({"game_id": q.game_id, "team": q.posteam, "pid": q[col_id], "fp": v.values}))
    fl = p[(p.fumble_lost == 1) & p.fumbled_1_player_id.notna()]
    fp.append(pd.DataFrame({"game_id": fl.game_id, "team": fl.posteam, "pid": fl.fumbled_1_player_id, "fp": -2.0}))
    F = pd.concat(fp); F["pos"] = F.pid.map(positions).fillna("")
    F = F[F.pos.isin(["QB", "RB", "WR", "TE"])].groupby(["game_id", "team", "pos"]).fp.sum().unstack("pos").fillna(0)
    for pos in ["QB", "RB", "WR", "TE"]: out[f"fp_{pos}"] = F.get(pos, pd.Series(dtype=float)).reindex(out.index).fillna(0)
    out.index.names = ["game_id", "team"]
    return out.reset_index()

def per_game(tg: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """-> (offense per game by team, defense allowed per game by team) with rank columns (1 = most)."""
    off = tg.groupby("team")[[k for k, _ in STATS]].mean()
    opp = tg.copy(); games = tg.groupby("game_id").team.apply(list)
    opp["team"] = [next(t for t in games[g] if t != tm) if len(games[g]) == 2 else None for g, tm in zip(opp.game_id, opp.team)]
    de = opp.dropna(subset=["team"]).groupby("team")[[k for k, _ in STATS]].mean()
    for df in (off, de):
        for k, _ in STATS:
            df[f"rk_{k}"] = df[k].round(1).rank(ascending=False, method="min").astype(int)      # most
            df[f"rkl_{k}"] = df[k].round(1).rank(ascending=True, method="min").astype(int)      # fewest
            df[f"tie_{k}"] = df.groupby(df[k].round(1))[k].transform("size") > 1
    return off, de

def rank_label(r: int, tie: bool) -> str: return ("T-" if tie else "") + ordinal(r).upper()

# ---- picture --------------------------------------------------------------------------------------------------
W, H = 1600, 2000
def team_color(t):
    p, s, _ = TEAMS.get(t, ("8794A5", "C9D1DB", t)); return p if _lum(p) > 0.10 else s

def draw_page(games, off, de, week, season, thru, page, pages, out):
    F = Fonts(); surf = skia.Surface.MakeRaster(skia.ImageInfo.Make(W, H, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType)); c = surf.getCanvas(); c.clear(col(INK))
    # header
    c.drawRect(skia.Rect.MakeLTRB(0, 0, W, 176), paint(col(SURFACE))); c.drawRect(skia.Rect.MakeLTRB(0, 174, W, 178), paint(col(BULB)))
    text(c, f"WEEK {week}", 64, 92, F.f(F.display, 76), col(PAINT)); w = F.f(F.display, 76).measureText(f"WEEK {week} ")
    text(c, "CHEAT SHEET", 64 + w, 92, F.f(F.display, 76), col(BULB))
    text(c, "Every offense's top-10 ranks · every defense's top-10 most and fewest allowed · per game", 64, 140, F.f(F.semi, 24), col(DIM))
    text(c, "sixpts", W - 64, 92, F.f(F.display, 54), col(BULB), "right"); text(c, "sixpts.com", W - 64, 136, F.f(F.semi, 22), col(DIM), "right")
    # legend
    y = 214; x = 64
    for colr, label in ((BULB, "OFFENSE · TOP 10"), (GAIN, "WEAK D · TOP 10 MOST ALLOWED"), (LOSS, "STRONG D · TOP 10 FEWEST ALLOWED")):
        c.drawRoundRect(skia.Rect.MakeXYWH(x, y - 16, 18, 18), 3, 3, paint(col(colr))); text(c, label, x + 28, y, F.f(F.bold, 18), col(PAINT)); x += F.f(F.bold, 18).measureText(label) + 70
    # cards
    cw, chh = (W - 64 * 2 - 28) / 2, 820; y0 = 250
    for k, g in enumerate(games):
        cx = 64 + (k % 2) * (cw + 28); cy = y0 + (k // 2) * (chh + 28)
        draw_card(c, F, g, off, de, cx, cy, cw, chh)
    text(c, f"Data: nflverse play-by-play · {season} regular season through Week {thru} · per game · fantasy = PPR", 64, H - 40, F.f(F.semi, 20), col(FAINT))
    text(c, f"{page} of {pages}", W - 64, H - 40, F.f(F.semi, 20), col(FAINT), "right")
    surf.makeImageSnapshot().save(str(out), skia.kPNG)

def draw_card(c, F, g, off, de, x, y, w, h):
    away, home = g["away"], g["home"]
    c.drawRoundRect(skia.Rect.MakeXYWH(x, y, w, h), 12, 12, paint(col(SURFACE))); c.drawRoundRect(skia.Rect.MakeXYWH(x, y, w, h), 12, 12, paint(col(LINE), "stroke", 2))
    c.save(); c.clipRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), 12, 12), True)
    c.drawRect(skia.Rect.MakeXYWH(x, y, w / 2, 7), paint(col(team_color(away)))); c.drawRect(skia.Rect.MakeXYWH(x + w / 2, y, w / 2, 7), paint(col(team_color(home)))); c.restore()
    # matchup line
    text(c, f"{away} @ {home}", x + 26, y + 58, F.f(F.display, 40), col(PAINT))
    text(c, f"{TEAMS.get(away, ('', '', away))[2]} at {TEAMS.get(home, ('', '', home))[2]}", x + 26, y + 88, F.f(F.semi, 20), col(DIM))
    kick = g.get("kickoff", ""); tf = F.f(F.display, 22); kw = tf.measureText(kick) + 36
    c.drawRoundRect(skia.Rect.MakeXYWH(x + w - 26 - kw, y + 34, kw, 44), 6, 6, paint(col(INK))); text(c, kick, x + w - 26 - kw / 2, y + 64, tf, col(BULB), "center")
    if g.get("total") or g.get("spread") is not None:
        line = f"O/U {g.get('total', '—')}" + (f" · {home} {g['spread']:+.1f}" if g.get("spread") is not None else "")
        text(c, line, x + w - 26, y + 104, F.f(F.semi, 18), col(DIM), "right")
    c.drawLine(x + 26, y + 118, x + w - 26, y + 118, paint(col(LINE_S), "stroke", 2))
    # two team blocks
    by = y + 134; bh = (h - 134 - 18) / 2
    for i, team in enumerate((away, home)):
        draw_team(c, F, team, off, de, x + 26, by + i * bh, w - 52, bh - 12)

def draw_team(c, F, team, off, de, x, y, w, h):
    c.drawRect(skia.Rect.MakeXYWH(x, y + 4, 6, 30), paint(col(team_color(team))))
    text(c, TEAMS.get(team, ("", "", team))[2].upper(), x + 18, y + 30, F.f(F.display, 28), col(PAINT))
    colw = (w - 24) / 2; lx, rx = x, x + colw + 24; hy = y + 62
    text(c, "OFFENSE RANKS", lx, hy, F.f(F.bold, 15), col(BULB)); c.drawLine(lx, hy + 8, lx + colw, hy + 8, paint(col(BULB), "stroke", 2))
    text(c, "DEFENSE ALLOWS", rx, hy, F.f(F.bold, 15), col(PAINT)); dw = F.f(F.bold, 15).measureText("DEFENSE ALLOWS ")
    text(c, "MOST", rx + dw, hy, F.f(F.bold, 15), col(GAIN)); mw = F.f(F.bold, 15).measureText("MOST ")
    text(c, "/ FEWEST", rx + dw + mw, hy, F.f(F.bold, 15), col(LOSS)); c.drawLine(rx, hy + 8, rx + colw, hy + 8, paint(col(LINE_S), "stroke", 2))
    rows_o, rows_d = [], []
    if team in off.index:
        o = off.loc[team]
        for k, label in STATS:
            if o[f"rk_{k}"] <= TOP: rows_o.append((o[f"rk_{k}"], rank_label(int(o[f"rk_{k}"]), bool(o[f"tie_{k}"])), label, o[k], BULB))
    if team in de.index:
        d = de.loc[team]
        for k, label in STATS:
            if d[f"rk_{k}"] <= TOP: rows_d.append((d[f"rk_{k}"], rank_label(int(d[f"rk_{k}"]), bool(d[f"tie_{k}"])), label, d[k], GAIN))
        for k, label in STATS:
            if d[f"rkl_{k}"] <= TOP: rows_d.append((100 + d[f"rkl_{k}"], rank_label(int(d[f"rkl_{k}"]), bool(d[f"tie_{k}"])), label, d[k], LOSS))
    rows_o.sort(key=lambda r: r[0]); rows_d.sort(key=lambda r: r[0])
    max_rows = int((h - 80) // 38)
    for cx, rows in ((lx, rows_o[:max_rows]), (rx, rows_d[:max_rows])):
        ry = hy + 24
        if not rows: text(c, "Not top 10 in anything", cx, ry + 26, F.f(F.med, 17), col(FAINT))
        for _, rk, label, val, colr in rows:
            draw_row(c, F, cx, ry, colw, rk, label, val, colr); ry += 38

def draw_row(c, F, x, y, w, rk, label, val, colr):
    cf = F.f(F.bold, 14); cw = max(56, cf.measureText(rk) + 18)
    c.drawRoundRect(skia.Rect.MakeXYWH(x, y + 6, cw, 26), 4, 4, paint(col(colr, 0.16))); c.drawRoundRect(skia.Rect.MakeXYWH(x, y + 6, cw, 26), 4, 4, paint(col(colr), "stroke", 1.5))
    text(c, rk, x + cw / 2, y + 24, cf, col(colr), "center")
    text(c, label, x + cw + 12, y + 25, F.f(F.semi, 18), col(PAINT))
    vf = F.f(F.display, 20); v = f"{val:.1f}"; vw = vf.measureText(v)
    text(c, v, x + w - 26, y + 25, vf, col(PAINT), "right"); text(c, "/G", x + w - 24, y + 25, F.f(F.bold, 12), col(FAINT))
    c.drawLine(x, y + 36, x + w, y + 36, paint(col(LINE), "stroke", 1))

# ---- main -----------------------------------------------------------------------------------------------------
def main(season: int, week: int, out_dir: pathlib.Path | None):
    pbp = ingest.load_pbp(season); pbp = pbp[pbp.week < week]
    pl = pd.read_parquet(D / "players.parquet") if (D / "players.parquet").exists() else pd.read_parquet(ingest.nflverse_file("players/players.parquet"))
    positions = dict(zip(pl.gsis_id, pl.position))
    tg = team_games(pbp, positions); off, de = per_game(tg)
    board = json.load(open(D / f"board_w{week}_public.json")); slate = board["slate"]; rows = board["board"]
    games = []
    for g in slate:
        gid = g["game"]; away, home = gid.split("_")[2], gid.split("_")[3]
        r_home = next((r for r in rows if r["game_id"] == gid and r["team"] == home), None)
        kick = g.get("kickoff", "")
        try: kick = pd.Timestamp(kick).strftime("%a %b %-d").upper()
        except Exception: pass
        games.append(dict(away=away, home=home, kickoff=kick, total=g.get("total"), spread=-float(r_home["spread"]) if r_home else None))  # standard sign
    out_dir = out_dir or (D / "replays" / f"w{week}"); out_dir.mkdir(parents=True, exist_ok=True)
    pages = max(1, (len(games) + 3) // 4); thru = int(pbp.week.max()) if len(pbp) else week - 1
    for pg in range(pages):
        out = out_dir / f"cheatsheet_w{week}_p{pg + 1}.png"
        draw_page(games[pg * 4:(pg + 1) * 4], off, de, week, season, thru, pg + 1, pages, out); print(out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); ap.add_argument("--out")
    a = ap.parse_args(); main(a.season, a.week, pathlib.Path(a.out) if a.out else None)
