"""Feature engineering shared by fit_model.py and score_week.py."""
import pandas as pd, numpy as np
K = 4.0

# ---- priors, measured on 2021-2025 (see docs/model-notes.md) ----
POS_PRIOR = {"RB": .45, "WR": .32, "TE": .22, "QB": .30}                      # last resort
SNAP_PRIOR = {  # expected TDs per game by position x snap share — snaps stabilise after one game
    ("QB", 0): .110, ("QB", 1): .101, ("QB", 2): .177, ("QB", 3): .175,
    ("RB", 0): .156, ("RB", 1): .311, ("RB", 2): .531, ("RB", 3): .681,
    ("TE", 0): .099, ("TE", 1): .118, ("TE", 2): .193, ("TE", 3): .280,
    ("WR", 0): .099, ("WR", 1): .156, ("WR", 2): .244, ("WR", 3): .385}
DRAFT_PRIOR = {  # cold start: no snaps yet (week 1 rookies). round bucket 1 / 2-3 / 4-7 / undrafted
    ("RB", 1): .518, ("RB", 2): .412, ("RB", 4): .220, ("RB", 9): .202,
    ("WR", 1): .365, ("WR", 2): .256, ("WR", 4): .196, ("WR", 9): .163,
    ("TE", 1): .235, ("TE", 2): .219, ("TE", 4): .149, ("TE", 9): .133,
    ("QB", 1): .176, ("QB", 2): .151, ("QB", 4): .127, ("QB", 9): .153}
K_OPP = 25.0        # shrinkage half-weight in touches: a 25-touch sample counts equally with the prior

def _snap_bucket(x):
    return np.where(x < .25, 0, np.where(x < .5, 1, np.where(x < .75, 2, 3)))

def _round_bucket(r):
    return np.where(r.isna(), 9, np.where(r <= 1, 1, np.where(r <= 3, 2, 4)))

def role_prior(df: pd.DataFrame) -> pd.Series:
    """What we expect before this season's touches count for anything.
    Snap share first (it stabilises after one game), draft capital for players with no snaps yet,
    position average only as a floor."""
    pos = df.position.fillna("WR")
    snap = df.offense_pct_std.fillna(df.offense_pct_prev) if "offense_pct_prev" in df else df.offense_pct_std
    sb = pd.Series(_snap_bucket(snap.fillna(-1)), index=df.index).where(snap.notna())
    out = pd.Series([SNAP_PRIOR.get((p, b)) if pd.notna(b) else None for p, b in zip(pos, sb)], index=df.index, dtype=float)
    rb = pd.Series(_round_bucket(df.draft_round if "draft_round" in df else pd.Series(np.nan, index=df.index)), index=df.index)
    draft = pd.Series([DRAFT_PRIOR.get((p, int(b))) for p, b in zip(pos, rb)], index=df.index, dtype=float)
    return out.fillna(draft).fillna(pos.map(POS_PRIOR)).fillna(.25)

def make_features(df: pd.DataFrame):
    df = df.copy()
    # ----- feature engineering: shrunk role rates (same idea as v1, but the model learns the weights) -----
    n = df.g_std.clip(lower=0)
    # opportunity, not games: one lucky target should not look like a role
    opp = ((df.targets_std.fillna(0) + df.carries_std.fillna(0)) * n).clip(lower=0)
    w = opp / (opp + K_OPP)
    base_prior = role_prior(df)
    def shrunk(cur, prev, fallback):
        """Blend this season's rate toward the prior, weighted by how many touches it rests on."""
        prev = prev.fillna(fallback); cur = cur.fillna(prev)
        return w * cur + (1 - w) * prev
    pos_fb = base_prior          # snap-conditioned / draft-capital prior, not a flat position average
    for c, fb in [("xtd", pos_fb), ("x_rec_td", pos_fb * .7), ("x_rush_td", pos_fb * .3), ("rz_tgt", 0.8), ("rz_carry", 0.8), ("i5_carry", 0.2), ("ez_tgt", 0.3),
                  ("tgt_share", 0.1), ("car_share", 0.1), ("rz_tgt_share", 0.1), ("rz_car_share", 0.1), ("xtd_share", 0.12), ("offense_pct", 0.5), ("td", pos_fb), ("targets", 3), ("carries", 3)]:
        df[f"{c}_shr"] = shrunk(df[f"{c}_std"], df[f"{c}_prev"], fb)
    # offense / defense: season-to-date if >=3 games else previous season
    def blend(std, prev):
        w = (df.g_std.clip(upper=6) / 6.0)  # crude: trust current season more as it goes
        return w * std.fillna(prev) + (1 - w) * prev.fillna(std)
    off_cols = ["plays", "pass_rate", "rz_trips", "rz_pass_rate", "team_xtd_g", "team_td_g", "pass_oe", "rz_td_pct", "neutral_pass_rate", "gl_concentration", "rz_tgt_concentration", "rz_pass_rate_trail", "rz_pass_rate_lead"]
    def_cols = ["d_pass_td", "d_rush_td", "d_xtd", "d_td", "d_rz_trips", "d_rz_td", "d_td_RB", "d_td_WR", "d_td_TE", "d_td_over_x"]
    for c in off_cols: df[f"off_{c}"] = blend(df[f"off_{c}_std"], df[f"off_{c}_prev"])
    for c in def_cols: df[f"def_{c}"] = blend(df[f"{c}_std"], df[f"{c}_prev"])
    df["def_rz_td_pct"] = df.def_d_rz_td / df.def_d_rz_trips.replace(0, np.nan)
    # interactions: what the offense does x what share of it this player gets
    df["x_rz_pass_share"] = df.off_rz_pass_rate * df.rz_tgt_share_shr          # passing RZ offense x his RZ target share
    df["x_rz_rush_share"] = (1 - df.off_rz_pass_rate) * df.rz_car_share_shr    # rushing RZ offense x his RZ carry share
    df["x_trips_xtd"] = df.off_rz_trips * df.xtd_share_shr                     # RZ trips x his share of expected TDs
    # ---- role trend: last-2-games vs season-to-date (NaN when < 2 games -> model treats as "no trend info")
    for c in ["xtd", "xtd_share", "rz_tgt_share", "rz_car_share", "i5_carry", "offense_pct"]:
        df[f"{c}_trend"] = df[f"{c}_l2"] - df[f"{c}_std"]
    df["xtd_volatility"] = df.xtd_sd
    # ---- game script: expected trailing pressure from the spread, times how the offense behaves when trailing
    df["trail_pressure"] = (-df.spread).clip(lower=0)                            # points of underdog-ness
    df["x_trail_pass"] = df.trail_pressure * df.off_rz_pass_rate_trail.fillna(df.off_rz_pass_rate)
    df["x_lead_rush"] = df.spread.clip(lower=0) * (1 - df.off_rz_pass_rate_lead.fillna(df.off_rz_pass_rate))
    # ---- availability
    df["abs_same_grp"] = np.where(df.position.eq("RB"), df.abs_rush, df.abs_pass)
    df["x_abs_share"] = df.abs_same_grp * df.xtd_share_shr
    # ---- weather: wind hurts passing TDs; interact with how much of his xTD is receiving
    df["x_wind_pass"] = df.wind.fillna(0) * (df.x_rec_td_shr / (df.xtd_shr + 1e-6)).clip(0, 1)
    df["pos_RB"] = (df.position == "RB").astype(int); df["pos_WR"] = (df.position == "WR").astype(int); df["pos_TE"] = (df.position == "TE").astype(int)
    df["games_to_date"] = df.g_std; df["has_prev"] = df.games_prev.notna().astype(int)

    FEATS = [f"{c}_shr" for c in ["xtd", "x_rec_td", "x_rush_td", "rz_tgt", "rz_carry", "i5_carry", "ez_tgt", "tgt_share", "car_share", "rz_tgt_share", "rz_car_share", "xtd_share", "offense_pct", "td", "targets", "carries"]] + \
            [f"off_{c}" for c in off_cols] + [f"def_{c}" for c in def_cols] + ["def_rz_td_pct", "x_rz_pass_share", "x_rz_rush_share", "x_trips_xtd", "implied", "total", "spread", "home", "pos_RB", "pos_WR", "pos_TE", "games_to_date", "has_prev", "new_team",
         "xtd_l2", "xtd_share_l2", "rz_tgt_share_l2", "rz_car_share_l2", "i5_carry_l2", "offense_pct_l2", "xtd_trend", "xtd_share_trend", "rz_car_share_trend", "rz_tgt_share_trend", "i5_carry_trend", "offense_pct_trend", "xtd_volatility",
         "trail_pressure", "x_trail_pass", "x_lead_rush", "off_rz_pass_rate_trail", "off_rz_pass_rate_lead",
         "abs_same_grp", "abs_xtd_team", "abs_n", "x_abs_share", "questionable"]  # weather kept as call-out only (backtest: no gain)

    FEATS_dedup = list(dict.fromkeys(FEATS))  # newer sklearn rejects duplicate column names
    return df, FEATS_dedup
