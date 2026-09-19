"""sixpts API. Private routes need X-Token == SIXPTS_TOKEN and return everything.
Public routes read the *_public JSON / public_* views only — PropFinder fields never leave this process on a public route."""
import os, json, pathlib
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import Optional
def _db_url() -> str:
    u = os.environ.get("DATABASE_URL", "sqlite:///data/sixpts.db")
    # Supabase/Heroku hand out postgres:// or postgresql:// — pin the psycopg3 driver we actually install
    if u.startswith("postgres://"): u = "postgresql+psycopg://" + u[len("postgres://"):]
    elif u.startswith("postgresql://"): u = "postgresql+psycopg://" + u[len("postgresql://"):]
    return u

ENGINE = create_engine(_db_url(), pool_pre_ping=True)

DATA = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))
TOKEN = os.environ.get("SIXPTS_TOKEN")
app = FastAPI(title="sixpts")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])

def _load(week: int, public: bool):
    f = DATA / (f"board_w{week}_public.json" if public else f"board_w{week}.json")
    if not f.exists():
        import re
        ws = sorted({int(m.group(1)) for x in DATA.glob("board_w*_public.json") if (m := re.search(r"board_w(\d+)_public", x.name))}, reverse=True)
        raise HTTPException(404, detail={"message": f"Week {week} isn't published yet.", "latest": ws[0] if ws else None})
    return json.load(open(f))

@app.get("/api/weeks")
def weeks():
    """Weeks that have a published board, newest first, so the app can open on the latest."""
    import re
    ws = sorted({int(m.group(1)) for f in DATA.glob("board_w*_public.json") if (m := re.search(r"board_w(\d+)_public", f.name))}, reverse=True)
    return {"weeks": ws, "latest": ws[0] if ws else None}

@app.get("/api/board/{week}")
def public_board(week: int):
    return _load(week, public=True)

class Evaluate(BaseModel):
    week: int; prices: dict   # {"<gsis_id>|<game_id>": price}

@app.post("/api/evaluate")
def evaluate_public(body: Evaluate):
    """Public: the model reasons over prices the visitor typed. Nothing is stored."""
    board = {f"{r['gsis_id']}|{r['game_id']}": r for r in _load(body.week, public=True)["board"]}
    cards = [evaluate(board[k], int(v), "your book") for k, v in body.prices.items() if k in board and v]
    cards = rank(cards)
    return {"week": body.week, "priced": len(cards), "bets": [c for c in cards if c["tier"] == "Bet"], "leans": [c for c in cards if c["tier"] == "Lean"], "passes": [c for c in cards if c["tier"] == "Pass"], "unpriced_top": []}

@app.get("/api/private/board/{week}")
def private_board(week: int, x_token: str = Header(default="")):
    if not TOKEN or x_token != TOKEN: raise HTTPException(401, "private route")
    return _load(week, public=False)

import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.select_picks import evaluate, rank

class Odds(BaseModel):
    gsis_id: str; game_id: str; market: str = "anytime_td"; book: str = "book"; price: int; line: Optional[float] = None

@app.post("/api/odds")
def add_odds(o: Odds, x_token: str = Header(default=""), authorization: str = Header(default="")):
    """Store a price you saw (or a feed writes here). Latest per player/market/book wins. Signed-in users' prices feed the shared model picks too."""
    uid = _user(x_token, authorization)
    with ENGINE.begin() as c:
        c.execute(text("insert into odds(gsis_id, game_id, market, line, book, price, source, user_id) values (:g,:ga,:m,:l,:b,:p,'manual',:u)"),
                  {"g": o.gsis_id, "ga": o.game_id, "m": o.market, "l": o.line, "b": o.book, "p": o.price, "u": None if uid == "admin" else uid})
    return {"ok": True}

def _safe(fn, fallback):
    """A missing or not-yet-migrated table should degrade, not 500 the site."""
    try: return fn()
    except Exception as e:
        import logging; logging.getLogger("uvicorn.error").warning("db read failed (%s) — returning empty", e)
        return fallback

def _odds_rows(week: int, market: str, include_private: bool):
    q = ("select gsis_id, game_id, book, price, fetched_at from odds where market=:m and game_id like :w"
         + ("" if include_private else " and source <> 'pf'") + " order by fetched_at desc")
    def run():
        with ENGINE.begin() as c:
            return c.execute(text(q), {"m": market, "w": f"%_{week:02d}_%"}).mappings().all()
    return _safe(run, [])

@app.get("/api/odds/{week}")
def odds_for_week(week: int, market: str = "anytime_td", x_token: str = Header(default=""), authorization: str = Header(default="")):
    """Licensed odds (The Odds API) are public. PropFinder-sourced prices are added only for the admin/signed-in view."""
    private = False
    try: private = _user(x_token, authorization) is not None
    except HTTPException: private = False
    rows = _odds_rows(week, market, include_private=private)
    out: dict = {}
    for r in rows:
        k = f"{r['gsis_id']}|{r['game_id']}"
        out.setdefault(k, {})
        if r["book"] not in out[k]: out[k][r["book"]] = r["price"]
    return out

@app.get("/api/model-picks/{week}")
def model_picks(week: int, market: str = "anytime_td", x_token: str = Header(default=""), authorization: str = Header(default="")):
    """The model's picks with reasoning, over the best stored price per player.
    Public visitors get licensed odds; the admin view also sees PropFinder-sourced prices."""
    board = _load(week, public=True)["board"]
    private = False
    try: private = _user(x_token, authorization) is not None
    except HTTPException: private = False
    rows = _odds_rows(week, market, include_private=private)
    latest = {}
    for r in rows:                                   # latest per (player, book), then best price across books
        k = (r["gsis_id"], r["game_id"], r["book"])
        if k not in latest: latest[k] = r
    best = {}
    for (g, ga, b), r in latest.items():
        k = (g, ga)
        if k not in best or r["price"] > best[k]["price"]: best[k] = r
    cards = []
    for r in board:
        o = best.get((r["gsis_id"], r["game_id"]))
        cards.append(evaluate(r, o["price"] if o else None, o["book"] if o else ""))
    cards = rank(cards)
    n_priced = sum(1 for c in cards if c["tier"] != "No price")
    return {"week": week, "market": market, "priced": n_priced, "bets": [c for c in cards if c["tier"] == "Bet"], "leans": [c for c in cards if c["tier"] == "Lean"],
            "passes": [c for c in cards if c["tier"] == "Pass"], "unpriced_top": [c for c in cards if c["tier"] == "No price"][:15],
            "note": "The model only picks among players with a stored price. Enter prices on the board (they save when signed in) or connect an odds feed."}

class Pick(BaseModel):
    gsis_id: str; game_id: str; market: str = "anytime_td"; player: str = ""; team: str = ""; opp: str = ""
    line: Optional[float] = None; book: str = ""; price_taken: int; p_model: float; stake_units: float = 1.0; note: str = ""

import jwt  # PyJWT
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")
ADMIN_USER = os.environ.get("ADMIN_USER_ID")   # your Supabase user id -> house record + admin routes

def _user(x_token: str = "", authorization: str = "") -> str:
    """Returns a user id. Admin token -> 'admin'. Supabase JWT -> its sub. Else 401."""
    if TOKEN and x_token == TOKEN: return "admin"
    if SUPABASE_JWT_SECRET and authorization.lower().startswith("bearer "):
        try:
            claims = jwt.decode(authorization.split(" ", 1)[1], SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
            return claims["sub"]
        except Exception: raise HTTPException(401, "invalid session")
    raise HTTPException(401, "sign in to save")

def _auth(x_token: str):
    if not TOKEN or x_token != TOKEN: raise HTTPException(401, "private route")

@app.post("/api/picks")
def add_pick(p: Pick, x_token: str = Header(default=""), authorization: str = Header(default="")):
    uid = _user(x_token, authorization)
    with ENGINE.begin() as c:
        c.execute(text("""insert into picks(gsis_id, game_id, market, player, team, opp, line, book, price_taken, p_model, stake_units, note, user_id)
                          values (:gsis_id,:game_id,:market,:player,:team,:opp,:line,:book,:price_taken,:p_model,:stake_units,:note,:uid)"""), {**p.model_dump(), "uid": None if uid == "admin" else uid})
    return {"ok": True}

@app.get("/api/picks")
def list_picks(week: Optional[int] = None, x_token: str = Header(default=""), authorization: str = Header(default="")):
    uid = _user(x_token, authorization)
    q = "select * from picks where " + ("user_id is null" if uid == "admin" else "user_id = :u") + (" and game_id like :w" if week else "") + " order by placed_at desc"
    params = {"u": uid}
    if week: params["w"] = f"%_{week:02d}_%"
    with ENGINE.begin() as c:
        rows = c.execute(text(q), params).mappings().all()
    return [dict(r) for r in rows]

@app.delete("/api/picks/{pick_id}")
def del_pick(pick_id: int, x_token: str = Header(default="")):
    _auth(x_token)
    with ENGINE.begin() as c: c.execute(text("delete from picks where id=:i"), {"i": pick_id})
    return {"ok": True}

class Close(BaseModel):
    closing_price: int
@app.patch("/api/picks/{pick_id}/close")
def close_pick(pick_id: int, body: Close, x_token: str = Header(default="")):
    """Record the closing price (kickoff) so CLV can be computed by grade.py."""
    _auth(x_token)
    with ENGINE.begin() as c: c.execute(text("update picks set closing_price=:c where id=:i"), {"c": body.closing_price, "i": pick_id})
    return {"ok": True}

@app.get("/api/record")
def record(x_token: str = Header(default="")):
    _auth(x_token)
    with ENGINE.begin() as c:
        rows = c.execute(text("select market, count(*) n, sum(case when result='won' then 1 else 0 end) won, sum(case when result='lost' then 1 else 0 end) lost, "
                              "sum(coalesce(pnl_units,0)) pnl, avg(clv) clv from picks where result is not null group by market")).mappings().all()
    return [dict(r) for r in rows]

@app.get("/api/live/{week}")
def live(week: int):
    """Touchdowns as they happen, with what we said before the game. Written by engine.live_td."""
    f = DATA / f"live_td_w{week}.json"
    return {"week": week, "touchdowns": json.load(open(f)) if f.exists() else []}

@app.get("/api/scorecard/{week}")
def scorecard(week: int):
    """How the published board actually did that week. Public."""
    f = DATA / f"scorecard_w{week}.json"
    if not f.exists(): raise HTTPException(404, f"no scorecard for week {week}")
    return json.load(open(f))

@app.get("/api/scorecards")
def scorecards():
    import re
    ws = sorted({int(m.group(1)) for x in DATA.glob("scorecard_w*.json") if (m := re.search(r"scorecard_w(\d+)", x.name))})
    return {"weeks": ws}

@app.get("/api/record/public")
def record_public():
    """Graded record, no auth: the 'prove it' page."""
    def run():
        with ENGINE.begin() as c:
            rows = c.execute(text("select market, count(*) n, sum(case when result='won' then 1 else 0 end) won, sum(case when result='lost' then 1 else 0 end) lost, "
                                  "round(sum(coalesce(pnl_units,0)),2) pnl, round(avg(clv),4) clv, round(avg(p_model),3) avg_p from picks where result in ('won','lost') group by market")).mappings().all()
            recent = c.execute(text("select player, team, opp, market, line, price_taken, p_model, closing_price, result, pnl_units, clv, game_id from picks where result is not null order by placed_at desc limit 50")).mappings().all()
        return {"summary": [dict(r) for r in rows], "recent": [dict(r) for r in recent]}
    return _safe(run, {"summary": [], "recent": []})

@app.get("/api/teams/{week}")
def teams(week: int):
    f = DATA / f"teams_w{week}.json"
    if not f.exists(): raise HTTPException(404, f"no team profiles for week {week}")
    return json.load(open(f))

@app.get("/api/health")
def health():
    """Reports what the API can actually see — the fastest way to diagnose a deploy."""
    from sqlalchemy import inspect
    try:
        tables = sorted(inspect(ENGINE).get_table_names()); db_ok = True
    except Exception as e:
        tables, db_ok = [str(e)[:120]], False
    need = ["odds", "picks", "scores"]
    import re
    weeks = sorted({int(m.group(1)) for f in DATA.glob("board_w*_public.json") if (m := re.search(r"board_w(\d+)_public", f.name))}, reverse=True)
    counts = {}
    if db_ok and "odds" in tables:
        def run():
            with ENGINE.begin() as c:
                return {r[0]: r[1] for r in c.execute(text("select source, count(*) from odds group by 1")).all()}
        counts = _safe(run, {})
    return {"ok": db_ok and all(t in tables for t in need), "database": "connected" if db_ok else "error",
            "tables_present": tables, "missing_tables": [t for t in need if t not in tables],
            "odds_rows_by_source": counts, "boards_published": weeks, "admin_token_set": bool(TOKEN)}
