# sixpts

Touchdown-first NFL prop research. Expected touchdowns from where every touch happens, shrunk toward last season, adjusted for game environment and opponent, priced against the market.

## Layout
```
engine/   model.py (pure scoring), ingest.py (nflverse + PropFinder loaders, source-tagged), run_week.py (weekly job)
api/      FastAPI — /api/board/{week} public, /api/private/board/{week} token-gated
web/      Vite + React board (public by default; admin key unlocks stored prices and PF-backed notes)
db/       Postgres schema with `source` on every fact table and public_* views that exclude PropFinder
docs/     data-independence.md — how each PropFinder layer gets replaced
data/     downloads, board JSONs (gitignored)
```

## Run locally
```
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
# fitted model (the one the board serves)
python3 engine/build_training.py                         # downloads 2021-2026 pbp (~130 MB once), builds data/train.parquet
python3 engine/fit_model.py                              # trains, prints 2025 holdout + calibration, saves data/td_model.pkl
python3 engine/build_training.py --upcoming 2026 2       # walk-forward rows for the upcoming week
python3 -m engine.score_week --season 2026 --week 2      # writes data/board_w2.json + board_w2_public.json
python3 -m engine.team_profiles --season 2026 --week 2   # writes data/teams_w2.json (Games tab)
python3 -m engine.score_card --season 2026 --week 1      # after a week is played: grades the published board -> Record tab
python3 engine/backtest.py                               # ablations, weekly table, leakage checks (run after any feature change)
# (optional) formula model v1 for comparison:
# python3 -m engine.run_week --season 2026 --week 2 --pf-json data/pf_team_defense_2025.json
uvicorn api.main:app --reload --port 8000
cd web && npm i && npm run dev     # http://localhost:5173/?week=2
cd web && npm run check            # typecheck + renders the app in jsdom against data/*.json; run before every commit
```

## Data rules
- nflverse: public, CC-BY. Backbone for everything time-varying.
- PropFinder: your subscription, personal/non-commercial. Loaded only with `PF_COOKIE`/`PF_BEARER` in env, written with `source='pf'`, never served on a public route. Keep pulls at page-load volume (~10-15/day).
- Public deploy (sixpts.com) serves `board_w{n}_public.json` — PF-derived matchup notes are stripped; the blended multiplier remains.

## Deploying a new week
The API serves boards from the repo (Render's free tier has no disk). After the Tuesday run:
```
git add -f data/board_w<N>_public.json data/teams_w<N>.json && git commit -m "boards: week <N>" && git push
```
Render redeploys automatically. The private board, odds and picks live in the database, not the repo.

## Deploy
- API: Render (or Fly) with DATABASE_URL → Supabase Postgres (`db/schema.sql`), SIXPTS_TOKEN, PF_COOKIE (private only).
- Web: Vercel, root `web/`, env `VITE_API` if API is on another host. Point sixpts.com CNAME at Vercel.
- Cron: `.github/workflows/weekly.yml` (Tue build + Thu/Sat/Sun refresh). Secrets: DATABASE_URL, PF_COOKIE.

## Betting workflow (private key required)
1. Board defaults to sort-by-edge. Type the book price (and the line for Receptions) -> edge shows; yellow flag = 3+ pts.
2. "Log pick" posts to the DB with the price. At kickoff, record the closing price: `PATCH /api/picks/{id}/close {"closing_price": -160}` (or via the picks list).
3. Tuesday: `python3 -m engine.grade --season 2026 --week N` -> won/lost, units, CLV. `GET /api/record` for the running record.
Judge on CLV over 60+ picks, not on any single week.

## Model picks
`GET /api/model-picks/{week}` — the model chooses, with reasons, among players that have a stored price: value (edge vs the book's implied probability, EV per unit, quarter-Kelly stake) AND signal agreement (real scoring role, defense leaks, environment, available, role not falling, not a streak, certainty). Tiers: **Bet** (5+ pts edge, all-but-one signals green, high certainty), **Lean** (edge, mixed signals), **Pass** (says exactly why, e.g. "would need -120 or better"). Nothing shorter than -250 or longer than +600 can be a Bet.

## Odds from The Odds API (licensed — public)
```
python3 -m engine.odds_api --week 3                  # anytime TD across DK/FD/MGM/CZR
python3 -m engine.odds_api --week 3 --market receptions
python3 -m engine.odds_api --usage                   # credits remaining
python3 -m engine.odds_api --week 2 --replay data/oddsapi_sample.json --dry-run
```
Set `ODDS_API_KEY` in `.env`. One credit per event × market × region, so a full slate is ~16 credits;
the free 500/month covers a couple of refreshes a week. Rows land in `odds` with `source='oddsapi'`,
which is licensed for display — the public board and the public picks engine use them.
Refresh Thu / Sat / Sun morning.

## Odds from PropFinder (private only)
Save the props page(s) from your logged-in browser to `data/props.json` (one page or a JSON list of pages), or set `PF_COOKIE`/`PF_BEARER` in `.env` and pull live:
```
python3 -m engine.pf_odds --week 2 --file data/props.json
python3 -m engine.pf_odds --week 2 --live                             # paginated, ~4 calls for the TD board
python3 -m engine.pf_odds --week 2 --live --category receivingReceptions
```
Writes per-book rows to `odds` (source='pf'), separates the 1.5 line into `td_2plus`, prints unmatched names (add to `ALIASES` in `engine/pf_odds.py`) and PropFinder's questionable flags. Model picks then use the best price across books. Refresh Thu / Sat / Sun morning. PF-sourced odds never render on a public route.

## Accounts (optional)
Single-user by default (admin key = `SIXPTS_TOKEN`). To open it up: enable Supabase Auth (magic link), run `db/auth_migration.sql`, set `SUPABASE_JWT_SECRET` + `ADMIN_USER_ID` in `.env` and `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` in `web/.env`. Research stays public; sign-in only unlocks saving picks/prices. The public Record shows the house (admin) picks only.

## Live touchdown alerts
```
python3 -m engine.live_td --week 3 --probe     # confirm the ESPN feed parses (run during a game)
python3 -m engine.live_td --week 3             # watch; notifies on every TD, writes data/live_td_w3.json
python3 -m engine.live_td --week 2 --replay data/espn_sample.json --dry-run   # offline test
```
One scoreboard call every 25s; a game's detail call only when its score moves; sleeps 5 min when nothing is live.
Set `NTFY_TOPIC` (free, no signup — install the ntfy app and subscribe to the same topic) and/or Telegram/Discord in `.env`.
macOS also shows a local banner. The site's "Touchdowns today" panel reads the same feed.

## Weekly rhythm
- Tue: `score_card --week <last>` (model vs results) -> `grade --week <last>` (your picks) -> `build_training.py --upcoming <wk>` -> `score_week` -> `team_profiles`
- Thu/Sat/Sun: `pf_odds` for fresh prices; re-run `score_week` once the week's injury report is in nflverse (board shows a banner until then)
- Monthly: `build_training.py` (full) -> `fit_model.py` -> `backtest.py`

## Model (v2)
Gradient-boosted classifier, isotonic-calibrated, trained on 2021-2025 player-games (walk-forward features only). Layers: role (season-to-date shrunk to prior season), role trend (last 2 games vs season), offense identity, defense profile, game script, availability (injury report), environment. 2025 holdout: logloss 0.478, AUC 0.697, calibrated to ~2 pts through 60%.

## Model (v1, formula)
`P(TD) = 1 − e^(−xTD)`, `xTD = xTD_pg_shrunk × env_mult × matchup_mult`
- xTD per touch: empirical TD rate by catch point (targets) / yardline (carries), fit on prior season
- shrinkage: `(n·cur + k·prior)/(n+k)`, k=4 games (2 for new-team players)
- env: implied team total / 23
- matchup: 0.5·(opp RZ TD% vs avg) + 0.5·(pass/rush TD allowed vs avg), clipped 0.85–1.15
Tunables live in `engine/model.py::Config`.
