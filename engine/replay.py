"""Render a simulated game's play log to an mp4 you can post.

    python3 -m engine.replay --week 8                       # every game, 9x16 and 16x9
    python3 -m engine.replay --week 8 --game 2026_08_KC_DEN --format 9x16 --speed 1.5

Reads data/sim_w{week}.json (engine.sim_week) and writes data/replays/w{week}/{game}_{9x16|16x9}.mp4. Skia draws
every frame; ffmpeg encodes them from a pipe. The look is the sixpts scoreboard on a broadcast-style perspective
field: coin toss, kickoffs, the ball's flight drawn as a path, the carrier tagged by name, live leader panels,
a win-probability bar and a last-play block. Offence dots are filled, defence dots are rings, so the sides read
apart whatever the colours. Pace is about 2.8 s a snap (a game runs 6-7 min); --speed 1.5 shortens it.
~1 minute of render per minute of video on a laptop core.

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


def ease(t): t = min(max(t, 0.0), 1.0); return t * t * (3 - 2 * t)
def ease_out(t): t = min(max(t, 0.0), 1.0); return 1 - (1 - t) ** 3
def lerp(a, b, t): return a + (b - a) * t
def clock_str(s): s = max(int(s), 0); return f"{s // 60}:{s % 60:02d}"
def ordinal(n): return f"{n}{'tsnrhtdd'[(n // 10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"
def short(name):
    if not name: return ""
    parts = name.split(); return name if len(parts) < 2 else f"{parts[0][0]}. {' '.join(parts[1:])}"

# ---- the picture ----------------------------------------------------------------------------------------------

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
        x0, y0 = de[s]; lag = rng.uniform(0.15, 0.4); gap = rng.uniform(0.8, 2.5); side = rng.normal(0, 2.2)
        if typ in ("punt", "field goal"):
            moves[s] = path([(0, x0, y0), (1, x0 + rng.uniform(-2, 1) * d, y0)]); continue
        def chase(t, x0=x0, y0=y0, lag=lag, gap=gap, side=side, s=s):
            if ball is None or carrier is None: return x0 + (1.5 * d if s.startswith("DL") else 0) * ease(t), y0
            bx, by, _ = ball(max(0, t - lag)); u = ease(t / (1 - lag * 0.5))
            return lerp(x0, bx + gap * d, u), lerp(y0, by + side, u)
        moves[s] = chase
    for s in OFF_SLOTS:
        if s in moves: continue
        x0, y0 = off[s]; moves[s] = path([(0, x0, y0), (1, x0 + (1.0 if typ not in ("sack",) else -1.5) * d, y0)])
    return dict(off=off, de=de, moves=moves, ball=ball, carrier=carrier, d=d, los=los, end=end, slot=slot)

# ---- frames ---------------------------------------------------------------------------------------------------

# ---- geometry: field (x along 0..120, y across 0..53.3) -> screen through a homography -----------------------
def homography(src, dst):
    """3x3 matrix mapping four src points to four dst points (DLT)."""
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u]); A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.array(A, float)); h = vt[-1]; return (h / h[-1]).reshape(3, 3)

class Layout:
    """Both formats draw the field through a perspective: the far side is narrower, like a broadcast camera.
    16x9 shows the whole field from the sideline; 9x16 looks down the field from behind the offence's end and
    pans along x. to_px(x, y) is the only mapping the renderer uses; scale(x, y) is the local px-per-yard."""
    FW = 53.3
    def __init__(self, fmt):
        self.fmt = fmt; self.vertical = fmt == "9x16"; self.cam = 60.0
        if self.vertical:
            self.W, self.H = 1080, 1920
            self.top, self.bottom = 250, 1330                       # field band
            self.span = 62.0                                        # yards visible along the field
            quad = [(0.10 * self.W, self.top), (0.90 * self.W, self.top), (1.06 * self.W, self.bottom), (-0.06 * self.W, self.bottom)]
        else:
            self.W, self.H = 1920, 1080
            self.top, self.bottom = 215, 840
            self.span = 120.0
            quad = [(0.245 * self.W, self.top), (0.755 * self.W, self.top), (0.86 * self.W, self.bottom), (0.14 * self.W, self.bottom)]
        self.quad = quad; self.flip = False                               # 9x16: True when the offence drives -x, so it always drives "up"
        self.Hm = [float(v) for v in homography([(0, 0), (1, 0), (1, 1), (0, 1)], quad).ravel()]   # unit square (u right, v down) -> screen
    def uv(self, x, y):
        if self.vertical:
            if self.flip: return 1 - y / self.FW, (x - (self.cam - self.span / 2)) / self.span
            return y / self.FW, (self.cam + self.span / 2 - x) / self.span
        return x / 120.0, y / self.FW
    def to_px(self, x, y):
        u, v = self.uv(x, y); h = self.Hm; w = h[6] * u + h[7] * v + h[8]
        return (h[0] * u + h[1] * v + h[2]) / w, (h[3] * u + h[4] * v + h[5]) / w
    def scale(self, x, y):
        a = self.to_px(x, y - 0.5); b = self.to_px(x, y + 0.5); return math.hypot(a[0] - b[0], a[1] - b[1])
    def clamp_cam(self, x):
        half = self.span / 2
        return min(max(x, half), 120 - half) if self.span < 120 else 60.0
    def poly(self, c, pts, pnt):
        path = skia.Path(); sp = [self.to_px(*p) for p in pts]; path.moveTo(*sp[0])
        for p in sp[1:]: path.lineTo(*p)
        path.close(); c.drawPath(path, pnt)
    def seg(self, c, a, b, pnt):
        (x0, y0), (x1, y1) = self.to_px(*a), self.to_px(*b); c.drawLine(x0, y0, x1, y1, pnt)

# ---- the timeline: toss, kickoffs, plays, quarter cards, outro -----------------------------------------------
PACE = dict(setup=0.70, read=0.55, complete=1.55, incomplete=1.25, run=1.45, sack=1.25, int=2.1, fumble=1.9, punt=1.9,
            fg=1.9, kneel=0.8, kickoff=2.3, toss=4.2, qcard=2.4, outro=7.0, td=2.4, score=1.5, turnover=1.5)

def win_prob(margin_home, secs_left, spread_home, poss_home):
    """Pre-snap win probability for the home side: a normal approximation on the remaining margin, the market's
    pregame expectation shrinking as the clock runs, plus a small bump for having the ball."""
    f = max(secs_left / 3600.0, 0.004); mu = -spread_home * f + (1.4 if poss_home else -1.4 if poss_home is False else 0) * f
    z = (margin_home + mu) / (13.5 * math.sqrt(f)); return 0.5 * (1 + math.erf(z / math.sqrt(2)))

class Renderer:
    def __init__(self, game, gid, fmt, fps, speed):
        self.g, self.gid, self.L, self.fps, self.speed = game, gid, Layout(fmt), fps, max(0.5, speed)
        self.F = Fonts(); self.home, self.away = game["home"], game["away"]
        self.ch, self.ca = team_colors(self.home, self.away)
        self.plays = [p for p in game["log"]["plays"] if p["type"] != "final"]
        self.sim_index = game["log"].get("sim_index")
        qbs = [p for p in game["players"] if p["position"] == "QB"]
        self.qb = {t: max([p for p in qbs if p["team"] == t], key=lambda p: p.get("car", 0), default={"player": "QB"})["player"] for t in (self.home, self.away)}
        self.nick = {t: TEAMS.get(t, ("", "", t))[2] for t in (self.home, self.away)}
        self.timeline = self.build_timeline()
        self.last_pos = {}; self.trail = []; self.flash = {"home": -9, "away": -9}
        self.stats = {t: dict(att=0, cmp=0, pyds=0, ptd=0, inter=0, rush={}, recv={}) for t in (self.home, self.away)}
    # -- timeline
    def build_timeline(self):
        T = []; sp = self.speed
        def add(kind, dur, **kw): T.append(dict(kind=kind, dur=dur / sp, **kw))
        add("toss", PACE["toss"], receiver=self.plays[0]["off"] if self.plays else self.home)
        q = 1
        for i, p in enumerate(self.plays):
            prev = self.plays[i - 1] if i else None
            if p["q"] != q:
                add("qcard", PACE["qcard"], q=q, score=(prev["score_home"], prev["score_away"]) if prev else (0, 0)); q = p["q"]
            kicked = i == 0 or (prev and prev["result"] in ("TD", "FG good") and prev["off"] != p["off"]) or (prev and prev["q"] == 2 and p["q"] == 3)
            if kicked: add("kickoff", PACE["kickoff"], i=i)
            act = PACE["fg" if p["type"] == "field goal" else p["type"]] if (p["type"] in PACE or p["type"] == "field goal") else 1.3
            hold = PACE["td"] if p["result"] == "TD" else PACE["score"] if p["result"] == "FG good" else PACE["turnover"] if p["result"] in ("turnover", "FG miss", "downs") else PACE["read"]
            add("play", PACE["setup"] + act + hold, i=i, setup=PACE["setup"] / sp, action=act / sp, hold=hold / sp)
        add("outro", PACE["outro"])
        t = 0
        for s in T: s["t0"] = t; t += s["dur"]
        self.total = t; return T
    # -- helpers
    def score_before(self, i): return (self.plays[i - 1]["score_home"], self.plays[i - 1]["score_away"]) if i else (0, 0)
    def secs_left(self, p): q = min(p["q"], 4); return (4 - q) * 900 + p["clock"] if p["q"] <= 4 else p["clock"]
    def situation(self, p):
        yl = p["from_yl"]; side = p["off"] if yl <= 50 else (self.home if p["off"] == self.away else self.away); mark = yl if yl <= 50 else 100 - yl
        return (f"{ordinal(p['down']).upper()} & {'GOAL' if p['dist'] >= 100 - yl else p['dist']}", f"{side} {mark}") if p["down"] else ("", "")
    def describe(self, p):
        who = short(p["player"]); g = p["gain"]; t = p["type"]; r = p["result"]; qb = short(self.qb.get(p["off"], "QB"))
        yds = lambda n: "no gain" if n == 0 else f"{n} yard{'s' if n != 1 else ''}" if n > 0 else f"a loss of {-n}"
        if t == "complete": s = f"{qb} to {who} for {yds(g)}" if r != "TD" else f"{qb} to {who} for {g} yards — TOUCHDOWN"
        elif t == "incomplete": s = f"{qb} incomplete, intended for {who}"
        elif t == "run": s = f"{who} runs for {yds(g)}" if r != "TD" else f"{who} runs it in from {g} out — TOUCHDOWN"
        elif t == "sack": s = f"{qb} sacked for {yds(g)}"
        elif t == "int": s = f"{qb} INTERCEPTED" + (f", returned {-g} yards" if g < 0 else "")
        elif t == "fumble": s = f"{who} FUMBLES, {p['off']} turn it over"
        elif t == "punt": s = f"{p['off']} punt"
        elif t == "field goal": s = f"{p['fg_dist']}-yard field goal is {'GOOD' if r == 'FG good' else 'NO GOOD'}"
        elif t == "kneel": s = f"{qb} takes a knee"
        else: s = t
        if r == "downs": s += " — turnover on downs"
        colr = BULB if r in ("TD", "FG good") else LOSS if r in ("turnover", "FG miss", "downs") else PAINT
        return s, colr
    def apply_stats(self, p):
        """Count a finished play into the live leader panels."""
        S = self.stats[p["off"]]; t, g, who = p["type"], p["gain"], p["player"] or "—"
        if t in ("complete", "incomplete", "int"): S["att"] += 1
        if t == "complete":
            S["cmp"] += 1; S["pyds"] += g; S["ptd"] += p["result"] == "TD"
            r = S["recv"].setdefault(who, dict(rec=0, yds=0, td=0)); r["rec"] += 1; r["yds"] += g; r["td"] += p["result"] == "TD"
        if t == "int": S["inter"] += 1
        if t in ("run", "fumble"):
            r = S["rush"].setdefault(who, dict(car=0, yds=0, td=0)); r["car"] += 1; r["yds"] += g; r["td"] += p["result"] == "TD"
    # -- drawing: background, field, pieces
    def background(self, c):
        """Ink with a glow in each team's colour, painted once and blitted."""
        L = self.L
        if not hasattr(self, "_bg"):
            surf = skia.Surface.MakeRaster(skia.ImageInfo.Make(L.W, L.H, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType)); bc = surf.getCanvas(); bc.clear(col(INK))
            for colr, cx in ((self.ca, 0.0), (self.ch, 1.0)):
                x = cx * L.W; y = L.H * (0.12 if L.vertical else 0.45); r = L.W * (0.55 if L.vertical else 0.42)
                p = skia.Paint(AntiAlias=True); p.setShader(skia.GradientShader.MakeRadial((x, y), r, [col(colr, 0.28), col(colr, 0.0)])); bc.drawRect(skia.Rect.MakeLTRB(0, 0, L.W, L.H), p)
            self._bg = surf.makeImageSnapshot()
        c.drawImage(self._bg, 0, 0)
    PX = 18  # texture pixels per yard
    def field(self, c):
        """The field is painted once as a flat texture and drawn through the camera's perspective matrix, so the
        numbers and lines distort like real paint. The projected result is cached per camera position (1/4 yard),
        so frames where the camera rests — most of them — cost one blit."""
        L = self.L; key = (round(L.cam * 4), L.flip)
        if not hasattr(self, "_tex"): self._tex = self._paint_texture(); self._fcache = {}
        img = self._fcache.get(key)
        if img is None:
            if len(self._fcache) > 64: self._fcache.clear()
            PX = self.PX; pts = [(-3.0, -2.0), (123.0, -2.0), (123.0, L.FW + 2), (-3.0, L.FW + 2)]
            src = [(0, 0), (126 * PX, 0), (126 * PX, (L.FW + 4) * PX), (0, (L.FW + 4) * PX)]
            dst = [(x, y - (L.top - 4)) for x, y in (L.to_px(*q) for q in pts)]
            M = skia.Matrix(); M.setPolyToPoly([skia.Point(*q) for q in src], [skia.Point(*q) for q in dst])
            surf = skia.Surface.MakeRaster(skia.ImageInfo.Make(L.W, L.bottom - L.top + 8, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType))
            fc = surf.getCanvas(); fc.clear(skia.ColorSetARGB(0, 0, 0, 0)); fc.concat(M)
            fc.drawImage(self._tex, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear))
            img = self._fcache[key] = surf.makeImageSnapshot()
        c.drawImage(img, 0, L.top - 4)
    def _paint_texture(self):
        L = self.L; PX = self.PX; W, H = int(126 * PX), int((L.FW + 4) * PX)
        surf = skia.Surface.MakeRaster(skia.ImageInfo.Make(W, H, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType)); c = surf.getCanvas()
        X = lambda x: (x + 3) * PX; Y = lambda y: (y + 2) * PX
        rect = lambda x0, y0, x1, y1, pnt: c.drawRect(skia.Rect.MakeLTRB(X(x0), Y(y0), X(x1), Y(y1)), pnt)
        c.clear(col("#0B1511")); rect(0, 0, 120, L.FW, paint(col(TURF)))
        for i in range(10, 110, 10):
            if (i // 10) % 2: rect(i, 0, i + 10, L.FW, paint(col("#FFFFFF", 0.025)))
        for xa, team in ((0, self.home), (110, self.away)): rect(xa, 0, xa + 10, L.FW, paint(col(TEAMS.get(team, ("1E2836",))[0], 0.30)))
        wp = lambda a, w: paint(col("#FFFFFF", a), "stroke", w)
        line = lambda x0, y0, x1, y1, pnt: c.drawLine(X(x0), Y(y0), X(x1), Y(y1), pnt)
        for yd in range(10, 111, 5): line(yd, 0, yd, L.FW, wp(0.40, 0.12 * PX))
        for yd in range(11, 110):
            for hy in (0.6, 23.6, 29.0, 52.7): line(yd, hy, yd, hy + (0.9 if hy < 26 else -0.9), wp(0.28, 0.08 * PX))
        for yd in (10, 110): line(yd, 0, yd, L.FW, wp(0.80, 0.2 * PX))
        for yd in (0, 120): line(yd, L.FW / 2 - 3.1, yd, L.FW / 2 + 3.1, paint(col(BULB, 0.9), "stroke", 0.3 * PX))
        f = self.F.f(self.F.display, 3.0 * PX)
        for yd in range(20, 101, 10):
            n = yd - 10 if yd <= 60 else 110 - yd
            for hy, far in ((41.3, False), (12, True)):
                c.save(); c.translate(X(yd), Y(hy)); c.rotate(180 if far else 0); text(c, f"{n}", 0, f.getSize() * 0.36, f, col("#FFFFFF", 0.34), "center"); c.restore()
        f = self.F.f(self.F.display, 5.6 * PX)
        for xa, team in ((5, self.home), (115, self.away)):
            c.save(); c.translate(X(xa), Y(L.FW / 2)); c.rotate(90 if xa < 60 else -90); text(c, self.nick[team].upper(), 0, f.getSize() * 0.36, f, col("#FFFFFF", 0.45), "center"); c.restore()
        c.drawCircle(X(60), Y(L.FW / 2), 6 * PX, paint(col(BULB, 0.18), "stroke", 0.3 * PX))
        text(c, "sixpts", X(60), Y(L.FW / 2) + 1.0 * PX, self.F.f(self.F.display, 2.6 * PX), col(BULB, 0.22), "center")
        return surf.makeImageSnapshot()
    def markers(self, c, los, d, p):
        L = self.L; L.seg(c, (los, 0), (los, L.FW), paint(col("#4C8DFF", 0.8), "stroke", max(2, 0.16 * L.scale(los, L.FW / 2))))
        if p and p["down"] and p["dist"] < 100 - p["from_yl"]:
            fd = los + p["dist"] * d; L.seg(c, (fd, 0), (fd, L.FW), paint(col(BULB, 0.85), "stroke", max(2, 0.16 * L.scale(fd, L.FW / 2))))
    def dot(self, c, x, y, colr, offense, carrier=False, name=None):
        L = self.L; px, py = L.to_px(x, y); r = 0.72 * L.scale(x, y) * (1.0 if L.vertical else 1.1)
        c.drawCircle(px, py + r * 0.3, r * 1.05, paint(col(INK, 0.5), blur=r * 0.5))
        if offense:
            c.drawCircle(px, py, r, paint(col(colr))); c.drawCircle(px, py, r, paint(col("#FFFFFF", 0.7), "stroke", max(1.2, r * 0.16)))
        else:  # defence: dark centre, team-colour ring — tells the sides apart whatever the colours
            c.drawCircle(px, py, r, paint(col(INK, 0.85))); c.drawCircle(px, py, r * 0.78, paint(col(colr), "stroke", max(2, r * 0.36)))
        if carrier:
            c.drawCircle(px, py, r * 1.75, paint(col(BULB, 0.95), "stroke", max(2, r * 0.2)))
            if name:
                f = self.F.f(self.F.bold, max(14, r * 1.15)); w = f.measureText(name) + r * 1.6
                rect = skia.Rect.MakeXYWH(px - w / 2, py - r * 3.9, w, r * 1.9)
                c.drawRoundRect(rect, r * 0.5, r * 0.5, paint(col(INK, 0.86))); c.drawRoundRect(rect, r * 0.5, r * 0.5, paint(col(BULB, 0.9), "stroke", 1.5))
                text(c, name, px, py - r * 2.55, f, col(PAINT), "center")
        return px, py, r
    def ball(self, c, b, path_pts=None):
        x, y, h = b; L = self.L; px, py = L.to_px(x, y); s = L.scale(x, y)
        if path_pts:  # the flight: dotted ahead, solid behind
            pp = paint(col(BULB, 0.55), "stroke", 2.5); pp.setPathEffect(skia.DashPathEffect.Make([7, 9], 0))
            path = skia.Path(); path.moveTo(*path_pts[0])
            for q in path_pts[1:]: path.lineTo(*q)
            c.drawPath(path, pp)
        if h > 0: c.drawCircle(px, py, 0.3 * s, paint(col(INK, 0.45), blur=0.3 * s))
        py -= h * s * 1.7; r = 0.32 * s * (1 + 0.5 * h)
        c.drawOval(skia.Rect.MakeXYWH(px - r * 1.25, py - r * 0.85, r * 2.5, r * 1.7), paint(col("#B5642B")))
        c.drawOval(skia.Rect.MakeXYWH(px - r * 1.25, py - r * 0.85, r * 2.5, r * 1.7), paint(col("#FFFFFF", 0.85), "stroke", max(1, r * 0.22)))
        c.drawLine(px - r * 0.6, py, px + r * 0.6, py, paint(col("#FFFFFF", 0.9), "stroke", max(1, r * 0.18)))
    def flight_path(self, bfun, t0, t1, n=24):
        pts = []
        for k in range(n + 1):
            t = t0 + (t1 - t0) * k / n; x, y, h = bfun(t); px, py = self.L.to_px(x, y); pts.append((px, py - h * self.L.scale(x, y) * 1.7))
        return pts
    # -- chrome: scoreboard, win-prob, panels, last play, footer
    def scoreboard(self, c, q, clk, score, poss, t_abs, situation, wp):
        L, F = self.L, self.F; sh, sa = score
        if L.vertical: W = L.W - 60; x0 = 30; y0 = 28; h = 150
        else: W = 900; x0 = (L.W - W) / 2; y0 = 22; h = 118
        c.drawRoundRect(skia.Rect.MakeXYWH(x0, y0, W, h), 14, 14, paint(col(SURFACE, 0.96))); c.drawRoundRect(skia.Rect.MakeXYWH(x0, y0, W, h), 14, 14, paint(col(LINE), "stroke", 2))
        side_w = W * 0.36
        for key, team, s, colr, left in (("away", self.away, sa, self.ca, True), ("home", self.home, sh, self.ch, False)):
            xs = x0 if left else x0 + W - side_w
            c.drawRoundRect(skia.Rect.MakeXYWH(xs, y0 + h - 6, side_w, 6), 3, 3, paint(col(colr)))
            nf = F.f(F.display, 40 if L.vertical else 34); af = F.f(F.display, 70 if L.vertical else 62)
            name_x = xs + 28 if left else xs + side_w - 28
            text(c, team, name_x, y0 + (62 if L.vertical else 52), nf, col(PAINT), "left" if left else "right")
            text(c, self.nick[team], name_x, y0 + (92 if L.vertical else 78), F.f(F.semi, 22 if L.vertical else 18), col(DIM), "left" if left else "right")
            fl = t_abs - self.flash[key]; sc = 1 + 0.2 * max(0.0, 1 - fl / 1.2) if fl < 1.2 else 1
            score_x = xs + side_w - 40 if left else xs + 40
            c.save(); c.translate(score_x, y0 + (84 if L.vertical else 72)); c.scale(sc, sc); text(c, f"{s}", 0, 0, af, col(BULB_HI if fl < 1.2 else PAINT), "center"); c.restore()
            if poss == team: c.drawCircle(name_x + nf.measureText(team) + 16 if left else name_x - nf.measureText(team) - 16, y0 + (52 if L.vertical else 44), 6, paint(col(BULB)))
        mid = x0 + W / 2
        text(c, "OT" if q > 4 else f"Q{q}", mid, y0 + (46 if L.vertical else 40), F.f(F.semi, 24 if L.vertical else 20), col(DIM), "center")
        text(c, clk, mid, y0 + (92 if L.vertical else 80), F.f(F.display, 48 if L.vertical else 40), col(PAINT), "center")
        if situation[0]: text(c, f"{situation[0]} · {situation[1]}", mid, y0 + (124 if L.vertical else 106), F.f(F.semi, 20 if L.vertical else 16), col(BULB), "center")
        # win probability
        yb = y0 + h + 18; bw = W - 2 * 110; bx = x0 + 110
        c.drawRoundRect(skia.Rect.MakeXYWH(bx, yb + 6, bw, 8), 4, 4, paint(col(LINE_S)))
        c.drawRoundRect(skia.Rect.MakeXYWH(bx, yb + 6, bw * (1 - wp), 8), 4, 4, paint(col(self.ca, 0.9)))
        c.drawRoundRect(skia.Rect.MakeXYWH(bx + bw * (1 - wp), yb + 6, bw * wp, 8), 4, 4, paint(col(self.ch, 0.9)))
        mk = bx + bw * (1 - wp); c.drawCircle(mk, yb + 10, 9, paint(col(INK))); c.drawCircle(mk, yb + 10, 9, paint(col(BULB), "stroke", 3))
        pf = F.f(F.display, 26 if L.vertical else 22)
        text(c, f"{1 - wp:.0%}", bx - 14, yb + 20, pf, col(self.ca), "right"); text(c, f"{wp:.0%}", bx + bw + 14, yb + 20, pf, col(self.ch), "left")
        text(c, "WIN PROBABILITY", mid, yb + 36, F.f(F.semi, 12), col(FAINT), "center")
    def panels(self, c):
        """Live leaders for each team. 16x9: left/right columns. 9x16: two columns under the last-play block."""
        L, F = self.L, self.F
        if L.vertical: pw, ph = (L.W - 60 - 16) / 2, 250; boxes = [(30, 1620), (30 + pw + 16, 1620)]
        else: pw, ph = 250, 330; boxes = [(26, 250), (L.W - 26 - pw, 250)]
        for (bx, by), team, colr in zip(boxes, (self.away, self.home), (self.ca, self.ch)):
            S = self.stats[team]
            c.drawRoundRect(skia.Rect.MakeXYWH(bx, by, pw, ph), 12, 12, paint(col(SURFACE, 0.94))); c.drawRoundRect(skia.Rect.MakeXYWH(bx, by, pw, ph), 12, 12, paint(col(LINE), "stroke", 2))
            c.drawRoundRect(skia.Rect.MakeXYWH(bx, by, pw, 5), 2, 2, paint(col(colr)))
            text(c, self.nick[team].upper(), bx + 16, by + 38, F.f(F.display, 22), col(PAINT))
            y = by + 70; lf = F.f(F.semi, 13); nf = F.f(F.semi, 18); vf = F.f(F.med, 16)
            def row(label, name, val):
                nonlocal y
                text(c, label.upper(), bx + 16, y, lf, col(BULB)); y += 26
                text(c, name, bx + 16, y, nf, col(PAINT)); text(c, val, bx + pw - 16, y, vf, col(DIM), "right"); y += 34
                c.drawLine(bx + 16, y - 20, bx + pw - 16, y - 20, paint(col(LINE), "stroke", 1)); y += 4
            row("Passing", short(self.qb.get(team, "QB")), f"{S['cmp']}/{S['att']} · {S['pyds']} yds" + (f" · {S['ptd']} TD" if S["ptd"] else "") + (f" · {S['inter']} INT" if S["inter"] else ""))
            ru = max(S["rush"].items(), key=lambda kv: kv[1]["yds"], default=None)
            row("Rushing", short(ru[0]) if ru else "—", f"{ru[1]['car']} car · {ru[1]['yds']} yds" + (f" · {ru[1]['td']} TD" if ru and ru[1]["td"] else "") if ru else "")
            re = max(S["recv"].items(), key=lambda kv: kv[1]["yds"], default=None)
            row("Receiving", short(re[0]) if re else "—", f"{re[1]['rec']} rec · {re[1]['yds']} yds" + (f" · {re[1]['td']} TD" if re and re[1]["td"] else "") if re else "")
    def last_play(self, c, situation, line, colr, upcoming=False):
        L, F = self.L, self.F
        y = 1380 if L.vertical else L.bottom + 28; x0 = 30 if L.vertical else 60; W = L.W - 2 * x0
        chip = "NEXT SNAP" if upcoming else "LAST PLAY"; cf = F.f(F.bold, 13); cw = cf.measureText(chip) + 24
        c.drawRoundRect(skia.Rect.MakeXYWH(L.W / 2 - cw / 2, y - 12, cw, 24), 5, 5, paint(col(RAISE2))); text(c, chip, L.W / 2, y + 5, cf, col(DIM), "center")
        y += 30
        sf = F.f(F.display, 36 if L.vertical else 34); s1 = situation[0] or "—"; w1 = sf.measureText(s1) + 36
        c.drawRoundRect(skia.Rect.MakeXYWH(x0, y, w1, 70), 8, 8, paint(col(BULB))); text(c, s1, x0 + 18, y + 48, sf, col("#0A0D12"))
        tf = F.f(F.semi, 20); text(c, situation[1], x0 + w1 + 14, y + 46, tf, col(DIM))
        xd = x0 + w1 + 14 + tf.measureText(situation[1]) + 22
        c.drawLine(xd, y + 14, xd, y + 56, paint(col(LINE_S), "stroke", 3)); xd += 18
        df = F.f(F.display, 34 if L.vertical else 36)
        if L.vertical:
            # wrap onto up to two lines below the situation
            words, lines, cur = line.split(), [], ""
            for w in words:
                trial = (cur + " " + w).strip()
                if df.measureText(trial) > W - 8 and cur: lines.append(cur); cur = w
                else: cur = trial
            lines.append(cur)
            for i, ln in enumerate(lines[:2]): text(c, ln, x0, y + 122 + i * 42, df, col(colr))
        else:
            text(c, line, xd, y + 48, df, col(colr))
    def footer(self, c):
        L, F = self.L, self.F; y = L.H - 26
        text(c, "sixpts", 30 if L.vertical else 60, y, F.f(F.display, 26), col(BULB, 0.95))
        n = f"sim {self.sim_index + 1:,} of {self.g['n']:,}" if self.sim_index is not None else f"{self.g['n']:,} sims"
        text(c, f"Simulated game, not a prediction · {n}", L.W - (30 if L.vertical else 60), y, F.f(F.med, 16), col(FAINT), "right")
    def banner(self, c, s, u, colr):
        L, F = self.L, self.F; a = min(1, u * 6) * (1 if u < 0.8 else max(0, 1 - (u - 0.8) / 0.2)); sc = 1 + 0.25 * (1 - ease(min(1, u * 5)))
        y = (L.top + L.bottom) / 2 - (140 if L.vertical else 40)
        c.save(); c.translate(L.W / 2, y); c.scale(sc, sc)
        f = F.f(F.display, 118 if L.vertical else 96); w = f.measureText(s)
        c.drawRoundRect(skia.Rect.MakeXYWH(-w / 2 - 36, -f.getSize() * 0.95, w + 72, f.getSize() * 1.3), 16, 16, paint(col(INK, 0.8 * a)))
        text(c, s, 0, 0, f, col(colr, a), "center"); c.restore()
    def card(self, c, lines, alpha):
        L, F = self.L, self.F; c.drawRect(skia.Rect.MakeLTRB(0, L.top, L.W, L.bottom), paint(col(INK, 0.74 * alpha)))
        cy = (L.top + L.bottom) / 2 - sum(sz for _, sz, _, _ in lines) / 2
        for s, size, colr, face in lines:
            cy += size; text(c, s, L.W / 2, cy, F.f(getattr(F, face), size), col(colr, alpha), "center"); cy += 18
    def begin(self):
        surf = skia.Surface.MakeRaster(skia.ImageInfo.Make(self.L.W, self.L.H, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType))
        return surf, surf.getCanvas()
    def end(self, surf): return surf.makeImageSnapshot().tobytes()
    # -- frames
    def frames(self):
        L = self.L; fps = self.fps; score = (0, 0); poss = None; p_cur = None; wp = win_prob(0, 3600, self.g["spread"], None)
        for seg in self.timeline:
            n = max(1, int(round(seg["dur"] * fps)))
            for k in range(n):
                u = (k + 0.5) / n; t_abs = seg["t0"] + k / fps
                surf, c = self.begin(); self.background(c)
                kind = seg["kind"]
                if kind == "toss":
                    L.cam = 60; self.field(c)
                    self.toss(c, u, seg["receiver"])
                    self.scoreboard(c, 1, "15:00", (0, 0), None, t_abs, ("", ""), wp); self.panels(c)
                    self.last_play(c, ("KICKOFF", f"{self.nick[seg['receiver']]} will receive"), f"{self.away} @ {self.home} · {self.g['n']:,} simulated games · this is the median one", DIM, upcoming=True)
                elif kind == "kickoff":
                    p = self.plays[seg["i"]]; self.kickoff(c, u, p, score, t_abs, wp)
                elif kind == "play":
                    p = self.plays[seg["i"]]; p_cur = p
                    self.play_frame(c, seg, k, n, t_abs, p)
                    if k == n - 1: score = (p["score_home"], p["score_away"]); self.apply_stats(p)
                elif kind == "qcard":
                    L.cam = 60; self.field(c)
                    a = min(1, u * 6) * (1 if u < 0.75 else max(0, 1 - (u - 0.75) / 0.25))
                    self.card(c, [("HALFTIME" if seg["q"] == 2 else f"END OF {ordinal(seg['q']).upper()}" if seg["q"] <= 4 else "END OF OT", 88 if L.vertical else 72, BULB, "display"),
                                  (f"{self.away} {seg['score'][1]} · {self.home} {seg['score'][0]}", 52 if L.vertical else 44, PAINT, "display")], a)
                    self.scoreboard(c, seg["q"], "0:00", seg["score"], None, t_abs, ("", ""), wp); self.panels(c); self.last_play(c, ("", ""), "", PAINT)
                elif kind == "outro":
                    self.outro(c, u, score, t_abs)
                self.footer(c)
                yield self.end(surf)
    def toss(self, c, u, receiver):
        """A coin spinning at midfield, then the call."""
        L, F = self.L, self.F; px, py = L.to_px(60, L.FW / 2); s = L.scale(60, L.FW / 2)
        spin = min(1, u / 0.6); r = 7 * s * (1.0 if L.vertical else 1.2)
        if spin < 1:
            w = abs(math.cos(spin * math.pi * 9)) * r; lift = math.sin(spin * math.pi) * 6 * s
            c.drawOval(skia.Rect.MakeXYWH(px - r, py - r * 0.35 + 8, 2 * r, r * 0.7), paint(col(INK, 0.5), blur=r * 0.3))
            c.drawOval(skia.Rect.MakeXYWH(px - w, py - lift - r, 2 * w, 2 * r), paint(col(BULB)))
            c.drawOval(skia.Rect.MakeXYWH(px - w, py - lift - r, 2 * w, 2 * r), paint(col(BULB_HI), "stroke", 3))
            text(c, "COIN TOSS", L.W / 2, py - lift - r - 24, F.f(F.display, 34), col(PAINT, 0.9), "center")
        else:
            a = min(1, (u - 0.6) / 0.15); c.drawOval(skia.Rect.MakeXYWH(px - r, py - r, 2 * r, 2 * r), paint(col(BULB, a)))
            text(c, receiver, px, py + r * 0.3, F.f(F.display, r * 0.8), col("#0A0D12", a), "center")
            text(c, f"{self.nick[receiver].upper()} WILL RECEIVE", L.W / 2, py + r + 56, F.f(F.display, 44 if L.vertical else 40), col(PAINT, a), "center")
    def kickoff(self, c, u, p, score, t_abs, wp):
        """Ball from the kicking team's 35 to where the next drive starts; the returner settles under it."""
        L = self.L; rcv = p["off"]; d = 1 if rcv == self.home else -1          # receiving team drives d
        kick_x = 110 - 35 if d == 1 else 10 + 35                              # kicker's own 35 in absolute x
        land = 10 + p["from_yl"] if d == 1 else 110 - p["from_yl"]
        L.flip = L.vertical and d == -1
        L.cam += (L.clamp_cam(lerp(kick_x, land, ease(u))) - L.cam) * 0.25
        self.field(c); c.save(); c.clipRect(skia.Rect.MakeLTRB(0, L.top - 4, L.W, L.bottom + 4))
        tk, tl = 0.18, 0.72; c_ = L.FW / 2
        bfun = lambda t: (kick_x, c_, 0) if t < tk else (lerp(kick_x, land, ease_out((t - tk) / (tl - tk))), lerp(c_, c_ + 3, (t - tk) / (tl - tk)), math.sin(math.pi * min(1, (t - tk) / (tl - tk))) * 2.6) if t < tl else (land, c_ + 3, 0)
        rng = np.random.default_rng(hash((self.gid, "ko", int(p["clock"]))) & 0xFFFFFFFF)
        kcol, rcol = (self.ca, self.ch) if rcv == self.home else (self.ch, self.ca)
        for i in range(11):  # kicking team sweeps downfield
            y0 = 4 + i * 4.6; x0 = kick_x - (3 if i == 5 else 1) * d
            x = lerp(x0, land - 4 * d + rng.normal(0, 2), ease(max(0, (u - tk) / (1 - tk)))); self.dot(c, x, y0 + rng.normal(0, 0.3), kcol, False)
        for i in range(10):  # return team holds, then collapses toward the ball
            y0 = 6 + i * 4.8; x0 = land - 12 * d + rng.normal(0, 3)
            x = lerp(x0, land - 6 * d, ease(max(0, (u - 0.5) / 0.5))); self.dot(c, x, y0, rcol, True)
        rx = lerp(land - 2 * d, land, ease(min(1, u / tl))); self.dot(c, rx, c_ + 3, rcol, True, carrier=u > tl, name=None)
        b = bfun(u); self.ball(c, b, self.flight_path(bfun, tk, tl) if tk <= u < tl else None); c.restore()
        self.scoreboard(c, p["q"], clock_str(p["clock"] + 5), score, None, t_abs, ("", ""), wp); self.panels(c)
        self.last_play(c, ("KICKOFF", f"{self.nick[rcv]} receive"), f"{self.nick[rcv]} start at the {p['from_yl'] if p['from_yl'] <= 50 else 100 - p['from_yl']}", DIM, upcoming=True)
    def play_frame(self, c, seg, k, n, t_abs, p):
        L = self.L; i = seg["i"]; fps = self.fps
        local = k / fps; n_setup = max(2, int(seg["setup"] * fps)); n_action = max(4, int(seg["action"] * fps))
        if not hasattr(self, "_ch") or self._ch[0] != i:
            rng = np.random.default_rng(hash((self.gid, i)) & 0xFFFFFFFF); self._ch = (i, choreograph(p, self.plays[i + 1] if i + 1 < len(self.plays) else None, self.home, rng)); self.trail = []
        ch = self._ch[1]; d = ch["d"]
        prev = self.score_before(i); sit = self.situation(p); line, colr = self.describe(p)
        in_setup = k < n_setup; t = min(1.0, max(0.0, (k - n_setup + 1) / n_action)); hold_u = max(0.0, (k - n_setup - n_action + 1) / max(1, n - n_setup - n_action))
        bx, by, bh = ch["ball"](0 if in_setup else t)
        L.flip = L.vertical and d == -1
        tgt = L.clamp_cam(ch["los"] + 10 * d if in_setup else bx + 5 * d); L.cam = tgt if abs(tgt - L.cam) < 0.3 else L.cam + (tgt - L.cam) * min(1, 4.5 / fps)
        self.field(c); c.save(); c.clipRect(skia.Rect.MakeLTRB(0, L.top - 4, L.W, L.bottom + 4)); self.markers(c, ch["los"], d, p)
        form = ch["off"] | ch["de"]
        # flight path while a pass or kick is in the air
        path_pts = None
        if not in_setup and bh > 0:
            t0 = t; t1 = t
            while t0 > 0 and ch["ball"](t0 - 0.02)[2] > 0: t0 -= 0.02
            while t1 < 1 and ch["ball"](t1 + 0.02)[2] > 0: t1 += 0.02
            path_pts = self.flight_path(ch["ball"], max(0, t0 - 0.02), min(1, t1 + 0.02))
        # trail
        if ch["carrier"] and not in_setup:
            cx, cy = ch["moves"][ch["carrier"]](t)[:2]
            if t < 1: self.trail.append((cx, cy))
            for j, (tx, ty) in enumerate(self.trail[-16:]):
                px, py = L.to_px(tx, ty); c.drawCircle(px, py, 0.32 * L.scale(tx, ty), paint(col(BULB, (j + 1) / 16 * 0.5)))
        # players, far rows first so near dots overlap far ones
        order = sorted(OFF_SLOTS + DEF_SLOTS, key=lambda s: (form[s][1] if not L.vertical else -form[s][0]))
        for s in order:
            if in_setup:
                u = ease((k + 1) / n_setup); start = self.last_pos.get(s, form[s]); x, y = lerp(start[0], form[s][0], u), lerp(start[1], form[s][1], u)
            else:
                x, y = ch["moves"][s](t)[:2]; self.last_pos[s] = (x, y)
            off_side = (s in OFF_SLOTS) == (p["off"] == self.home)
            is_car = not in_setup and s == ch["carrier"] and t > 0.12
            name = None
            if is_car: name = short(p["player"]) if s == ch.get("slot") and p["player"] else short(self.qb.get(p["off"], "")) if s == "QB" else "INT" if p["type"] == "int" else None
            self.dot(c, x, y, self.ch if off_side else self.ca, offense=(s in OFF_SLOTS), carrier=is_car, name=name)
        if not (ch["carrier"] and bh == 0 and t >= 1 and not in_setup): self.ball(c, (bx, by, bh), path_pts)
        c.restore()
        # score + flash
        new = (p["score_home"], p["score_away"]); shown = new if (not in_setup and t >= 1) else prev
        if not in_setup and t >= 1 and new != prev and self.flash["home" if new[0] != prev[0] else "away"] < seg["t0"]:
            self.flash["home" if new[0] != prev[0] else "away"] = t_abs
        if not in_setup and t >= 1 and p["result"] in ("TD", "FG good", "turnover", "FG miss"):
            self.banner(c, "TOUCHDOWN" if p["result"] == "TD" else "FIELD GOAL" if p["result"] == "FG good" else "NO GOOD" if p["result"] == "FG miss" else "TURNOVER", hold_u, BULB if p["result"] in ("TD", "FG good") else LOSS)
        wp = win_prob(shown[0] - shown[1], self.secs_left(p) - (0 if in_setup else 20), self.g["spread"], p["off"] == self.home)
        self.scoreboard(c, p["q"], clock_str(p["clock"]), shown, p["off"], t_abs, sit, wp); self.panels(c)
        if in_setup or t < 0.55: self.last_play(c, sit, "…" if in_setup else "", DIM, upcoming=True)
        else: self.last_play(c, sit, line, colr)
    def outro(self, c, u, score, t_abs):
        L, F = self.L, self.F; g = self.g; L.cam = 60; self.field(c)
        a = min(1, u * 4); c.drawRect(skia.Rect.MakeLTRB(0, 0, L.W, L.H), paint(col(INK, 0.8 * a)))
        win = self.home if score[0] > score[1] else self.away if score[1] > score[0] else None
        cy = L.H * (0.26 if L.vertical else 0.22)
        text(c, "FINAL", L.W / 2, cy - (90 if L.vertical else 70), F.f(F.display, 34), col(BULB, a), "center")
        text(c, f"{self.nick[win].upper()} {max(score)}–{min(score)}" if win else f"TIE {score[0]}–{score[1]}", L.W / 2, cy, F.f(F.display, 110 if L.vertical else 96), col(PAINT, a), "center")
        text(c, f"{self.home} wins {g['p_home']:.0%} of {g['n']:,} sims · {self.away} {g['p_away']:.0%}", L.W / 2, cy + (64 if L.vertical else 50), F.f(F.semi, 32 if L.vertical else 26), col(DIM, a), "center")
        text(c, f"Median {self.away} {g['median_away']:.0f}–{self.home} {g['median_home']:.0f} · over {g['total']} in {g['p_over']:.0%} · {self.home} {g['spread']:+.1f} covers {g['p_home_cover']:.0%}", L.W / 2, cy + (108 if L.vertical else 84), F.f(F.semi, 28 if L.vertical else 22), col(DIM, a), "center")
        top = sorted(g["players"], key=lambda p: -p["p_td"])[:5]
        text(c, "MOST LIKELY TO SCORE", L.W / 2, cy + (200 if L.vertical else 150), F.f(F.bold, 24 if L.vertical else 20), col(BULB, a), "center")
        bw = L.W - 2 * (90 if L.vertical else 560); bx = (L.W - bw) / 2; rowh = 86 if L.vertical else 60
        for i, p in enumerate(top):
            ua = min(1, max(0, (u - 0.12 - i * 0.05) * 5)); y = cy + (250 if L.vertical else 190) + i * rowh
            text(c, p["player"], bx, y, F.f(F.semi, 34 if L.vertical else 26), col(PAINT, ua)); text(c, f"{p['team']} {p['position']}", bx, y + (28 if L.vertical else 22), F.f(F.med, 20 if L.vertical else 16), col(FAINT, ua))
            c.drawRoundRect(skia.Rect.MakeXYWH(bx, y + (40 if L.vertical else 30), bw, 6), 3, 3, paint(col(LINE_S, ua)))
            c.drawRoundRect(skia.Rect.MakeXYWH(bx, y + (40 if L.vertical else 30), bw * min(1, p["p_td"] / 0.8) * ease(ua), 6), 3, 3, paint(col(BULB, ua)))
            text(c, f"{p['p_td']:.0%}", bx + bw, y, F.f(F.display, 38 if L.vertical else 28), col(BULB_HI, ua), "right")
        text(c, "sixpts.com", L.W / 2, L.H - (120 if L.vertical else 70), F.f(F.display, 36 if L.vertical else 28), col(BULB, a), "center")

def render(game, gid, fmt, out, fps=30, speed=1.0, crf=23):
    R = Renderer(game, gid, fmt, fps, speed); W, H = R.L.W, R.L.H
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-profile:v", "high", "-level", "4.1", str(out)]
    n = 0; t0 = time.time()
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as ff:
        for fr in R.frames(): ff.stdin.write(fr); n += 1
        ff.stdin.close(); ff.wait()
    if ff.returncode: raise RuntimeError(f"ffmpeg failed on {out}")
    return n / fps, time.time() - t0

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--week", type=int, required=True); ap.add_argument("--game"); ap.add_argument("--format", default="both", choices=["9x16", "16x9", "both"])
    ap.add_argument("--speed", type=float, default=1.0, help="1 = broadcast pace (~2.8 s a snap); 1.5 for a shorter clip"); ap.add_argument("--fps", type=int, default=30); ap.add_argument("--out", default=None); ap.add_argument("--crf", type=int, default=23)
    a = ap.parse_args()
    d = json.load(open(D / f"sim_w{a.week}.json")); out = pathlib.Path(a.out) if a.out else D / "replays" / f"w{a.week}"
    games = [a.game] if a.game else list(d["games"])
    for gid in games:
        for fmt in (["9x16", "16x9"] if a.format == "both" else [a.format]):
            secs, took = render(d["games"][gid], gid, fmt, out / f"{gid}_{fmt}.mp4", a.fps, a.speed, a.crf)
            print(f"{gid} {fmt}: {secs / 60:.1f} min of video rendered in {took / 60:.1f} min -> {out / f'{gid}_{fmt}.mp4'}")

if __name__ == "__main__": main()
