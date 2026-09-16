# Data independence — replacing PropFinder layer by layer

Goal: every number on sixpts.com comes from data we own, compute, or license with display rights. PropFinder is a private accelerator while we get there, never a dependency of the public product.

## What PropFinder gives us today, and what replaces it

| Layer | PF field(s) | Replacement | Status | Effort |
|---|---|---|---|---|
| Red zone usage (player) | RZ tgt, RZ carries, i10/i5 | **nflverse pbp** — yardline on every touch | Done (engine) | — |
| Expected TDs | (PF has none) | **own xTD model** — P(TD) by catch point / yardline, fit on prior season | Done | Refit each offseason; add down/distance + defenders-in-box (nflverse participation, post-season) as features |
| Red zone defense (team) | rz_td_pct, g2g_td_pct, RZ targets/carries allowed, pass/rush TD allowed | **nflverse pbp** grouped by defteam — identical numbers | Not built yet, trivial | 1 hour |
| Game environment | (PF has none) | nflverse schedules (spread_line, total_line) | Done | — |
| Routes / route % | RTE, RTE% | **Approximation**: snap % × team dropback rate (nflverse snap counts + pbp). True routes are FTN charting — available free in nflverse only after the season | Approximation is fine for v1 | 2 hours; license FTN for in-season truth |
| Coverage shells | man/zone, one/two-high, Cover 0–6 | **Licensed**: FTN Data API (per-play coverage tags, in-season) or Sports Info Solutions. Free fallback: prior-season nflverse participation (FTN-derived) as static priors, since coverage tendencies are stable year to year (r≈0.66) | 2025 priors free in Jan; in-season needs a license | License cost, ~1 day integration |
| Receiver splits vs coverage | YPRR/TPRR vs man/zone/shell | Same as above — computed from licensed per-play coverage + pbp | Same | Same |
| Alignment leaks | slot/wide/inline/backfield multipliers | **NGS via nflverse `load_nextgen_stats`** gives receiver-level alignment-adjacent metrics; true per-play alignment needs FTN/SIS or nflverse participation (post-season) | Partial | Same license |
| Multi-book odds | props board | **Licensed odds API** (The Odds API, OpticOdds, SportsGameOdds) — all allow display; prices from ~$30/mo for player props | Not built | 1 day |
| Hit rates L5/L10/L20 | props board | Compute ourselves from nflverse weekly stats vs stored lines | Trivial once odds are stored | 2 hours |

## Order of operations
1. **Now (free):** team red-zone defense from pbp; route % approximation; store our own odds snapshots once an odds API is in. This alone makes the *public* board fully independent, minus the coverage layer.
2. **January 2027 (free):** load nflverse 2026 participation → coverage shells + routes + alignment for all of 2026. Fit the coverage-fit multiplier on that. Use as 2027 preseason priors.
3. **When the public board proves edge (paid):** license FTN or SIS for in-season coverage. This is the only layer that can't be owned without charting film, and the only reason PropFinder is currently irreplaceable.

## What "owning" coverage would actually take
Coverage tags come from humans charting film (FTN, SIS, PFF) or, recently, from tracking-data models (NGS). Building a charting operation is a business, not a feature. The realistic path to owning it is training a classifier on tracking data — the NFL Big Data Bowl releases sample tracking data each year, and nflverse participation gives labelled coverage for past seasons to train against — but in-season tracking data isn't public. Treat coverage as a licensed input for the foreseeable future and own everything else.

## Rule enforced in code
Every table row carries `source`. Public API routes and `public_*` SQL views exclude `source='pf'`. A PropFinder-derived number can only appear inside a blended multiplier, never as a displayed field, on any public route.
