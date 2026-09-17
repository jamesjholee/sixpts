-- sixpts schema. Every fact table carries `source` ('nflverse' | 'pf' | 'own').
-- Public routes read ONLY from the public_* views, which exclude source='pf'.

create table if not exists teams (
  abbr text primary key, name text, pf_team_id text
);

create table if not exists players (
  gsis_id text primary key, name text, position text, team text references teams(abbr), pf_player_id text
);

create table if not exists games (
  game_id text primary key, season int, week int, kickoff date,
  home text, away text, spread_line numeric, total_line numeric, result numeric
);

-- raw pulls, for replay/audit when the model changes
create table if not exists raw_pulls (
  id bigserial primary key, pulled_at timestamptz default now(), source text, endpoint text, params jsonb, payload jsonb
);

create table if not exists player_usage_week (
  gsis_id text references players, game_id text references games, season int, week int, team text,
  targets int, rz_tgt int, ez_tgt int, adot numeric, carries int, rz_carry int, i10_carry int, i5_carry int,
  rec_td int, rush_td int, x_rec_td numeric, x_rush_td numeric, offense_pct numeric,
  source text default 'nflverse', primary key (gsis_id, game_id)
);

create table if not exists player_prior (
  gsis_id text references players, season int, team text, games int,
  targets int, rz_tgt int, ez_tgt int, carries int, rz_carry int, i5_carry int, td int, xtd numeric,
  source text default 'nflverse', primary key (gsis_id, season)
);

create table if not exists team_defense (
  team text references teams, season int, stat_window text, as_of date,
  man_rate numeric, zone_rate numeric, one_high numeric, two_high numeric,
  cover0 numeric, cover1 numeric, cover2 numeric, cover2man numeric, cover3 numeric, cover4 numeric, cover6 numeric,
  blitzes int, dropbacks int, rz_td_pct numeric, g2g_td_pct numeric, rz_tgt_allowed int, rz_rush_allowed int,
  pass_td_allowed int, rush_td_allowed int, rz_trips int,
  source text not null, primary key (team, season, stat_window, source)
);

create table if not exists odds (
  gsis_id text references players, game_id text references games, market text, line numeric, book text,
  price int, fetched_at timestamptz default now(), source text not null, user_id text,
  primary key (gsis_id, game_id, market, line, book, fetched_at)
);

create table if not exists scores (
  gsis_id text references players, game_id text references games, market text, as_of timestamptz default now(),
  xtd_pg_2026 numeric, xtd_pg_prior numeric, xtd_pg_shrunk numeric, env_mult numeric, matchup_mult numeric,
  xtd_proj numeric, p_model numeric, fair_odds int, opp_score int, gravity_score int, matchup_score int, env_score int, score int,
  matchup_note text, matchup_source text, primary key (gsis_id, game_id, market, as_of)
);

create table if not exists picks (
  id bigserial primary key, gsis_id text, game_id text, market text, player text, team text, opp text, line numeric,
  book text, price_taken int, p_model numeric, stake_units numeric default 1, note text,
  placed_at timestamptz default now(), closing_price int, result text, actual numeric, pnl_units numeric, clv numeric, user_id text
);

-- ---------- public-safe views (no PropFinder-sourced fields) ----------
create or replace view public_team_defense as
  select * from team_defense where source <> 'pf';

create or replace view public_scores as
  select gsis_id, game_id, market, as_of, xtd_pg_2026, xtd_pg_prior, xtd_pg_shrunk, env_mult, matchup_mult,
         xtd_proj, p_model, fair_odds, opp_score, gravity_score, matchup_score, env_score, score,
         case when matchup_source = 'pf' then null else matchup_note end as matchup_note
  from scores;

create or replace view public_odds as select * from odds where source <> 'pf';
