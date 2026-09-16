"""Create the schema in a local SQLite DB for development (Postgres/Supabase in prod uses schema.sql directly)."""
import sqlite3, re, pathlib
sql = pathlib.Path(__file__).with_name("schema.sql").read_text()
sql = (sql.replace("bigserial", "integer").replace("timestamptz", "text").replace("jsonb", "text").replace("numeric", "real")
          .replace("default now()", "default current_timestamp").replace("create or replace view", "create view if not exists"))
c = sqlite3.connect("data/sixpts.db"); c.executescript(sql); c.commit(); print("local schema ok")
