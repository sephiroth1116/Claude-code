"""Scores a scraped listing against your e-bike wishlist (brand + Class 3 soft signal).

This is deliberately simple keyword/attribute matching, not ML -- it's meant to rank a
few dozen listings a day for a human to eyeball, not to auto-decide anything. Never treat
a high score as certainty; always visually verify before acting on a match.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .config import AppConfig
from .geo import haversine_miles
from .models import Listing

_BRAND_WEIGHT = 3.0
_KEYWORD_WEIGHT = 0.5
_CLASS_3_BONUS_WEIGHT = 2.0

_CLASS_3_PHRASES = ["class 3", "class iii", "28 mph", "28mph", "28 mi/h"]

_SHIPS_PHRASES = ["can ship", "will ship", "can deliver", "nationwide shipping", "ships nationwide", "free shipping"]
_PICKUP_ONLY_PHRASES = ["local pickup only", "pickup only", "no shipping", "cash only local", "local only"]


def build_search_queries(config: AppConfig) -> list[str]:
    """One query per wishlist brand.

    Craigslist (and most of these search boxes) AND-match every word in a query, so
    combining several terms almost never matches anything real -- sellers don't type a
    bike's full name out. Cast a wide net on brand and let score_listing() do the actual
    narrowing across the results that come back.
    """
    return [b.strip() for b in config.wishlist.brands if b.strip()]


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


def is_class_3_confirmed(text: str) -> bool:
    """True if the listing text explicitly states a Class 3 / 28mph motor. We never infer
    this from brand or model name alone -- the same model often ships in multiple speed
    classes, so guessing risks being wrong in either direction. This is a soft signal
    (scoring bonus + a visible badge), not a hard filter: live testing found that real
    sellers essentially never write legal speed classification into casual listings, so
    treating it as required returns close to nothing. Verify the actual class yourself
    before buying regardless of what this says."""
    text = text.lower()
    return any(phrase in text for phrase in _CLASS_3_PHRASES)


_MILEAGE_RE = re.compile(r"(\d[\d,]{0,5})\s*(?:miles?|mi\.?)\b", re.IGNORECASE)


def extract_mileage(text: str) -> float | None:
    """Best-effort odometer mileage from listing text, e.g. "only 496 miles" -> 496.0.
    Returns the smallest number found if there are several (titles sometimes separately
    mention a motor's max range in miles, which tends to be a larger, rounder number)."""
    matches = _MILEAGE_RE.findall(text)
    if not matches:
        return None
    values = [float(m.replace(",", "")) for m in matches]
    return min(values)


def infer_delivery(text: str) -> bool | None:
    """Text-only heuristic for sources (Craigslist) that don't give us real shipping data.
    None means "couldn't tell" -- most local-classifieds listings never mention it either
    way, and that's not the same as confirmed local-pickup-only."""
    text = text.lower()
    if any(phrase in text for phrase in _SHIPS_PHRASES):
        return True
    if any(phrase in text for phrase in _PICKUP_ONLY_PHRASES):
        return False
    return None


def _score_against_brand(listing: Listing, brand: str, config: AppConfig) -> tuple[float, list[str]]:
    text = f"{listing.title} {listing.description}".strip()

    score = 0.0
    reasons: list[str] = []

    if not _fuzzy_contains(text, brand):
        return 0.0, []

    score += _BRAND_WEIGHT
    reasons.append(f"mentions brand '{brand}'")

    for keyword in config.wishlist.keywords:
        if keyword and _fuzzy_contains(text, keyword):
            score += _KEYWORD_WEIGHT
            reasons.append(f"mentions keyword '{keyword}'")

    if is_class_3_confirmed(text):
        score += _CLASS_3_BONUS_WEIGHT
        reasons.append("confirms Class 3 / 28mph motor")

    return round(score, 2), reasons


def score_listing(listing: Listing, config: AppConfig) -> None:
    """Scores against every wishlist brand and keeps the best match in place."""
    best_score = 0.0
    best_reasons: list[str] = []
    best_brand: str | None = None

    for brand in config.wishlist.brands:
        score, reasons = _score_against_brand(listing, brand, config)
        if score > best_score:
            best_score, best_reasons, best_brand = score, reasons, brand

    listing.score = best_score
    listing.score_reasons = best_reasons
    listing.matched_brand = best_brand

    text = f"{listing.title} {listing.description}"
    listing.mileage_miles = extract_mileage(text)
    listing.class_3_confirmed = is_class_3_confirmed(text)
    if listing.delivery_available is None:
        listing.delivery_available = infer_delivery(text)


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
    """Hard filters: excluded keywords, over budget, outside the search radius.

    Class 3 confirmation is intentionally NOT a hard filter here even when
    wishlist.require_class_3 is set -- it's a soft signal instead (scoring bonus +
    listing.class_3_confirmed badge), because real sellers essentially never write legal
    speed classification into casual listings; requiring it returns close to nothing.

    Call this after score_listing() has set listing.matched_brand.
    """
    text = f"{listing.title} {listing.description}"
    if is_excluded_text(text, config.search.exclude_keywords):
        return False

    if config.wishlist.max_price_usd and listing.price_usd is not None:
        if listing.price_usd > config.wishlist.max_price_usd:
            return False

    if listing.distance_miles is not None and listing.distance_miles > config.search.radius_miles:
        return False

    return True
