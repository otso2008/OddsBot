
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
from fastapi.responses import HTMLResponse
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
        "dbname": os.getenv("ODDSBANK_DB_NAME", "Oddsbank"),
        "user": os.getenv("ODDSBANK_DB_USER", "postgres"),
        "password": os.getenv("ODDSBANK_DB_PASSWORD", 'ABC!"#'),
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

    # Lisätään ottelun tiedot EV-vastaukseen. Nämä voivat olla None jos
    # kysely ei liitä matches-taulua, mutta tyypillisesti ne ovat mukana.
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    league: Optional[str] = None
    start_time: Optional[datetime] = None


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

    # Lisätään ottelun tiedot arbitraasi-vastaukseen.
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    league: Optional[str] = None
    start_time: Optional[datetime] = None


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
    hours: Optional[int] = Query(None, ge=0, le=168),
    _: None = Depends(verify_api_key),
) -> List[EvResult]:
    """
    Palauta korkeimmat EV-vedot, järjestettynä EV:n mukaan.

    limit  – montako riviä palautetaan (1–100)
    offset – sivutus (esim. 0, 20, 40, ...)
    hours  – valinnainen aikaraja tuleville otteluille. Jos asetettu,
              palauttaa vain ottelut joiden start_time on välillä [now, now+hours].
              Jos ei asetettu, palauttaa kaikki tulevat ottelut (start_time >= now).
    """
    enforce_rate_limit("ev", request, max_per_minute=120)

    # Rakennetaan kysely dynaamisesti. EV-vedot liittyvät aina otteluihin, joten
    # liitetään matches-tauluun, jotta voidaan suodattaa vain tulevat ottelut.
    params: List[Any] = []
    sql = """
        SELECT
            ev.match_id,
            ev.bookmaker_name,
            ev.market_code,
            ev.outcome,
            ev.odds,
            ev.ev_value AS ev_fraction,
            ev.fair_probability,
            ev.reference_bookmaker_name,
            ev.collected_at,
            m.home_team,
            m.away_team,
            m.league,
            m.start_time
        FROM ev_results ev
        JOIN matches m ON m.id = ev.match_id
        WHERE m.start_time >= NOW()
          -- Only include rows from the most recent run.  A small window
          -- (2 seconds) is used to capture all rows inserted in the
          -- latest batch.
          AND ev.collected_at >= (
              SELECT MAX(collected_at) FROM ev_results
          ) - INTERVAL '2 seconds'
    """
   if hours is not None:
     sql += " AND m.start_time < NOW() + (%s || ' hours')::interval"
     params.append(hours)
    # Order by EV value descending and apply pagination
   sql += " ORDER BY ev.ev_value DESC LIMIT %s OFFSET %s;"
   params.extend([limit, offset])
   rows = fetch_query(sql, tuple(params))

   dedup: Dict[tuple, Dict[str, Any]] = {}
   
   for row in rows:
       key = (
           row["match_id"],
           row["market_code"],
           row["outcome"],
           row["bookmaker_name"],
       )
   
       # pidä uusin / paras
       prev = dedup.get(key)
       if prev is None or row["collected_at"] > prev["collected_at"]:
           dedup[key] = row
   
   return list(dedup.values())
   


@app.get(
    "/api/arbs/latest",
    tags=["arbitrage"],
    response_model=List[ArbResult],
)
async def get_latest_arbs(
    request: Request,
    limit: int = Query(20, gt=0, le=100),
    offset: int = Query(0, ge=0),
    hours: Optional[int] = Query(None, ge=0, le=168),
    _: None = Depends(verify_api_key),
) -> List[ArbResult]:
    """
    Palauta uusimmat arbitraasit, järjestettynä found_at DESC.

    limit  – montako riviä palautetaan (1–100)
    offset – sivutus (esim. 0, 20, 40, ...)
    hours  – valinnainen aikaraja tuleville otteluille. Jos asetettu,
              palauttaa vain arbitraasit joiden ottelut ovat välillä [now, now+hours].
              Jos ei asetettu, palauttaa kaikki tulevat ottelut (start_time >= now).
    """
    enforce_rate_limit("arbs", request, max_per_minute=120)

    params: List[Any] = []
    sql = """
        SELECT
            arb.match_id,
            arb.market_code,
            arb.roi AS roi_fraction,
            arb.legs,
            arb.stake_split,
            arb.found_at,
            m.home_team,
            m.away_team,
            m.league,
            m.start_time
        FROM arb_results arb
        JOIN matches m ON m.id = arb.match_id
        WHERE m.start_time >= NOW()
          -- Only include rows from the most recent run.  A small window
          -- (2 seconds) is used to capture all rows inserted in the
          -- latest batch.
          AND arb.found_at >= (
              SELECT MAX(found_at) FROM arb_results
          ) - INTERVAL '2 seconds'
    """
    if hours is not None:
        sql += " AND m.start_time < NOW() + (%s || ' hours')::interval"
        params.append(hours)
   sql += " ORDER BY arb.found_at DESC LIMIT %s OFFSET %s;"
   params.extend([limit, offset])
   rows = fetch_query(sql, tuple(params))
   
   dedup: Dict[tuple, Dict[str, Any]] = {}
   
   for row in rows:
       key = (
           row["match_id"],
           row["market_code"],
       )
   
       prev = dedup.get(key)
       if prev is None or row["found_at"] > prev["found_at"]:
           dedup[key] = row
   
   # legs / stake_split JSON-parsing säilyy ennallaan
   rows = list(dedup.values())
   return rows


@app.get(
    "/api/matches/upcoming",
    tags=["matches"],
    response_model=List[MatchItem],
)
async def get_upcoming_matches(
    request: Request,
    hours: int = Query(168, gt=0, le=168),
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


# ---------------------------------------------------------------------------
# Additional endpoint: return all upcoming matches without a time window
# ---------------------------------------------------------------------------

@app.get(
    "/api/matches/upcoming/all",
    tags=["matches"],
    response_model=List[MatchItem],
)
async def get_all_upcoming_matches(
    request: Request,
    _: None = Depends(verify_api_key),
) -> List[MatchItem]:
    """Lista kaikista tulevista otteluista (start_time >= NOW()).

    Tämä reitti palauttaa kaikki tulevat ottelut ilman aikarajaa. Se on
    hyödyllinen silloin, kun frontend haluaa näyttää koko listan ilman
    parametrin hours rajausta.
    """
    # Käytetään erillistä rate-limiting bucketia tälle reitille. Yksi pyyntö
    # minuutissa per IP riittää, koska data ei muutu usein.
    enforce_rate_limit("matches_all", request, max_per_minute=60)
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
        ORDER BY start_time;
    """
    rows = fetch_query(sql)
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

# ---------------------------------------------------------------------------
# Additional endpoint: filterable upcoming matches by league and hours
# ---------------------------------------------------------------------------

@app.get(
    "/api/matches",
    tags=["matches"],
    response_model=List[MatchItem],
)
async def get_filtered_matches(
    request: Request,
    league: Optional[str] = Query(None, description="Rajaa tulokset tiettyyn liigaan"),
    hours: Optional[int] = Query(None, gt=0, le=168, description="Aikaraja tuleville otteluille"),
    _: None = Depends(verify_api_key),
) -> List[MatchItem]:
    """
    Palauta tulevat ottelut suodatettuna liigan ja/tai aikarajan perusteella.

    Jos league-parametri on asetettu, palauttaa vain kyseisen liigan ottelut.
    Jos hours-parametri on asetettu, palauttaa vain ottelut jotka alkavat
    seuraavan `hours` tunnin sisällä. Molempia parametreja voi käyttää yhtä
    aikaa. Jos parametria ei ole asetettu, palauttaa kaikki tulevat ottelut
    (start_time >= now()).
    """
    # Käytetään erillistä bucketia rate limiting -logiikkaa varten
    enforce_rate_limit("matches_filtered", request, max_per_minute=60)

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
    """
    params: List[Any] = []
    # Lisätään aikaraja jos annettu
    if hours is not None:
        sql += " AND start_time < NOW() + (%s || ' hours')::interval"
        params.append(hours)
    # Lisätään liigan suodatus jos annettu
    if league:
        sql += " AND league = %s"
        params.append(league)
    sql += " ORDER BY start_time;"
    rows = fetch_query(sql, tuple(params))
    return rows

# ---------------------------------------------------------------------------
# Frontend (SPA) serving
# ---------------------------------------------------------------------------

# Provide a simple route to serve a single-page application (SPA).  This route
# reads the index.html file specified by the environment variable
# `ODDSBANK_FRONTEND_FILE` (default "index.html"), performs simple string
# substitutions to inject runtime configuration values from the environment,
# and returns the processed HTML.  This allows the frontend to pick up
# the API base URL, API key and affiliate map from environment variables at
# runtime without bundling them in the static assets.
@app.get("/app", response_class=HTMLResponse)
async def serve_spa() -> HTMLResponse:
    """Serve the compiled single-page application with environment injection."""
    # Determine the path of the frontend file.  Allow overriding via env var.
    index_file = os.getenv("ODDSBANK_FRONTEND_FILE", "index.html")
    try:
        with open(index_file, "r", encoding="utf-8") as fh:
            html = fh.read()
    except FileNotFoundError:
        # If the file is not found, return a simple error page.
        return HTMLResponse(
            status_code=404,
            content=f"<h1>Not Found</h1><p>Could not locate {index_file}</p>",
        )
    # Collect runtime configuration variables.  These will be replaced in the HTML.
    api_base = os.getenv("ODDSBANK_PUBLIC_API_BASE", "")
    api_key = os.getenv("API_KEY", "")
    affiliate_map = os.getenv("ODDSBANK_AFFILIATE_MAP", "{}")
    # Replace placeholder tokens with actual values.  Use double curly braces in the
    # HTML template (e.g. {{API_BASE}}) to mark replacement points.
    html = html.replace("{{API_BASE}}", api_base)
    html = html.replace("{{API_KEY}}", api_key)
    html = html.replace("{{AFFILIATE_MAP}}", affiliate_map)
    return HTMLResponse(html)
