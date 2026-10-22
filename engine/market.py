"""Market probability: what the books, taken together, say — and how much of it to trust.

Why this exists (measured on 2026 week 2, the only week with raw odds in the repo): the model ranks
players exactly as well as the book (AUC .709 vs .713) and the two agree on how many touchdowns a game
produces. Where they disagree on *who*, the book has been right. Every player we priced 3+ points above
the market that week went 2-for-38 at an average of +941. So the number we bet on is a blend, weighted
by how much evidence sits behind our number, and "edge" is that blend minus the price — not model minus price.

    p_market  = per-book implied probabilities averaged, then devigged game by game
    p_bet     = w(certainty) * p_model + (1 - w) * p_market
    edge      = p_bet - implied(best price)

Devig: anytime-TD markets have no "No" side to normalise against, so we scale each game's implied
probabilities so they sum to the model's own calibrated count of scorers for that game (the model's
game totals match reality within noise: predicted 20.8% vs 21.4% actual over weeks 1-6). Only ever
scales down.
"""
from __future__ import annotations
from collections import defaultdict

# Fitted by log-loss on 2026 weeks 2-6 (1,367 priced player-games), shrunk a little toward 0.5. The sign surprised
# us: on established starters the book is the better number (fit 0.1); on backups, rookies and committees our
# red-zone usage model is (fit 0.9). Books price the top of the depth chart carefully and the bottom lazily.
MODEL_WEIGHT = {"high": 0.25, "medium": 0.35, "low": 0.70}   # share of the bet number that is ours
DEVIG_MIN, DEVIG_MAX = 0.70, 1.00                           # per-game scale on raw implied


def implied(price: float) -> float:
    return (-price) / (-price + 100) if price < 0 else 100 / (price + 100)


def consensus(prices_by_player: dict[tuple, dict[str, float]]) -> dict[tuple, float]:
    """{(gsis_id, game_id): {book: american}} -> {(gsis_id, game_id): mean raw implied across books}."""
    out = {}
    for k, books in prices_by_player.items():
        ps = [implied(p) for p in books.values() if p]
        if ps: out[k] = sum(ps) / len(ps)
    return out


def devig_by_game(raw: dict[tuple, float], board: list[dict]) -> tuple[dict[tuple, float], dict[str, float]]:
    """Scale each game's raw implied so it sums to the model's expected number of scorers for that game.
    Returns (p_market by player, scale by game)."""
    model_sum = defaultdict(float); imp_sum = defaultdict(float)
    for r in board:
        k = (r["gsis_id"], r["game_id"])
        if k in raw:
            model_sum[r["game_id"]] += float(r["p_model"]); imp_sum[r["game_id"]] += raw[k]
    scale = {g: max(DEVIG_MIN, min(DEVIG_MAX, model_sum[g] / imp_sum[g])) if imp_sum[g] > 0 else 1.0 for g in imp_sum}
    out = {k: v * scale.get(k[1], 1.0) for k, v in raw.items()}
    return out, scale


def blend(p_model: float, p_market: float | None, certainty_label: str | None) -> tuple[float, float]:
    """-> (p_bet, model_weight). With no market number the model stands alone."""
    if p_market is None: return float(p_model), 1.0
    w = MODEL_WEIGHT.get(certainty_label or "medium", 0.35)
    return w * float(p_model) + (1 - w) * float(p_market), w


def market_for_week(odds_rows, board: list[dict]) -> dict[str, float]:
    """odds rows (gsis_id, game_id, book, price, fetched_at — newest first) -> {"gsis|game": p_market}.
    Takes the latest price per (player, book) before averaging."""
    latest: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in odds_rows:
        k = (r["gsis_id"], r["game_id"])
        if r["book"] not in latest[k]: latest[k][r["book"]] = r["price"]
    raw = consensus(latest)
    p_market, _ = devig_by_game(raw, board)
    return {f"{g}|{ga}": round(v, 4) for (g, ga), v in p_market.items()}
