"""Scores a scraped listing against the description of your stolen bike.

This is deliberately simple keyword/attribute matching, not ML -- it's meant to rank a
few dozen listings a day for a human to eyeball, not to auto-decide anything. Never treat
a high score as certainty; always visually verify before doing anything with a match.
"""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher

from .config import AppConfig, BikeInfo
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


def build_search_queries(config: AppConfig) -> list[str]:
    """One query per bike -- just the make (falling back to model).

    Craigslist (and most of these search boxes) AND-match every word in the query, so a
    query combining make + model + extra keywords almost never matches anything real --
    sellers don't type a bike's full name out. Cast a wide net with just the make and let
    score_listing() do the actual narrowing across the results that come back.
    """
    queries = []
    for bike in config.bikes:
        query = (bike.make or bike.model).strip()
        if query:
            queries.append(query)
    return queries


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


_SERIAL_WEIGHT = 10.0  # sellers almost never type serials, but if it's there, that's it


def _score_against_bike(listing: Listing, bike: BikeInfo, config: AppConfig) -> tuple[float, list[str]]:
    text = f"{listing.title} {listing.description}".strip()

    score = 0.0
    reasons: list[str] = []

    if bike.serial_number and _fuzzy_contains(text, bike.serial_number, threshold=0.95):
        score += _SERIAL_WEIGHT
        reasons.append(f"mentions serial number '{bike.serial_number}'")

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

    return round(score, 2), reasons


def score_listing(listing: Listing, config: AppConfig) -> None:
    """Scores against every configured bike and keeps the best match in place."""
    best_score = 0.0
    best_reasons: list[str] = []
    best_bike: BikeInfo | None = None

    for bike in config.bikes:
        score, reasons = _score_against_bike(listing, bike, config)
        if score > best_score:
            best_score, best_reasons, best_bike = score, reasons, bike

    listing.score = best_score
    listing.score_reasons = best_reasons
    listing.matched_bike = best_bike.name if best_bike else None


def compute_distance(listing: Listing, config: AppConfig) -> None:
    if listing.lat is None or listing.lon is None:
        return
    if config.search.lat is None or config.search.lon is None:
        return
    listing.distance_miles = round(
        haversine_miles(config.search.lat, config.search.lon, listing.lat, listing.lon), 1
    )


def is_excluded_text(text: str, exclude_keywords: list[str]) -> bool:
    """True if `text` mentions any of exclude_keywords. Used both for new listings
    (in is_relevant) and to re-filter listings already sitting in the database, so
    adding a keyword to config.yaml hides matching listings immediately on next load
    instead of only affecting listings scraped after that point."""
    return any(keyword and _fuzzy_contains(text, keyword) for keyword in exclude_keywords)


def is_relevant(listing: Listing, config: AppConfig) -> bool:
    """Hard filters: posted before the matched bike's theft, outside the search radius,
    or matching one of search.exclude_keywords (e.g. "mountain bike", "fatboy" -- things
    that share your bike's make but are never going to be the bike itself).

    Call this after score_listing() has set listing.matched_bike.
    """
    text = f"{listing.title} {listing.description}"
    if is_excluded_text(text, config.search.exclude_keywords):
        return False

    bike = next((b for b in config.bikes if b.name == listing.matched_bike), None)
    if bike and bike.stolen_date and listing.posted_at:
        posted = _parse_date(listing.posted_at)
        stolen = _parse_date(bike.stolen_date)
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
