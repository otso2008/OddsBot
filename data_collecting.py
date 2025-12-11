# data_loader.py

import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

import os

from fastapi_backend import API_KEY

load_dotenv()  # lataa .env-tiedoston sisällön
SPORT_KEYS: List[str] = [
    "soccer_epl",
    "soccer_spain_la_liga"
]


API_KEY: str = os.environ.get("ODDS_API_KEY")
REGIONS: str = os.environ.get("Regions", "eu",)
BASE_MARKETS: str = os.environ.get("BASE_MARKETS", "h2h,totals,spreads")

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
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/events/?apiKey={API_KEY}"
    try:
        print(f"[DEBUG] Haetaan events: {sport}")
        r = requests.get(url, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ERROR] Events haku epäonnistui sportissa {sport}: {e}")
        return []


def fetch_base_odds(sport: str):
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?"
        f"apiKey={API_KEY}&markets={BASE_MARKETS}&oddsFormat=decimal&regions={REGIONS}"
    )
    try:
        print(f"[DEBUG] Haetaan odds: {sport}")
        r = requests.get(url, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ERROR] Odds haku epäonnistui sportissa {sport}: {e}")
        return []



def fetch_base_odds(sport: str):
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
        f"?apiKey={API_KEY}&markets={BASE_MARKETS}&oddsFormat=decimal&regions={REGIONS}"
    )
    r = requests.get(url, timeout=12)
    r.raise_for_status()
    return r.json()


def combine_data(events, odds):
    out = {}
    for b in odds:
        out[b["id"]] = {"event": b, "extra": None}

    for e in events:
        if e["id"] not in out:
            out[e["id"]] = {
                "event": {
                    "id": e["id"],
                    "home_team": e.get("home_team"),
                    "away_team": e.get("away_team"),
                    "commence_time": e.get("commence_time"),
                    "bookmakers": [],
                },
                "extra": None
            }
        out[e["id"]]["extra"] = e
    return out


def build_matches_for_sport(combined, sport):
    matches = []
    now = datetime.now(timezone.utc)

    for _id, data in combined.items():
        evt = data["event"]
        home, away = evt.get("home_team"), evt.get("away_team")
        if not home or not away:
            continue

        ts = evt.get("commence_time")
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

        for book in evt.get("bookmakers", []):
            raw_name = (book.get("title") or book.get("key") or "").strip()

            # 1. OHITA BAD BOOKMAKERS JO TÄSSÄ VAIHEESSA
            if is_bad_bookmaker(raw_name):
                continue

            # 2. NORMALISOI UNIBET
            name = normalize_bookmaker_name(raw_name)

            for m in book.get("markets", []):
                key = m.get("key")
                outcomes = m.get("outcomes", [])
                if not outcomes:
                    continue

                # ---------- H2H ----------
                if key in ("h2h", "h2h_3_way"):
                    om = {o["name"]: float(o["price"]) for o in outcomes if "price" in o}

                    if home in om and away in om:
                        norm = {"home": om[home], "away": om[away]}
                        if "Draw" in om:
                            norm["draw"] = om["Draw"]

                        rec["markets"].setdefault("h2h", {})[name] = norm

                # ---------- TOTALS / SPREADS / ALTERNATES ----------
                if key in (
                    "totals", "alternate_totals", "team_totals",
                    "alternate_team_totals", "spreads", "alternate_spreads"
                ):
                    for o in outcomes:
                        p = o.get("price")
                        pt = o.get("point")
                        nm = o.get("name", "")
                        if p is None or pt is None:
                            continue

                        mk = f"over_under_{str(pt).replace('.', '_')}"
                        entry = rec["markets"].setdefault(mk, {}).setdefault(name, {})

                        if "over" in nm.lower():
                            entry["over"] = float(p)
                        elif "under" in nm.lower():
                            entry["under"] = float(p)

        # Poista vajaat totals-linjat
        for k in list(rec["markets"].keys()):
            if k.startswith("over_under_"):
                for bn in list(rec["markets"][k].keys()):
                    if "over" not in rec["markets"][k][bn] or "under" not in rec["markets"][k][bn]:
                        del rec["markets"][k][bn]

                if not rec["markets"][k]:
                    del rec["markets"][k]

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
        except:
            continue

    return all_matches
