"""Scores a scraped listing against the description of your stolen bike.

This is deliberately simple keyword/attribute matching, not ML -- it's meant to rank a
few dozen listings a day for a human to eyeball, not to auto-decide anything. Never treat
a high score as certainty; always visually verify before doing anything with a match.
"""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher

from .config import AppConfig
from .geo import haversine_miles
from .models import Listing

_MAKE_WEIGHT = 3.0
_MODEL_WEIGHT = 3.0
_COLOR_WEIGHT = 2.0
_FRAME_SIZE_WEIGHT = 1.0
_FEATURE_WEIGHT = 1.5
_EXTRA_KEYWORD_WEIGHT = 0.5
_CHEAP_PRICE_WEIGHT = 1.5
_CHEAP_PRICE_RATIO = 0.4  # priced under 40% of stated value looks suspicious


def build_search_query(config: AppConfig) -> str:
    parts = [config.bike.make, config.bike.model]
    parts.extend(config.search.extra_keywords)
    return " ".join(p for p in parts if p).strip()


def _fuzzy_contains(haystack: str, needle: str, threshold: float = 0.82) -> bool:
    """True if `needle` appears in `haystack`, allowing near-matches for typos/plurals."""
    if not needle:
        return False
    haystack = haystack.lower()
    needle = needle.lower()
    if needle in haystack:
        return True
    words = haystack.split()
    needle_words = needle.split()
    window = len(needle_words)
    for i in range(len(words) - window + 1):
        candidate = " ".join(words[i : i + window])
        if SequenceMatcher(None, candidate, needle).ratio() >= threshold:
            return True
    return False


def score_listing(listing: Listing, config: AppConfig) -> None:
    """Mutates listing.score and listing.score_reasons in place."""
    bike = config.bike
    text = f"{listing.title} {listing.description}".strip()

    score = 0.0
    reasons: list[str] = []

    if bike.make and _fuzzy_contains(text, bike.make):
        score += _MAKE_WEIGHT
        reasons.append(f"mentions make '{bike.make}'")

    if bike.model and _fuzzy_contains(text, bike.model):
        score += _MODEL_WEIGHT
        reasons.append(f"mentions model '{bike.model}'")

    if bike.color and _fuzzy_contains(text, bike.color):
        score += _COLOR_WEIGHT
        reasons.append(f"mentions color '{bike.color}'")

    if bike.frame_size and _fuzzy_contains(text, bike.frame_size):
        score += _FRAME_SIZE_WEIGHT
        reasons.append(f"mentions frame size '{bike.frame_size}'")

    for feature in bike.features:
        if feature and _fuzzy_contains(text, feature):
            score += _FEATURE_WEIGHT
            reasons.append(f"mentions distinguishing feature '{feature}'")

    for keyword in config.search.extra_keywords:
        if keyword and _fuzzy_contains(text, keyword):
            score += _EXTRA_KEYWORD_WEIGHT
            reasons.append(f"mentions keyword '{keyword}'")

    if bike.approx_value_usd and listing.price_usd:
        if listing.price_usd <= bike.approx_value_usd * _CHEAP_PRICE_RATIO:
            score += _CHEAP_PRICE_WEIGHT
            reasons.append("priced suspiciously low for this bike's value")

    listing.score = round(score, 2)
    listing.score_reasons = reasons


def compute_distance(listing: Listing, config: AppConfig) -> None:
    if listing.lat is None or listing.lon is None:
        return
    if config.search.lat is None or config.search.lon is None:
        return
    listing.distance_miles = round(
        haversine_miles(config.search.lat, config.search.lon, listing.lat, listing.lon), 1
    )


def is_relevant(listing: Listing, config: AppConfig) -> bool:
    """Hard filters: posted before the theft, or clearly outside the search radius."""
    if config.bike.stolen_date and listing.posted_at:
        posted = _parse_date(listing.posted_at)
        stolen = _parse_date(config.bike.stolen_date)
        if posted and stolen and posted < stolen:
            return False

    if listing.distance_miles is not None and listing.distance_miles > config.search.radius_miles:
        return False

    return True


def _parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
