"""Render a simulated game's play log to an mp4 you can post.

    python3 -m engine.replay --week 8                       # every game, 9x16 and 16x9
    python3 -m engine.replay --week 8 --game 2026_08_KC_DEN --format 9x16 --target 75

Reads data/sim_w{week}.json (engine.sim_week) and writes data/replays/w{week}/{game}_{9x16|16x9}.mp4. Skia draws
every frame; ffmpeg encodes them from a pipe. The look is the sixpts scoreboard: ink background, surface panels,
field-paint white, one amber. 9x16 is a broadcast-style camera that follows the ball down a vertical field;
16x9 shows the whole field. ~1 minute of render per minute of video on a laptop core.

Choreography is deterministic (seeded by the play index) so a re-render of the same week is identical.
"""
from __future__ import annotations
import argparse, json, math, os, pathlib, subprocess, sys, time
import numpy as np
try:
    import skia
except ImportError:  # pragma: no cover
    sys.exit("pip install skia-python")

D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))
FONTS = pathlib.Path(__file__).resolve().parent.parent / "render" / "fonts"

# ---- sixpts tokens (web/src/tokens.css) ------------------------------------------------------------------------
INK, SURFACE, RAISE, RAISE2, LINE, LINE_S = "#070A0F", "#0D131C", "#131C28", "#1A2431", "#1E2836", "#2B3949"
PAINT, DIM, FAINT = "#EEF2F6", "#8794A5", "#7A8799"
BULB, BULB_HI, GAIN, LOSS = "#FFB020", "#FFC247", "#35C77F", "#F2555A"
TURF = "#10201A"          # the field: a dark, nearly-ink green so the paint and the dots carry the picture

TEAMS = {  # primary, secondary, nickname
    "ARI": ("97233F", "FFB612", "Cardinals"), "ATL": ("A71930", "A5ACAF", "Falcons"), "BAL": ("241773", "9E7C0C", "Ravens"),
    "BUF": ("00338D", "C60C30", "Bills"), "CAR": ("0085CA", "BFC0BF", "Panthers"), "CHI": ("0B162A", "C83803", "Bears"),
    "CIN": ("FB4F14", "FFFFFF", "Bengals"), "CLE": ("FF3C00", "311D00", "Browns"), "DAL": ("003594", "869397", "Cowboys"),
    "DEN": ("FB4F14", "002244", "Broncos"), "DET": ("0076B6", "B0B7BC", "Lions"), "GB": ("203731", "FFB612", "Packers"),
    "HOU": ("03202F", "A71930", "Texans"), "IND": ("002C5F", "A2AAAD", "Colts"), "JAX": ("006778", "D7A22A", "Jaguars"),
    "KC": ("E31837", "FFB81C", "Chiefs"), "LV": ("A5ACAF", "000000", "Raiders"), "LAC": ("0080C6", "FFC20E", "Chargers"),
    "LA": ("003594", "FFA300", "Rams"), "MIA": ("008E97", "FC4C02", "Dolphins"), "MIN": ("4F2683", "FFC62F", "Vikings"),
    "NE": ("002244", "C60C30", "Patriots"), "NO": ("D3BC8D", "101820", "Saints"), "NYG": ("0B2265", "A71930", "Giants"),
    "NYJ": ("125740", "FFFFFF", "Jets"), "PHI": ("004C54", "A5ACAF", "Eagles"), "PIT": ("FFB612", "101820", "Steelers"),
    "SF": ("AA0000", "B3995D", "49ers"), "SEA": ("69BE28", "002244", "Seahawks"), "TB": ("D50A0A", "FF7900", "Buccaneers"),
    "TEN": ("4B92DB", "0C2340", "Titans"), "WAS": ("5A1414", "FFB612", "Commanders"),
}

def _rgb(h): h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
def _lum(h): r, g, b = [c / 255 for c in _rgb(h)]; return 0.2126 * r + 0.7152 * g + 0.0722 * b
def col(h, a=1.0): r, g, b = _rgb(h); return skia.ColorSetARGB(int(round(a * 255)), r, g, b)

SILVER = "C9D1DB"
def _dist(a, b): return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5
def team_colors(home, away):
    """One dot colour per team: bright enough on dark turf, far from the other team's, and far from the brand amber
    (which marks the ball carrier). Silver stands in when a team's own colours can't manage that."""
    def cands(t): p, s, _ = TEAMS.get(t, ("8794A5", "EEF2F6", t)); return [c for c in (p, s) if _lum(c) > 0.16] + [SILVER]
    best, score = None, -1
    for h in cands(home):
        for a in cands(away):
            d = min(_dist(h, a), _dist(h, BULB), _dist(a, BULB)) - (40 if SILVER in (h, a) else 0) - (80 if h == a else 0)
            if d > score: best, score = (h, a), d
    return best

# ---- timing ---------------------------------------------------------------------------------------------------
SETUP = 0.28  # seconds the players take to line up before every snap
ACTION = {"complete": 0.80, "incomplete": 0.60, "run": 0.72, "sack": 0.70, "int": 1.10, "fumble": 1.05, "punt": 0.85,
          "field goal": 0.95, "kneel": 0.35}
HOLD_TD, HOLD_SCORE, HOLD_TURNOVER = 1.5, 0.8, 0.6
INTRO, OUTRO, QUARTER = 2.6, 5.0, 1.3

def ease(t): t = min(max(t, 0.0), 1.0); return t * t * (3 - 2 * t)
def ease_out(t): t = min(max(t, 0.0), 1.0); return 1 - (1 - t) ** 3
def lerp(a, b, t): return a + (b - a) * t
def clock_str(s): s = max(int(s), 0); return f"{s // 60}:{s % 60:02d}"
def ordinal(n): return f"{n}{'tsnrhtdd'[(n // 10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"
def short(name):
    if not name: return ""
    parts = name.split(); return name if len(parts) < 2 else f"{parts[0][0]}. {' '.join(parts[1:])}"

# ---- the picture ----------------------------------------------------------------------------------------------
class Layout:
    """Field coordinates: x along the length in yards 0..120 (end zones 0-10 and 110-120), y across 0..53.3.
    The home team drives +x. Screen mapping depends on the format; 9x16 adds a camera that pans along x."""
    FW = 53.3
    def __init__(self, fmt):
        self.fmt = fmt
        if fmt == "9x16":
            self.W, self.H = 1080, 1920; self.vertical = True
            self.top, self.bottom = 330, 1920 - 300            # scoreboard above, caption below
            self.margin = 36
            self.s = (self.W - 2 * self.margin) / self.FW        # px per yard, uniform
            self.span = (self.bottom - self.top) / self.s        # yards visible along the field
        else:
            self.W, self.H = 1920, 1080; self.vertical = False
            self.top, self.bottom = 150, 1080 - 150
            self.s = min((self.W - 2 * 60) / 120, (self.bottom - self.top) / self.FW)
            self.span = 120
            self.margin = (self.W - 120 * self.s) / 2
        self.cam = 60.0
    def to_px(self, x, y):
        if self.vertical:
            cy = (self.top + self.bottom) / 2
            return self.margin + y * self.s, cy - (x - self.cam) * self.s
        fy = (self.top + self.bottom) / 2
        return self.margin + (x - 60 + self.span / 2) * self.s, fy - (y - self.FW / 2) * self.s
    def clamp_cam(self, x):
        half = self.span / 2
        return min(max(x, half), 120 - half) if self.span < 120 else 60.0

class Fonts:
    def __init__(self):
        tf = lambda f: skia.Typeface.MakeFromFile(str(FONTS / f))
        self.display = tf("InterDisplay-ExtraBold.otf"); self.bold = tf("Inter-Bold.otf"); self.semi = tf("Inter-SemiBold.otf"); self.med = tf("Inter-Medium.otf")
    def f(self, face, size):
        fo = skia.Font(face, size); fo.setSubpixel(True); fo.setEdging(skia.Font.Edging.kSubpixelAntiAlias); return fo

def paint(color, style="fill", width=1.0, blur=0.0):
    p = skia.Paint(AntiAlias=True, Color=color)
    if style == "stroke": p.setStyle(skia.Paint.kStroke_Style); p.setStrokeWidth(width); p.setStrokeCap(skia.Paint.kRound_Cap)
    if blur: p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    return p

def text(c, s, x, y, font, color, align="left", tracking=0.0):
    p = paint(color)
    w = font.measureText(s)
    if align == "center": x -= w / 2
    elif align == "right": x -= w
    c.drawString(s, x, y, font, p)
    return w

# ---- choreography ---------------------------------------------------------------------------------------------
OFF_SLOTS = ["QB", "RB", "TE", "WR1", "WR2", "WR3", "OL1", "OL2", "OL3", "OL4", "OL5"]
DEF_SLOTS = ["DL1", "DL2", "DL3", "DL4", "LB1", "LB2", "LB3", "CB1", "CB2", "S1", "S2"]

def formation(los, d, rng):
    """Formation around the line of scrimmage, offence moving in direction d. Returns {slot: (x, y)}."""
    c = Layout.FW / 2; j = lambda s=0.6: rng.normal(0, s)
    shotgun = rng.random() < 0.55
    off = {"QB": (los - (5.0 if shotgun else 1.4) * d, c + j(0.3)), "RB": (los - (5.5 if shotgun else 7.0) * d, c + (1.5 if shotgun else 0.5) + j()),
           "TE": (los - 0.6 * d, c + 6.5 + j()), "WR1": (los - 0.8 * d, c - 21 + j(1.2)), "WR2": (los - 0.8 * d, c + 22 + j(1.2)), "WR3": (los - 1.6 * d, c - 11 + j(1.2))}
    for i in range(5): off[f"OL{i + 1}"] = (los - 0.7 * d, c - 4.8 + i * 2.4)
    de = {"DL1": (los + 1.2 * d, c - 5.5), "DL2": (los + 1.2 * d, c - 1.8), "DL3": (los + 1.2 * d, c + 1.8), "DL4": (los + 1.2 * d, c + 5.5),
          "LB1": (los + 4.5 * d, c - 6 + j()), "LB2": (los + 4.8 * d, c + j()), "LB3": (los + 4.5 * d, c + 6 + j()),
          "CB1": (los + 6.5 * d, c - 20 + j()), "CB2": (los + 6.5 * d, c + 21 + j()), "S1": (los + 12 * d, c - 8 + j(1.5)), "S2": (los + 12 * d, c + 8 + j(1.5))}
    return off, de

def choreograph(play, nxt, home, rng):
    """Positions as functions of action time t in [0,1] for every player plus the ball. Everything returned
    is a dict of slot -> callable(t) -> (x, y, h) where h is ball height (0 on the ground) for the ball only."""
    off_team = play["off"]; d = 1 if off_team == home else -1
    los = 10 + play["from_yl"] if d == 1 else 110 - play["from_yl"]
    end = los + play["gain"] * d
    typ, pos, air = play["type"], play["pos"] or "", float(play["air"] or 0)
    off, de = formation(los, d, rng)
    c = Layout.FW / 2
    # who carries: the sim names the target / rusher; map it to a slot
    slot = {"RB": "RB", "FB": "RB", "TE": "TE", "QB": "QB"}.get(pos) or ("WR1", "WR2", "WR3")[hash(play["player"] or "") % 3]
    moves = {}
    def path(points):  # points: [(t, x, y)] -> callable, smoothstep between keyframes
        def f(t):
            for (t0, x0, y0), (t1, x1, y1) in zip(points, points[1:]):
                if t <= t1:
                    u = ease((t - t0) / (t1 - t0)) if t1 > t0 else 1
                    return lerp(x0, x1, u), lerp(y0, y1, u)
            return points[-1][1], points[-1][2]
        return f
    def weave(f, amp, k):
        return lambda t: (f(t)[0], f(t)[1] + amp * math.sin(k * t * math.pi))
    qb = off["QB"]; drop = (qb[0] - 2.5 * d, qb[1])
    catch_y = off[slot][1] + (rng.normal(0, 3) if slot[:2] == "WR" else rng.normal(0, 4))
    catch_y = min(max(catch_y, 2), Layout.FW - 2)
    end_y = min(max(lerp(catch_y, c, 0.35) + rng.normal(0, 4), 1.5), Layout.FW - 1.5)
    ball = None
    if typ in ("complete", "incomplete", "int"):
        catch_x = los + air * d
        t_throw, t_catch = 0.32, 0.56
        moves["QB"] = path([(0, *qb), (0.3, *drop), (1, *drop)])
        if typ == "complete":
            moves[slot] = path([(0, *off[slot]), (t_catch, catch_x, catch_y), (0.95, end, end_y), (1, end, end_y)])
            ball = lambda t: (*moves["QB"](t)[:2], 0) if t < t_throw else (lerp(drop[0], catch_x, (t - t_throw) / (t_catch - t_throw)), lerp(drop[1], catch_y, (t - t_throw) / (t_catch - t_throw)), math.sin(math.pi * (t - t_throw) / (t_catch - t_throw)) * (0.4 + air / 25)) if t < t_catch else (*moves[slot](t), 0)
            carrier = slot
        elif typ == "incomplete":
            miss_x, miss_y = catch_x + 1.5 * d, catch_y + (3 if rng.random() < 0.5 else -3)
            moves[slot] = path([(0, *off[slot]), (t_catch, catch_x, catch_y), (0.8, catch_x + 1.2 * d, catch_y), (1, catch_x + 1.2 * d, catch_y)])
            ball = lambda t: (*moves["QB"](t)[:2], 0) if t < t_throw else (lerp(drop[0], miss_x, (t - t_throw) / (t_catch + 0.08 - t_throw)), lerp(drop[1], miss_y, (t - t_throw) / (t_catch + 0.08 - t_throw)), max(0, math.sin(math.pi * (t - t_throw) / (t_catch + 0.1 - t_throw))) * (0.4 + air / 25)) if t < t_catch + 0.08 else (miss_x, miss_y, 0)
            carrier = None
        else:  # interception: a defender beats the receiver to the ball and returns it to `end`
            picker = min(("CB1", "CB2", "S1", "S2"), key=lambda k: abs(de[k][1] - catch_y))
            moves[slot] = path([(0, *off[slot]), (t_catch, catch_x - 1.5 * d, catch_y + 1.5), (1, catch_x - 2 * d, catch_y + 2)])
            moves[picker] = path([(0, *de[picker]), (t_catch, catch_x, catch_y), (0.95, end, end_y), (1, end, end_y)])
            ball = lambda t: (*moves["QB"](t)[:2], 0) if t < t_throw else (lerp(drop[0], catch_x, (t - t_throw) / (t_catch - t_throw)), lerp(drop[1], catch_y, (t - t_throw) / (t_catch - t_throw)), math.sin(math.pi * (t - t_throw) / (t_catch - t_throw)) * (0.4 + air / 25)) if t < t_catch else (*moves[picker](t), 0)
            carrier = picker
        # the other receivers run routes
        for s in ("WR1", "WR2", "WR3", "TE", "RB"):
            if s not in moves:
                x0, y0 = off[s]; depth = rng.uniform(6, 18) if s != "RB" else rng.uniform(2, 6)
                moves[s] = path([(0, x0, y0), (0.6, x0 + depth * d, min(max(y0 + rng.normal(0, 5), 2), Layout.FW - 2)), (1, x0 + (depth + 3) * d, y0)])
    elif typ in ("run", "fumble", "kneel"):
        t_hand = 0.18
        moves["QB"] = path([(0, *qb), (t_hand, qb[0] - 1.0 * d, qb[1]), (1, qb[0] - 1.5 * d, qb[1] + 1)]) if typ != "kneel" else path([(0, *qb), (0.5, qb[0] - 1.2 * d, qb[1]), (1, qb[0] - 1.2 * d, qb[1])])
        if typ == "kneel":
            ball = lambda t: (*moves["QB"](t), 0); carrier = "QB"
        else:
            rs = "QB" if slot == "QB" else slot if slot in ("RB", "TE") else slot
            x0, y0 = off[rs]; hole_y = c + rng.choice([-7, -3, 0, 3, 7]) + rng.normal(0, 1.5)
            mid = (lerp(los, end, 0.45), lerp(hole_y, end_y, 0.3))
            pts = [(0, x0, y0), (t_hand, qb[0] - 0.5 * d, lerp(y0, hole_y, 0.3)), (0.5, *mid), (0.93, end, end_y), (1, end, end_y)] if rs != "QB" else [(0, x0, y0), (0.5, *mid), (0.93, end, end_y), (1, end, end_y)]
            moves[rs] = weave(path(pts), 0.8, 5)
            ball = (lambda t: (*moves["QB"](t), 0) if t < t_hand else (*moves[rs](t), 0)) if rs != "QB" else (lambda t: (*moves[rs](t), 0))
            carrier = rs
            if typ == "fumble":  # the ball pops loose at the end and a defender falls on it
                scooper = min(("LB1", "LB2", "LB3", "S1", "S2"), key=lambda k: abs(de[k][1] - end_y))
                moves[scooper] = path([(0, *de[scooper]), (0.9, end + 1.5 * d, end_y + 1), (1, end + 1.5 * d, end_y + 1)])
                base_ball = ball
                ball = lambda t: base_ball(t) if t < 0.85 else (end + 1.5 * d * ease((t - 0.85) / 0.1), end_y + ease((t - 0.85) / 0.1), 0.15 * math.sin(math.pi * min(1, (t - 0.85) / 0.1)))
        for s in ("WR1", "WR2", "WR3", "TE"):
            if s not in moves:
                x0, y0 = off[s]; moves[s] = path([(0, x0, y0), (1, x0 + rng.uniform(3, 8) * d, y0 + rng.normal(0, 2))])
    elif typ == "sack":
        rusher = rng.choice(["DL1", "DL4", "LB1", "LB3"])
        moves["QB"] = path([(0, *qb), (0.35, qb[0] - 3 * d, qb[1] + rng.normal(0, 1)), (0.8, end, qb[1] + rng.normal(0, 1.5)), (1, end, qb[1])])
        moves[rusher] = path([(0, *de[rusher]), (0.8, end + 0.8 * d, qb[1] + 0.5), (1, end + 0.8 * d, qb[1] + 0.5)])
        ball = lambda t: (*moves["QB"](t), 0); carrier = "QB"
        for s in ("WR1", "WR2", "WR3", "TE"):
            x0, y0 = off[s]; moves[s] = path([(0, x0, y0), (1, x0 + rng.uniform(8, 16) * d, y0 + rng.normal(0, 3))])
    elif typ in ("punt", "field goal"):
        kick_x = los - (13 if typ == "punt" else 7) * d
        if typ == "punt":
            if nxt and nxt["off"] and nxt["off"] != off_team: land = (10 + nxt["from_yl"]) if nxt["off"] == home else (110 - nxt["from_yl"])
            else: land = min(max(los + 42 * d, 11), 109)
            land_y = c + rng.normal(0, 8); good = True
        else:
            land = 120 if d == 1 else 0; good = play["result"] == "FG good"
            land_y = c + (rng.normal(0, 1.5) if good else rng.choice([-1, 1]) * rng.uniform(5, 9))
        moves["QB"] = path([(0, kick_x - 0.5 * d, c), (0.25, kick_x + 0.5 * d, c), (1, kick_x + 1.0 * d, c)])  # the kicker/punter
        t_kick, t_land = 0.25, 0.9 if typ == "punt" else 0.8
        ball = lambda t: (kick_x, c, 0) if t < t_kick else (lerp(kick_x, land, ease_out((t - t_kick) / (t_land - t_kick)) ** 0.9), lerp(c, land_y, (t - t_kick) / (t_land - t_kick)), math.sin(math.pi * min(1, (t - t_kick) / (t_land - t_kick))) * (2.0 if typ == "punt" else 1.4)) if t < t_land else (land, land_y, 0)
        carrier = None
        if typ == "punt":
            ret = "S1" if land_y < c else "S2"
            moves[ret] = path([(0, land - 3 * d, c + rng.normal(0, 3)), (t_land, land, land_y), (1, land - 2 * d, land_y)])
            de = dict(de); de[ret] = (land - 3 * d, c)
            for s in OFF_SLOTS:
                if s != "QB": x0, y0 = off[s]; moves[s] = path([(0, x0, y0), (1, land - 6 * d + rng.normal(0, 3), lerp(y0, land_y, 0.6))])
    else:
        ball = lambda t: (los, c, 0); carrier = None
    # defenders converge on the ball carrier (with lag), linemen hold the line
    for s in DEF_SLOTS:
        if s in moves: continue
        x0, y0 = de[s]; lag = rng.uniform(0.15, 0.4); gap = rng.uniform(0.8, 2.5)
        if typ in ("punt", "field goal"):
            moves[s] = path([(0, x0, y0), (1, x0 + rng.uniform(-2, 1) * d, y0)]); continue
        def chase(t, x0=x0, y0=y0, lag=lag, gap=gap, s=s):
            if ball is None or carrier is None: return x0 + (1.5 * d if s.startswith("DL") else 0) * ease(t), y0
            bx, by, _ = ball(max(0, t - lag)); u = ease(t / (1 - lag * 0.5))
            return lerp(x0, bx + gap * d, u), lerp(y0, by + rng.normal(0, 0.1) * 0, u)
        moves[s] = chase
    for s in OFF_SLOTS:
        if s in moves: continue
        x0, y0 = off[s]; moves[s] = path([(0, x0, y0), (1, x0 + (1.0 if typ not in ("sack",) else -1.5) * d, y0)])
    return dict(off=off, de=de, moves=moves, ball=ball, carrier=carrier, d=d, los=los, end=end, slot=slot)

# ---- frames ---------------------------------------------------------------------------------------------------
class Renderer:
    def __init__(self, game, gid, fmt, fps, target):
        self.g, self.gid, self.L, self.fps = game, gid, Layout(fmt), fps
        self.F = Fonts(); self.home, self.away = game["home"], game["away"]
        self.ch, self.ca = team_colors(self.home, self.away)
        self.plays = [p for p in game["log"]["plays"] if p["type"] != "final"]
        self.final = game["log"]["plays"][-1]
        qbs = [p for p in game["players"] if p["position"] == "QB"]
        self.qb = {t: max([p for p in qbs if p["team"] == t], key=lambda p: p.get("car", 0), default={"player": "QB"})["player"] for t in (self.home, self.away)}
        season, wk = gid.split("_")[:2]
        self.title = f"{self.away} @ {self.home}"; self.sub = f"Week {int(wk)} · {TEAMS.get(self.away, ('', '', self.away))[2]} at {TEAMS.get(self.home, ('', '', self.home))[2]}"
        # timeline: list of (kind, payload, duration)
        nominal = sum(SETUP + ACTION.get(p["type"], 0.7) + (HOLD_TD if p["result"] == "TD" else HOLD_SCORE if p["result"] == "FG good" else HOLD_TURNOVER if p["result"] in ("turnover", "FG miss") else 0) for p in self.plays)
        self.speed = min(1.8, max(1.0, nominal / target)) if target else 1.0
        self.trail = []; self.last_pos = {}; self.score_flash = {"home": -9, "away": -9}
    # -- drawing primitives
    def field(self, c, t_abs):
        L, F = self.L, self.F
        c.save(); clip = skia.Rect.MakeLTRB(0, L.top, L.W, L.bottom) if L.vertical else skia.Rect.MakeLTRB(L.margin - 6, L.top - 6, L.W - L.margin + 6, L.bottom + 6)
        c.clipRect(clip)
        x0, y0 = L.to_px(0, 0); x1, y1 = L.to_px(120, L.FW)
        c.drawRect(skia.Rect.MakeLTRB(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), paint(col(TURF)))
        # mowing stripes
        for i in range(10, 110, 10):
            if (i // 10) % 2: a, b = L.to_px(i, 0), L.to_px(i + 10, L.FW); c.drawRect(skia.Rect.MakeLTRB(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), paint(col("#FFFFFF", 0.022)))
        # end zones in team colour
        for xa, xb, team in ((0, 10, self.home), (110, 120, self.away)):
            colr = TEAMS.get(team, ("1E2836",))[0]
            a, b = L.to_px(xa, 0), L.to_px(xb, L.FW); r = skia.Rect.MakeLTRB(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
            c.drawRect(r, paint(col(colr, 0.26)))
            font = F.f(F.display, 5.6 * L.s)
            c.save(); cx, cy = r.centerX(), r.centerY(); c.translate(cx, cy)
            if not L.vertical: c.rotate(90 if xa == 0 else -90)
            text(c, TEAMS.get(team, ("", "", team))[2].upper(), 0, font.getSize() * 0.36, font, col("#FFFFFF", 0.5), "center"); c.restore()
        # yard lines, hashes and numbers
        wp = paint(col("#FFFFFF", 0.42), "stroke", max(1.5, 0.12 * L.s)); hp = paint(col("#FFFFFF", 0.30), "stroke", max(1.2, 0.1 * L.s))
        for yd in range(10, 111, 5):
            a, b = L.to_px(yd, 0), L.to_px(yd, L.FW); c.drawLine(a[0], a[1], b[0], b[1], wp)
        for yd in range(11, 110):
            for hy in (0.6, 23.6, 29.0, 52.7):
                a = L.to_px(yd, hy); b = L.to_px(yd, hy + 0.9 if hy < 26 else hy - 0.9)
                c.drawLine(a[0], a[1], b[0], b[1], hp)
        nf = F.f(F.display, 2.6 * L.s)
        for yd in range(20, 101, 10):
            n = yd - 10 if yd <= 60 else 110 - yd
            for hy, flip in ((12, False), (41.3, True)):
                px, py = L.to_px(yd, hy); c.save(); c.translate(px, py)
                c.rotate((-90 if not flip else 90) if L.vertical else (180 if flip else 0))
                text(c, f"{n}", 0, nf.getSize() * 0.36, nf, col("#FFFFFF", 0.34), "center"); c.restore()
        # goal lines and uprights
        gp = paint(col("#FFFFFF", 0.75), "stroke", max(2, 0.18 * L.s))
        for yd in (10, 110): a, b = L.to_px(yd, 0), L.to_px(yd, L.FW); c.drawLine(a[0], a[1], b[0], b[1], gp)
        up = paint(col(BULB, 0.9), "stroke", max(2, 0.22 * L.s))
        for yd in (0, 120):
            a, b = L.to_px(yd, L.FW / 2 - 3.1), L.to_px(yd, L.FW / 2 + 3.1); c.drawLine(a[0], a[1], b[0], b[1], up)
        c.restore()
    def dot(self, c, x, y, colr, r, ring=None, alpha=1.0, h=0.0):
        px, py = self.L.to_px(x, y)
        if h: c.drawCircle(px, py, r * 0.9, paint(col(INK, 0.35 * alpha), blur=r * 0.5)); py -= h * self.L.s * 1.6
        c.drawCircle(px, py + r * 0.25, r, paint(col(INK, 0.45 * alpha), blur=r * 0.45))
        c.drawCircle(px, py, r, paint(col(colr, alpha)))
        c.drawCircle(px, py, r, paint(col("#FFFFFF", 0.55 * alpha), "stroke", max(1.2, r * 0.14)))
        if ring: c.drawCircle(px, py, r * 1.9, paint(col(ring, 0.95), "stroke", max(2, r * 0.22)))
        return px, py
    def scoreboard(self, c, play, score, t_abs, phase_t, situation):
        L, F = self.L, self.F
        sh, sa = score
        if L.vertical:
            pad = 36; top = 54; panel = skia.Rect.MakeXYWH(pad, top, L.W - 2 * pad, 222)
        else:
            pad = 60; top = 22; panel = skia.Rect.MakeXYWH(pad, top, L.W - 2 * pad, 104)
        c.drawRoundRect(panel, 14, 14, paint(col(SURFACE))); c.drawRoundRect(panel, 14, 14, paint(col(LINE), "stroke", 2))
        big = F.f(F.display, 112 if L.vertical else 64); abbr = F.f(F.display, 44 if L.vertical else 30); small = F.f(F.semi, 26 if L.vertical else 20); tiny = F.f(F.med, 22 if L.vertical else 18)
        cy = panel.centerY()
        off = play["off"] if play else None
        for side, team, s, colr, key in (("L", self.away, sa, self.ca, "away"), ("R", self.home, sh, self.ch, "home")):
            x = panel.left() + (170 if L.vertical else 150) if side == "L" else panel.right() - (170 if L.vertical else 150)
            text(c, team, x, cy - (62 if L.vertical else 26), abbr, col(PAINT), "center")
            flash = t_abs - self.score_flash[key]; scale = 1 + 0.18 * max(0.0, 1 - flash / 0.9) if flash < 0.9 else 1
            c.save(); c.translate(x, cy + (48 if L.vertical else 28)); c.scale(scale, scale)
            text(c, f"{s}", 0, 0, big, col(BULB_HI if flash < 0.9 else PAINT), "center"); c.restore()
            if off == team: c.drawCircle(x + (-100 if side == "L" else 100) * (1 if L.vertical else 0.75), cy + (36 if L.vertical else 20), 7 if L.vertical else 5, paint(col(BULB)))
            c.drawRoundRect(skia.Rect.MakeXYWH(x - (46 if L.vertical else 32), panel.bottom() - (14 if L.vertical else 10), 92 if L.vertical else 64, 5 if L.vertical else 4), 2, 2, paint(col(colr)))
        if play:
            q = play["q"]; qs = "OT" if q > 4 else f"{ordinal(q)} QTR"
            text(c, qs, panel.centerX(), cy - (34 if L.vertical else 14), small, col(DIM), "center")
            text(c, clock_str(play["clock"]), panel.centerX(), cy + (18 if L.vertical else 18), F.f(F.display, 56 if L.vertical else 36), col(PAINT), "center")
            text(c, situation, panel.centerX(), cy + (64 if L.vertical else 44), tiny, col(BULB), "center")
        else:
            text(c, "FINAL", panel.centerX(), cy + 10, F.f(F.display, 56 if L.vertical else 36), col(BULB), "center")
        # wordmark
        wm = F.f(F.display, 28 if L.vertical else 22)
        text(c, "sixpts", L.W - pad, (L.H - 28) if L.vertical else (L.H - 20), wm, col(BULB, 0.9), "right")
        text(c, "5,000 simulated games · the median one · not a prediction", pad if L.vertical else L.W - pad - 110, (L.H - 28) if L.vertical else (L.H - 20), F.f(F.med, 20 if L.vertical else 16), col(FAINT), "left" if L.vertical else "right")
    def caption(self, c, line1, line2, colr=PAINT):
        L, F = self.L, self.F
        if L.vertical:
            y = L.bottom + 74; f1 = F.f(F.display, 44); f2 = F.f(F.med, 26)
            # wrap line1 at the panel width
            words, lines, cur = line1.split(), [], ""
            for w in words:
                trial = (cur + " " + w).strip()
                if f1.measureText(trial) > L.W - 2 * L.margin - 20 and cur: lines.append(cur); cur = w
                else: cur = trial
            lines.append(cur)
            for i, ln in enumerate(lines[:2]): text(c, ln, L.margin + 10, y + i * 52, f1, col(colr))
            text(c, line2, L.margin + 10, y + 52 * min(len(lines), 2) + 16, f2, col(DIM))
        else:
            y = L.bottom + 50; text(c, line1, L.margin + 6, y, F.f(F.display, 36), col(colr)); text(c, line2, L.margin + 6, y + 34, F.f(F.med, 20), col(DIM))
    def describe(self, p):
        """Caption for a play: what happened, in the sixpts voice."""
        who = short(p["player"]); g = p["gain"]; t = p["type"]; r = p["result"]; qb = short(self.qb.get(p["off"], "QB"))
        yds = lambda n: "no gain" if n == 0 else f"{n} yds" if n > 0 else f"a loss of {-n}"
        if t == "complete": s = f"{qb} → {who} for {yds(g)}" if r != "TD" else f"{qb} → {who} · TOUCHDOWN"
        elif t == "incomplete": s = f"Incomplete, intended for {who}"
        elif t == "run": s = f"{who} runs for {yds(g)}" if r != "TD" else f"{who} runs it in · TOUCHDOWN"
        elif t == "sack": s = f"{qb} sacked for {yds(g)}"
        elif t == "int": s = f"INTERCEPTED · {qb} picked off, returned {-g} yds" if g < 0 else f"INTERCEPTED · {qb} picked off"
        elif t == "fumble": s = f"{who} FUMBLES · {p['off']} turns it over"
        elif t == "punt": s = f"{p['off']} punts"
        elif t == "field goal": s = f"{p['fg_dist']}-yd field goal is {'GOOD' if r == 'FG good' else 'NO GOOD'}"
        elif t == "kneel": s = f"{qb} kneels"
        else: s = t
        if r == "downs": s += " · turnover on downs"
        colr = BULB if r in ("TD", "FG good") else LOSS if r in ("turnover", "FG miss", "downs") else PAINT
        return s, colr
    def situation(self, p):
        yl = p["from_yl"]; side = p["off"] if yl <= 50 else (self.home if p["off"] == self.away else self.away); mark = yl if yl <= 50 else 100 - yl
        return f"{ordinal(p['down'])} & {'Goal' if p['dist'] >= 100 - yl else p['dist']} · {side} {mark}" if p["down"] else ""
    def card(self, c, lines, alpha):
        L, F = self.L, self.F
        c.drawRect(skia.Rect.MakeLTRB(0, 0, L.W, L.H), paint(col(INK, 0.72 * alpha)))
        cy = L.H / 2 - 40 * len(lines) / 2
        for i, (s, size, colr, face) in enumerate(lines):
            text(c, s, L.W / 2, cy + i * (size * 1.25 + 22), F.f(getattr(F, face), size), col(colr, alpha), "center"); cy += size * 0.4
    # -- frames
    def frames(self):
        L = self.L; fps = self.fps; t_abs = 0.0
        yield from self.intro()
        score = (0, 0); prev_q = 1
        self.L.cam = L.clamp_cam(10 + self.plays[0]["from_yl"] if self.plays[0]["off"] == self.home else 110 - self.plays[0]["from_yl"]) if self.plays else 60
        t_abs = INTRO
        for i, p in enumerate(self.plays):
            nxt = self.plays[i + 1] if i + 1 < len(self.plays) else None
            if p["q"] != prev_q:
                yield from self.quarter_card(prev_q, score, t_abs); t_abs += QUARTER; prev_q = p["q"]
            rng = np.random.default_rng(hash((self.gid, i)) & 0xFFFFFFFF)
            ch = choreograph(p, nxt, self.home, rng)
            new_score = (p["score_home"], p["score_away"])
            setup = SETUP / self.speed; action = ACTION.get(p["type"], 0.7) / self.speed
            hold = (HOLD_TD if p["result"] == "TD" else HOLD_SCORE if p["result"] == "FG good" else HOLD_TURNOVER if p["result"] in ("turnover", "FG miss", "downs") else 0.05) / max(1.0, self.speed ** 0.5)
            n_setup, n_action, n_hold = max(2, int(setup * fps)), max(4, int(action * fps)), int(hold * fps)
            start = {s: self.last_pos.get(s, (ch["off"] | ch["de"])[s]) for s in OFF_SLOTS + DEF_SLOTS}
            form = ch["off"] | ch["de"]
            line1, colr = self.describe(p); sit = self.situation(p); qlab = "OT" if p["q"] > 4 else f"Q{p['q']}"; line2 = f"{qlab} {clock_str(p['clock'])} · {sit}" if sit else f"{qlab} {clock_str(p['clock'])}"
            # setup: slide into formation; caption shows the situation only
            for k in range(n_setup):
                u = ease((k + 1) / n_setup); surf, c = self.begin()
                self.L.cam += (L.clamp_cam(ch["los"] + 12 * ch["d"]) - self.L.cam) * min(1, 6 / fps)
                self.field(c, t_abs)
                self.los_marker(c, ch, p)
                for s in OFF_SLOTS + DEF_SLOTS:
                    x, y = lerp(start[s][0], form[s][0], u), lerp(start[s][1], form[s][1], u); self.dot(c, x, y, self.ch if (s in OFF_SLOTS) == (p["off"] == self.home) else self.ca, self.r(s))
                self.ball(c, ch["ball"](0))
                self.scoreboard(c, p, score, t_abs, 0, sit); self.caption(c, sit or "", line2.split(" · ")[0], DIM)
                yield self.end(surf); t_abs += 1 / fps
            # action
            self.trail = []
            for k in range(n_action + n_hold):
                t = min(1.0, (k + 1) / n_action); surf, c = self.begin()
                bx, by, bh = ch["ball"](t)
                self.L.cam += (L.clamp_cam(bx + 6 * ch["d"]) - self.L.cam) * min(1, 5 / fps)
                self.field(c, t_abs); self.los_marker(c, ch, p)
                if ch["carrier"]:
                    cx, cy = ch["moves"][ch["carrier"]](t)[:2]
                    if k < n_action: self.trail.append((cx, cy))
                    for j, (tx, ty) in enumerate(self.trail[-14:]):
                        a = (j + 1) / 14 * 0.45; px, py = L.to_px(tx, ty); c.drawCircle(px, py, self.r("RB") * 0.55, paint(col(BULB, a)))
                for s in OFF_SLOTS + DEF_SLOTS:
                    x, y = ch["moves"][s](t)[:2]; self.last_pos[s] = (x, y)
                    off_side = (s in OFF_SLOTS) == (p["off"] == self.home)
                    self.dot(c, x, y, self.ch if off_side else self.ca, self.r(s), ring=BULB if s == ch["carrier"] and t > 0.12 else None)
                if not (ch["carrier"] and bh == 0 and t >= 1): self.ball(c, (bx, by, bh))
                shown_score = new_score if k >= n_action - 1 else score
                if k == n_action - 1 and new_score != score:
                    self.score_flash["home" if new_score[0] != score[0] else "away"] = t_abs
                self.scoreboard(c, p, shown_score, t_abs, t, sit)
                if k >= n_action - 1 and p["result"] in ("TD", "FG good", "turnover", "FG miss"):
                    self.banner(c, "TOUCHDOWN" if p["result"] == "TD" else "FIELD GOAL" if p["result"] == "FG good" else "NO GOOD" if p["result"] == "FG miss" else "TURNOVER", (k - n_action + 1) / max(1, n_hold), BULB if p["result"] in ("TD", "FG good") else LOSS)
                self.caption(c, line1 if t > 0.5 else (sit or ""), line2, colr if t > 0.5 else DIM)
                yield self.end(surf); t_abs += 1 / fps
            score = new_score
        yield from self.outro(score, t_abs)
    def r(self, slot): return (0.66 if slot.startswith(("OL", "DL")) else 0.78) * self.L.s * (1.0 if self.L.vertical else 1.15)
    def los_marker(self, c, ch, p):
        L = self.L; a, b = L.to_px(ch["los"], 0), L.to_px(ch["los"], L.FW)
        c.drawLine(a[0], a[1], b[0], b[1], paint(col("#4C8DFF", 0.75), "stroke", max(2, 0.16 * L.s)))
        if p["down"] and p["dist"] < 100 - p["from_yl"]:
            fd = ch["los"] + p["dist"] * ch["d"]; a, b = L.to_px(fd, 0), L.to_px(fd, L.FW)
            c.drawLine(a[0], a[1], b[0], b[1], paint(col(BULB, 0.8), "stroke", max(2, 0.16 * L.s)))
    def ball(self, c, b):
        x, y, h = b; L = self.L; px, py = L.to_px(x, y)
        if h > 0: c.drawCircle(px, py, 0.28 * L.s, paint(col(INK, 0.4), blur=0.3 * L.s))
        py -= h * L.s * 1.6; r = 0.3 * L.s * (1 + 0.6 * h)
        c.drawCircle(px, py, r, paint(col("#B5642B"))); c.drawCircle(px, py, r, paint(col("#FFFFFF", 0.8), "stroke", max(1, r * 0.2)))
    def banner(self, c, s, u, colr):
        L, F = self.L, self.F; a = min(1, u * 6) * (1 if u < 0.8 else max(0, 1 - (u - 0.8) / 0.2)); sc = 1 + 0.25 * (1 - ease(min(1, u * 5)))
        y = (L.top + L.bottom) / 2 - (120 if L.vertical else 0)
        c.save(); c.translate(L.W / 2, y); c.scale(sc, sc)
        f = F.f(F.display, 128 if L.vertical else 96); w = f.measureText(s)
        c.drawRoundRect(skia.Rect.MakeXYWH(-w / 2 - 36, -f.getSize() * 0.95, w + 72, f.getSize() * 1.3), 16, 16, paint(col(INK, 0.78 * a)))
        text(c, s, 0, 0, f, col(colr, a), "center"); c.restore()
    def full_field(self, c):
        L = self.L; keep = (L.top, L.bottom, L.span, L.cam)
        L.top, L.bottom = 0, L.H; L.span = (L.H / L.s) if L.vertical else 120; L.cam = 60; self.field(c, 0)
        L.top, L.bottom, L.span, L.cam = keep
    def begin(self):
        surf = skia.Surface.MakeRaster(skia.ImageInfo.Make(self.L.W, self.L.H, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType))
        c = surf.getCanvas(); c.clear(col(INK)); return surf, c
    def end(self, surf): return surf.makeImageSnapshot().tobytes()
    def intro(self):
        L, F = self.L, self.F; n = int(INTRO * self.fps); g = self.g
        for k in range(n):
            u = k / n; a = min(1, u * 5) * (1 if u < 0.8 else max(0, 1 - (u - 0.8) / 0.2)); surf, c = self.begin()
            self.full_field(c)
            c.drawRect(skia.Rect.MakeLTRB(0, 0, L.W, L.H), paint(col(INK, 0.6)))
            rise = (1 - ease(min(1, u * 3))) * 30
            cy = L.H * 0.42
            text(c, "sixpts", L.W / 2, cy - (210 if L.vertical else 150) + rise, F.f(F.display, 60 if L.vertical else 44), col(BULB, a), "center")
            text(c, self.title, L.W / 2, cy + rise, F.f(F.display, 150 if L.vertical else 110), col(PAINT, a), "center")
            text(c, self.sub, L.W / 2, cy + (70 if L.vertical else 52) + rise, F.f(F.semi, 34 if L.vertical else 26), col(DIM, a), "center")
            l1 = f"{self.home} wins {g['p_home']:.0%} of 5,000 sims · median {self.away} {g['median_away']:.0f}–{self.home} {g['median_home']:.0f}"
            l2 = f"Spread {self.home} {g['spread']:+.1f} · O/U {g['total']} · over {g['p_over']:.0%}"
            text(c, l1, L.W / 2, cy + (170 if L.vertical else 110) + rise, F.f(F.semi, 30 if L.vertical else 24), col(PAINT, a), "center")
            text(c, l2, L.W / 2, cy + (214 if L.vertical else 142) + rise, F.f(F.med, 26 if L.vertical else 20), col(DIM, a), "center")
            text(c, "This is the median game, play by play.", L.W / 2, cy + (300 if L.vertical else 200) + rise, F.f(F.semi, 30 if L.vertical else 22), col(BULB, a), "center")
            yield self.end(surf)
    def quarter_card(self, q, score, t_abs):
        n = int(QUARTER * self.fps)
        for k in range(n):
            u = k / n; a = min(1, u * 6) * (1 if u < 0.75 else max(0, 1 - (u - 0.75) / 0.25)); surf, c = self.begin()
            self.field(c, t_abs); self.scoreboard(c, None if False else {"q": q, "clock": 0, "off": None}, score, t_abs, 0, "")
            self.card(c, [("HALFTIME" if q == 2 else f"END OF {ordinal(q).upper()}" if q <= 4 else "END OF OT", 96 if self.L.vertical else 72, BULB, "display"),
                          (f"{self.away} {score[1]} · {self.home} {score[0]}", 56 if self.L.vertical else 44, PAINT, "display")], a)
            yield self.end(surf)
    def outro(self, score, t_abs):
        L, F = self.L, self.F; g = self.g; n = int(OUTRO * self.fps)
        top = sorted(g["players"], key=lambda p: -p["p_td"])[:5]
        for k in range(n):
            u = k / n; a = min(1, u * 4); surf, c = self.begin(); self.full_field(c)
            self.scoreboard(c, None, score, t_abs, 0, "")
            c.drawRect(skia.Rect.MakeLTRB(0, 0, L.W, L.H), paint(col(INK, 0.78 * a)))
            cy = L.H * (0.30 if L.vertical else 0.27)
            win = self.home if score[0] > score[1] else self.away if score[1] > score[0] else None
            text(c, f"{win} {max(score)}–{min(score)}" if win else f"TIE {score[0]}–{score[1]}", L.W / 2, cy, F.f(F.display, 150 if L.vertical else 100), col(PAINT, a), "center")
            text(c, f"{self.home} wins {g['p_home']:.0%} of sims · {self.away} {g['p_away']:.0%}", L.W / 2, cy + (70 if L.vertical else 50), F.f(F.semi, 34 if L.vertical else 24), col(DIM, a), "center")
            text(c, f"Over {g['total']} in {g['p_over']:.0%} · {self.home} covers {g['spread']:+.1f} in {g['p_home_cover']:.0%}", L.W / 2, cy + (116 if L.vertical else 82), F.f(F.semi, 34 if L.vertical else 24), col(DIM, a), "center")
            text(c, "MOST LIKELY TO SCORE", L.W / 2, cy + (220 if L.vertical else 150), F.f(F.bold, 26 if L.vertical else 20), col(BULB, a), "center")
            bw = (L.W - 2 * L.margin - 120) if L.vertical else 900; bx = (L.W - bw) / 2; rowh = 86 if L.vertical else 58
            for i, p in enumerate(top):
                ua = min(1, max(0, (u - 0.15 - i * 0.06) * 5)); y = cy + (270 if L.vertical else 190) + i * rowh
                text(c, f"{p['player']}", bx, y, F.f(F.semi, 36 if L.vertical else 26), col(PAINT, ua)); text(c, f"{p['team']} {p['position']}", bx, y + (30 if L.vertical else 22), F.f(F.med, 22 if L.vertical else 16), col(FAINT, ua))
                c.drawRoundRect(skia.Rect.MakeXYWH(bx, y + (42 if L.vertical else 30), bw, 6), 3, 3, paint(col(LINE_S, ua)))
                c.drawRoundRect(skia.Rect.MakeXYWH(bx, y + (42 if L.vertical else 30), bw * min(1, p["p_td"] / 0.8) * ease(ua), 6), 3, 3, paint(col(BULB, ua)))
                text(c, f"{p['p_td']:.0%}", bx + bw, y, F.f(F.display, 40 if L.vertical else 28), col(BULB_HI, ua), "right")
            text(c, "sixpts.com · simulated, not predicted", L.W / 2, L.H - (150 if L.vertical else 80), F.f(F.semi, 28 if L.vertical else 20), col(DIM, a), "center")
            yield self.end(surf)

def render(game, gid, fmt, out, fps=30, target=75, crf=20):
    R = Renderer(game, gid, fmt, fps, target); W, H = R.L.W, R.L.H
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-profile:v", "high", "-level", "4.1", str(out)]
    n = 0; t0 = time.time()
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as ff:
        for fr in R.frames(): ff.stdin.write(fr); n += 1
        ff.stdin.close(); ff.wait()
    if ff.returncode: raise RuntimeError(f"ffmpeg failed on {out}")
    return n / fps, time.time() - t0, R.speed

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--week", type=int, required=True); ap.add_argument("--game"); ap.add_argument("--format", default="both", choices=["9x16", "16x9", "both"])
    ap.add_argument("--target", type=float, default=80, help="aim for about this many seconds of play; 0 = natural pace"); ap.add_argument("--fps", type=int, default=30); ap.add_argument("--out", default=None); ap.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()
    d = json.load(open(D / f"sim_w{a.week}.json")); out = pathlib.Path(a.out) if a.out else D / "replays" / f"w{a.week}"
    games = [a.game] if a.game else list(d["games"])
    for gid in games:
        for fmt in (["9x16", "16x9"] if a.format == "both" else [a.format]):
            secs, took, speed = render(d["games"][gid], gid, fmt, out / f"{gid}_{fmt}.mp4", a.fps, a.target, a.crf)
            print(f"{gid} {fmt}: {secs:.0f}s of video (pace x{speed:.2f}) rendered in {took:.0f}s -> {out / f'{gid}_{fmt}.mp4'}")

if __name__ == "__main__": main()
