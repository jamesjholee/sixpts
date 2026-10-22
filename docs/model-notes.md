# Model notes — where the numbers come from

## Shrinkage: opportunity, not games (Sep 18 2026)
The first version blended this season's rate toward a prior weighted by **games played**. One target
that happened to land in the end zone therefore looked like a red-zone role. On the week 2 board that
produced players priced at 24% whom four sportsbooks priced at 6%.

Measured on 2021-2025 player-games, the season-to-date rate carries almost no signal until a player
has been given the ball a meaningful number of times:

| touches to date | correlation of rate with scoring |
|---|---|
| 0–5 | +0.02 |
| 5–15 | +0.09 |
| 15–30 | +0.14 |
| 60+ | +0.15 |

So the blend is now weighted by **touches**, with `K_OPP = 25` (a 25-touch sample counts equally with
the prior).

## The prior itself: snaps first, draft capital for cold starts
Touch-weighted shrinkage toward a flat position average made the thin tail *worse* (+10.2 points of
over-prediction vs +7.4 before), because the flat average is built from starters. Snap share is the fix —
it stabilises after a single game and separates sharply:

| rookie snap share | scored |
|---|---|
| under 25% | 7.7% |
| 25–50% | 16.6% |
| 50–75% | 25.9% |
| 75%+ | 31.5% |

`SNAP_PRIOR` is expected TDs per game by position × snap bucket. For players with no snaps yet
(week 1 rookies) `DRAFT_PRIOR` uses draft round: first-rounders score 29.3%, undrafted 14.7%.
Draft capital is mostly a proxy for playing time — among rookies already at 50%+ snaps it only
separates 32.1% vs 27.7% — so it is a cold-start fallback, never a replacement for snaps.

Result on the holdout, by sample size:

| tier | predicted | actual | bias |
|---|---|---|---|
| thin (<15 touches) | 14.5% | 13.7% | +0.8 pts (was +7.4) |
| mid | 21.1% | 20.6% | +0.4 |
| starters (60+) | 33.7% | 34.7% | −1.0 |

Snaps are the denominator, not the driver: among players at 70%+ snaps, those with no red-zone
targets score 25% and those with a 15%+ red-zone target share score 32%.

## The market as a guard, not a feature
`select_picks.py` refuses to call a Bet or Lean when our probability is more than 2.2x the book's
**and** the player's usage is thin. The market is not blended into the probability — doing that would
make "edge vs the market" meaningless. It only vetoes.

## What is still wrong
Weeks 1 and 2 of 2026 both came in under-confident in the 20–40% band (said 24%, actual 40% in week 1).
Two weeks is not enough to retune; revisit after week 4 with `score_card`.

## Feature overlap: measured, then deliberately not acted on (Sep 19 2026)
74 features cluster into 32 independent groups by rank correlation — three clusters of 7-8 features each
are all measuring one thing (expected touchdowns, rushing role, receiving role).

Permuting each **cluster as a unit** on the 2025 holdout (the correct test when features are correlated;
permuting them one at a time understates every member of a correlated group):

| cluster | logloss increase when shuffled |
|---|---|
| expected TDs (7 features) | +0.0211 |
| rushing role (8) | +0.0066 |
| snap share (2) | +0.0066 |
| game environment (5) | +0.0040 |
| receiving role (7) | +0.0037 |
| offense tendencies (3) | +0.0004 |
| defense (4) | +0.0003 |
| the other 24 clusters | ~0.0000 or negative |

A model with one representative per cluster was then fitted and compared:

| model | features | logloss | AUC | thin-tail bias | teammate-out rows |
|---|---|---|---|---|---|
| full | 74 | 0.4789 | 0.6944 | −0.2% | 0.4684 |
| clustered | 32 | 0.4785 | 0.6919 | −0.4% | 0.4705 |

**Not shipped.** Log loss is a rounding error better, AUC is worse, and the rows where the dropped
features exist to help (a teammate out) get worse. Fewer features would be easier to maintain, but not
at the cost of the cases the extra layers were built for.

Two things the exercise did settle:
- **Snap share carries as much signal as the entire rushing-role cluster.** It is one feature against eight.
- **The contributions shown to users were never at risk from this.** `score_week` computes them by
  permuting a whole layer (role / offense / defense / environment), which is already the grouped method.
  Correlated features inside a layer cannot misattribute credit between layers.

Worth revisiting after week 8, when the trend and availability features have enough in-season rows to
show an effect that a season-wide average currently buries.

## Parlays and correlation (Sep 25 2026)
`engine/parlay.py` estimates the joint probability by simulation rather than multiplying legs, because
anytime-TD legs are not independent. Each game draws a shared scoring factor (lognormal, sd 0.34) that
scales every leg in it; teammates then compete, with one scorer dragging the others down 12%.

Measured on the week 3 board:

| shape | naive product | honest | effect |
|---|---|---|---|
| two teammates (CIN RB + WR) | 21.2% | 18.4% | **−2.9 pts** |
| two opponents in one game | 21.7% | 23.8% | **+2.1 pts** |
| two different games | 17.8% | 17.6% | −0.2 pts |

The direction flips with the shape, which is the whole reason to compute it. Teammates cannibalise each
other's red zone trips; opponents in a shootout rise together. My first version of the explanatory note
asserted that same-game legs always help — the simulation disproved it on the first test.

Nothing here makes parlays a good idea: the vig compounds with every leg, and the panel reports how much.

## Oct 22 2026 — favorites check, and why the model was NOT changed

Weeks 1–6 graded (1,708 player-games): Brier skill +8.4% vs base rate, AUC .68, calibration within noise at
every decile except the top band, where players priced at even money or better scored 76% against a 57%
prediction (n=45). Tested whether that is structural, walk-forward on 2021–24 → 2025 and 2021–25 → 2026 W1–6:

| variant | 2025 holdout AUC | 2025 fav ≥50% pred→act | 2026 W1–6 fav ≥50% pred→act |
|---|---|---|---|
| baseline (K_OPP 25) | .694 | .614 → .535 | .580 → .756 |
| K_OPP 12 | .695 | .610 → .555 | .583 → .743 |
| K_OPP 6 | .695 | .646 → .609 | .594 → .730 |
| + team xTD rank / share-of-top feats | .696 | .619 → .545 | .567 → .732 |

In 2025 the same band was *over*-predicted; no variant moves 2026 and none improves log-loss. The 2026 gap is
~2.4 SD of binomial noise on 45 rows and reverses in the prior season, so it was left alone. Revisit after Week 12
with the pooled sample; if high-certainty players are still under by 4+ points, fit a per-certainty recalibration.

What did change (engine/market.py): the bet number is a certainty-weighted blend of model and devigged
market, because on the one week with raw prices in the repo (W2) the model's ranking skill equals the book's
(AUC .709 vs .715) and every bucket where we were above the market under-performed the market's number.
The scorecard now reports model-vs-market every week.
