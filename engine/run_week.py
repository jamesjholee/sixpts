"""Weekly run. `python -m engine.run_week --season 2026 --week 2 [--pf-json data/pf_team_defense_2025.json] [--no-pf]`

Writes: scores table (all sources) + data/board_w{week}.json (private) + data/board_w{week}_public.json (no PF fields).
"""
from __future__ import annotations
import argparse, json, os, pathlib, pandas as pd, numpy as np
from . import ingest, model

def build_slate(games: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    g = games[(games.season == season) & (games.week == week)]
    rows = []
    for _, gm in g.iterrows():
        h = (gm.total_line + gm.spread_line) / 2; a = (gm.total_line - gm.spread_line) / 2
        rows.append(dict(team=gm.home_team, opp=gm.away_team, implied=h, total=gm.total_line, spread=gm.spread_line, game_id=gm.game_id, kickoff=str(gm.gameday)))
        rows.append(dict(team=gm.away_team, opp=gm.home_team, implied=a, total=gm.total_line, spread=-gm.spread_line, game_id=gm.game_id, kickoff=str(gm.gameday)))
    return pd.DataFrame(rows)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--pf-json", default=None); ap.add_argument("--no-pf", action="store_true"); a = ap.parse_args()
    cfg = model.Config()

    prior_season = a.season - 1
    p_prior = model.prep_pbp(ingest.load_pbp(prior_season))
    p_cur = model.prep_pbp(ingest.load_pbp(a.season, force=True)); p_cur = p_cur[p_cur.week < a.week]
    pr, rr = model.fit_xtd(p_prior)
    prior = model.player_usage(p_prior, pr, rr)[["gsis_id", "team", "games", "xtd", "td"]].rename(columns={"team": "team_prior", "games": "games_prior", "xtd": "xtd_prior", "td": "td_prior"})
    cur = model.player_usage(p_cur, pr, rr)
    stats = ingest.load_weekly_stats(a.season)[["player_id", "position", "team"]].drop_duplicates("player_id").rename(columns={"player_id": "gsis_id", "team": "team_now"})
    u = cur.merge(prior, on="gsis_id", how="left").merge(stats, on="gsis_id", how="left")
    u = u[u.position.isin(["RB", "WR", "TE", "QB"])].copy()
    u["team"] = u.team_now.fillna(u.team); u["new_team"] = u.team_prior.notna() & (u.team_prior != u.team)
    u = model.shrink(u, cfg)

    games = ingest.load_games(); slate = build_slate(games, a.season, a.week)

    defense = None
    if not a.no_pf:
        if a.pf_json: defense = ingest.pf_team_defense_from_json(a.pf_json)
        elif os.environ.get("PF_COOKIE") or os.environ.get("PF_BEARER"): defense = ingest.pf_team_defense(prior_season)
        if defense is not None and not defense.empty:
            ingest.write(defense, "team_defense", f"source='pf' and season={prior_season}")

    board = model.score_board(u, slate, defense, cfg)
    board["season"] = a.season; board["week"] = a.week; board["market"] = "anytime_td"
    keep = ["gsis_id", "player", "position", "team", "opp", "game_id", "kickoff", "implied", "total", "spread", "new_team", "games",
            "targets", "rz_tgt", "ez_tgt", "carries", "rz_carry", "i5_carry", "td", "xtd", "xtd_pg_2026", "xtd_pg_prior", "xtd_pg_shrunk",
            "env_mult", "matchup_mult", "matchup_note", "matchup_source", "xtd_proj", "p_model", "fair_odds",
            "opp_score", "gravity_score", "matchup_score", "env_score", "score", "season", "week", "market"]
    board = board[keep].round(3)
    board = board.astype(object).where(pd.notna(board), None)  # JSON-safe: no NaN

    # scores table (all sources); public view strips PF matchup notes via SQL view
    ingest.write(board[["gsis_id", "game_id", "market", "xtd_pg_2026", "xtd_pg_prior", "xtd_pg_shrunk", "env_mult", "matchup_mult", "xtd_proj", "p_model",
                        "fair_odds", "opp_score", "gravity_score", "matchup_score", "env_score", "score", "matchup_note", "matchup_source"]], "scores")

    out = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))
    slate_json = [dict(game=g, kickoff=k, total=t) for g, k, t in slate.drop_duplicates("game_id")[["game_id", "kickoff", "total"]].values]
    json.dump({"week": a.week, "season": a.season, "board": board.to_dict("records"), "slate": slate_json}, open(out / f"board_w{a.week}.json", "w"))
    pub = board.copy(); pub.loc[pub.matchup_source.eq("pf"), "matchup_note"] = None; pub = pub.drop(columns=["matchup_source"])
    json.dump({"week": a.week, "season": a.season, "board": pub.to_dict("records"), "slate": slate_json}, open(out / f"board_w{a.week}_public.json", "w"))
    print(board.head(20)[["player", "team", "opp", "p_model", "fair_odds", "xtd_proj", "matchup_source"]].to_string(index=False))
    print(f"\n{len(board)} scored; {(board.matchup_source != 'pf').mean():.0%} without PF matchup")

if __name__ == "__main__":
    main()
