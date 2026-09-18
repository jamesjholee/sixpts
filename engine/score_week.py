"""Score an upcoming week with the fitted model. Adds 'why' contributions per layer via local perturbation.
python -m engine.score_week --season 2026 --week 2
"""
import argparse, json, os, pathlib, pickle, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401  (loads .env)
from engine.features import make_features
from engine import model as M
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

def _pg(u: str) -> str:
    """Supabase hands out postgres:// or postgresql:// — pin the psycopg3 driver we install."""
    if u.startswith("postgres://"): return "postgresql+psycopg://" + u[len("postgres://"):]
    if u.startswith("postgresql://"): return "postgresql+psycopg://" + u[len("postgresql://"):]
    return u

def american(p): return M.american(np.asarray(p))

def main(season, week):
    bundle = pickle.load(open(D / "td_model.pkl", "rb")); clf, FEATS = bundle["model"], bundle["feats"]
    df = pd.read_parquet(D / f"upcoming_w{week}.parquet")
    df, _ = make_features(df)
    up = df[(df.season == season) & (df.week == week)].copy()
    X = up[FEATS]
    up["p_model"] = clf.predict_proba(X)[:, 1]

    # layer contributions: set a layer's features to the slate median and see how P moves (simple, honest "why")
    med = X.median()
    layers = {"role": [f for f in FEATS if f.endswith("_shr")], "offense": [f for f in FEATS if f.startswith("off_")],
              "defense": [f for f in FEATS if f.startswith("def_")], "environment": ["implied", "total", "spread", "home"]}
    for name, cols in layers.items():
        Xn = X.copy(); Xn[cols] = med[cols].values
        up[f"c_{name}"] = up.p_model - clf.predict_proba(Xn)[:, 1]

    # ---- range: same model, role features at "recent 2 games" vs "season rate" -> how much the trend could swing it
    Xr = X.copy()
    for c in ["xtd", "xtd_share", "rz_tgt_share", "rz_car_share", "i5_carry", "offense_pct"]:
        Xr[f"{c}_trend"] = 0.0; Xr[f"{c}_l2"] = up[f"{c}_std"].fillna(up[f"{c}_l2"])
    p_season = clf.predict_proba(Xr)[:, 1]
    up["p_low"] = np.minimum(up.p_model, p_season); up["p_high"] = np.maximum(up.p_model, p_season)
    # ---- certainty: evidence + stability + how concentrated the job is
    eff_games = up.games_to_date + 0.5 * up.games_prev.fillna(0).clip(upper=8)   # last season counts, at half weight, up to 8 games
    ev = (eff_games.clip(upper=6) / 6)                                          # 0..1 games of evidence
    stab = 1 - (up.xtd_volatility.fillna(0.30) / (up.xtd_shr + 0.15)).clip(0, 1)  # low weekly variance relative to level
    conc = np.where(up.position.eq("RB"), up.rz_car_share_shr, up.rz_tgt_share_shr).clip(0, 1)
    up["certainty"] = (100 * (0.4 * ev + 0.35 * stab + 0.25 * pd.Series(conc, index=up.index).fillna(0))).fillna(0).round(0)
    up["certainty_label"] = pd.cut(up.certainty, [-1, 40, 65, 101], labels=["low", "medium", "high"]).astype(str)
    # ---- flags (things to call out)
    fl = []
    for _, r in up.iterrows():
        f = []
        if r.get("i5_carry_trend", 0) >= 0.75: f.append("goal-line role rising")
        if r.get("i5_carry_trend", 0) <= -0.75 and (r.get("off_gl_concentration") is not None and pd.notna(r.get("off_gl_concentration"))): f.append("goal-line role falling")
        if r.get("rz_tgt_share_trend", 0) >= 0.12: f.append("RZ target share rising")
        if r.get("rz_tgt_share_trend", 0) <= -0.12: f.append("RZ target share falling")
        if r.get("offense_pct_trend", 0) >= 0.15: f.append("snaps rising")
        if r.get("offense_pct_trend", 0) <= -0.15: f.append("snaps falling")
        if r.get("abs_n", 0) > 0: f.append(f"{int(r.abs_n)} teammate(s) out at position" + (f" (+{r.abs_same_grp:.0%} of RZ work open)" if r.abs_same_grp > 0.05 else ""))
        if r.get("questionable", 0) == 1: f.append("QUESTIONABLE on injury report")
        if r.get("new_team", 0) == 1: f.append("new team — last season weighted less")
        if r.get("indoor", 0) == 0 and pd.notna(r.get("wind")) and r.wind >= 15: f.append(f"wind {r.wind:.0f} mph — passing TDs suppressed")
        if r.get("indoor", 0) == 0 and pd.notna(r.get("temp")) and r.temp <= 32: f.append(f"cold {r.temp:.0f}°F")
        if r.get("td_shr", 0) - r.get("xtd_shr", 0) > 0.25 and r.games_to_date >= 3: f.append("scoring above expectation — regression risk")
        if r.get("xtd_shr", 0) - r.get("td_shr", 0) > 0.25 and r.games_to_date >= 3: f.append("scoring below expectation — positive regression")
        fl.append(f)
    up["flags"] = fl
    # ---- receptions market: expected receptions = shrunk targets/game x catch rate x environment
    catch_rate = np.where(up.position.eq("RB"), 0.78, np.where(up.position.eq("TE"), 0.72, 0.64))
    up["exp_targets"] = up.targets_shr * (up.implied / 23.0).clip(0.7, 1.3) ** 0.5
    up["exp_rec"] = up.exp_targets * catch_rate
    up["rec_sd"] = np.sqrt(up.exp_rec * 1.25 + 0.3)   # 2025 backtest: residual var/mean 1.25, MAE 1.65, corr 0.48
    # ---- TD hit rate, last 5 games played (across seasons), with expected TDs over the same games
    hist = df[df.td.notna()].sort_values(["season", "week"]).groupby("gsis_id").tail(5)
    hr = hist.groupby("gsis_id").agg(hit_l5=("scored", "sum"), n_l5=("scored", "size"), xtd_l5=("xtd", "sum")).reset_index()
    up = up.merge(hr, on="gsis_id", how="left")
    # ---- plain-English verdict
    verdicts = []
    for _, r in up.iterrows():
        why = []
        if r.position == "RB":
            if r.i5_carry_shr >= 1.0: why.append(f"{r.i5_carry_shr:.1f} goal-line carries/game")
            elif r.rz_carry_shr >= 3: why.append(f"{r.rz_carry_shr:.1f} red zone carries/game")
        else:
            if r.ez_tgt_shr >= 0.8: why.append(f"{r.ez_tgt_shr:.1f} end zone targets/game")
            elif r.rz_tgt_shr >= 2: why.append(f"{r.rz_tgt_shr:.1f} red zone targets/game")
        if pd.notna(r.def_rz_td_pct) and r.def_rz_td_pct >= 0.6: why.append(f"{r.defteam} allows TDs on {r.def_rz_td_pct:.0%} of red zone trips")
        if pd.notna(r.def_rz_td_pct) and r.def_rz_td_pct <= 0.45: why.append(f"{r.defteam} allows TDs on only {r.def_rz_td_pct:.0%} of red zone trips")
        if r.implied >= 27: why.append(f"implied {r.implied:.0f} points")
        if r.implied <= 18: why.append(f"implied only {r.implied:.0f} points")
        if r.get("abs_same_grp", 0) > 0.1: why.append(f"{r.abs_same_grp:.0%} of the position's red zone work opened by injuries")
        fair = int(round(float(M.american(np.array([r.p_model]))[0]))); lead = f"{'Bet' if r.p_model >= 0.3 else 'Only'} at {fair:+d} or better" if r.p_model >= 0.15 else f"Longshot — fair {fair:+d}"
        verdicts.append(lead + (" — " + ", ".join(why[:3]) if why else ""))
    up["touches_to_date"] = ((up.targets_std.fillna(0) + up.carries_std.fillna(0)) * up.g_std).round(1)
    up["verdict"] = verdicts
    names = pd.read_parquet(D / "players.parquet")[["gsis_id", "display_name"]].drop_duplicates("gsis_id")
    up = up.merge(names, on="gsis_id", how="left")
    up["fair_odds"] = american(up.p_model).round(0)
    keep = {"gsis_id": "gsis_id", "display_name": "player", "position": "position", "posteam": "team", "defteam": "opp", "game_id": "game_id",
            "implied": "implied", "total": "total", "spread": "spread", "new_team": "new_team", "games_to_date": "games",
            "xtd_shr": "xtd_pg_shrunk", "rz_tgt_shr": "rz_tgt_pg", "rz_carry_shr": "rz_carry_pg", "i5_carry_shr": "i5_carry_pg", "ez_tgt_shr": "ez_tgt_pg",
            "offense_pct_shr": "snap_pct", "off_rz_trips": "off_rz_trips", "off_rz_pass_rate": "off_rz_pass_rate", "off_pass_oe": "off_pass_oe",
            "def_rz_td_pct": "def_rz_td_pct", "def_d_td_RB": "def_td_rb_pg", "def_d_td_WR": "def_td_wr_pg", "def_d_td_TE": "def_td_te_pg",
            "p_model": "p_model", "fair_odds": "fair_odds", "c_role": "c_role", "c_offense": "c_offense", "c_defense": "c_defense", "c_environment": "c_environment",
            "p_low": "p_low", "p_high": "p_high", "certainty": "certainty", "certainty_label": "certainty_label", "flags": "flags",
            "xtd_l2": "xtd_recent", "i5_carry_l2": "i5_carry_recent", "rz_tgt_share_shr": "rz_tgt_share", "rz_car_share_shr": "rz_carry_share", "abs_same_grp": "rz_share_open",
            "off_rz_pass_rate_trail": "off_rz_pass_rate_trailing", "off_rz_pass_rate_lead": "off_rz_pass_rate_leading",
            "exp_targets": "exp_targets", "exp_rec": "exp_rec", "rec_sd": "rec_sd", "indoor": "indoor", "wind": "wind", "temp": "temp",
            "hit_l5": "hit_l5", "n_l5": "n_l5", "xtd_l5": "xtd_l5", "verdict": "verdict", "touches_to_date": "touches_recent"}
    out = up[list(keep)].rename(columns=keep).sort_values("p_model", ascending=False).round(3)
    out["new_team"] = out.new_team.astype(bool)
    out = out.astype(object).where(pd.notna(out), None)
    games = pd.read_parquet(D / "games.parquet"); g = games[(games.season == season) & (games.week == week)]
    slate = [dict(game=r.game_id, kickoff=str(r.gameday), total=r.total_line) for _, r in g.iterrows()]
    import datetime as _dt
    def mtime(p): 
        p = D / p; return _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%b %d %H:%M") if p.exists() else "n/a"
    sources = {"play_by_play": f"nflverse play-by-play through Week {week - 1} (file {mtime(f'play_by_play_{season}.parquet')})",
               "injury_report": f"official NFL injury report via nflverse (file {mtime(f'injuries_{season}.parquet')})",
               "lines": f"spread / total from nflverse schedule (file {mtime('games.parquet')})",
               "model": "gbm_v1 — trained 2021-2025, 2025 holdout logloss 0.478 / AUC 0.697", "scored_at": _dt.datetime.now().strftime("%b %d %H:%M")}
    payload = {"week": week, "season": season, "model": "gbm_v1", "sources": sources, "board": out.to_dict("records"), "slate": slate}
    inj_path = D / f"injuries_{season}.parquet"
    inj_weeks = sorted(pd.read_parquet(inj_path).week.unique().tolist()) if inj_path.exists() else []
    if week in inj_weeks:
        wk = pd.read_parquet(inj_path); wk = wk[wk.week == week]; n_status = int(wk.report_status.notna().sum())
        payload["injury_report"] = f"loaded: {n_status} game statuses (Out/Doubtful/Questionable) posted" if n_status else "not published yet (only practice notes so far — game statuses post Friday; re-run score_week then)"
    else:
        payload["injury_report"] = f"not published yet (have weeks {inj_weeks}); re-run score_week Friday/Saturday"
    json.dump(payload, open(D / f"board_w{week}_gbm.json", "w"))
    json.dump(payload, open(D / f"board_w{week}_public.json", "w"))   # fitted model is nflverse-only -> public-safe
    json.dump(payload, open(D / f"board_w{week}.json", "w"))          # private view gets the same numbers until PF layers are added
    out.to_csv(D / f"board_w{week}_gbm.csv", index=False)
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(_pg(os.environ.get("DATABASE_URL", "sqlite:///data/sixpts.db")))
        sc = out[["gsis_id", "game_id", "p_model", "fair_odds"]].copy(); sc["market"] = "anytime_td"; sc["matchup_source"] = "own"
        for c in ["xtd_pg_shrunk", "c_role", "c_offense", "c_defense", "c_environment", "certainty"]: sc[c] = out[c]
        sc = sc.rename(columns={"c_role": "opp_score", "c_offense": "gravity_score", "c_defense": "matchup_score", "c_environment": "env_score", "certainty": "score"})
        sc["as_of"] = pd.Timestamp.utcnow().isoformat()
        with eng.begin() as c: sc.to_sql("scores", c, if_exists="append", index=False)
        print(f"archived {len(sc)} scores to DB")
    except Exception as e: print("DB archive skipped:", e)
    print(out.head(12)[["player", "team", "opp", "p_model", "fair_odds", "hit_l5", "xtd_l5", "verdict"]].to_string(index=False))
    print("\nInjury report:", payload["injury_report"])
    print(f"\n{len(out)} scored")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True); a = ap.parse_args(); main(a.season, a.week)
