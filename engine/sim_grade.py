"""Grade last week's published simulation against what happened — the sim's own scorecard.

    python3 -m engine.sim_grade --season 2026 --week 7

Reads data/sim_w{week}.json exactly as it was served (not a re-simulation) and the week's play-by-play, and reports:
game level — sim vs market vs actual on totals and margins, how often the sim's favourite won, home-win Brier;
player level — projected carries/targets vs actual (MAE, correlation) for everyone the sim gave a touch, and
P(TD) Brier/AUC vs the model's number. score_card calls this and stores the result under "sim" in the week's
scorecard so the Record page can show whether the usage model is getting sharper or duller.
"""
from __future__ import annotations
import argparse, json, os, pathlib, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import env  # noqa: F401
from engine import ingest, model as M
D = pathlib.Path(os.environ.get("SIXPTS_DATA", "data"))

def grade(season: int, week: int, pbp: pd.DataFrame | None = None) -> dict | None:
    f = D / f"sim_w{week}.json"
    if not f.exists(): return None
    sim = json.load(open(f))
    p = pbp if pbp is not None else M.prep_pbp(ingest.load_pbp(season, force=True))
    p = p[p.week == week]
    if p.empty: return None
    final = p.groupby("game_id").agg(h=("home_score", "max"), a=("away_score", "max"))
    scored = set(zip(p[p.touchdown == 1].td_player_id, p[p.touchdown == 1].game_id))
    played = set(p.receiver_player_id.dropna()) | set(p.rusher_player_id.dropna()) | set(p.passer_player_id.dropna())
    car = p[p.rush_attempt == 1].groupby("rusher_player_id").size(); tgt = p[p.pass_attempt == 1].groupby("receiver_player_id").size()
    G, P = [], []
    for gid, g in sim["games"].items():
        if gid not in final.index: continue
        h, a = float(final.loc[gid, "h"]), float(final.loc[gid, "a"])
        G.append(dict(game=gid, sim_total=g["mean_total"], mkt_total=g["total"], act_total=h + a,
                      sim_margin=g["median_home"] - g["median_away"], mkt_margin=-g["spread"], act_margin=h - a,
                      p_home=g["p_home"], home_won=float(h > a), p_over=g["p_over"], over=float(h + a > g["total"]), p_cover=g["p_home_cover"], covered=float(h - a + g["spread"] > 0)))
        for x in g["players"]:
            if x["gsis_id"] not in played: continue
            P.append(dict(player=x["player"], team=x["team"], pos=x["position"], p_td=x["p_td"], p_gbm=x.get("p_gbm"), y=int((x["gsis_id"], gid) in scored),
                          car_sim=x["car"], car_act=float(car.get(x["gsis_id"], 0)), tgt_sim=x.get("tgt", x["rec"]), tgt_act=float(tgt.get(x["gsis_id"], 0))))
    if not G: return None
    G, P = pd.DataFrame(G), pd.DataFrame(P)
    out = dict(week=week, games=len(G),
               total_mae_sim=round(float((G.sim_total - G.act_total).abs().mean()), 2), total_mae_market=round(float((G.mkt_total - G.act_total).abs().mean()), 2),
               margin_mae_sim=round(float((G.sim_margin - G.act_margin).abs().mean()), 2), margin_mae_market=round(float((G.mkt_margin - G.act_margin).abs().mean()), 2),
               favourite_won=round(float(((G.p_home > 0.5) == (G.home_won == 1)).mean()), 3), brier_home=round(float(((G.p_home - G.home_won) ** 2).mean()), 4),
               over_hit=round(float(((G.p_over > 0.5) == (G.over == 1)).mean()), 3), cover_hit=round(float(((G.p_cover > 0.5) == (G.covered == 1)).mean()), 3))
    if len(P):
        U = P[(P.car_sim + P.tgt_sim) >= 1.0]
        out.update(players=len(P), usage_n=len(U), usage_mae_car=round(float((U.car_sim - U.car_act).abs().mean()), 2), usage_mae_tgt=round(float((U.tgt_sim - U.tgt_act).abs().mean()), 2),
                   usage_corr_car=round(float(np.corrcoef(U.car_sim, U.car_act)[0, 1]), 3) if len(U) > 2 else None, usage_corr_tgt=round(float(np.corrcoef(U.tgt_sim, U.tgt_act)[0, 1]), 3) if len(U) > 2 else None,
                   td_brier_sim=round(float(((P.p_td - P.y) ** 2).mean()), 4), td_rate_sim=round(float(P.p_td.mean()), 3), td_rate_actual=round(float(P.y.mean()), 3))
        P2 = P.dropna(subset=["p_gbm"])
        if len(P2) and P2.y.nunique() == 2:
            from sklearn.metrics import roc_auc_score
            out["td_auc_sim"] = round(float(roc_auc_score(P2.y, P2.p_td)), 3); out["td_auc_model"] = round(float(roc_auc_score(P2.y, P2.p_gbm)), 3); out["td_brier_model"] = round(float(((P2.p_gbm - P2.y) ** 2).mean()), 4)
        # the biggest usage misses, so a human can see what the model got wrong (injury? role change? blowout?)
        U = U.assign(miss=(U.car_sim + U.tgt_sim) - (U.car_act + U.tgt_act))
        out["usage_misses"] = [dict(player=r.player, team=r.team, pos=r.pos, projected=round(r.car_sim + r.tgt_sim, 1), actual=int(r.car_act + r.tgt_act)) for r in U.sort_values("miss", key=abs, ascending=False).head(8).itertuples()]
    return out

def main(season: int, week: int):
    out = grade(season, week)
    if not out: print(f"nothing to grade for week {week}"); return
    print(f"Sim grade, week {week}: {out['games']} games")
    print(f"  totals MAE   sim {out['total_mae_sim']}  market {out['total_mae_market']}")
    print(f"  margins MAE  sim {out['margin_mae_sim']}  market {out['margin_mae_market']}   favourite won {out['favourite_won']:.0%}  home Brier {out['brier_home']}")
    if "usage_n" in out:
        print(f"  usage ({out['usage_n']} players): carries MAE {out['usage_mae_car']} corr {out['usage_corr_car']} · targets MAE {out['usage_mae_tgt']} corr {out['usage_corr_tgt']}")
        print(f"  P(TD): sim Brier {out['td_brier_sim']} (rate {out['td_rate_sim']:.3f} vs actual {out['td_rate_actual']:.3f})" + (f" · AUC sim {out['td_auc_sim']} model {out['td_auc_model']}" if "td_auc_sim" in out else ""))
        print("  biggest usage misses: " + "; ".join(f"{m['player']} {m['projected']}→{m['actual']}" for m in out["usage_misses"]))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--season", type=int, default=2026); ap.add_argument("--week", type=int, required=True)
    a = ap.parse_args(); main(a.season, a.week)
