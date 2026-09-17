"""Backtest & sanity for the v2 feature set. Train <=2024, test 2025.
- ablation: base features vs +trend vs +script vs +availability vs all
- weekly log loss table
- leakage checks: shuffled-label AUC ~0.5; no single feature with suspicious AUC; no feature computed from the same game
python3 engine/backtest.py
"""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.features import make_features
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import log_loss, roc_auc_score, brier_score_loss
D = os.environ.get("SIXPTS_DATA", "data")

df = pd.read_parquet(f"{D}/train.parquet"); df, FEATS = make_features(df)
tr, te = df[df.season <= 2024], df[df.season == 2025]
print(f"train {len(tr)}  test {len(te)}  | rows with a teammate Out/Doubtful: {(df.abs_n > 0).mean():.1%}  | questionable: {df.questionable.mean():.1%}")

def fit(feats, X=tr, seed=7):
    base = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=60, l2_regularization=1.0, random_state=seed)
    return CalibratedClassifierCV(base, method="isotonic", cv=5).fit(X[feats], X.scored)
def ev(clf, feats, X=te):
    p = clf.predict_proba(X[feats])[:, 1]; return log_loss(X.scored, p), roc_auc_score(X.scored, p), brier_score_loss(X.scored, p), p

TREND = [f for f in FEATS if f.endswith("_l2") or f.endswith("_trend") or f == "xtd_volatility"]
SCRIPT = ["trail_pressure", "x_trail_pass", "x_lead_rush", "off_rz_pass_rate_trail", "off_rz_pass_rate_lead"]
AVAIL = ["abs_same_grp", "abs_xtd_team", "abs_n", "x_abs_share", "questionable"]
WEATHER = ["indoor", "wind", "temp", "x_wind_pass"]
BASE = [f for f in FEATS if f not in TREND + SCRIPT + AVAIL + WEATHER]
sets = {"base (v1 features)": BASE, "+ role trend": BASE + TREND, "+ game script": BASE + SCRIPT, "+ availability": BASE + AVAIL, "+ weather": BASE + WEATHER, "all (v2)": FEATS}
res = {}; preds = {}
for name, fs in sets.items():
    m = fit(fs); ll, auc, br, p = ev(m, fs); res[name] = (ll, auc, br); preds[name] = p
    print(f"{name:22s} logloss {ll:.4f}  auc {auc:.4f}  brier {br:.4f}")

# --- where availability matters: rows with a teammate out
mask = te.abs_n > 0
if mask.sum() > 50:
    for name in ["base (v1 features)", "all (v2)"]:
        print(f"  teammate-out rows (n={mask.sum()}): {name:20s} logloss {log_loss(te.scored[mask], preds[name][mask]):.4f}")

# --- weekly table for v2
te2 = te.assign(p=preds["all (v2)"], p0=preds["base (v1 features)"])
wk = te2.groupby("week").apply(lambda g: pd.Series(dict(n=len(g), actual=g.scored.mean(), v2=log_loss(g.scored, g.p), v1=log_loss(g.scored, g.p0)))).round(3)
print("\n2025 by week (logloss v2 vs v1 features):"); print(wk.T.to_string())

# --- calibration v2
c = te2.assign(b=pd.cut(te2.p, [0, .1, .2, .3, .4, .5, .6, .7, 1])).groupby("b", observed=True).agg(n=("scored", "size"), pred=("p", "mean"), actual=("scored", "mean")).round(3)
print("\nCalibration v2:"); print(c.to_string())

# --- leakage checks
rng = np.random.default_rng(0); sh = tr.copy(); sh["scored"] = rng.permutation(sh.scored.values)
m = fit(FEATS, sh); ll, auc, _, _ = ev(m, FEATS)
print(f"\nLEAK CHECK 1 shuffled labels -> holdout AUC {auc:.3f} (should be ~0.50)")
single = {}
for f in FEATS:
    x = te[f].fillna(te[f].median()); 
    try: a = roc_auc_score(te.scored, x); single[f] = max(a, 1 - a)
    except Exception: pass
s = pd.Series(single).sort_values(ascending=False)
print("LEAK CHECK 2 strongest single features (AUC alone) — anything > 0.80 would mean same-game info leaked:"); print(s.head(6).round(3).to_string())
same_game = [f for f in FEATS if not (f.endswith(("_shr", "_l2", "_trend", "_prev")) or f.startswith(("off_", "def_", "x_", "pos_", "abs_")) or f in ["def_rz_td_pct", "implied", "total", "spread", "home", "games_to_date", "has_prev", "new_team", "xtd_volatility", "trail_pressure", "questionable", "indoor", "wind", "temp"])]
print("LEAK CHECK 3 features not derived from prior games/pregame info:", same_game or "none")
