"""Load .env for command-line engine scripts (the API does this itself via api/main.py).

Import this first in any module that reads os.environ:  from engine import env  # noqa: F401
Values already set in the shell always win over the file.
"""
import os, pathlib

def load(path: str | os.PathLike | None = None) -> None:
    f = pathlib.Path(path or (pathlib.Path(__file__).resolve().parent.parent / ".env"))
    if not f.exists(): return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.split(" #")[0].strip().strip('"').strip("'")
        if k and k not in os.environ and v: os.environ[k] = v

load()
