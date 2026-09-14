"""Thin sqlite3 wrapper. One table: listings. No ORM needed for this scale."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Listing

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "listings.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    price_usd REAL,
    url TEXT NOT NULL,
    image_url TEXT,
    location_text TEXT,
    lat REAL,
    lon REAL,
    posted_at TEXT,
    first_seen_at TEXT NOT NULL,
    distance_miles REAL,
    score REAL NOT NULL DEFAULT 0,
    score_reasons TEXT,
    matched_bike TEXT,
    status TEXT NOT NULL DEFAULT 'new'
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def upsert_listing(listing: Listing) -> bool:
    """Insert a new listing, or update score/distance on an existing one.

    Returns True if this was a newly-seen listing (useful for alerting).
    """
    conn = get_connection()
    try:
        existing = conn.execute("SELECT id, status FROM listings WHERE id = ?", (listing.id,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE listings SET score = ?, score_reasons = ?, distance_miles = ?, matched_bike = ?
                   WHERE id = ?""",
                (
                    listing.score,
                    json.dumps(listing.score_reasons or []),
                    listing.distance_miles,
                    listing.matched_bike,
                    listing.id,
                ),
            )
            conn.commit()
            return False

        conn.execute(
            """INSERT INTO listings (
                id, source, title, description, price_usd, url, image_url, location_text,
                lat, lon, posted_at, first_seen_at, distance_miles, score, score_reasons,
                matched_bike, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing.id,
                listing.source,
                listing.title,
                listing.description,
                listing.price_usd,
                listing.url,
                listing.image_url,
                listing.location_text,
                listing.lat,
                listing.lon,
                listing.posted_at,
                listing.first_seen_at,
                listing.distance_miles,
                listing.score,
                json.dumps(listing.score_reasons or []),
                listing.matched_bike,
                listing.status,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def listing_exists(listing_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def list_listings(min_score: float = 0.0) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM listings WHERE score >= ? ORDER BY score DESC, first_seen_at DESC",
            (min_score,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["score_reasons"] = json.loads(d["score_reasons"] or "[]")
            result.append(d)
        return result
    finally:
        conn.close()


def set_status(listing_id: str, status: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE listings SET status = ? WHERE id = ?", (status, listing_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
