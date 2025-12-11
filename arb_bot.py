# arb_bot.py

from typing import Dict, Any, List

MIN_ROI_THRESHOLD = 0.5  # vain ROI > 1% kelpaa (voit säätää)


def find_arbitrage(all_matches: List[Dict[str, Any]], total_stake=100.0):
    arbs = []

    for match in all_matches:
        for mk, md in match["markets"].items():

            # selvitä kaikki mahdolliset lopputulemat
            outcomes = set()
            for odds in md.values():
                outcomes.update(odds.keys())
            outcomes = list(outcomes)

            if len(outcomes) < 2:
                continue

            # hae paras kerroin jokaiselle lopputulokselle
            best = {o: {"book": None, "odds": 0.0} for o in outcomes}

            for b, ods in md.items():
                for o in outcomes:
                    if o in ods and ods[o] > best[o]["odds"]:
                        best[o] = {"book": b, "odds": ods[o]}

            # jos jotain kerrointa puuttuu
            if any(best[o]["odds"] <= 0 for o in outcomes):
                continue

            # arbitraasiehto
            inv_sum = sum(1 / best[o]["odds"] for o in outcomes)
            if inv_sum >= 1:
                continue

            # arbitraasin tuotto
            payout = total_stake / inv_sum
            profit = payout - total_stake
            roi = (profit / total_stake) * 100

            if roi < MIN_ROI_THRESHOLD:
                continue

            # ⭐ LASKETAAN PANOSJAKO ARBITRAASILLE
            stakes = {}
            for o in outcomes:
                odds_i = best[o]["odds"]
                stakes[o] = total_stake / (odds_i * inv_sum)

            arbs.append({
                "match": match["match"],
                "sport": match["sport"],
                "start_time": match["start_time"],
                "market": mk,
                "best_odds": best,
                "roi": roi,
                "profit": profit,
                "total_stake": total_stake,
                "stakes": stakes,        # ⭐ panokset lisätty
            })


    return arbs

