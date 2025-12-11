"""
FastAPI backend for the Odds Analysis project.

Tämä moduuli tarjoaa HTTP-API:n, jolla voidaan lukea valmiiksi
laskettuja ja PostgreSQL-tietokantaan tallennettuja tietoja:

- EV-vedot (ev_results)
- arbitraasit (arb_results)
- ottelut (matches)
- nykyiset kertoimet (current_odds)
- fair probabilities / no-vig odds (fair_probs)
Itse kerääminen ja laskenta tehdään erillisissä skripteissä
(main.py, ev_calc.py, arb_bot.py jne.). Tämä backend EI laske
mitään itse, vaan lukee dataa tietokannasta ja palauttaa
sen JSON-muodossa frontendille, Telegram-botille tms.

TÄRKEÄT OPTIMOINTI-KOHDAT TÄSSÄ VERSIOSSA
----------------------------------------
1. PostgreSQL-yhteyspooli (SimpleConnectionPool) → ei avata
   / suljeta yhteyttä joka kutsulla → nopeampi ja skaalautuvampi.
2. READ COMMITTED -eristystaso → luetaan vain valmiiksi commitattua
   dataa, kun main.py kirjoittaa samaan aikaan.
3. API-avain (header: X-API-Key) → jos ODDSBANK_API_KEY on asetettu,
   kaikki API-reitit vaativat oikean avaimen.
4. Yksinkertainen rate limiting per IP → estää spämmin
   (429 Too Many Requests).
5. Pagination EV- ja arb-reiteille (limit + offset).
6. Pydantic-mallit (response_model) → selkeä & vakaa JSON-rakenne,
   parempi dokumentaatio ja validation.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool
from psycopg2 import extensions as pg_ext

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Depends,
    Header,
    Request,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Database connection pool
# ---------------------------------------------------------------------------

POOL: Optional[SimpleConnectionPool] = None


def _get_db_config() -> Dict[str, Any]:
    """Read database configuration from environment variables."""
    return {
        "host": os.getenv("ODDSBANK_DB_HOST", "localhost"),
        "port": int(os.getenv("ODDSBANK_DB_PORT", "5432")),
        "dbname": os.getenv("ODDSBANK_DB_NAME", "oddsbank"),
        "user": os.getenv("ODDSBANK_DB_USER", "postgres"),
        "password": os.getenv("ODDSBANK_DB_PASSWORD", 'Goala411'),
    }


def _init_pool() -> None:
    """Initialise the global connection pool if it is not already created."""
    global POOL
    if POOL is not None:
        return

    cfg = _get_db_config()
    try:
        POOL = SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            **cfg,
        )
    except Exception as exc:
        # Jos poolin luonti epäonnistuu, koko API ei voi toimia.
        raise RuntimeError(f"Failed to create DB connection pool: {exc}") from exc


@contextmanager
def get_conn():
    """Yield a pooled database connection with READ COMMITTED isolation."""
    if POOL is None:
        _init_pool()

    assert POOL is not None  # tyyppitarkennusta varten
    conn: psycopg2.extensions.connection = POOL.getconn()
    try:
        # Luetaan vain commitattua dataa; read-only riittää
        conn.set_session(
            isolation_level=pg_ext.ISOLATION_LEVEL_READ_COMMITTED,
            readonly=True,
            autocommit=False,
        )
        yield conn
    finally:
        POOL.putconn(conn)


def fetch_query(sql: str, params: tuple | None = None) -> List[Dict[str, Any]]:
    """Execute a SQL query and return all rows as a list of dicts."""
    try:
        with get_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        # Wrapataan virhe HTTP 500:ksi, jotta FastAPI palauttaa selkeän vastauksen.
        raise HTTPException(
            status_code=500, detail=f"Database query failed: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Pydantic-mallit vastauksille
# ---------------------------------------------------------------------------


class EvResult(BaseModel):
    match_id: int
    bookmaker_name: str
    market_code: str
    outcome: str
    odds: float
    ev_fraction: float
    fair_probability: float
    reference_bookmaker_name: str
    collected_at: datetime


class ArbLeg(BaseModel):
    book: str
    odds: float


class ArbResult(BaseModel):
    match_id: int
    market_code: str
    roi_fraction: float
    legs: Dict[str, ArbLeg]
    stake_split: Dict[str, float]
    found_at: datetime


class MatchItem(BaseModel):
    match_id: int
    sport: str
    league: str
    home_team: str
    away_team: str
    start_time: datetime


class OddsItem(BaseModel):
    bookmaker_name: str
    market_code: str
    outcome: str
    price: float
    line: Optional[float]
    implied_probability: Optional[float]
    updated_at: datetime


class FairItem(BaseModel):
    market_code: str
    outcome: str
    fair_probability: float
    no_vig_odds: float
    margin: float
    reference_bookmaker_name: str
    collected_at: datetime


# ---------------------------------------------------------------------------
# API-avain (header: X-API-Key)
# ---------------------------------------------------------------------------

API_KEY: Optional[str] = os.getenv("API_KEY")


async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    """Varmista, että X-API-Key vastaa ODDSBANK_API_KEY:tä (jos asetettu).

    Jos ODDSBANK_API_KEY ei ole asetettu, autentikointi on pois päältä.
    """
    if not API_KEY:
        # Ei API-avainta ympäristössä → ei autentikointia.
        return
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


# ---------------------------------------------------------------------------
# Yksinkertainen rate limiting per IP + endpoint
# ---------------------------------------------------------------------------

RATE_LIMIT_STATE: Dict[str, Dict[str, List[float]]] = {}
RATE_WINDOW_SECONDS = 60.0  # 1 minuutin liukuva ikkuna


def enforce_rate_limit(
    bucket_name: str,
    request: Request,
    max_per_minute: int,
) -> None:
    """Rajoita pyyntöjä per IP per bucket.

    Tämä on yksinkertainen prosessikohtainen toteutus, joka riittää
    kehitysvaiheeseen / pieneen tuotantoon.
    """
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    bucket = RATE_LIMIT_STATE.setdefault(bucket_name, {}).setdefault(client_ip, [])
    # Putsaa vanhat aikaleimat pois ikkunan ulkopuolelta
    bucket = [t for t in bucket if now - t < RATE_WINDOW_SECONDS]

    if len(bucket) >= max_per_minute:
        # Liian monta pyyntöä minuutissa
        RATE_LIMIT_STATE[bucket_name][client_ip] = bucket
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, slow down.",
        )

    bucket.append(now)
    RATE_LIMIT_STATE[bucket_name][client_ip] = bucket


# ---------------------------------------------------------------------------
# FastAPI-sovellus ja CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Odds Analysis API",
    description=(
        "API for retrieving match, odds, fair probability, expected value and "
        "arbitrage data from the Oddsbank Postgres database. This service "
        "sits on top of the existing data collection and analysis pipeline "
        "and exposes the results over HTTP in JSON format."
    ),
    version="2.0.0",
)

cors_origins = os.getenv("ODDSBANK_CORS_ORIGINS", "*")
if cors_origins.strip() == "*":
    origins: List[str] = ["*"]
else:
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", tags=["health"])
async def root() -> Dict[str, str]:
    """Yksinkertainen health check."""
    return {"message": "Odds Analysis API is running"}


@app.get(
    "/api/ev/top",
    tags=["ev"],
    response_model=List[EvResult],
)
async def get_top_ev(
    request: Request,
    limit: int = Query(20, gt=0, le=100),
    offset: int = Query(0, ge=0),
    _: None = Depends(verify_api_key),
) -> List[EvResult]:
    """Palauta korkeimmat EV-vedot, järjestettynä EV:n mukaan.

    limit  – montako riviä palautetaan (1–100)
    offset – sivutus (esim. 0, 20, 40, ...)
    """
    enforce_rate_limit("ev", request, max_per_minute=120)

    sql = """
        SELECT
            match_id,
            bookmaker_name,
            market_code,
            outcome,
            odds,
            ev_value AS ev_fraction,
            fair_probability,
            reference_bookmaker_name,
            collected_at
        FROM ev_results
        ORDER BY ev_value DESC
        LIMIT %s OFFSET %s;
    """
    rows = fetch_query(sql, (limit, offset))
    return rows  # FastAPI + Pydantic konvertoi EvResult-malleiksi


@app.get(
    "/api/arbs/latest",
    tags=["arbitrage"],
    response_model=List[ArbResult],
)
async def get_latest_arbs(
    request: Request,
    limit: int = Query(20, gt=0, le=100),
    offset: int = Query(0, ge=0),
    _: None = Depends(verify_api_key),
) -> List[ArbResult]:
    """Palauta uusimmat arbitraasit, järjestettynä found_at DESC.

    limit  – montako riviä palautetaan (1–100)
    offset – sivutus (esim. 0, 20, 40, ...)
    """
    enforce_rate_limit("arbs", request, max_per_minute=120)

    sql = """
        SELECT
            match_id,
            market_code,
            roi AS roi_fraction,
            legs,
            stake_split,
            found_at
        FROM arb_results
        ORDER BY found_at DESC
        LIMIT %s OFFSET %s;
    """
    rows = fetch_query(sql, (limit, offset))

    # legs ja stake_split voivat olla JSON stringejä → yritetään parse
    for row in rows:
        for field in ("legs", "stake_split"):
            val = row.get(field)
            if isinstance(val, str):
                try:
                    row[field] = json.loads(val)
                except Exception:
                    # Jos parse kaatuu, jätetään alkuperäinen arvo
                    pass

    return rows


@app.get(
    "/api/matches/upcoming",
    tags=["matches"],
    response_model=List[MatchItem],
)
async def get_upcoming_matches(
    request: Request,
    hours: int = Query(24, gt=0, le=168),
    _: None = Depends(verify_api_key),
) -> List[MatchItem]:
    """Lista tulevista otteluista seuraavan N tunnin sisällä."""
    enforce_rate_limit("matches", request, max_per_minute=60)

    sql = """
        SELECT
            id AS match_id,
            sport,
            league,
            home_team,
            away_team,
            start_time
        FROM matches
        WHERE start_time >= NOW()
          AND start_time < NOW() + (%s || ' hours')::interval
        ORDER BY start_time;
    """
    rows = fetch_query(sql, (hours,))
    return rows


@app.get(
    "/api/odds/{match_id}",
    tags=["odds"],
    response_model=List[OddsItem],
)
async def get_current_odds(
    match_id: int,
    request: Request,
    _: None = Depends(verify_api_key),
) -> List[OddsItem]:
    """Hae nykyiset kertoimet yhdelle ottelulle kaikista markkinoista."""
    enforce_rate_limit("odds", request, max_per_minute=120)

    sql = """
        SELECT
            bookmaker_name,
            market_code,
            outcome,
            price,
            line,
            implied_probability,
            updated_at
        FROM current_odds
        WHERE match_id = %s
        ORDER BY market_code, outcome;
    """
    rows = fetch_query(sql, (match_id,))
    return rows


@app.get(
    "/api/fair/{match_id}",
    tags=["fair probabilities"],
    response_model=List[FairItem],
)
async def get_latest_fair_probabilities(
    match_id: int,
    request: Request,
    _: None = Depends(verify_api_key),
) -> List[FairItem]:
    """Palauta viimeisimmät fair probs / no-vig odds / margin per markkina + outcome."""
    enforce_rate_limit("fair", request, max_per_minute=120)

    sql = """
        SELECT DISTINCT ON (market_code, outcome)
            market_code,
            outcome,
            fair_probability,
            no_vig_odds,
            margin,
            reference_bookmaker_name,
            collected_at
        FROM fair_probs
        WHERE match_id = %s
        ORDER BY market_code, outcome, collected_at DESC;
    """
    rows = fetch_query(sql, (match_id,))
    return rows
