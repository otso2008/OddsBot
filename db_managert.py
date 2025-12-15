import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Tuple
import psycopg2
from psycopg2 import sql
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

import os

load_dotenv()  # lataa .env-tiedoston sisällön


class OddsBankLoader:
    def __init__(self, conn) -> None:
        self.conn = conn


    # --------------------------------------------------
    # BOOKMAKER HANDLING
    # --------------------------------------------------

    def get_or_create_bookmaker(self, name: str) -> Tuple[int, str]:
        name = name.strip()

        BOOKMAKER_META = {
            "Pinnacle": {"country": "MT", "is_sharp": True, "reliability_score": 100, "short_code": "PIN"},
            "Betfair": {"country": "UK", "is_sharp": True, "reliability_score": 95, "short_code": "BF"},
            "Matchbook": {"country": "UK", "is_sharp": True, "reliability_score": 90, "short_code": "MB"},
            "Coolbet": {"country": "EE", "is_sharp": False, "reliability_score": 85, "short_code": "COOL"},
            "Unibet": {"country": "MT", "is_sharp": False, "reliability_score": 80, "short_code": "UNI"},
            "Betsson": {"country": "SE", "is_sharp": False, "reliability_score": 75, "short_code": "BSS"},
            "Nordic Bet": {"country": "SE", "is_sharp": False, "reliability_score": 75, "short_code": "NB"},
            "888sport": {"country": "GI", "is_sharp": False, "reliability_score": 75, "short_code": "888"},
            "LeoVegas (SE)": {"country": "SE", "is_sharp": False, "reliability_score": 70, "short_code": "LEO"},
            "Tipico": {"country": "MT", "is_sharp": False, "reliability_score": 70, "short_code": "TIP"},
            "Betclic (FR)": {"country": "FR", "is_sharp": False, "reliability_score": 60, "short_code": "BCL"},
            "Winamax (FR)": {"country": "FR", "is_sharp": False, "reliability_score": 60, "short_code": "WFR"},
            "Winamax (DE)": {"country": "DE", "is_sharp": False, "reliability_score": 60, "short_code": "WDE"},
            "Parions Sport (FR)": {"country": "FR", "is_sharp": False, "reliability_score": 55, "short_code": "PSF"},
        }

        meta = BOOKMAKER_META.get(name, {
            "country": None,
            "is_sharp": False,
            "reliability_score": 70,
            "short_code": None,
        })

        with self.conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM bookmakers WHERE name=%s", (name,))
            row = cur.fetchone()
            if row:
                return int(row[0]), name

            cur.execute(
                """
                INSERT INTO bookmakers (name, country, is_sharp, reliability_score, short_code)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (name, meta["country"], meta["is_sharp"], meta["reliability_score"], meta["short_code"])
            )

            return int(cur.fetchone()[0]), name



    # --------------------------------------------------
    # INSERT CLOSING ODDS (only once per match/book/outcome)
    # --------------------------------------------------
    def insert_ev_closing_result(self, match_id, market_code, outcome,
                                 offered_odds, fair_odds_closing,
                                 ev_percent, beat_closing, timestamp):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ev_closing_results
                (match_id, market_code, outcome,
                 offered_odds, fair_odds_closing,
                 ev_percent, beat_closing, evaluated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (match_id, market_code, outcome,
                  offered_odds, fair_odds_closing,
                  ev_percent, beat_closing, timestamp))
            self.conn.commit()

    def insert_closing_odds(self, match_id: int, bookmaker_id: int, book_name: str, market_code: str,
                            outcome: str, price: float, line: Any, timestamp: datetime) -> None:
        implied_probability = (1.0 / price) if price > 0 else None

        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM closing_odds
                WHERE match_id=%s AND bookmaker_id=%s AND market_code=%s AND outcome=%s
                """,
                (match_id, bookmaker_id, market_code, outcome)
            )
            if cur.fetchone():
                return  # Skip if already exists

            cur.execute(
                """
                INSERT INTO closing_odds
                (match_id, bookmaker_id, bookmaker_name,
                 market_code, outcome, price, line, implied_probability, collected_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    match_id, bookmaker_id, book_name,
                    market_code, outcome, price, line,
                    implied_probability, timestamp
                )
            )
            self.conn.commit()
    # --------------------------------------------------
    # MATCH HANDLING
    # --------------------------------------------------

    def get_or_create_match(self, m: Dict[str, Any]) -> int:
        sport = m["sport"]
        home = m["home"]
        away = m["away"]
        start_time = m["start_time"]

        # Extract league from sport code (soccer_epl → epl)
        try:
            league = sport.split("_", 1)[1]
        except:
            league = None

        match_key = f"{sport}_{home}_{away}_{start_time}"

        with self.conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("DELETE FROM matches WHERE start_time < NOW()")

            cur.execute("SELECT id FROM matches WHERE match_key=%s", (match_key,))
            row = cur.fetchone()
            if row:
                return int(row[0])

            cur.execute(
                """
                INSERT INTO matches (match_key, sport, league, home_team, away_team, start_time)
                VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (match_key, sport, league, home, away, start_time)
            )

            return int(cur.fetchone()[0])


    # --------------------------------------------------
    # MARKET TYPE
    # --------------------------------------------------

    def get_or_create_market_type(self, code: str) -> int:
        code = code.strip()
        with self.conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM market_types WHERE market_code=%s", (code,))
            row = cur.fetchone()
            if row:
                return int(row[0])

            cur.execute(
                "INSERT INTO market_types (market_code) VALUES (%s) RETURNING id",
                (code,)
            )
            return int(cur.fetchone()[0])


    # --------------------------------------------------
    # INSERT CURRENT ODDS
    # --------------------------------------------------

    def insert_current_odds(self, match_id, bookmaker_id, book_name, market_code,
                            outcome, price, line, timestamp):

        implied_probability = (1.0 / price) if price > 0 else None

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO current_odds
                (match_id, bookmaker_id, bookmaker_name,
                 market_code, outcome, price, line, implied_probability, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (match_id, bookmaker_id, market_code, outcome)
                DO UPDATE SET
                    price=EXCLUDED.price,
                    line=EXCLUDED.line,
                    implied_probability=EXCLUDED.implied_probability,
                    updated_at=NOW(),
                    bookmaker_name=EXCLUDED.bookmaker_name
                """,
                (
                    match_id, bookmaker_id, book_name,
                    market_code, outcome, price, line,
                    implied_probability, timestamp
                )
            )


    # --------------------------------------------------
    # INSERT ODDS HISTORY
    # --------------------------------------------------

    def insert_history(self, match_id, bookmaker_id, book_name, market_code,
                       outcome, price, line, timestamp):

        implied_probability = (1.0 / price) if price > 0 else None

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO odds_history
                (match_id, bookmaker_id, bookmaker_name,
                 market_code, outcome, price, line, implied_probability, collected_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    match_id, bookmaker_id, book_name,
                    market_code, outcome, price, line,
                    implied_probability, timestamp
                )
            )


    # --------------------------------------------------
    # INSERT FAIR PROB (with margin + no_vig_odds)
    # --------------------------------------------------

    def insert_fair_prob(self, match_id, market_code, outcome,
                         probability, no_vig_odds, margin,
                         ref_book_id, ref_book_name, timestamp):

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fair_probs
                (match_id, market_code, outcome,
                 fair_probability, no_vig_odds, margin,
                 reference_bookmaker_id, reference_bookmaker_name, collected_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    match_id, market_code, outcome,
                    probability, no_vig_odds, margin,
                    ref_book_id, ref_book_name, timestamp
                )
            )


    # --------------------------------------------------
    # INSERT EV RESULT
    # --------------------------------------------------

    def insert_ev(self, match_id, bookmaker_id, book_name, market_code,
                  outcome, odds, ev_fraction, fair_prob, ref_book_id, ref_book_name,
                  timestamp):

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ev_results
                (match_id, bookmaker_id, bookmaker_name, market_code,
                 outcome, odds, ev_value, fair_probability,
                 reference_bookmaker_id, reference_bookmaker_name, collected_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    match_id, bookmaker_id, book_name,
                    market_code, outcome, odds, ev_fraction, fair_prob,
                    ref_book_id, ref_book_name, timestamp
                )
            )


    # --------------------------------------------------
    # INSERT ARB RESULT
    # --------------------------------------------------

    def insert_arb(self, match_id, market_code, roi_fraction, legs, timestamp):

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO arb_results
                (match_id, market_code, roi, legs, stake_split, found_at)
                VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                """,
                (
                    match_id, market_code, roi_fraction,
                    json.dumps(legs["legs"], sort_keys=True),
                    json.dumps(legs["stake_split"], sort_keys=True),
                    timestamp
                )
            )

    def insert_placed_arb_bet(self, match_id: int, market_code: str, roi_fraction: float,
                              legs: Dict[str, Any], stake_split: Dict[str, Any], timestamp: datetime) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO placed_arb_bets
                (match_id, market_code, roi, legs, stake_split, placed_at)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    match_id,
                    market_code,
                    roi_fraction,
                    json.dumps(legs, sort_keys=True),
                    json.dumps(stake_split, sort_keys=True),
                    timestamp
                )
            )


    # --------------------------------------------------
    # MAIN SAVE PIPELINE
    # --------------------------------------------------

    def run(self, all_matches, fair_prob_data, no_vig, ev_list, arb_list):

        timestamp = datetime.now(timezone.utc)
        id_map = {}

        # MATCHES
        for m in all_matches:
            id_map[m["match"]] = self.get_or_create_match(m)

        # CURRENT ODDS + HISTORY
        for m in all_matches:
            match_id = id_map[m["match"]]
            for market_code, books in m["markets"].items():
                self.get_or_create_market_type(market_code)

                for book_name, outcomes in books.items():
                    book_id, book_norm = self.get_or_create_bookmaker(book_name)

                    for outcome, price in outcomes.items():
                        self.insert_current_odds(
                            match_id, book_id, book_norm,
                            market_code, outcome, float(price),
                            None, timestamp
                        )

                        self.insert_history(
                            match_id, book_id, book_norm,
                            market_code, outcome, float(price),
                            None, timestamp
                        )

        # FAIR PROBABILITIES (with margin)
        for nv in no_vig:
            mid = id_map[nv["match"]]
            ref_id, ref_name = self.get_or_create_bookmaker(nv["reference_book"])

            margin = nv.get("margin", None)

            self.insert_fair_prob(
                mid,
                nv["market_code"],
                nv["outcome"],
                nv["fair_probability"],
                nv["no_vig_odds"],
                margin,
                ref_id,
                ref_name,
                timestamp
            )

        # EV RESULTS
        for ev in ev_list:
            mid = id_map[ev["match"]]
            book_id, book_name = self.get_or_create_bookmaker(ev["book"])
            ref_id, ref_name = self.get_or_create_bookmaker(ev["reference_book"])

            self.insert_ev(
                mid,
                book_id,
                book_name,
                ev["market"],
                ev["outcome"],
                float(ev["offered_odds"]),
                ev["ev_percent"] / 100.0,
                ev["probability"],
                ref_id,
                ref_name,
                timestamp
            )

        # ARBITRAGES
        for arb in arb_list:
            mid = id_map[arb["match"]]

            legs_with_stakes = {}
            for outcome, info in arb["best_odds"].items():
                legs_with_stakes[outcome] = {
                    "book": info["book"],
                    "odds": info["odds"]
                }

            stake_split = arb["stakes"]

            self.insert_arb(
                mid,
                arb["market"],
                arb["roi"] / 100.0,
                {"legs": legs_with_stakes, "stake_split": stake_split},
                timestamp
            )

            # SAVE PLACED ARB BET
            self.insert_placed_arb_bet(
                mid,
                arb["market"],
                arb["roi"] / 100.0,
                legs_with_stakes,
                stake_split,
                timestamp
            )

        self.conn.commit()


# --------------------------------------------------
# DB CONNECTION
# --------------------------------------------------

def get_db_connection():
    conn = psycopg2.connect(
        dbname='Oddsbank',
        user="postgres",
        password=os.getenv('POSTGRES_PASSWORD'),
        host="localhost",
        port=5432,
    )

    conn.autocommit = True
    return conn


# --------------------------------------------------
# SAVE WRAPPER (used by main.py)
# --------------------------------------------------

def save_to_database(all_matches, no_vig_data, ev_list, arb_list):

    conn = get_db_connection()

    try:
        loader = OddsBankLoader(conn)
        loader.run(all_matches, [], no_vig_data, ev_list, arb_list)
    finally:
        conn.close()
