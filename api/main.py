"""sixpts API. Private routes need X-Token == SIXPTS_TOKEN and return everything.
Public routes read the *_public JSON / public_* views only — PropFinder fields never leave this process on a public route."""
import os, json, pathlib
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DATA = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))
TOKEN = os.environ.get("SIXPTS_TOKEN")
app = FastAPI(title="sixpts")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])

def _load(week: int, public: bool):
    f = DATA / (f"board_w{week}_public.json" if public else f"board_w{week}.json")
    if not f.exists(): raise HTTPException(404, f"no board for week {week}")
    return json.load(open(f))

@app.get("/api/board/{week}")
def public_board(week: int):
    return _load(week, public=True)

@app.get("/api/private/board/{week}")
def private_board(week: int, x_token: str = Header(default="")):
    if not TOKEN or x_token != TOKEN: raise HTTPException(401, "private route")
    return _load(week, public=False)

@app.get("/api/health")
def health(): return {"ok": True}
