"""sixpts scoring model — anytime TD (v1).

P(TD) = 1 - exp(-xTD_proj)
xTD_proj = xTD_pg_shrunk * env_mult * matchup_mult

Pure functions; no I/O. See ingest.py for data loading and run_week.py for orchestration.
"""
from __future__ import annotations
import numpy as np, pandas as pd
from dataclasses import dataclass, field

@dataclass
class Config:
    k_games: float = 4.0          # prior weight (in games) for returning players
    k_new_team: float = 2.0       # prior weight for players who changed teams
    league_pts: float = 23.0      # env_mult = implied_total / league_pts
    matchup_clip: tuple = (0.85, 1.15)
    min_prior_games: int = 6
    pos_prior_xtd: dict = field(default_factory=lambda: {"RB": 0.45, "WR": 0.32, "TE": 0.22, "QB": 0.30})
    weights: dict = field(default_factory=lambda: {"opp": .35, "gravity": .30, "matchup": .20, "env": .15})

PASS_BINS = ([-100, 0, 5, 10, 20, 40, 200], ["EZ", "1-5", "6-10", "11-20", "21-40", "40+"])
RUSH_BINS = ([0, 1, 2, 5, 10, 20, 40, 100], ["1", "2", "3-5", "6-10", "11-20", "21-40", "40+"])

def american(p: pd.Series | np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.where(p >= 0.5, -100 * p / (1 - p), 100 * (1 - p) / p)

def implied(price: float) -> float:
    return (-price) / (-price + 100) if price < 0 else 100 / (price + 100)

# ---------- xTD ----------
def prep_pbp(p: pd.DataFrame) -> pd.DataFrame:
    p = p[p.season_type.eq("REG") & p.play_type.isin(["pass", "run"]) & p.posteam.notna()].copy()
    p["catch_yl"] = p.yardline_100 - p.air_yards
    p["is_target"] = p.play_type.eq("pass") & p.receiver_player_id.notna()
    p["is_carry"] = p.play_type.eq("run") & p.rusher_player_id.notna()
    return p

def fit_xtd(p: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Empirical P(TD) by catch point (targets) and by yardline (carries)."""
    t = p[p.is_target].copy(); t["b"] = pd.cut(t.catch_yl, *PASS_BINS).astype(str)
    r = p[p.is_carry].copy(); r["b"] = pd.cut(r.yardline_100, *RUSH_BINS).astype(str)
    return t.groupby("b").pass_touchdown.mean(), r.groupby("b").rush_touchdown.mean()

def player_usage(p: pd.DataFrame, pr: pd.Series, rr: pd.Series, by_game: bool = False) -> pd.DataFrame:
    """Per-player (optionally per-game) usage + xTD."""
    t = p[p.is_target].copy(); t["b"] = pd.cut(t.catch_yl, *PASS_BINS).astype(str); t["xtd"] = t.b.map(pr).fillna(pr.mean())
    r = p[p.is_carry].copy(); r["b"] = pd.cut(r.yardline_100, *RUSH_BINS).astype(str); r["xtd"] = r.b.map(rr).fillna(rr.mean())
    gk = ["game_id"] if by_game else []
    tg = t.groupby(["receiver_player_id"] + gk).agg(team=("posteam", "last"), player=("receiver_player_name", "last"), games=("game_id", "nunique"),
        targets=("play_id", "count"), rz_tgt=("yardline_100", lambda s: (s <= 20).sum()), ez_tgt=("catch_yl", lambda s: (s <= 0).sum()),
        adot=("air_yards", "mean"), rec_td=("pass_touchdown", "sum"), x_rec_td=("xtd", "sum")).reset_index().rename(columns={"receiver_player_id": "gsis_id"})
    ru = r.groupby(["rusher_player_id"] + gk).agg(team_r=("posteam", "last"), player_r=("rusher_player_name", "last"), games_r=("game_id", "nunique"),
        carries=("play_id", "count"), rz_carry=("yardline_100", lambda s: (s <= 20).sum()), i10_carry=("yardline_100", lambda s: (s <= 10).sum()),
        i5_carry=("yardline_100", lambda s: (s <= 5).sum()), rush_td=("rush_touchdown", "sum"), x_rush_td=("xtd", "sum")).reset_index().rename(columns={"rusher_player_id": "gsis_id"})
    u = tg.merge(ru, on=["gsis_id"] + gk, how="outer")
    u["team"] = u.team.fillna(u.team_r); u["player"] = u.player.fillna(u.player_r)
    u["games"] = u[["games", "games_r"]].max(axis=1)
    u = u.drop(columns=["team_r", "player_r", "games_r"]).fillna(0)
    u["td"] = u.rec_td + u.rush_td; u["xtd"] = u.x_rec_td + u.x_rush_td
    return u

# ---------- shrinkage ----------
def shrink(u: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Blend current-season per-game xTD toward prior-season rate. Expects columns games, xtd, games_prior, xtd_prior, position, new_team."""
    n = u.games.clip(lower=1)
    cur = u.xtd / n
    has_prior = u.games_prior.fillna(0) >= cfg.min_prior_games
    prior = pd.Series(np.where(has_prior, u.xtd_prior / u.games_prior.replace(0, np.nan), u.position.map(cfg.pos_prior_xtd)), index=u.index)
    prior = prior.fillna(u.position.map(cfg.pos_prior_xtd)).fillna(0.25)
    k = np.where(u.new_team, cfg.k_new_team, cfg.k_games)
    u = u.copy()
    u["xtd_pg_2026"] = cur.round(3); u["xtd_pg_prior"] = prior.round(3)
    u["xtd_pg_shrunk"] = (n * cur + k * prior) / (n + k)
    u["pass_share"] = np.where(u.xtd > 0, u.x_rec_td / u.xtd, np.where(u.position.eq("RB"), 0.25, 0.95))
    return u

# ---------- matchup ----------
def matchup_mult(defense: pd.DataFrame, opp: str, pass_share: float, cfg: Config) -> tuple[float, str, str]:
    """defense: team_defense rows (one per team) with rz_td_pct, pass_td_allowed, rush_td_allowed, man_rate, two_high, source.
    Returns (multiplier, note, source)."""
    if defense is None or defense.empty or opp not in set(defense.team):
        return 1.0, "league avg (no defense row)", "none"
    row = defense[defense.team == opp].iloc[0]
    rz = row.rz_td_pct / defense.rz_td_pct.mean()
    split = pass_share * (row.pass_td_allowed / defense.pass_td_allowed.mean()) + (1 - pass_share) * (row.rush_td_allowed / defense.rush_td_allowed.mean())
    m = float(np.clip(0.5 * rz + 0.5 * split, *cfg.matchup_clip))
    note = f"RZ TD% {row.rz_td_pct:.0f} | man {row.man_rate:.0f}% / 2-high {row.two_high:.0f}%"
    return m, note, str(row.source)

# ---------- scoring ----------
def score_board(u: pd.DataFrame, slate: pd.DataFrame, defense: pd.DataFrame | None, cfg: Config) -> pd.DataFrame:
    """u: shrunk usage with team; slate: rows (team, opp, implied, total, spread, game_id, kickoff)."""
    b = u.merge(slate, on="team", how="inner")
    b["env_mult"] = (b.implied / cfg.league_pts).clip(0.7, 1.3)
    mm = [matchup_mult(defense, o, ps, cfg) for o, ps in zip(b.opp, b.pass_share)]
    b["matchup_mult"] = [m for m, _, _ in mm]; b["matchup_note"] = [n for _, n, _ in mm]; b["matchup_source"] = [s for _, _, s in mm]
    b["xtd_proj"] = b.xtd_pg_shrunk * b.env_mult * b.matchup_mult
    b["p_model"] = 1 - np.exp(-b.xtd_proj)
    b["fair_odds"] = american(b.p_model).round(0).astype(int)
    pct = lambda s: (s.rank(pct=True) * 100).round(0)
    b["opp_score"] = pct((b.targets + b.carries) / b.games.clip(lower=1))
    b["gravity_score"] = pct(b.xtd_pg_shrunk); b["matchup_score"] = pct(b.matchup_mult); b["env_score"] = pct(b.env_mult)
    w = cfg.weights
    b["score"] = (w["opp"] * b.opp_score + w["gravity"] * b.gravity_score + w["matchup"] * b.matchup_score + w["env"] * b.env_score).round(0)
    return b[b.xtd_proj > 0.05].sort_values("p_model", ascending=False).reset_index(drop=True)
