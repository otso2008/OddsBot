from datetime import datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import DictCursor
from db_managert import get_db_connection

REF_BOOK = "Pinnacle"

def save_closing_odds_from_latest():
    timestamp = datetime.now(timezone.utc)
    conn = get_db_connection()

    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute("""
            SELECT id, match_key, start_time FROM matches
            WHERE start_time < %s AND start_time > %s
        """, (timestamp, timestamp - timedelta(minutes=30)))

        matches = cur.fetchall()

        for match in matches:
            match_id = match["id"]
            match_key = match["match_key"]

            # Get last Pinnacle odds
            cur.execute("""
                SELECT market_code, outcome, price FROM current_odds
                WHERE match_id = %s AND bookmaker_name = %s
            """, (match_id, REF_BOOK))
            odds_rows = cur.fetchall()

            for row in odds_rows:
                market = row["market_code"]
                outcome = row["outcome"]
                closing_price = row["price"]
                implied_probability = 1.0 / closing_price if closing_price > 0 else None

                cur.execute("""
                    INSERT INTO closing_odds (match_id, bookmaker_id, bookmaker_name, market_code,
                        outcome, price, line, implied_probability, collected_at)
                    SELECT %s, id, %s, %s, %s, %s, %s, %s, %s
                    FROM bookmakers WHERE name = %s
                    ON CONFLICT (match_id, bookmaker_id, market_code, outcome) DO NOTHING
                """, (
                    match_id, REF_BOOK, market, outcome, closing_price,
                    None, implied_probability, timestamp, REF_BOOK
                ))

            # Get highest EV per market/outcome
            cur.execute("""
                SELECT market_code, outcome, odds, ev_value, fair_probability,
                       bookmaker_name, collected_at
                FROM ev_results
                WHERE match_id = %s
                ORDER BY market_code, outcome, ev_value DESC
            """, (match_id,))

            best_ev_map = {}
            for row in cur.fetchall():
                key = (row["market_code"], row["outcome"])
                if key not in best_ev_map:
                    best_ev_map[key] = row

            # Compare to closing odds
            for (market_code, outcome), ev_row in best_ev_map.items():
                cur.execute("""
                    SELECT price FROM closing_odds
                    WHERE match_id = %s AND market_code = %s AND outcome = %s AND bookmaker_name = %s
                """, (match_id, market_code, outcome, REF_BOOK))

                closing = cur.fetchone()
                if not closing:
                    continue

                closing_odds = closing["price"]
                offered_odds = ev_row["odds"]
                ev_percent = ev_row["ev_value"] * 100
                beat = offered_odds > closing_odds

                cur.execute("""
                    INSERT INTO ev_closing_results
                    (match_id, market_code, outcome, offered_odds,
                     fair_odds_closing, ev_percent, beat_closing, evaluated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (match_id, market_code, outcome) DO NOTHING
                """, (
                    match_id, market_code, outcome,
                    offered_odds, closing_odds, ev_percent,
                    beat, timestamp
                ))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    save_closing_odds_from_latest()
