"""
telegram_email_alerts.py — supports MULTIPLE telegram chats & emails
====================================================================
"""

from __future__ import annotations

import json
import os
import smtplib
from email.mime.text import MIMEText
from typing import Iterable, Dict, Any, Set, List
import requests
from dotenv import load_dotenv

import os

load_dotenv()  # lataa .env-tiedoston sisällön


# --------------------------------------------------------
# READ TELEGRAM CONFIG (supports MULTIPLE CHAT IDs)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# CHAT ID:t voivat olla muodossa: "123,456,789"
_raw_chats = os.getenv("TELEGRAM_CHAT_IDS", "")
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw_chats.split(",") if cid.strip()]

# --------------------------------------------------------
# EMAIL CONFIG (Gmail)
# --------------------------------------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO_LIST = [email.strip() for email in os.getenv("EMAIL_TO", "").split(",") if email.strip()]

# --------------------------------------------------------
# DUPLICATE + CHANGE-THRESHOLD FILTERS
# --------------------------------------------------------
_sent_ev_ids: Set[str] = set()
_sent_arb_ids: Set[str] = set()

# NEW: track last % values to avoid spam
_last_ev_pct: Dict[str, float] = {}
_last_arb_pct: Dict[str, float] = {}

# change thresholds (percentage points)
EV_THRESHOLD = 0.5      # 0.5 %-yksikköä
ROI_THRESHOLD = 0.5     # 0.5 %-yksikköä


# --------------------------------------------------------
# TELEGRAM SENDER
# --------------------------------------------------------
def _send_telegram_message(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for chat_id in TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code != 200:
                print(f"Telegram API error ({chat_id}): {response.status_code}: {response.text}")
        except Exception as exc:
            print(f"Error sending Telegram message to {chat_id}: {exc}")


# --------------------------------------------------------
# EMAIL SENDER
# --------------------------------------------------------
def _send_email(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(EMAIL_TO_LIST)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            try:
                server.starttls()
            except Exception:
                pass
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO_LIST, msg.as_string())
    except Exception as exc:
        print(f"Error sending email: {exc}")


# --------------------------------------------------------
# EV MESSAGE FORMATTER
# --------------------------------------------------------
def _format_ev_message(ev: Dict[str, Any]) -> tuple[str, str]:
    match = ev.get("match", "")
    market = ev.get("market", "")
    outcome = ev.get("outcome", "")
    book = ev.get("book", "")
    offered_odds = ev.get("offered_odds", "")
    ref_book = ev.get("reference_book", "")
    prob = ev.get("probability", 0.0)
    ev_pct = ev.get("ev_percent", 0.0)
    start_time = ev.get("start_time", "")

    telegram_msg = (
        f"🔥 <b>+{ev_pct:.2f}% EV</b>\n"
        f"{match}\n"
        f"Market: {market} – {outcome}\n"
        f"Book: {book} @ {offered_odds}\n"
        f"Reference: {ref_book} (p={prob:.3f})\n"
        f"Start: {start_time}"
    )

    email_body = (
        f"+{ev_pct:.2f}% EV\n"
        f"Match: {match}\n"
        f"Market: {market} – {outcome}\n"
        f"Book: {book} @ {offered_odds}\n"
        f"Reference: {ref_book} (p={prob:.3f})\n"
        f"Start: {start_time}"
    )

    return telegram_msg, email_body


# --------------------------------------------------------
# ARBITRAGE MESSAGE FORMATTER
# --------------------------------------------------------
def _format_arb_message(arb: Dict[str, Any]) -> tuple[str, str]:
    match = arb.get("match", "")
    market = arb.get("market", "")
    books = arb.get("books", "")
    roi = arb.get("roi", 0.0)
    start_time = arb.get("start_time", "")
    details = arb.get("details", "")

    telegram_msg = (
        f"🟢 <b>Arbitrage Opportunity</b>\n"
        f"{match}\n"
        f"Market: {market}\n"
        f"Books: {books}\n"
        f"ROI: {roi:.2f}%\n"
        f"Start: {start_time}"
    )

    if details:
        telegram_msg += f"\n{details}"

    email_body = (
        f"Arbitrage Opportunity\n"
        f"Match: {match}\n"
        f"Market: {market}\n"
        f"Books: {books}\n"
        f"ROI: {roi+1:.2f}%\n"
        f"Start: {start_time}"
    )

    if details:
        email_body += f"\nDetails: {details}"

    return telegram_msg, email_body


# --------------------------------------------------------
# SEND EV ALERT (with threshold)
# --------------------------------------------------------
def send_ev_alert(ev: Dict[str, Any]) -> None:
    # unique key
    key = f"{ev.get('match','')}|{ev.get('market','')}|{ev.get('outcome','')}"

    current_pct = ev.get("ev_percent", 0.0)
    last_pct = _last_ev_pct.get(key)

    # send only if significant change
    if last_pct is not None and abs(current_pct - last_pct) < EV_THRESHOLD:
        return

    telegram_msg, email_body = _format_ev_message(ev)
    subject = f"EV Alert: +{current_pct:.2f}% {ev.get('match','')}"

    _send_telegram_message(telegram_msg)
    _send_email(subject, email_body)

    _last_ev_pct[key] = current_pct


# --------------------------------------------------------
# SEND ARB ALERT (with threshold)
# --------------------------------------------------------
def send_arb_alert(arb: Dict[str, Any]) -> None:
    key = f"{arb.get('match','')}|{arb.get('market','')}"

    current_pct = arb.get("roi", 0.0) * 100
    last_pct = _last_arb_pct.get(key)

    if last_pct is not None and abs(current_pct - last_pct) < ROI_THRESHOLD:
        return

    telegram_msg, email_body = _format_arb_message(arb)
    subject = f"Arbitrage Alert: {arb.get('match','')}"

    _send_telegram_message(telegram_msg)
    _send_email(subject, email_body)

    _last_arb_pct[key] = current_pct


# --------------------------------------------------------
# WRAPPER
# --------------------------------------------------------
def notify(ev_opportunities: Iterable[Dict[str, Any]], arb_opportunities: Iterable[Dict[str, Any]]) -> None:
    for ev in ev_opportunities:
        try:
            send_ev_alert(ev)
        except Exception as exc:
            print(f"Error sending EV alert: {exc}")

    for arb in arb_opportunities:
        try:
            send_arb_alert(arb)
        except Exception as exc:
            print(f"Error sending arbitrage alert: {exc}")
