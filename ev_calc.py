from typing import List, Dict, Any
from datetime import datetime, timedelta, timezone

def calculate_ev(
    all_matches: List[Dict[str, Any]],
    no_vig_data: List[Dict[str, Any]],
    min_ev_percent: float = 3
) -> List[Dict[str, Any]]:

    # --- 0. Muodosta lookup no-vig datasta ---
    no_vig_lookup = {}
    for item in no_vig_data:
        key = (item["match"], item["market_code"], item["outcome"])
        no_vig_lookup[key] = item

    opportunities = []
    high_ev_opportunities = {}
    now = datetime.now(timezone.utc)

    for match in all_matches:
        match_name = match["match"]

        # --- 1. Ota mukaan vain seuraavan 12h alkavat ottelut ---
        try:
            start_dt = datetime.fromisoformat(match["start_time"].replace("Z", "+00:00"))
        except Exception:
            continue

        if start_dt - now > timedelta(hours=48):
            continue

        markets = match.get("markets", {})

        # --- 2. Käydään markkinat läpi ---
        for market_code, market_data in markets.items():

            # --- 3. Käydään kaikki bookkerit ja niiden outcomes ---
            for book, odds in market_data.items():
                for outcome, offered_odds in odds.items():

                    key = (match_name, market_code, outcome)
                    if key not in no_vig_lookup:
                        continue  # ei no-vig dataa

                    fair_prob = no_vig_lookup[key]["fair_probability"]
                    ref_book = no_vig_lookup[key]["reference_book"]
                    ref_odds = no_vig_lookup[key]["no_vig_odds"]

                    # --- 4. EV-laskenta ---
                    ev_fraction = fair_prob * offered_odds - 1
                    ev_pct = ev_fraction * 100

                    result = {
                        "match": match_name,
                        "sport": match["sport"],
                        "start_time": match["start_time"],
                        "market": market_code,
                        "outcome": outcome,
                        "reference_book": ref_book,
                        "reference_odds": ref_odds,
                        "probability": fair_prob,
                        "book": book,
                        "offered_odds": offered_odds,
                        "ev_percent": ev_pct
                    }

                    if ev_pct >= min_ev_percent:
                        opportunities.append(result)

                    if ev_pct >= 5:
                        existing = high_ev_opportunities.get(key)
                        if not existing or ev_pct > existing["ev_percent"]:
                            high_ev_opportunities[key] = result

    return opportunities, list(high_ev_opportunities.values())
