"""Build the anytime-TD training set. Every feature uses only information available BEFORE the game.
Output: data/train.parquet  (one row per player-game, label = scored a rush/rec TD)
"""
import pandas as pd, numpy as np, sys, os, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import model as M
from engine import ingest

D = os.environ.get("SIXPTS_DATA", "data")
def ensure_files():
    """Download anything missing from nflverse (one-time ~130 MB)."""
    for s in SEASONS:
        ingest.nflverse_file(f"pbp/play_by_play_{s}.parquet", force=(s == max(SEASONS)))
        ingest.nflverse_file(f"snap_counts/snap_counts_{s}.parquet", force=(s == max(SEASONS)))
        ingest.nflverse_file(f"injuries/injuries_{s}.parquet", force=(s == max(SEASONS)))
    ingest.nflverse_file("players/players.parquet"); ingest.nflverse_file("schedules/games.parquet", force=True)
SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]
POS_OK = ["RB", "WR", "TE", "QB"]

def load_all():
    frames = []
    for s in SEASONS:
        p = pd.read_parquet(f"{D}/play_by_play_{s}.parquet")
        frames.append(M.prep_pbp(p))
    return pd.concat(frames, ignore_index=True)

def xtd_rates_by_season(p):
    """Fit P(TD) buckets on the PRIOR season only (walk-forward)."""
    out = {}
    for s in SEASONS:
        prev = p[p.season == s - 1]
        if prev.empty: prev = p[p.season == s]  # 2021 has no 2020 here; small leak only for first year
        out[s] = M.fit_xtd(prev)
    return out

def expanding_prior(df, keys, cols, season_col="season", order=["season", "week"]):
    """For each group (keys), season-to-date mean of cols BEFORE this row, plus games-to-date."""
    df = df.sort_values(order)
    g = df.groupby(keys + [season_col])
    for c in cols:
        df[f"{c}_std"] = g[c].transform(lambda s: s.shift(1).expanding().mean())
    df["g_std"] = g.cumcount()
    return df

def recent_window(df, keys, cols, n=2, season_col="season", order=["season", "week"]):
    """Mean of the last n games BEFORE this row (same season) -> {col}_l{n}; plus expanding std of col -> {col}_sd."""
    df = df.sort_values(order); g = df.groupby(keys + [season_col])
    for c in cols:
        df[f"{c}_l{n}"] = g[c].transform(lambda s: s.shift(1).rolling(n, min_periods=1).mean())
        df[f"{c}_sd"] = g[c].transform(lambda s: s.shift(1).expanding().std())
    return df

def prev_season_rate(df, keys, cols, season_col="season"):
    """Previous-season per-game mean of cols, joined onto this season's rows."""
    ps = df.groupby(keys + [season_col])[cols].mean().reset_index()
    ps["games_prev"] = df.groupby(keys + [season_col]).size().values
    ps[season_col] = ps[season_col] + 1
    ps = ps.rename(columns={c: f"{c}_prev" for c in cols})
    return df.merge(ps, on=keys + [season_col], how="left")

def add_upcoming(pg, off, dfn, season, week, games):
    """Append empty rows for the upcoming week so expanding features include all played games."""
    g = games[(games.season == season) & (games.week == week)]
    rows = []
    for _, gm in g.iterrows():
        for team in (gm.home_team, gm.away_team):
            cands = pg[(pg.season == season) & (pg.posteam == team)].gsis_id.unique()
            for pid in cands: rows.append(dict(season=season, week=week, game_id=gm.game_id, posteam=team, gsis_id=pid))
    up = pd.DataFrame(rows).drop_duplicates(["season", "week", "game_id", "posteam", "gsis_id"])
    # a replayed week already has real rows — keep those, add placeholders only for players without one
    have = set(map(tuple, pg.loc[(pg.season == season) & (pg.week == week), ["game_id", "gsis_id"]].itertuples(index=False, name=None)))
    if have: up = up[~up.apply(lambda r: (r.game_id, r.gsis_id) in have, axis=1)]
    pg = pd.concat([pg, up], ignore_index=True)
    orows = pd.DataFrame([dict(season=season, week=week, game_id=gm.game_id, posteam=t) for _, gm in g.iterrows() for t in (gm.home_team, gm.away_team)])
    drows = pd.DataFrame([dict(season=season, week=week, game_id=gm.game_id, defteam=t) for _, gm in g.iterrows() for t in (gm.home_team, gm.away_team)])
    # don't duplicate team rows that already exist (replaying a week that has been played)
    have_o = set(map(tuple, off.loc[(off.season == season) & (off.week == week), ["game_id", "posteam"]].itertuples(index=False, name=None)))
    have_d = set(map(tuple, dfn.loc[(dfn.season == season) & (dfn.week == week), ["game_id", "defteam"]].itertuples(index=False, name=None)))
    if have_o: orows = orows[~orows.apply(lambda r: (r.game_id, r.posteam) in have_o, axis=1)]
    if have_d: drows = drows[~drows.apply(lambda r: (r.game_id, r.defteam) in have_d, axis=1)]
    return pg, pd.concat([off, orows], ignore_index=True), pd.concat([dfn, drows], ignore_index=True)

def main(upcoming=None):
    ensure_files()
    p = load_all()
    rates = xtd_rates_by_season(p)
    # per-play xTD using prior-season buckets
    p["xtd_play"] = 0.0
    for s in SEASONS:
        pr, rr = rates[s]; m = p.season == s
        t = m & p.is_target; r = m & p.is_carry
        p.loc[t, "xtd_play"] = pd.cut(p.loc[t, "catch_yl"], *M.PASS_BINS).astype(str).map(pr).fillna(pr.mean()).values
        p.loc[r, "xtd_play"] = pd.cut(p.loc[r, "yardline_100"], *M.RUSH_BINS).astype(str).map(rr).fillna(rr.mean()).values

    # ---------- player-game ----------
    t = p[p.is_target]; r = p[p.is_carry]
    tg = t.groupby(["season", "week", "game_id", "posteam", "receiver_player_id"]).agg(
        targets=("play_id", "count"), rz_tgt=("yardline_100", lambda s: (s <= 20).sum()), ez_tgt=("catch_yl", lambda s: (s <= 0).sum()),
        rec_td=("pass_touchdown", "sum"), x_rec_td=("xtd_play", "sum")).reset_index().rename(columns={"receiver_player_id": "gsis_id"})
    ru = r.groupby(["season", "week", "game_id", "posteam", "rusher_player_id"]).agg(
        carries=("play_id", "count"), rz_carry=("yardline_100", lambda s: (s <= 20).sum()), i5_carry=("yardline_100", lambda s: (s <= 5).sum()),
        rush_td=("rush_touchdown", "sum"), x_rush_td=("xtd_play", "sum")).reset_index().rename(columns={"rusher_player_id": "gsis_id"})
    pg = tg.merge(ru, on=["season", "week", "game_id", "posteam", "gsis_id"], how="outer").fillna(0)
    pg["td"] = pg.rec_td + pg.rush_td; pg["xtd"] = pg.x_rec_td + pg.x_rush_td; pg["scored"] = (pg.td > 0).astype(int)
    pg["touches"] = pg.targets + pg.carries
    # team totals for shares
    tt = pg.groupby(["game_id", "posteam"]).agg(team_tgt=("targets", "sum"), team_car=("carries", "sum"), team_rz_tgt=("rz_tgt", "sum"), team_rz_car=("rz_carry", "sum"), team_xtd=("xtd", "sum")).reset_index()
    pg = pg.merge(tt, on=["game_id", "posteam"])
    pg["tgt_share"] = pg.targets / pg.team_tgt.replace(0, np.nan); pg["car_share"] = pg.carries / pg.team_car.replace(0, np.nan)
    pg["rz_tgt_share"] = pg.rz_tgt / pg.team_rz_tgt.replace(0, np.nan); pg["rz_car_share"] = pg.rz_carry / pg.team_rz_car.replace(0, np.nan)
    pg["xtd_share"] = pg.xtd / pg.team_xtd.replace(0, np.nan)
    pg = pg.fillna({"tgt_share": 0, "car_share": 0, "rz_tgt_share": 0, "rz_car_share": 0, "xtd_share": 0})

    # position from rosters (players.parquet has position)
    players = pd.read_parquet(f"{D}/players.parquet")[["gsis_id", "position"]].dropna().drop_duplicates("gsis_id")
    pg = pg.merge(players, on="gsis_id", how="left"); pg = pg[pg.position.isin(POS_OK)].copy()

    # snap %
    snaps = pd.concat([pd.read_parquet(f"{D}/snap_counts_{s}.parquet") for s in SEASONS])[["game_id", "pfr_player_id", "player", "team", "offense_pct"]]
    # nflverse snap counts key on pfr id; map via players table
    pmap = pd.read_parquet(f"{D}/players.parquet")[["gsis_id", "pfr_id"]].dropna()
    snaps = snaps.merge(pmap, left_on="pfr_player_id", right_on="pfr_id", how="inner")[["game_id", "gsis_id", "offense_pct"]]
    pg = pg.merge(snaps, on=["game_id", "gsis_id"], how="left")

    games_tbl = pd.read_parquet(f"{D}/games.parquet")
    # ---------- raw team offense / defense per game (walk-forward applied after upcoming rows are added) ----------
    off = p.groupby(["season", "week", "game_id", "posteam"]).agg(
        plays=("play_id", "count"), pass_rate=("play_type", lambda s: (s == "pass").mean()),
        team_xtd_g=("xtd_play", "sum"), team_td_g=("touchdown", "sum"), pass_oe=("pass_oe", "mean")).reset_index()
    rz = p[p.yardline_100 <= 20].groupby(["game_id", "posteam"]).agg(rz_trips=("drive", "nunique"), rz_pass_rate=("play_type", lambda s: (s == "pass").mean()), rz_td_pct=("touchdown", "mean")).reset_index()
    neu = p[p.wp.between(0.2, 0.8) & (p.game_seconds_remaining > 300)].groupby(["game_id", "posteam"]).play_type.apply(lambda s: (s == "pass").mean()).rename("neutral_pass_rate").reset_index()
    i5 = p[p.is_carry & (p.yardline_100 <= 5)].groupby(["game_id", "posteam", "rusher_player_id"]).size().reset_index(name="n")
    i5c = i5.groupby(["game_id", "posteam"]).n.apply(lambda s: s.max() / s.sum()).rename("gl_concentration").reset_index()
    rzt = p[p.is_target & (p.yardline_100 <= 20)].groupby(["game_id", "posteam", "receiver_player_id"]).size().reset_index(name="n")
    rztc = rzt.groupby(["game_id", "posteam"]).n.apply(lambda s: s.max() / s.sum()).rename("rz_tgt_concentration").reset_index()
    rzp = p[p.yardline_100 <= 20].copy(); rzp["trail"] = rzp.score_differential < 0
    gs = rzp.groupby(["game_id", "posteam", "trail"]).play_type.apply(lambda s: (s == "pass").mean()).unstack("trail")
    gs = gs.rename(columns={True: "rz_pass_rate_trail", False: "rz_pass_rate_lead"}).reset_index()
    for c in ["rz_pass_rate_trail", "rz_pass_rate_lead"]:
        if c not in gs: gs[c] = np.nan
    off = off.merge(rz, on=["game_id", "posteam"], how="left").merge(neu, on=["game_id", "posteam"], how="left").merge(i5c, on=["game_id", "posteam"], how="left").merge(rztc, on=["game_id", "posteam"], how="left").merge(gs[["game_id", "posteam", "rz_pass_rate_trail", "rz_pass_rate_lead"]], on=["game_id", "posteam"], how="left")
    off = off.fillna({"rz_trips": 0, "rz_pass_rate": 0.5, "rz_td_pct": 0.5})
    dfn = p.groupby(["season", "week", "game_id", "defteam"]).agg(
        d_pass_td=("pass_touchdown", "sum"), d_rush_td=("rush_touchdown", "sum"), d_xtd=("xtd_play", "sum"), d_td=("touchdown", "sum")).reset_index()
    drz = p[p.yardline_100 <= 20].groupby(["game_id", "defteam"]).agg(d_rz_trips=("drive", "nunique"), d_rz_td=("touchdown", "sum")).reset_index()
    dfn = dfn.merge(drz, on=["game_id", "defteam"], how="left").fillna({"d_rz_trips": 0, "d_rz_td": 0})
    posmap = players.set_index("gsis_id").position
    p["scorer_pos"] = p.td_player_id.map(posmap)
    for pos in ["RB", "WR", "TE"]:
        dp = p[(p.touchdown == 1) & (p.scorer_pos == pos)].groupby(["game_id", "defteam"]).size().rename(f"d_td_{pos}").reset_index()
        dfn = dfn.merge(dp, on=["game_id", "defteam"], how="left").fillna({f"d_td_{pos}": 0})
    dfn["d_td_over_x"] = dfn.d_td - dfn.d_xtd
    if upcoming:
        pg, off, dfn = add_upcoming(pg, off, dfn, upcoming[0], upcoming[1], games_tbl)
        pg["position"] = pg.position.fillna(pg.gsis_id.map(posmap))

    # walk-forward player features
    role_cols = ["xtd", "x_rec_td", "x_rush_td", "targets", "carries", "rz_tgt", "rz_carry", "i5_carry", "ez_tgt", "tgt_share", "car_share", "rz_tgt_share", "rz_car_share", "xtd_share", "offense_pct", "td"]
    pg = expanding_prior(pg, ["gsis_id"], role_cols)
    pg = recent_window(pg, ["gsis_id"], ["xtd", "xtd_share", "rz_tgt_share", "rz_car_share", "i5_carry", "offense_pct", "touches"], n=2)
    pg = prev_season_rate(pg, ["gsis_id"], role_cols)

    # ---------- availability: injury report for THIS week (published before the game) ----------
    inj = pd.concat([pd.read_parquet(f"{D}/injuries_{s}.parquet") for s in SEASONS if pathlib.Path(f"{D}/injuries_{s}.parquet").exists()])
    inj = inj[inj.season_type.eq("REG") if "season_type" in inj else inj.game_type.eq("REG")][["season", "week", "team", "gsis_id", "position", "report_status"]].dropna(subset=["gsis_id"])
    inj["team"] = inj.team.replace({"LAR": "LA", "OAK": "LV", "SD": "LAC", "STL": "LA"})
    out_ids = inj[inj.report_status.isin(["Out", "Doubtful"])]
    q_ids = inj[inj.report_status.eq("Questionable")][["season", "week", "gsis_id"]].assign(questionable=1)
    pg = pg.merge(q_ids, on=["season", "week", "gsis_id"], how="left").fillna({"questionable": 0})
    # what each player was worth BEFORE this week (share known as of this week): std if available else prev-season
    pg["share_pass_asof"] = pg.rz_tgt_share_std.fillna(pg.rz_tgt_share_prev).fillna(0)
    pg["share_rush_asof"] = pg.rz_car_share_std.fillna(pg.rz_car_share_prev).fillna(0)
    pg["xtd_share_asof"] = pg.xtd_share_std.fillna(pg.xtd_share_prev).fillna(0)
    # absent players have no row this week -> take their latest known share from any earlier row in the season, else prev season
    hist = pg[["season", "week", "gsis_id", "posteam", "position", "share_pass_asof", "share_rush_asof", "xtd_share_asof"]].sort_values(["season", "week"])
    last = hist.groupby(["season", "gsis_id"]).last().reset_index()[["season", "gsis_id", "posteam", "position", "share_pass_asof", "share_rush_asof", "xtd_share_asof"]]
    prevs = pg.groupby(["season", "gsis_id"]).agg(posteam=("posteam", "last"), position=("position", "last"), share_pass_asof=("rz_tgt_share", "mean"), share_rush_asof=("rz_car_share", "mean"), xtd_share_asof=("xtd_share", "mean")).reset_index()
    prevs["season"] += 1
    known = pd.concat([last, prevs]).drop_duplicates(["season", "gsis_id"], keep="first")
    absent = out_ids.merge(known, on=["season", "gsis_id"], how="left", suffixes=("", "_k"))
    absent["posteam"] = absent.posteam.fillna(absent.team); absent["position"] = absent.position_k.fillna(absent.position) if "position_k" in absent else absent.position
    absent = absent[absent.position.isin(POS_OK)].fillna({"share_pass_asof": 0, "share_rush_asof": 0, "xtd_share_asof": 0})
    absent["grp"] = np.where(absent.position.eq("RB"), "RB", "REC")
    agg = absent.groupby(["season", "week", "posteam", "grp"]).agg(abs_pass=("share_pass_asof", "sum"), abs_rush=("share_rush_asof", "sum"), abs_xtd=("xtd_share_asof", "sum"), abs_n=("gsis_id", "count")).reset_index()
    pg["grp"] = np.where(pg.position.eq("RB"), "RB", "REC")
    pg = pg.merge(agg, on=["season", "week", "posteam", "grp"], how="left").fillna({"abs_pass": 0, "abs_rush": 0, "abs_xtd": 0, "abs_n": 0})
    # any-position absent xTD share on the team (a WR1 out helps the TE too)
    agg_all = absent.groupby(["season", "week", "posteam"]).xtd_share_asof.sum().rename("abs_xtd_team").reset_index()
    pg = pg.merge(agg_all, on=["season", "week", "posteam"], how="left").fillna({"abs_xtd_team": 0})
    # players themselves listed Out/Doubtful for an upcoming week: drop (they won't play)
    pg = pg.merge(out_ids[["season", "week", "gsis_id"]].assign(is_out=1), on=["season", "week", "gsis_id"], how="left")
    pg = pg[pg.is_out.ne(1) | pg.game_id.isin(pg[pg.td.notna()].game_id)].drop(columns="is_out")
    # team change flag: prior-season team differs
    pt = pg.groupby(["gsis_id", "season"]).posteam.agg(lambda s: s.mode().iloc[0]).reset_index(); pt["season"] += 1; pt = pt.rename(columns={"posteam": "team_prev"})
    pg = pg.merge(pt, on=["gsis_id", "season"], how="left"); pg["new_team"] = (pg.team_prev.notna() & (pg.team_prev != pg.posteam)).astype(int)

    off_cols = ["plays", "pass_rate", "rz_trips", "rz_pass_rate", "team_xtd_g", "team_td_g", "pass_oe", "rz_td_pct", "neutral_pass_rate", "gl_concentration", "rz_tgt_concentration", "rz_pass_rate_trail", "rz_pass_rate_lead"]
    off = expanding_prior(off, ["posteam"], off_cols); off = prev_season_rate(off, ["posteam"], off_cols)
    off = off[["game_id", "posteam"] + [f"{c}_std" for c in off_cols] + [f"{c}_prev" for c in off_cols]].rename(columns={f"{c}_std": f"off_{c}_std" for c in off_cols} | {f"{c}_prev": f"off_{c}_prev" for c in off_cols})

    def_cols = ["d_pass_td", "d_rush_td", "d_xtd", "d_td", "d_rz_trips", "d_rz_td", "d_td_RB", "d_td_WR", "d_td_TE", "d_td_over_x"]
    dfn = expanding_prior(dfn, ["defteam"], def_cols); dfn = prev_season_rate(dfn, ["defteam"], def_cols)
    dfn = dfn[["game_id", "defteam"] + [f"{c}_std" for c in def_cols] + [f"{c}_prev" for c in def_cols]]

    # ---------- environment ----------
    g = pd.read_parquet(f"{D}/games.parquet")[["game_id", "home_team", "away_team", "spread_line", "total_line", "roof", "temp", "wind"]]
    g["indoor"] = g.roof.isin(["dome", "closed"]).astype(int)
    g.loc[g.indoor == 1, ["temp", "wind"]] = [70.0, 0.0]   # controlled environment
    env = []
    for _, gm in g.iterrows():
        w = dict(indoor=gm.indoor, temp=gm.temp, wind=gm.wind)
        env.append(dict(game_id=gm.game_id, posteam=gm.home_team, defteam=gm.away_team, implied=(gm.total_line + gm.spread_line) / 2, total=gm.total_line, spread=gm.spread_line, home=1, **w))
        env.append(dict(game_id=gm.game_id, posteam=gm.away_team, defteam=gm.home_team, implied=(gm.total_line - gm.spread_line) / 2, total=gm.total_line, spread=-gm.spread_line, home=0, **w))
    env = pd.DataFrame(env)

    df = pg.merge(env, on=["game_id", "posteam"], how="inner").merge(off, on=["game_id", "posteam"], how="left").merge(dfn, on=["game_id", "defteam"], how="left")
    out = f"{D}/train.parquet" if not upcoming else f"{D}/upcoming_w{upcoming[1]}.parquet"
    df.to_parquet(out, index=False)
    print(df.shape, "rows ->", out)

if __name__ == "__main__":
    import argparse; ap = argparse.ArgumentParser(); ap.add_argument("--upcoming", nargs=2, type=int, metavar=("SEASON", "WEEK")); a = ap.parse_args()
    main(tuple(a.upcoming) if a.upcoming else None)
