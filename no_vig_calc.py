from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterable, List, Optional


def _choose_reference_market(
    market_data: Dict[str, Dict[str, float]],
    reference_books: Iterable[str],
) -> Optional[str]:
    """Return the first reference bookmaker present in this market."""
    for ref in reference_books:
        if ref in market_data:
            return ref
    return None


def _compute_no_vig_probabilities(odds: Dict[str, float]) -> Dict[str, float]:
    """Compute normalized no-vig probabilities from offered odds."""
    inv_probs: Dict[str, float] = {}
    for outcome, price in odds.items():
        if price <= 0:
            continue
        inv_probs[outcome] = 1.0 / price

    total = sum(inv_probs.values())
    if total == 0:
        return {}

    return {outcome: inv / total for outcome, inv in inv_probs.items()}


def compute_fair_and_no_vig(
    all_matches: List[Dict[str, Any]],
    reference_books: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Compute fair (no-vig) probabilities, fair odds and margin for all valid markets.
    Returns list of dicts with:
      - match
      - market_code
      - outcome
      - reference_book
      - fair_probability
      - no_vig_odds
      - margin
    """
    if reference_books is None:
        reference_books = [
            "Pinnacle"

        ]

    results: List[Dict[str, Any]] = []

    for m in all_matches:
        match_key = m.get("match")
        markets = m.get("markets", {})
        if not match_key or not markets:
            continue

        for market_code, market_data in markets.items():

            # 1) SELECT REFERENCE BOOKMAKER
            ref_book = _choose_reference_market(market_data, reference_books)
            if not ref_book:
                continue

            ref_odds = market_data[ref_book]
            if not ref_odds:
                continue

            # 2) COMPUTE NO-VIG PROBABILITIES
            fair_probs = _compute_no_vig_probabilities(ref_odds)
            if not fair_probs:
                continue

            # 3) COMPUTE MARGIN = sum(1/odds) - 1
            inv_sum = 0.0
            for price in ref_odds.values():
                if price > 0:
                    inv_sum += 1.0 / price
            margin = inv_sum - 1.0

            # 4) BUILD RESULT ROWS
            for outcome, fair_prob in fair_probs.items():
                no_vig_odds = 1.0 / fair_prob

                results.append(
                    {
                        "match": match_key,
                        "market_code": market_code,
                        "outcome": outcome,
                        "reference_book": ref_book,
                        "fair_probability": fair_prob,
                        "no_vig_odds": no_vig_odds,
                        "margin": margin,
                    }
                )

    return results


# -------------------------------
# OPTIONAL CLI SUPPORT (unchanged)
# -------------------------------

def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: List[str]) -> None:
    if len(argv) != 2:
        print("Usage: python no_vig_calc.py <all_matches_json_path>", file=sys.stderr)
        return

    all_matches_path = argv[1]
    try:
        all_matches = _read_json(all_matches_path)
    except Exception as e:
        print(f"Failed to read all_matches: {e}", file=sys.stderr)
        return

    fair_results = compute_fair_and_no_vig(all_matches)

    fair_probs_output = []
    for item in fair_results:
        fair_probs_output.append(
            {
                "match": item["match"],
                "market_code": item["market_code"],
                "outcome": item["outcome"],
                "reference_book": item["reference_book"],
                "fair_probability": item["fair_probability"],
                "no_vig_odds": item["no_vig_odds"],
                "margin": item["margin"],
            }
        )

    try:
        _write_json("fair_probs.json", fair_probs_output)
        print(f"Saved fair_probs.json with {len(fair_results)} records.")
    except Exception as e:
        print(f"Error writing JSON: {e}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
