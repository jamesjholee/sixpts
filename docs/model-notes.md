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

## Oct 22 2026 (later) — fitted blend weights and the Longshot tier

Weeks 3–6 odds turned out to be Sunday-morning fetches (near closing), so the W2-only conclusion above was
re-done on all five weeks (1,367 priced player-games, data/market_rows_w2_6.csv):

- Blend weight fitted by log-loss: high-certainty 0.1, medium 0.3, low 0.9 (shipped as .25/.35/.70, shrunk toward
  .5). Books price established starters better than we do; we price backups and committees better than they do.
- The market is miscalibrated at long prices: +1500 and longer implies 4.5% and scores 12–15% (n=150). Betting every
  active RB/WR/TE there wins in all five weeks. The model's selection roughly doubles the hit rate inside that pool
  (18% vs 7%).
- Tier backtest, flat 1u, near-closing best price (reconstructed, ±8%): old Bet/Lean rules lose (-4u / -21u);
  Longshot rule (+1200..+4000, RB/WR/TE, blended edge ≥3, availability ok, some usage) 86 picks +232u, every week
  positive, every leave-one-week-out split positive. TE best (+110u/32), QB excluded (-26u in the tail).
- Shipped: Longshot / Lean / Pass. Lean is +150..+1200 with edge and signals, labelled research-only (break-even).
  Favorites under +150 are always Pass. Longshot stake is a flat quarter unit; expect ~1 hit in 6.
- Caveats: ~20 hits drive the result; books limit deep props; the Week 7 snapshot lists far more deep players than
  earlier weeks did (cap at +4000 keeps it inside what was tested). Grade every Longshot pick; if the tier's hit rate
  falls under 9% over 150 picks, the pocket has closed.

## Oct 23 2026 — why 47 of 64 week-6 longshots never played, and the fix

The Sunday workflow starts 12:00 UTC and fetches odds as its last step, which landed at 11:23 ET: seven minutes
before inactives post. So 41 of the 47 DNPs still carried a live price. Weekly roster status (nflverse) removes
11 of the 47 (IR/PUP/practice squad) and none of the 17 who played; the other 36 were active-roster healthy
scratches, which nothing published before 11:30 ET identifies.

Shipped: (1) a second Sunday job at 12:10 ET that only refreshes odds, after inactives; books pull scratched
players' markets, so "has a price in the latest refresh" becomes the activity check. (2) The Longshot tier needs
that fresh price; a stale one is a Pass with the reason. (3) score_week drops INA/RES/CUT/RET/EXE/DEV/TRD players.
Until the first post-inactives fetch each week the list is research; the 12:10 ET list is the bet list.

## Simulator, part 1 (display-only) — added week 8

`engine/sim.py` plays each game snap by snap, 5,000 times, vectorised across games. Inputs are nflverse only:
team pass/run rates, completion, sack, interception and yards-per-play mixtures blended prior/current season
(w = g/(g+4)); player target, carry and red-zone shares from the same `_std`/`_prev` feature columns the gbm uses
(w = g/(g+2)); the market spread nudges efficiency ±1%/pt (cap 12%) and the total nudges pace ±5%; HFA 0.02.
Pre-snap 4th-down decisions, a 45-yd FG table, 41-yd net punts. `engine/sim_week.py` writes `data/sim_w{N}.json`
(outcome probabilities, quantiles, per-player P(TD)/P(2+TD)/receptions/yards, QB lines, and the play log of the
simulated game nearest the median score, carrying renderer-ready fields for the replay). The weekly workflow
runs it after `score_week`; `/api/sim/{week}` serves summaries and `/api/sim/{week}/{game_id}` one game with its log.

**Walk-forward backtest, 2026 weeks 1–7, n=1,500 per game** (`python3 -m engine.sim_backtest --weeks 1-7`):

| | sim | market | gbm |
|---|---|---|---|
| total MAE vs market | 3.2 (bias −2.0) | — | — |
| total MAE vs actual | 11.4 | 10.7 | — |
| margin MAE vs actual | 10.8 | 12.1 | — |
| home win % (actual 48.6%) | 50.9% | — | — |
| player TD rate (actual .220) | .222 | — | .218 |
| TD AUC | .668 | — | .688 |
| TD AUC, 50/50 sim+gbm | .685 | | |

By position the sim runs RB slightly hot (.306 vs .287 actual) and TE slightly cold (.201 vs .211); the gbm is
closer on both. Week 7 held out behaves the same. The sim does not beat the gbm at ranking scorers, which is why
it is **display-only**: sim P(TD) sits next to the model's number in the row detail and feeds nothing in
`select_picks`. What it adds is the game-level picture (median score, win %, over %) and the replay. Known lean:
sim totals run ~2–3 points under the market (week 8 slate: −2.9 on average, LV@NYJ −7.1); pace is nudged only
±5% by the total, so high totals are under-reached. Revisit once a few graded weeks show whether the market or
the sim was closer on those games. Median-game play counts of 135–187 include kicks; the high end comes from
games with 50+ incompletions stopping the clock.

## Simulator, part 2 — replay videos (week 8)

`engine/replay.py` turns a game's median-game play log into an mp4 for posting: `python3 -m engine.replay --week 8
--game 2026_08_KC_DEN --format 9x16` (needs `pip install skia-python` and ffmpeg). Skia draws every frame
(~13 ms at 1080×1920), ffmpeg encodes from a pipe; ~1 s of render per second of video on one core. 9x16 is a
broadcast-style camera that follows the ball down a vertical field; 16x9 shows the whole field. Plays are
choreographed from the log's fields (type, air, gain, carrier, result): 22 dots, the carrier ringed in amber
with a trail, defenders converging with lag, the ball arcing on passes and kicks. Pace is capped at 1.8× nominal,
so a 140-play game runs ~90 s; intro and outro carry the sim numbers (win %, median, over %, top P(TD)).
Team dot colours are chosen per matchup to be bright on the turf, apart from each other, and apart from the brand
amber, with silver standing in when a team's palette can't manage it (KC@DEN: red vs silver).

The Tuesday workflow renders every game 9x16 in a `replays` job after the board commits and publishes the files
as assets of a `replays-w{N}` GitHub release (videos never enter the repo); `data/replays_w{N}.json` carries
the URLs, `/api/sim/{week}` attaches them per game, and the Games view links "Watch the sim". The median-game
picker now skips ties (a 19–19 "median game" made a poor video).
