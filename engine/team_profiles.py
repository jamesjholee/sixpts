"""Team profiles: what each offense and defense is doing, 2025 baseline vs 2026 to-date.
python -m engine.team_profiles --season 2026 --week 2   -> data/teams_w2.json
"""
import argparse, json, os, pathlib, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401  (loads .env)
from engine import model as M, ingest
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

def team_off(p):
    g = p.groupby(["posteam", "game_id"])
    per = g.agg(plays=("play_id", "count"), pass_rate=("play_type", lambda s: (s == "pass").mean()), pass_oe=("pass_oe", "mean"),
                td=("touchdown", "sum"), xtd=("xtd_play", "sum")).reset_index()
    rz = p[p.yardline_100 <= 20].groupby(["posteam", "game_id"]).agg(rz_trips=("drive", "nunique"), rz_pass_rate=("play_type", lambda s: (s == "pass").mean()), rz_td=("touchdown", "sum")).reset_index()
    per = per.merge(rz, on=["posteam", "game_id"], how="left").fillna({"rz_trips": 0, "rz_pass_rate": np.nan, "rz_td": 0})
    neu = p[p.wp.between(0.2, 0.8) & (p.game_seconds_remaining > 300)].groupby(["posteam", "game_id"]).play_type.apply(lambda s: (s == "pass").mean()).rename("neutral_pass_rate").reset_index()
    per = per.merge(neu, on=["posteam", "game_id"], how="left")
    out = per.groupby("posteam").agg(games=("game_id", "nunique"), plays=("plays", "mean"), pass_rate=("pass_rate", "mean"), neutral_pass_rate=("neutral_pass_rate", "mean"),
                                     pass_oe=("pass_oe", "mean"), rz_trips=("rz_trips", "mean"), rz_pass_rate=("rz_pass_rate", "mean"),
                                     rz_td_pct=("rz_td", "sum"), td_pg=("td", "mean"), xtd_pg=("xtd", "mean")).reset_index()
    tot_trips = per.groupby("posteam").rz_trips.sum(); out["rz_td_pct"] = (out.rz_td_pct / tot_trips.reindex(out.posteam).values).round(3)
    return out.round(3)

def team_def(p, posmap):
    p = p.copy(); p["scorer_pos"] = p.td_player_id.map(posmap)
    g = p.groupby(["defteam", "game_id"])
    per = g.agg(td=("touchdown", "sum"), xtd=("xtd_play", "sum"), pass_td=("pass_touchdown", "sum"), rush_td=("rush_touchdown", "sum")).reset_index()
    rz = p[p.yardline_100 <= 20].groupby(["defteam", "game_id"]).agg(rz_trips=("drive", "nunique"), rz_td=("touchdown", "sum")).reset_index()
    per = per.merge(rz, on=["defteam", "game_id"], how="left").fillna(0)
    for pos in ["RB", "WR", "TE"]:
        d = p[(p.touchdown == 1) & (p.scorer_pos == pos)].groupby(["defteam", "game_id"]).size().rename(f"td_{pos}").reset_index()
        per = per.merge(d, on=["defteam", "game_id"], how="left").fillna({f"td_{pos}": 0})
    out = per.groupby("defteam").agg(games=("game_id", "nunique"), td_allowed=("td", "mean"), xtd_allowed=("xtd", "mean"), pass_td=("pass_td", "mean"), rush_td=("rush_td", "mean"),
                                     rz_trips=("rz_trips", "mean"), rz_td=("rz_td", "sum"), td_RB=("td_RB", "mean"), td_WR=("td_WR", "mean"), td_TE=("td_TE", "mean")).reset_index()
    tot = per.groupby("defteam").rz_trips.sum(); out["rz_td_pct"] = (out.rz_td / tot.reindex(out.defteam).values).round(3)
    out["td_over_x"] = (out.td_allowed - out.xtd_allowed).round(3)
    return out.drop(columns="rz_td").round(3)

def role_shares(p, names):
    """Who owns the TD-relevant work on each offense: RZ targets, end-zone targets, carries inside the 5, and xTD share."""
    t = p[p.is_target]; r = p[p.is_carry]
    tg = t.groupby(["posteam", "receiver_player_id"]).agg(targets=("play_id", "count"), rz_tgt=("yardline_100", lambda s: (s <= 20).sum()), ez_tgt=("catch_yl", lambda s: (s <= 0).sum()), xtd=("xtd_play", "sum"), td=("pass_touchdown", "sum")).reset_index().rename(columns={"receiver_player_id": "gsis_id"})
    ru = r.groupby(["posteam", "rusher_player_id"]).agg(carries=("play_id", "count"), rz_carry=("yardline_100", lambda s: (s <= 20).sum()), i5_carry=("yardline_100", lambda s: (s <= 5).sum()), xtd_r=("xtd_play", "sum"), td_r=("rush_touchdown", "sum")).reset_index().rename(columns={"rusher_player_id": "gsis_id"})
    u = tg.merge(ru, on=["posteam", "gsis_id"], how="outer").fillna(0)
    u["xtd"] = u.xtd + u.xtd_r; u["td"] = u.td + u.td_r; u = u.drop(columns=["xtd_r", "td_r"])
    for c in ["rz_tgt", "ez_tgt", "i5_carry", "xtd", "targets", "carries"]:
        tot = u.groupby("posteam")[c].transform("sum").replace(0, np.nan); u[f"{c}_share"] = (u[c] / tot).round(3)
    u = u.merge(names, on="gsis_id", how="left")
    return u

def main(season, week):
    prev, cur = M.prep_pbp(ingest.load_pbp(season - 1)), M.prep_pbp(ingest.load_pbp(season, force=True)); cur = cur[cur.week < week]
    no_cur = cur.empty   # week 1: nothing has been played yet this season
    pr, rr = M.fit_xtd(prev)
    for p in (prev, cur):
        p["xtd_play"] = 0.0
        t, r = p.is_target, p.is_carry
        p.loc[t, "xtd_play"] = pd.cut(p.loc[t, "catch_yl"], *M.PASS_BINS).astype(str).map(pr).fillna(pr.mean()).values
        p.loc[r, "xtd_play"] = pd.cut(p.loc[r, "yardline_100"], *M.RUSH_BINS).astype(str).map(rr).fillna(rr.mean()).values
    players = pd.read_parquet(ingest.nflverse_file("players/players.parquet"))
    posmap = players.set_index("gsis_id").position; names = players[["gsis_id", "display_name", "position"]].drop_duplicates("gsis_id")
    off_prev, def_prev, roles_prev = team_off(prev), team_def(prev, posmap), role_shares(prev, names)
    off_cur = off_prev.iloc[0:0] if no_cur else team_off(cur)
    def_cur = def_prev.iloc[0:0] if no_cur else team_def(cur, posmap)
    roles_cur = roles_prev.iloc[0:0] if no_cur else role_shares(cur, names)
    games = pd.read_parquet(ingest.nflverse_file("schedules/games.parquet", force=True)); g = games[(games.season == season) & (games.week == week)]
    nxt = {}
    for _, gm in g.iterrows():
        nxt[gm.home_team] = dict(opp=gm.away_team, home=True, implied=(gm.total_line + gm.spread_line) / 2, total=gm.total_line, spread=gm.spread_line)
        nxt[gm.away_team] = dict(opp=gm.home_team, home=False, implied=(gm.total_line - gm.spread_line) / 2, total=gm.total_line, spread=-gm.spread_line)
    # ---- call-outs ----
    # last-game role vs season-before-last-game (needs >=2 games); Week 2 falls back to "vs last season"
    def week_shares(p):
        t = p[p.is_target]; r = p[p.is_carry]
        a = t.groupby(["posteam", "week", "receiver_player_id"]).agg(rz_tgt=("yardline_100", lambda s: (s <= 20).sum()), xtd=("xtd_play", "sum")).reset_index().rename(columns={"receiver_player_id": "gsis_id"})
        b = r.groupby(["posteam", "week", "rusher_player_id"]).agg(i5=("yardline_100", lambda s: (s <= 5).sum()), xtd_r=("xtd_play", "sum")).reset_index().rename(columns={"rusher_player_id": "gsis_id"})
        u = a.merge(b, on=["posteam", "week", "gsis_id"], how="outer").fillna(0); u["xtd"] = u.xtd + u.xtd_r
        for c in ["rz_tgt", "i5", "xtd"]:
            tot = u.groupby(["posteam", "week"])[c].transform("sum")
            u[f"{c}_share"] = np.where(tot >= 2, u[c] / tot.replace(0, np.nan), np.nan)   # no share without a real denominator
        return u
    ws = week_shares(cur) if not no_cur else pd.DataFrame(columns=["posteam", "week", "gsis_id"])
    last_wk = int(ws.week.max()) if not ws.empty else 0
    callouts = {tm: [] for tm in set(off_prev.posteam) | set(off_cur.posteam)}
    if last_wk >= 1:
        lastg = ws[ws.week == last_wk]
        before = ws[ws.week < last_wk].groupby(["posteam", "gsis_id"])[["rz_tgt_share", "i5_share", "xtd_share"]].mean().reset_index() if last_wk >= 2 else roles_prev[["posteam", "gsis_id", "rz_tgt_share", "i5_carry_share", "xtd_share"]].rename(columns={"i5_carry_share": "i5_share"})
        cmp = lastg.merge(before, on=["posteam", "gsis_id"], how="left", suffixes=("", "_before")).merge(names, on="gsis_id", how="left")
        basis = f"vs weeks 1-{last_wk-1}" if last_wk >= 2 else "vs 2025"
        for _, r in cmp.iterrows():
            nm = r.display_name or r.gsis_id
            for c, lab, thr in [("i5_share", "goal-line carry share", 0.35), ("rz_tgt_share", "red zone target share", 0.25), ("xtd_share", "expected-TD share", 0.25)]:
                b = r.get(f"{c}_before"); v = r[c]
                if pd.isna(b) or pd.isna(v): continue   # team had too few of these events to measure a share
                if v - b >= thr: callouts[r.posteam].append(f"{nm} ({r.position}): {lab} {v:.0%} in Week {last_wk}, {basis} {b:.0%} — role rising")
                if b - v >= thr and b >= 0.4: callouts[r.posteam].append(f"{nm} ({r.position}): {lab} {v:.0%} in Week {last_wk}, {basis} {b:.0%} — role falling")
        # teams with nothing to measure
        for tm, g in (cur.groupby("posteam") if not no_cur else []):
            if g[g.yardline_100 <= 20].drive.nunique() == 0: callouts[tm].append(f"No red zone trips in Week {last_wk} — role shares can't be read yet")
    # regression: TDs vs expected, season to date (needs >= 1 game; flag only large gaps)
    for _, r in (roles_cur.merge(names[["gsis_id"]], on="gsis_id") if not roles_cur.empty else roles_cur).iterrows():
        if r.xtd >= 0.6 and r.td - r.xtd >= 1.0: callouts[r.posteam].append(f"{r.display_name} ({r.position}): {int(r.td)} TD on {r.xtd:.1f} expected — regression risk")
        if r.xtd >= 0.8 and r.xtd - r.td >= 0.8: callouts[r.posteam].append(f"{r.display_name} ({r.position}): {int(r.td)} TD on {r.xtd:.1f} expected — due")
    # depth chart changes (latest snapshot vs previous snapshot), offense skill positions only
    try:
        dc = pd.read_parquet(ingest.nflverse_file(f"depth_charts/depth_charts_{season}.parquet", force=True))
        dc = dc[dc.pos_abb.isin(["RB", "WR", "TE", "QB", "FB"])].copy(); dc["dt"] = pd.to_datetime(dc.dt)
        snaps_dt = sorted(dc.dt.unique())
        if len(snaps_dt) >= 2:
            a, b = dc[dc.dt == snaps_dt[-1]], dc[dc.dt == snaps_dt[-2]]
            m = a.merge(b[["team", "gsis_id", "pos_rank"]], on=["team", "gsis_id"], how="outer", suffixes=("", "_prev"), indicator=True)
            for _, r in m.iterrows():
                tm = r.team
                if tm not in callouts: continue
                if r._merge == "left_only" and r.pos_rank == 1: callouts[tm].append(f"Depth chart: {r.player_name} ({r.pos_abb}) newly listed as starter")
                elif r._merge == "both" and pd.notna(r.pos_rank_prev) and r.pos_rank != r.pos_rank_prev and min(r.pos_rank, r.pos_rank_prev) == 1:
                    callouts[tm].append(f"Depth chart: {r.player_name} ({r.pos_abb}) moved {int(r.pos_rank_prev)} -> {int(r.pos_rank)}")
    except Exception as e:
        print("depth chart call-outs skipped:", e)
    # injuries this week (skill positions)
    try:
        inj = pd.read_parquet(ingest.nflverse_file(f"injuries/injuries_{season}.parquet", force=True)); inj = inj[(inj.week == week) & inj.position.isin(["RB", "WR", "TE", "QB"])]
        for _, r in inj[inj.report_status.isin(["Out", "Doubtful", "Questionable"])].iterrows():
            tm = r.team.replace("LAR", "LA")
            if tm in callouts: callouts[tm].append(f"Injury report: {r.full_name} ({r.position}) {r.report_status}" + (f" — {r.report_primary_injury}" if pd.notna(r.report_primary_injury) else ""))
    except Exception as e:
        print("injury call-outs skipped:", e)
    # weather
    for _, gm in g.iterrows():
        if gm.roof not in ("dome", "closed"):
            note = []
            if pd.notna(gm.wind) and gm.wind >= 15: note.append(f"wind {gm.wind:.0f} mph")
            if pd.notna(gm.temp) and gm.temp <= 32: note.append(f"{gm.temp:.0f}°F")
            if note:
                for tm in (gm.home_team, gm.away_team):
                    if tm in callouts: callouts[tm].append("Weather: " + ", ".join(note) + " — passing TDs suppressed")
    teams = sorted(set(off_prev.posteam) | set(off_cur.posteam))
    def rec(df, key, team): 
        r = df[df[key] == team]; return None if r.empty else {k: (None if pd.isna(v) else v) for k, v in r.iloc[0].drop(key).items()}
    def top_roles(df, team, n=6):
        r = df[df.posteam == team].sort_values("xtd", ascending=False).head(n)
        return [{k: (None if pd.isna(v) else v) for k, v in row.items()} for _, row in r[["gsis_id", "display_name", "position", "targets", "carries", "rz_tgt", "ez_tgt", "i5_carry", "xtd", "td", "rz_tgt_share", "ez_tgt_share", "i5_carry_share", "xtd_share"]].iterrows()]
    out = {}
    for tm in teams:
        out[tm] = dict(next=nxt.get(tm), offense=dict(prev=rec(off_prev, "posteam", tm), cur=rec(off_cur, "posteam", tm)),
                       defense=dict(prev=rec(def_prev, "defteam", tm), cur=rec(def_cur, "defteam", tm)),
                       roles=dict(prev=top_roles(roles_prev, tm), cur=top_roles(roles_cur, tm) if not no_cur else []), callouts=callouts.get(tm, []),
                       note=(f"No {season} games yet — everything shown is {season - 1}." if no_cur else None))
    json.dump({"season": season, "week": week, "teams": out}, open(D / f"teams_w{week}.json", "w"), default=float)
    # quick console: biggest scheme shifts so far
    if no_cur:
        json_path = D / f"teams_w{week}.json"; print(f"wrote {json_path} ({season - 1} baseline only — no {season} games yet)"); return
    m = off_cur.merge(off_prev, on="posteam", suffixes=("_26", "_25"))
    m["rz_pass_shift"] = m.rz_pass_rate_26 - m.rz_pass_rate_25; m["poe_shift"] = m.pass_oe_26 - m.pass_oe_25
    print("Biggest red-zone pass-rate shifts vs 2025 (one week of data — treat as leads, not conclusions):")
    print(m.sort_values("rz_pass_shift")[["posteam", "rz_pass_rate_25", "rz_pass_rate_26", "rz_pass_shift", "pass_oe_25", "pass_oe_26", "rz_trips_26"]].round(2).to_string(index=False))
    print(f"\nwrote {D / f'teams_w{week}.json'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); a = ap.parse_args(); main(a.season, a.week)
