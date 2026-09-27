"""Parlay maths that doesn't lie about correlation.

Multiplying probabilities assumes the legs are independent. Anytime-TD legs usually aren't:

  * two players on the same offence compete for the same red zone trips — one scoring makes it
    slightly *less* likely the other does (negative correlation)
  * everyone in a shootout benefits together — a game that goes 38-31 lifts every leg in it
    (positive correlation, through the game total)

The second effect is larger. Net: same-game parlays hit more often than the naive product says,
which is exactly why sportsbooks price same-game parlays with their own correlation model and
a much bigger hold. Knowing the direction is the point — it stops you thinking a 4-leg SGP at
+900 is a steal when the honest number is +700 and the vig is 20%.

We estimate the joint probability by simulation: draw a game-level scoring factor shared by every
leg in that game, scale each leg's probability by it, then compete teammates for the same scores.
"""
from __future__ import annotations
import numpy as np

GAME_SD = 0.34        # how much a game's scoring environment swings around its expectation
TEAMMATE_DRAG = 0.12  # how much one teammate scoring suppresses another's chance

def implied(price: float) -> float:
    return (-price) / (-price + 100) if price < 0 else 100 / (price + 100)

def decimal(price: float) -> float:
    return 1 + (price / 100 if price > 0 else 100 / -price)

def american(p: float) -> float:
    return -100 * p / (1 - p) if p >= .5 else 100 * (1 - p) / p

def evaluate(legs: list[dict], n: int = 40_000, seed: int = 7) -> dict:
    """legs: [{player, team, game_id, p_model, price}]. Returns book price vs our joint estimate."""
    if not legs: return {"error": "no legs"}
    rng = np.random.default_rng(seed)
    p = np.array([max(1e-6, min(.999, float(l["p_model"]))) for l in legs])
    games = [l.get("game_id", "") for l in legs]
    teams = [l.get("team", "") for l in legs]
    uniq = {g: i for i, g in enumerate(dict.fromkeys(games))}
    gidx = np.array([uniq[g] for g in games])

    # a shared scoring factor per game, lognormal with mean 1
    z = rng.normal(0, GAME_SD, size=(n, len(uniq)))
    factor = np.exp(z - GAME_SD ** 2 / 2)[:, gidx]
    adj = np.clip(p * factor, 1e-6, .999)

    hit = rng.random((n, len(legs))) < adj
    # teammates compete: if one already scored, shade the others down
    for t in set(teams):
        cols = [i for i, x in enumerate(teams) if x == t and t]
        if len(cols) < 2: continue
        for i in cols:
            others = [c for c in cols if c != i]
            drag = hit[:, others].sum(axis=1) * TEAMMATE_DRAG
            keep = rng.random(n) > np.clip(drag, 0, .9)
            hit[:, i] &= keep

    joint_sim = float(hit.all(axis=1).mean())
    joint_ind = float(np.prod(p))
    dec = float(np.prod([decimal(l["price"]) for l in legs])) if all(l.get("price") for l in legs) else None
    book_p = 1 / dec if dec else None
    out = dict(
        legs=len(legs), games=len(uniq),
        joint_independent=round(joint_ind, 4), joint_correlated=round(joint_sim, 4),
        correlation_effect=round(joint_sim - joint_ind, 4),
        fair_odds_independent=int(round(american(joint_ind))) if joint_ind > 0 else None,
        fair_odds_correlated=int(round(american(joint_sim))) if joint_sim > 0 else None,
    )
    if dec:
        out.update(book_decimal=round(dec, 2), book_odds=int(round((dec - 1) * 100)),
                   book_implied=round(book_p, 4),
                   edge=round(joint_sim - book_p, 4),
                   ev_per_unit=round(joint_sim * (dec - 1) - (1 - joint_sim), 4),
                   hold=round(sum(implied(l["price"]) for l in legs) / len(legs) - 1 / (1 + sum(implied(l["price"]) - 1 for l in legs) / len(legs)), 4) if False else None)
        singles_vig = float(np.prod([implied(l["price"]) for l in legs]))
        out["vig_compounded"] = round(singles_vig - joint_ind, 4)
    dup_team = len(teams) != len(set(teams))
    same_game = len(uniq) < len(legs)
    if dup_team:
        out["note"] = ("Two or more legs are on the same offence. They compete for the same red zone trips, so the "
                       "honest joint probability is LOWER than multiplying them together — teammates cannibalise "
                       "each other even though a shootout helps them both.")
    elif same_game:
        out["note"] = ("Legs are in the same game but on opposing teams. A high-scoring game lifts both, so the honest "
                       "number is HIGHER than the naive product — which is also why books price same-game parlays with "
                       "a bigger hold.")
    else:
        out["note"] = ("Legs are in different games, so independence is a fair assumption. Note the vig compounds with "
                       "every leg you add.")
    out["shape"] = "same team" if dup_team else ("same game, opposing" if same_game else "independent")
    return out
