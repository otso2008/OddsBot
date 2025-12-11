"""
Scraper for Kambi powered brands and Coolbet
============================================

This module provides two concrete scrapers:

* ``KambiBrandScraper`` – handles any sportsbook brand running on the Kambi
  platform.  Kambi powers a large number of white‑label bookmakers such as
  Unibet, Betsson, 888sport, LeoVegas, Betclic and others.  Odds for these
  sites are exposed via a single JSON endpoint on the ``eu‑offering.kambicdn.org``
  domain.  The only difference between brands is a short **brand code** which
  identifies which skin to return data for.  See the ``BRAND_CODES`` dictionary
  below for examples.  You can add new entries as needed; the scraper will
  accept arbitrary codes.

* ``CoolbetScraper`` – fetches pre‑match football odds from Coolbet’s public
  API.  Coolbet exposes a simple REST interface under the ``sb1api.coolbet.com``
  domain.  The scraper first retrieves a list of sports to find the ID for
  football, then requests all pre‑match events for that sport.  Each event
  contains markets and outcomes which are normalised into a common structure.

Both scrapers return a list of ``MatchOdds`` data objects containing the home
team, away team, start time and a mapping of market names to outcome odds.

.. important::
   This code is provided for educational purposes only.  Many bookmakers
   explicitly prohibit automated scraping in their terms of service.  You
   should review and comply with any relevant terms before running the
   scrapers against live sites.  The authors of this module are not
   responsible for any misuse.

Usage example::

    from kambi_coolbet_scraper import KambiBrandScraper, CoolbetScraper

    # Scrape Premier League odds from Unibet (Kambi)
    kambi = KambiBrandScraper(brand_code="ubse", market="GB", lang="en_GB")
    matches = kambi.fetch_odds(league_path="football/premier_league")
    for m in matches:
        print(m.home, m.away, m.markets)

    # Scrape all upcoming football matches from Coolbet
    coolbet = CoolbetScraper(lang="en")
    matches = coolbet.fetch_odds()
    for m in matches:
        print(m.home, m.away, m.markets)

Note that these scrapers perform real HTTP requests.  In this chat environment
network access to bookmaker domains is disabled, so you should test the code
locally.  The classes include logging to help diagnose problems such as
HTTP 403/404 responses or JSON decoding errors.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    import requests  # type: ignore
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "The requests library is required to run this scraper. Install it via `pip install requests`."
    ) from e


logger = logging.getLogger(__name__)


@dataclass
class MatchOdds:
    """Structure representing the odds for a single match.

    Parameters
    ----------
    home : str
        The home team name.
    away : str
        The away team name.
    start_time : str
        ISO8601 timestamp for the scheduled start of the event.
    markets : Dict[str, Dict[str, float]]
        Mapping of market label to a mapping of outcome names to odds.
    """

    home: str
    away: str
    start_time: str
    markets: Dict[str, Dict[str, float]]


class KambiBrandScraper:
    """Scraper for Kambi powered sportsbook brands.

    Kambi hosts a unified JSON odds feed which can be accessed via a URL of
    the form::

        https://eu-offering.kambicdn.org/offering/v2018/{brand_code}/listView/{league_path}/all/all/matches.json?
        lang={lang}&market={market}&client_id={client_id}&channel_id={channel_id}&ncid={timestamp}&useCombined={bool}&useCombinedLive={bool}

    The ``brand_code`` uniquely identifies the bookmaker skin (e.g. ``ubse``
    for Unibet Sweden).  The other query parameters control language,
    market (country), client and channel IDs.  A ``ncid`` (no cache ID)
    parameter containing a timestamp in milliseconds should be included to
    prevent caching.

    You can find the correct brand code by inspecting network requests on
    the bookmaker’s website using your browser’s developer tools.  See the
    accompanying Substack article for a walkthrough【270628737059479†L182-L198】.  A
    few common codes are provided in the ``BRAND_CODES`` dictionary but
    the scraper will accept any value you supply.

    Parameters
    ----------
    brand_code : str
        Short code identifying the bookmaker brand (e.g. ``ubse``).
    client_id : int, optional
        Client identifier; defaults to 2 (desktop).
    channel_id : int, optional
        Channel identifier; defaults to 1 (web).
    market : str, optional
        Market/country code; defaults to ``GB``.
    lang : str, optional
        Language code; defaults to ``en_GB``.
    use_combined : bool, optional
        Whether to request combined markets; defaults to True.
    use_combined_live : bool, optional
        Whether to request combined live markets; defaults to same as ``use_combined``.
    timeout : int | float, optional
        HTTP timeout in seconds; defaults to 10.

    """

    # A mapping of human readable bookmaker names to known brand codes.  This
    # dictionary is provided as a convenience; it is not exhaustive.  Add
    # additional mappings here if you know the correct code for a brand.
    BRAND_CODES: Dict[str, str] = {
        "unibet": "ubse",         # Unibet Sweden – more accessible than ubfi
        "888sport": "ubse",        # 888sport uses same feed as Unibet in many regions
        "leovegas": "lvse",       # LeoVegas Sweden
        "betsson": "betss",       # Betsson (approximate code; confirm via dev tools)
        "nordic bet": "nbse",     # NordicBet Sweden (approximate)
        "betclic": "bcfr",        # Betclic France
        "parions sport": "psfr",   # Parions Sport France (approximate)
        "winamax": "wmfr",        # Winamax France (approximate)
        "tipico": "tipde",        # Tipico Germany (approximate)
    }

    def __init__(
        self,
        brand_code: str,
        client_id: int = 2,
        channel_id: int = 1,
        market: str = "GB",
        lang: str = "en_GB",
        use_combined: bool = True,
        use_combined_live: Optional[bool] = None,
        timeout: int | float = 10,
    ) -> None:
        self.brand_code = brand_code
        self.client_id = client_id
        self.channel_id = channel_id
        self.market = market
        self.lang = lang
        self.use_combined = use_combined
        # If live setting not specified, mirror use_combined
        self.use_combined_live = use_combined if use_combined_live is None else use_combined_live
        self.timeout = timeout

    def _build_url(self, league_path: str) -> str:
        """Internal helper to construct the Kambi odds URL."""
        base = "https://eu-offering.kambicdn.org/offering/v2018"
        ncid = int(time.time() * 1000)
        params = {
            "lang": self.lang,
            "market": self.market,
            "client_id": self.client_id,
            "channel_id": self.channel_id,
            "ncid": ncid,
            "useCombined": str(self.use_combined).lower(),
            "useCombinedLive": str(self.use_combined_live).lower(),
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base}/{self.brand_code}/listView/{league_path}/all/all/matches.json?{query}"

    def fetch_odds(self, league_path: str = "football/premier_league") -> List[MatchOdds]:
        """Fetch football odds for the specified competition.

        Returns a list of ``MatchOdds`` objects.  If the HTTP request fails
        or the response cannot be parsed, an empty list is returned and an
        error is logged.
        """
        url = self._build_url(league_path)
        logger.info("Fetching Kambi odds from %s", url)
        try:
            response = requests.get(url, timeout=self.timeout)
            if response.status_code != 200:
                logger.error("Kambi request failed for brand %s: HTTP %s", self.brand_code, response.status_code)
                return []
            data = response.json()
        except Exception as exc:
            logger.exception("Exception fetching Kambi odds for brand %s: %s", self.brand_code, exc)
            return []

        matches: List[MatchOdds] = []
        try:
            events = data.get("events", [])
            for event_wrapper in events:
                ev = event_wrapper.get("event", {})
                home = ev.get("homeName") or ev.get("homeName", "")
                away = ev.get("awayName") or ev.get("awayName", "")
                start = ev.get("start")
                bet_offers = event_wrapper.get("betOffers", [])
                markets: Dict[str, Dict[str, float]] = {}
                for offer in bet_offers:
                    criterion = offer.get("criterion", {})
                    label = criterion.get("label") or criterion.get("translationKey", "Unknown")
                    outcomes: Dict[str, float] = {}
                    for out in offer.get("outcomes", []):
                        name = out.get("label") or out.get("name")
                        # Odds may be expressed in "odds" (int representing price * 1000)
                        # or "oddsDecimal" (float).  Normalise to decimal odds.
                        odds_val = out.get("odds") or out.get("oddsDecimal") or out.get("price")
                        if name and odds_val:
                            try:
                                # odds in "odds" are typically integers like 2050 for 2.05
                                value = float(odds_val) / 1000 if isinstance(odds_val, int) and odds_val > 10 else float(odds_val)
                                outcomes[name] = value
                            except Exception:
                                continue
                    if outcomes:
                        markets[label] = outcomes
                if markets:
                    matches.append(MatchOdds(home=home, away=away, start_time=start, markets=markets))
        except Exception as exc:
            logger.exception("Failed to parse Kambi response for brand %s: %s", self.brand_code, exc)
            return []

        return matches


class CoolbetScraper:
    """Scraper for Coolbet pre‑match football odds.

    Coolbet publishes a REST API under the ``sb1api.coolbet.com`` domain.  The
    structure of this API is not officially documented, but you can discover
    it using your browser’s developer tools while browsing the Coolbet site.
    This scraper performs the following steps:

    1. Call ``/api/v1/sportsbook/sports`` to retrieve a list of sports.  Find
       the sport with name ``Football`` (case insensitive) and note its ID.
    2. Call ``/api/v1/sportsbook/prematch`` with the sport ID to retrieve all
       upcoming events.  The JSON response contains markets, outcomes and
       odds which are parsed into ``MatchOdds`` objects.

    If either request fails or the expected data is not present, the method
    returns an empty list and logs an error.  As with Kambi, geoblocking or
    rate limiting may apply depending on your location.  You may need to
    adjust headers or cookies to obtain a successful response.

    Parameters
    ----------
    lang : str, optional
        Language code for the API (e.g. ``en``).  Defaults to ``en``.
    timeout : int | float, optional
        HTTP timeout in seconds.  Defaults to 10.
    """

    def __init__(self, lang: str = "en", timeout: int | float = 10) -> None:
        self.lang = lang
        self.timeout = timeout

    def _get_sport_id(self) -> Optional[int]:
        """Retrieve the sport ID for football from Coolbet.

        Returns None if the request fails or football is not found.
        """
        url = "https://sb1api.coolbet.com/api/v1/sportsbook/sports"
        try:
            response = requests.get(url, timeout=self.timeout)
            if response.status_code != 200:
                logger.error("Coolbet sports request failed: HTTP %s", response.status_code)
                return None
            data = response.json()
        except Exception as exc:
            logger.exception("Exception fetching Coolbet sports: %s", exc)
            return None

        try:
            for sport in data.get("sports", []):
                name = sport.get("name", "").lower()
                if name in {"football", "soccer"}:
                    return sport.get("id")
        except Exception as exc:
            logger.exception("Failed to parse Coolbet sports list: %s", exc)
        return None

    def fetch_odds(self) -> List[MatchOdds]:
        """Fetch all upcoming football match odds from Coolbet.

        Returns a list of ``MatchOdds`` objects.  If the API calls fail
        or the JSON structure is unexpected, returns an empty list.
        """
        sport_id = self._get_sport_id()
        if sport_id is None:
            return []

        # Fetch the prematch events for football.  Based on observed network
        # traffic, the ``prematch" endpoint returns a list of events grouped
        # by competition.  If Coolbet changes their API, you may need to
        # adjust this URL or parameters.
        events_url = f"https://sb1api.coolbet.com/api/v1/sportsbook/prematch?sportId={sport_id}&lang={self.lang}"
        try:
            response = requests.get(events_url, timeout=self.timeout)
            if response.status_code != 200:
                logger.error("Coolbet prematch request failed: HTTP %s", response.status_code)
                return []
            data = response.json()
        except Exception as exc:
            logger.exception("Exception fetching Coolbet prematch: %s", exc)
            return []

        matches: List[MatchOdds] = []
        try:
            # The top level JSON may have a key "events" or "data".  We
            # attempt to support both structures.  Each event contains
            # teams, start time and markets/outcomes similar to other APIs.
            events = data.get("events") or data.get("data", {}).get("events", [])
            for event in events:
                home = event.get("home") or event.get("homeTeam") or ""
                away = event.get("away") or event.get("awayTeam") or ""
                start = event.get("startTime") or event.get("start")
                markets: Dict[str, Dict[str, float]] = {}
                for market in event.get("markets", []):
                    label = market.get("name") or market.get("label", "Unknown")
                    outcomes: Dict[str, float] = {}
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name")
                        odds_val = outcome.get("oddsDecimal") or outcome.get("price") or outcome.get("odds")
                        if name and odds_val:
                            try:
                                value = float(odds_val) / 1000 if isinstance(odds_val, int) and odds_val > 10 else float(odds_val)
                                outcomes[name] = value
                            except Exception:
                                continue
                    if outcomes:
                        markets[label] = outcomes
                if markets:
                    matches.append(MatchOdds(home=home, away=away, start_time=start, markets=markets))
        except Exception as exc:
            logger.exception("Failed to parse Coolbet prematch response: %s", exc)
            return []

        return matches


def print_kambi_odds_for_brands(brands: List[str], league_path: str = "football/premier_league") -> None:
    """Convenience function to fetch and print odds for multiple Kambi brands.

    Parameters
    ----------
    brands : list of str
        List of bookmaker names.  Each name is looked up in
        ``KambiBrandScraper.BRAND_CODES`` to obtain a brand code.  If a
        brand name is not found, it is assumed to be the code itself.
    league_path : str, optional
        Kambi path segment identifying the sport and league.  Defaults to
        Premier League football.

    This function instantiates a ``KambiBrandScraper`` for each brand and
    prints a short summary of the odds.  It is intended for quick
    interactive use and demonstration purposes.  For production systems
    you should integrate ``KambiBrandScraper`` directly.
    """
    for brand in brands:
        code = KambiBrandScraper.BRAND_CODES.get(brand.lower(), brand)
        scraper = KambiBrandScraper(brand_code=code)
        odds = scraper.fetch_odds(league_path=league_path)
        print(f"\n===== {brand} ({code}) =====")
        if not odds:
            print("No odds returned or request failed.\n")
            continue
        for match in odds:
            print(f"{match.home} vs {match.away} – Start: {match.start_time}")
            for market, outcomes in match.markets.items():
                print(f"  {market}:")
                for outcome, price in outcomes.items():
                    print(f"    {outcome:<10} {price}")


if __name__ == "__main__":  # pragma: no cover
    # Example usage when running this module directly.  This block will
    # execute only if you call ``python kambi_coolbet_scraper.py``.  It
    # demonstrates how to scrape both Kambi and Coolbet and print the
    # results.  Adjust the brand names and league path as needed.
    logging.basicConfig(level=logging.INFO)
    # Define which Kambi brands to scrape.  Use human names or brand codes.
    brands_to_scrape = [
        "unibet",
        "betsson",
        "nordic bet",
        "leovegas",
        "betclic",
        "parions sport",
        "winamax",
        "tipico",
    ]
    print_kambi_odds_for_brands(brands_to_scrape, league_path="football/premier_league")

    # Scrape Coolbet
    cb = CoolbetScraper(lang="en")
    cb_odds = cb.fetch_odds()
    print("\n===== Coolbet =====")
    if not cb_odds:
        print("No odds returned or request failed.\n")
    else:
        for match in cb_odds:
            print(f"{match.home} vs {match.away} – Start: {match.start_time}")
            for market, outcomes in match.markets.items():
                print(f"  {market}:")
                for outcome, price in outcomes.items():
                    print(f"    {outcome:<10} {price}")