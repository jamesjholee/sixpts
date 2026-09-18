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
