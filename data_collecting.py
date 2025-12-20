import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

load_dotenv()  # lataa .env-tiedoston sisällön
SPORT_KEYS: List[str] = [
    "EPL"
]

API_KEY: str = os.environ.get("ODDS_API_KEY")
REGIONS: str = os.environ.get("Regions", "eu")
BASE_MARKETS: str = os.environ.get( "h2h")

# ---------- BAD BOOKMAKERS (POISTETAAN KOKONAAN) ----------
BAD_BOOKMAKERS: List[str] = [
    "1xBet", "BetUS", "MyBookie.ag", "Bovada", "BetOnline.ag",
    "LowVig.ag", "GTbets", "SportsBetting.ag", "BetRivers",
    "SportsBet", "Codere", "Codere (IT)", "PMU (FR)",
    "Everygame", "Suprabets",
]

# ---------- UNIBET ALIAS LISTA ----------
UNIBET_NAMES = [
    "Unibet", "Unibet (SE)", "Unibet (NL)", "Unibet (FR)",
    "Unibet (DK)", "Unibet (FI)", "Unibet (NO)"
]

PREFERRED_UNIBET = "Unibet (SE)"


def is_bad_bookmaker(name: Optional[str]) -> bool:
    if not name:
        return False
    return name.strip() in BAD_BOOKMAKERS


# Muutetaan kaikki Unibet-versiot → "Unibet"
def normalize_bookmaker_name(name: str) -> str:
    name = name.strip()
    if name in UNIBET_NAMES:
        return "Unibet"
    return name


def fetch_events(sport: str):
    url = f"https://api.sportsdata.io/v4/soccer/odds/json/GameOddsByDate/{sport}/{datetime.today().strftime('%Y-%m-%d')}"
    headers = {"Ocp-Apim-Subscription-Key": API_KEY}
    r = requests.get(url, headers=headers, timeout=12)
    r.raise_for_status()
    return r.json()


def fetch_base_odds(sport: str):
    return []  # ei tarvita tässä versiolla


def combine_data(events, odds):
    out = {}
    for b in events:
        event_id = b.get("GameId")
        out[event_id] = {"event": b, "extra": None}
    return out


def build_matches_for_sport(combined, sport):
    matches = []
    now = datetime.now(timezone.utc)

    for _id, data in combined.items():
        evt = data["event"]
        home = evt.get("HomeTeam")
        away = evt.get("AwayTeam")
        if not home or not away:
            continue

        ts = evt.get("DateTime")
        if not ts:
            continue

        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except:
            continue

        if dt <= now:
            continue

        rec = {
            "match": f"{home} vs {away}",
            "home": home,
            "away": away,
            "start_time": dt.isoformat(),
            "sport": sport,
            "markets": {},
        }

        for bookmaker in evt.get("PregameOdds", []):
            book_name = bookmaker.get("Sportsbook")
            if is_bad_bookmaker(book_name):
                continue

            book_name = normalize_bookmaker_name(book_name)
            market_code = "h2h"
            rec["markets"].setdefault(market_code, {}).setdefault(book_name, {})

            if bookmaker.get("HomeTeamMoneyLine"):
                rec["markets"][market_code][book_name]["home"] = float(bookmaker["HomeTeamMoneyLine"])
            if bookmaker.get("AwayTeamMoneyLine"):
                rec["markets"][market_code][book_name]["away"] = float(bookmaker["AwayTeamMoneyLine"])
            if bookmaker.get("DrawMoneyLine"):
                rec["markets"][market_code][book_name]["draw"] = float(bookmaker["DrawMoneyLine"])

        if rec["markets"]:
            matches.append(rec)

    return matches


def build_all_matches_once():
    all_matches = []

    for sport in SPORT_KEYS:
        try:
            events = fetch_events(sport)
            odds = fetch_base_odds(sport)
            combined = combine_data(events, odds)
            all_matches.extend(build_matches_for_sport(combined, sport))
        except Exception as e:
            print(f"Error loading data for {sport}: {e}")

    return all_matches
