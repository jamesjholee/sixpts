"""Model picks: the model chooses, with reasons. Not "top P(TD)" — it needs price value AND agreeing signals.

Inputs: board rows (score_week output) + odds rows (best available price per player/market).
Output per player: tier (Bet / Lean / Pass / No price), edge, EV per unit, quarter-Kelly stake, signals checklist, reasons for/against.
"""
from __future__ import annotations
import numpy as np

MIN_EDGE = {"Bet": 0.05, "Lean": 0.03}
MARKET_RATIO_MAX = 2.2      # if our number is >2.2x the book's, that's our error, not their mispricing
MIN_TOUCHES = 6             # below this, a per-game rate is noise (2021-25: corr with scoring is ~0.02)
POS_ROLE_FLOOR = {"RB": 0.45, "WR": 0.30, "TE": 0.22, "QB": 0.25}   # xTD/game that counts as a "real role"

def implied(price: float) -> float:
    return (-price) / (-price + 100) if price < 0 else 100 / (price + 100)

def decimal(price: float) -> float:
    return 1 + (price / 100 if price > 0 else 100 / -price)

def evaluate(r: dict, price: float | None, book: str = "") -> dict:
    """r: one board row (anytime_td). price: American odds or None."""
    p = float(r["p_model"]); pos = r.get("position", "WR")
    flags = r.get("flags") or []
    sig = {}; why = []; against = []

    # --- signals (each True/False with a sentence) ---
    role_ok = float(r.get("xtd_pg_shrunk") or 0) >= POS_ROLE_FLOOR.get(pos, 0.3)
    sig["role"] = role_ok
    if role_ok:
        if pos in ("RB", "QB") and (r.get("i5_carry_pg") or 0) >= 0.5: why.append(f"{r['i5_carry_pg']:.1f} goal-line carries per game")
        elif pos in ("RB", "QB"): why.append(f"{r.get('rz_carry_pg') or 0:.1f} red zone carries per game")
        elif (r.get("ez_tgt_pg") or 0) >= 0.7: why.append(f"{r['ez_tgt_pg']:.1f} end zone targets per game")
        else: why.append(f"{r.get('rz_tgt_pg') or 0:.1f} red zone targets per game")
    else: against.append(f"expected TDs only {float(r.get('xtd_pg_shrunk') or 0):.2f}/game — no real scoring role")

    d = r.get("def_rz_td_pct"); c_def = float(r.get("c_defense") or 0)
    sig["defense"] = (d is not None and d >= 0.52) or c_def >= 0.01
    if d is not None:
        (why if sig["defense"] else against).append(f"{r['opp']} allows TDs on {d:.0%} of red zone trips" + ("" if sig["defense"] else " — tough matchup"))

    imp = float(r.get("implied") or 0); sig["environment"] = imp >= 22
    (why if sig["environment"] else against).append(f"implied team total {imp:.0f}" + ("" if sig["environment"] else " — low-scoring script"))

    sig["availability"] = not any("QUESTIONABLE" in f for f in flags)
    if not sig["availability"]: against.append("listed questionable on the injury report")
    if (r.get("rz_share_open") or 0) > 0.1: why.append(f"{r['rz_share_open']:.0%} of the position's red zone work opened by injuries")

    falling = any("falling" in f for f in flags); rising = any("rising" in f for f in flags)
    sig["trend"] = not falling
    if falling: against.append("role trending down over the last two games")
    if rising: why.append("role trending up over the last two games")

    hot = (r.get("hit_l5") is not None and r.get("xtd_l5") is not None and (r["hit_l5"] - r["xtd_l5"]) >= 1.5)
    sig["not_a_streak"] = not hot
    if hot: against.append(f"scored in {int(r['hit_l5'])} of last {int(r['n_l5'])} on {r['xtd_l5']:.1f} expected — streak, not role")

    cert = r.get("certainty_label") or "medium"
    sig["certainty"] = cert != "low"
    if cert == "low": against.append("low certainty: committee, rookie, or role in flux")

    greens = sum(sig.values()); n = len(sig)

    out = dict(gsis_id=r["gsis_id"], game_id=r["game_id"], player=r["player"], position=pos, team=r["team"], opp=r["opp"],
               p_model=round(p, 3), fair_odds=int(r["fair_odds"]), signals=sig, greens=greens, n_signals=n,
               reasons_for=why[:4], reasons_against=against[:4], book=book, price=price)

    # --- value ---
    if price is None:
        out.update(tier="No price", edge=None, ev=None, stake=0, headline=f"Fair {int(r['fair_odds']):+d}. Needs a book price to evaluate.")
        return out
    ip = implied(price); edge = p - ip; dec = decimal(price)
    ev = p * (dec - 1) - (1 - p)                       # per 1 unit
    b = dec - 1; kelly = max(0.0, (p * b - (1 - p)) / b); stake = round(0.25 * kelly, 3)   # quarter Kelly
    out.update(edge=round(edge, 3), ev=round(ev, 3), stake=stake, implied_book=round(ip, 3))

    # --- sanity vs the market ---
    # Four sportsbooks pricing a player at 6% when we say 24% means our sample is thin, not that
    # we found 18 points of edge. Measured on 2026 week 2: every such "edge" was a one-touch artefact.
    ratio = p / ip if ip > 0 else 99
    touches = float(r.get("touches_recent") or ((r.get("rz_tgt_pg") or 0) + (r.get("rz_carry_pg") or 0)) * max(1, r.get("games") or 1) * 4)
    thin = (r.get("certainty_label") == "low") or touches < MIN_TOUCHES
    model_far_above = ratio >= MARKET_RATIO_MAX
    sig["market_agrees"] = not (model_far_above and thin)
    if not sig["market_agrees"]:
        against.append(f"we say {p:.0%}, the market says {ip:.0%} on thin usage — that gap is our uncertainty, not an edge")
    greens = sum(sig.values()); n = len(sig)
    out.update(signals=sig, greens=greens, n_signals=n, reasons_against=against[:4], market_ratio=round(float(ratio), 2))

    # --- decision ---
    hard_no = (not sig["availability"]) or falling or (not sig["role"]) or (not sig["market_agrees"])
    if hard_no: tier = "Pass"
    elif edge >= MIN_EDGE["Bet"] and greens >= n - 1 and cert == "high": tier = "Bet"
    elif edge >= MIN_EDGE["Bet"] and greens >= n - 2: tier = "Lean"
    elif edge >= MIN_EDGE["Lean"] and greens >= n - 1: tier = "Lean"
    else: tier = "Pass"
    # price sanity: don't Bet anything shorter than -250 or longer than +600 regardless
    if tier != "Pass" and (price <= -250 or price >= 600): tier = "Lean" if tier == "Bet" else tier; against.append("price outside the range where the model is calibrated")

    if tier == "Bet": head = f"Bet {price:+d} ({book}). Model {p:.0%} vs book {ip:.0%}: +{edge*100:.1f} pts edge, {greens}/{n} signals green."
    elif tier == "Lean": head = f"Lean {price:+d} ({book}). +{edge*100:.1f} pts edge but " + ("only " if greens < n - 1 else "") + f"{greens}/{n} signals green" + (", certainty not high" if cert != "high" else "") + "."
    elif edge < MIN_EDGE["Lean"]: head = f"Pass at {price:+d}. Model {p:.0%} vs book {ip:.0%}: no edge — would need {int(r['fair_odds']):+d} or better."
    elif not sig["market_agrees"]: head = f"Pass at {price:+d}. We say {p:.0%}, four books say {ip:.0%} — on this little usage the market is the better estimate."
    else: head = f"Pass at {price:+d} despite +{edge*100:.1f} pts edge: " + (against[0] if against else "signals disagree") + "."
    out.update(tier=tier, headline=head)
    return out

def rank(cards: list[dict]) -> list[dict]:
    order = {"Bet": 0, "Lean": 1, "Pass": 2, "No price": 3}
    return sorted(cards, key=lambda c: (order[c["tier"]], -(c["ev"] or -9), -(c["greens"] or 0)))
