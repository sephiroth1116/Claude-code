"""Data shapes for a scraped listing, independent of source or storage."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class Listing:
    id: str  # stable id: f"{source}:{external_id}"
    source: str  # "craigslist" | "ebay" | "facebook"
    title: str
    description: str
    price_usd: float | None
    url: str
    image_url: str | None
    location_text: str
    lat: float | None
    lon: float | None
    posted_at: str | None  # ISO 8601 string, or None if unknown
    first_seen_at: str
    distance_miles: float | None = None
    score: float = 0.0
    score_reasons: list[str] | None = None
    status: str = "new"  # "new" | "reviewed" | "false_positive" | "contacted_police"

    def to_dict(self) -> dict:
        return asdict(self)


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"
