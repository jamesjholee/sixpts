-- Run in Supabase SQL editor after enabling Auth. Local SQLite ignores RLS (dev uses the admin token).
alter table picks add column if not exists user_id uuid;
alter table odds  add column if not exists user_id uuid;
create table if not exists user_prefs (user_id uuid primary key, hidden_cols jsonb default '[]', default_book text, updated_at timestamptz default now());

alter table picks enable row level security;
alter table odds enable row level security;
alter table user_prefs enable row level security;
create policy "own picks"  on picks      for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own odds"   on odds       for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own prefs"  on user_prefs for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
-- public read of the house record: a view over the admin's graded picks only
create or replace view public_record as select market, player, team, opp, line, price_taken, p_model, closing_price, result, pnl_units, clv, game_id, placed_at
  from picks where result is not null and user_id = (select id from auth.users where email = current_setting('app.admin_email', true));
