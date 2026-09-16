# sixpts

Touchdown-first NFL prop research. Expected touchdowns from where every touch happens, shrunk toward last season, adjusted for game environment and opponent, priced against the market.

## Layout
```
engine/   model.py (pure scoring), ingest.py (nflverse + PropFinder loaders, source-tagged), run_week.py (weekly job)
api/      FastAPI — /api/board/{week} public, /api/private/board/{week} token-gated
web/      Vite + React board (public by default; paste the private key to see PF-backed matchup notes)
db/       Postgres schema with `source` on every fact table and public_* views that exclude PropFinder
docs/     data-independence.md — how each PropFinder layer gets replaced
data/     downloads, board JSONs (gitignored)
```

## Run locally
```
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
python -m engine.run_week --season 2026 --week 2 --pf-json data/pf_team_defense_2025.json   # or --no-pf
uvicorn api.main:app --reload --port 8000
cd web && npm i && npm run dev     # http://localhost:5173/?week=2
```

## Data rules
- nflverse: public, CC-BY. Backbone for everything time-varying.
- PropFinder: your subscription, personal/non-commercial. Loaded only with `PF_COOKIE`/`PF_BEARER` in env, written with `source='pf'`, never served on a public route. Keep pulls at page-load volume (~10-15/day).
- Public deploy (sixpts.com) serves `board_w{n}_public.json` — PF-derived matchup notes are stripped; the blended multiplier remains.

## Deploy
- API: Render (or Fly) with DATABASE_URL → Supabase Postgres (`db/schema.sql`), SIXPTS_TOKEN, PF_COOKIE (private only).
- Web: Vercel, root `web/`, env `VITE_API` if API is on another host. Point sixpts.com CNAME at Vercel.
- Cron: `.github/workflows/weekly.yml` (Tue build + Thu/Sat/Sun refresh). Secrets: DATABASE_URL, PF_COOKIE.

## Model (v1)
`P(TD) = 1 − e^(−xTD)`, `xTD = xTD_pg_shrunk × env_mult × matchup_mult`
- xTD per touch: empirical TD rate by catch point (targets) / yardline (carries), fit on prior season
- shrinkage: `(n·cur + k·prior)/(n+k)`, k=4 games (2 for new-team players)
- env: implied team total / 23
- matchup: 0.5·(opp RZ TD% vs avg) + 0.5·(pass/rush TD allowed vs avg), clipped 0.85–1.15
Tunables live in `engine/model.py::Config`.
