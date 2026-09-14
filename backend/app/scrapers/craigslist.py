"""Craigslist search scraper.

Craigslist discontinued RSS feeds for search results (format=rss now 404s), so this
parses the plain HTML search page instead -- specifically its no-JS "static" result
list (<li class="cl-static-search-result">), which is server-rendered and doesn't need
a browser.

Craigslist has no single nationwide search: the US is split into ~700 separate regional
sites (subdomains like "minneapolis" or "chicago"), each with its own listings, and a
site's lat/lon + search_distance params only filter *within that one site's own results*
-- they never reach into a neighboring site's listings no matter how large search_distance
is set. So a radius bigger than one metro area is meaningless unless multiple sites are
actually queried. _sites_within_radius() below fetches Craigslist's own published site
list (https://reference.craigslist.org/Areas, cached per-process) and picks every site
whose center falls within the configured radius; search() then queries all of them
concurrently and merges the results.

The search results list itself does NOT include per-listing images, exact geo-coordinates,
or posted dates -- none of that is in the static result list, and fetching every listing's
own page just to get them would multiply request volume by however many results come back
(easily thousands, once several regional sites are in play for a large radius). distance_miles
and posted_at are left unset here; images and dates are fetched lazily instead, via
fetch_listing_details() below -- callers should only call it for listings that already
scored some textual match, not the whole result set, to keep that trade-off intact.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable

import requests

from ..config import CraigslistConfig, SearchArea
from ..geo import haversine_miles
from ..models import Listing, now_iso

logger = logging.getLogger("bike_finder")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_AREAS_URL = "https://reference.craigslist.org/Areas"
_SITE_QUERY_CONCURRENCY = 8

_areas_cache: list[dict] | None = None


def _load_us_sites() -> list[dict]:
    """Fetches and caches Craigslist's published list of {hostname, lat, lon} sites."""
    global _areas_cache
    if _areas_cache is not None:
        return _areas_cache

    try:
        resp = requests.get(_AREAS_URL, headers={"User-Agent": _USER_AGENT}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        logger.exception("Failed to fetch Craigslist site list")
        _areas_cache = []
        return _areas_cache

    _areas_cache = [
        {"hostname": d["Hostname"], "lat": d["Latitude"], "lon": d["Longitude"]}
        for d in data
        if d.get("Country") == "US" and d.get("Hostname") and d.get("Latitude") is not None
    ]
    return _areas_cache


def _sites_within_radius(lat: float, lon: float, radius_miles: float, fallback_site: str) -> list[dict]:
    """Returns {hostname, lat, lon} for every site within radius, so callers can tag
    each listing with its site's coordinates -- close enough for a "miles away" figure
    without geocoding every individual listing."""
    sites = _load_us_sites()
    if not sites:
        return [{"hostname": fallback_site, "lat": None, "lon": None}]

    within = [s for s in sites if haversine_miles(lat, lon, s["lat"], s["lon"]) <= radius_miles]
    if not any(s["hostname"] == fallback_site for s in within):
        fallback = next((s for s in sites if s["hostname"] == fallback_site), None)
        within.append(fallback or {"hostname": fallback_site, "lat": None, "lon": None})
    return within


class _ResultListParser(HTMLParser):
    """Pulls title/url/price/location out of Craigslist's static result <li> markup."""

    def __init__(self):
        super().__init__()
        self.items: list[dict] = []
        self._current: dict | None = None
        self._capture_field: str | None = None  # "price" | "location" | None
        self._seen_link_in_current = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "li" and "cl-static-search-result" in (attrs_d.get("class") or ""):
            self._current = {"title": attrs_d.get("title", ""), "url": "", "price": "", "location": ""}
            self._seen_link_in_current = False
            return

        if self._current is None:
            return

        if tag == "a" and not self._seen_link_in_current and attrs_d.get("href"):
            self._current["url"] = attrs_d["href"]
            self._seen_link_in_current = True
        elif tag == "div":
            cls = attrs_d.get("class") or ""
            if "price" in cls:
                self._capture_field = "price"
            elif "location" in cls:
                self._capture_field = "location"

    def handle_data(self, data):
        if self._current is not None and self._capture_field:
            self._current[self._capture_field] += data.strip()

    def handle_endtag(self, tag):
        if tag == "div":
            self._capture_field = None
        elif tag == "li" and self._current is not None:
            if self._current["url"]:
                self.items.append(self._current)
            self._current = None


class CraigslistScraper:
    name = "craigslist"

    def __init__(self, config: CraigslistConfig, search_area: SearchArea):
        self.config = config
        self.search_area = search_area

    def search(self, query: str) -> Iterable[Listing]:
        if not self.config.enabled or not self.config.site:
            return []

        if self.search_area.lat is not None and self.search_area.lon is not None:
            sites = _sites_within_radius(
                self.search_area.lat, self.search_area.lon, self.search_area.radius_miles, self.config.site
            )
        else:
            fallback = next(
                (s for s in _load_us_sites() if s["hostname"] == self.config.site),
                {"hostname": self.config.site, "lat": None, "lon": None},
            )
            sites = [fallback]

        # Craigslist shows a listing under more than one nearby site when large radii
        # overlap (a Minneapolis listing can also turn up in an Eau Claire search), so
        # the same listing ID can arrive twice tagged with two different sites' coords.
        # Keep whichever site is closest to the searcher's own location -- the true
        # origin is almost always the nearest site that returned it, not a distant one.
        best_by_id: dict[str, tuple[float, Listing]] = {}
        with ThreadPoolExecutor(max_workers=min(_SITE_QUERY_CONCURRENCY, len(sites))) as pool:
            futures = {pool.submit(self._search_one_site, site, query): site for site in sites}
            for future in as_completed(futures):
                site = futures[future]
                try:
                    site_distance = (
                        haversine_miles(self.search_area.lat, self.search_area.lon, site["lat"], site["lon"])
                        if self.search_area.lat is not None and site["lat"] is not None
                        else 0.0
                    )
                    for listing in future.result():
                        existing = best_by_id.get(listing.id)
                        if existing is None or site_distance < existing[0]:
                            best_by_id[listing.id] = (site_distance, listing)
                except Exception:
                    logger.exception("Craigslist query failed for site %r", site["hostname"])

        return [listing for _, listing in best_by_id.values()]

    def _search_one_site(self, site: dict, query: str) -> Iterable[Listing]:
        hostname = site["hostname"]
        url = f"https://{hostname}.craigslist.org/search/{self.config.category}"
        params = {"query": query, "sort": "date"}
        if self.search_area.lat is not None and self.search_area.lon is not None:
            params["lat"] = self.search_area.lat
            params["lon"] = self.search_area.lon
            params["search_distance"] = self.search_area.radius_miles

        resp = requests.get(url, params=params, headers={"User-Agent": _USER_AGENT}, timeout=15)
        resp.raise_for_status()

        return list(self._parse_html(resp.text, site))

    def _parse_html(self, html_text: str, site: dict) -> Iterable[Listing]:
        parser = _ResultListParser()
        parser.feed(html_text)

        for item in parser.items:
            url = item["url"]
            external_id = url.rstrip("/").rsplit("/", 1)[-1]
            if not external_id:
                continue

            yield Listing(
                id=f"craigslist:{external_id}",
                source="craigslist",
                title=item["title"],
                description="",
                price_usd=_extract_price(item["price"]),
                url=url,
                image_url=None,
                location_text=item["location"] or site["hostname"],
                # Approximate: the site's own hub city, not the listing's exact address --
                # Craigslist's static result list doesn't expose per-listing coordinates.
                # Close enough for a "how far away" figure without geocoding every listing.
                lat=site["lat"],
                lon=site["lon"],
                posted_at=None,
                first_seen_at=now_iso(),
            )


def _extract_price(text: str) -> float | None:
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


_OG_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"')
_POSTED_TIME_RE = re.compile(r'<time class="date timeago" datetime="([^"]+)"')


def fetch_listing_details(listing_url: str) -> tuple[str | None, str | None]:
    """Best-effort fetch of a single listing's main photo and posted date (image_url,
    posted_at). One request per call -- only call this for listings worth the extra
    round trip (new, and already scored some textual match)."""
    try:
        resp = requests.get(listing_url, headers={"User-Agent": _USER_AGENT}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return None, None

    image_match = _OG_IMAGE_RE.search(resp.text)
    date_match = _POSTED_TIME_RE.search(resp.text)
    return image_match.group(1) if image_match else None, _normalize_to_utc(date_match)


def _normalize_to_utc(date_match: re.Match | None) -> str | None:
    """Craigslist's posted-date carries the site's own local offset (e.g. -0500). Convert
    to UTC so that sorting listings by posted_at as plain strings is actually correct
    across sites in different timezones -- not just visually similar."""
    if not date_match:
        return None
    raw = date_match.group(1)
    try:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return raw
