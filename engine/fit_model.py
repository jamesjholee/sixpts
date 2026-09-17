"""Fit P(TD) classifier. Train 2021-2024, test 2025 (never seen). Compare to the v1 formula.
Saves data/td_model.pkl and prints calibration + feature importance.
"""
import pandas as pd, numpy as np, pickle, sys
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.inspection import permutation_importance
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score
import os
D = os.environ.get("SIXPTS_DATA", "data")

df = pd.read_parquet(f"{D}/train.parquet")
df = df[df.season >= 2021].copy()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.features import make_features, K
df, FEATS = make_features(df)
train = df[df.season <= 2024]; test = df[df.season == 2025]
# drop early-season rows with no info at all (week 1 rookies with no prior) — keep; model handles NaN
X, y = train[FEATS], train.scored; Xt, yt = test[FEATS], test.scored

base = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=60, l2_regularization=1.0, random_state=7)
clf = CalibratedClassifierCV(base, method="isotonic", cv=5).fit(X, y)
p = clf.predict_proba(Xt)[:, 1]

# ----- v1 formula baseline on the same rows -----
env = (test.implied / 23).clip(0.7, 1.3)
p_v1 = 1 - np.exp(-(test.xtd_shr * env))

def report(name, pr):
    print(f"{name:12s} logloss {log_loss(yt, pr):.4f}  brier {brier_score_loss(yt, pr):.4f}  auc {roc_auc_score(yt, pr):.4f}")
print("2025 holdout, n =", len(test)); report("v1 formula", p_v1); report("fitted", p)
mkt_like = np.full(len(yt), yt.mean()); report("base rate", mkt_like)

# calibration table
cal = pd.DataFrame({"p": p, "y": yt.values}); cal["bucket"] = pd.cut(cal.p, [0, .1, .2, .3, .4, .5, .6, .7, 1])
print("\nCalibration (fitted): predicted vs actual TD rate")
print(cal.groupby("bucket", observed=True).agg(n=("y", "size"), pred=("p", "mean"), actual=("y", "mean")).round(3).to_string())

# feature importance (permutation on holdout, grouped)
pi = permutation_importance(clf, Xt, yt, n_repeats=5, random_state=7, scoring="neg_log_loss", n_jobs=-1)
imp = pd.Series(pi.importances_mean, index=FEATS).sort_values(ascending=False)
print("\nTop 15 features (permutation importance, holdout logloss):"); print(imp.head(15).round(4).to_string())
grp = {"role": [f for f in FEATS if f.endswith("_shr")], "offense": [f for f in FEATS if f.startswith("off_")], "defense": [f for f in FEATS if f.startswith("def_")], "environment": ["implied", "total", "spread", "home"]}
print("\nImportance by layer:"); print({k: round(float(imp[v].sum()), 4) for k, v in grp.items()})

# refit on everything through 2025 for production
full = df[df.season <= 2025]
clf_full = CalibratedClassifierCV(base, method="isotonic", cv=5).fit(full[FEATS], full.scored)
pickle.dump({"model": clf_full, "feats": FEATS, "K": K}, open(f"{D}/td_model.pkl", "wb"))
df.to_parquet(f"{D}/features.parquet", index=False)
print("\nsaved td_model.pkl")
