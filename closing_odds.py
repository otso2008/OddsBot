import json
from datetime import datetime, timezone
from typing import Any, Dict

from data_collecting import build_all_matches_once
from ev_calc import calculate_ev
from no_vig_calc import compute_fair_and_no_vig
from db_managert import get_db_connection


REF_BOOK = "Pinnacle"


def collect_closing_odds_and_eval_ev():
    timestamp = datetime.now(timezone.utc)
    conn = get_db_connection()
    loader = OddsBankLoader(conn)

    all_matches = build_all_matches_once()
    no_vig_data = compute_fair_and_no_vig(all_matches, reference_books=[REF_BOOK])
    evs = calculate_ev(all_matches, no_vig_data, min_ev_percent=0.0)

    # Map for quick lookup of no-vig closing odds
    closing_map = {}
    for row in no_vig_data:
        if row['reference_book'] != REF_BOOK:
            continue
        key = (row['match'], row['market_code'], row['outcome'])
        closing_map[key] = row['no_vig_odds']

    # Index all EVs by match, then select best per market
    ev_by_match_market = {}
    for ev in evs:
        m = ev['match']
        market = ev['market']
        outcome = ev['outcome']
        key = (m, market, outcome)
        if key not in ev_by_match_market or ev['ev_percent'] > ev_by_match_market[key]['ev_percent']:
            ev_by_match_market[key] = ev

    for match in all_matches:
        match_key = match['match']
        start_time = match['start_time']

        try:
            start_time_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except:
            continue

        seconds_until_start = (start_time_dt - timestamp).total_seconds()
        if not (0 <= seconds_until_start <= 1200):
            continue

        match_id = loader.get_or_create_match(match)
        for market_code, books in match['markets'].items():
            if REF_BOOK not in books:
                continue
            for outcome, price in books[REF_BOOK].items():
                book_id, book_name = loader.get_or_create_bookmaker(REF_BOOK)

                # Store closing odds if not already present
                loader.insert_closing_odds(
                    match_id=match_id,
                    bookmaker_id=book_id,
                    book_name=book_name,
                    market_code=market_code,
                    outcome=outcome,
                    price=price,
                    line=None,
                    timestamp=timestamp
                )

                ev_key = (match_key, market_code, outcome)
                if ev_key not in ev_by_match_market:
                    continue

                best_ev = ev_by_match_market[ev_key]
                closing_odds = closing_map.get(ev_key)
                if closing_odds is None:
                    continue

                beat = float(best_ev['offered_odds']) > closing_odds

                # Store result if not already present
                loader.insert_ev_closing_result(
                    match_id=match_id,
                    market_code=market_code,
                    outcome=outcome,
                    offered_odds=float(best_ev['offered_odds']),
                    fair_odds_closing=closing_odds,
                    ev_percent=best_ev['ev_percent'],
                    beat_closing=beat,
                    timestamp=timestamp
                )

    conn.close()


class OddsBankLoader:
    def __init__(self, conn) -> None:
        self.conn = conn

    def get_or_create_bookmaker(self, name: str) -> tuple[int, str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM bookmakers WHERE name=%s", (name,))
            row = cur.fetchone()
            if row:
                return int(row[0]), name
            cur.execute(
                """
                INSERT INTO bookmakers (name) VALUES (%s)
                RETURNING id
                """,
                (name,)
            )
            return int(cur.fetchone()[0]), name

    def get_or_create_match(self, m: Dict[str, Any]) -> int:
        match_key = m["match"]
        with self.conn.cursor() as cur:
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
                (
                    match_key, m["sport"], m.get("league"),
                    m["home"], m["away"], m["start_time"]
                )
            )
            return int(cur.fetchone()[0])

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
                return

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

    def insert_ev_closing_result(self, match_id: int, market_code: str, outcome: str,
                                 offered_odds: float, fair_odds_closing: float, ev_percent: float,
                                 beat_closing: bool, timestamp: datetime) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM ev_closing_results
                WHERE match_id=%s AND market_code=%s AND outcome=%s
                """,
                (match_id, market_code, outcome)
            )
            if cur.fetchone():
                return

            cur.execute(
                """
                INSERT INTO ev_closing_results
                (match_id, market_code, outcome,
                 offered_odds, fair_odds_closing, ev_percent,
                 beat_closing, evaluated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    match_id, market_code, outcome,
                    offered_odds, fair_odds_closing, ev_percent,
                    beat_closing, timestamp
                )
            )
            self.conn.commit()


if __name__ == "__main__":
    collect_closing_odds_and_eval_ev()
