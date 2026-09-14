"""Craigslist search scraper.

Craigslist discontinued RSS feeds for search results (format=rss now 404s), so this
parses the plain HTML search page instead -- specifically its no-JS "static" result
list (<li class="cl-static-search-result">), which is server-rendered and doesn't need
a browser. Confirmed against a live search: lat/lon + search_distance query params still
do server-side radius filtering on this endpoint, same as the old RSS one did.

This does NOT get per-listing images, exact geo-coordinates, or posted dates -- none of
that is in the static result list, and fetching each listing's own page to get them would
multiply request volume by however many results come back. distance_miles and posted_at
are left unset for Craigslist results; the radius restriction still happens server-side
via the lat/lon/search_distance params below, and results come back sorted newest-first.
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Iterable

import requests

from ..config import CraigslistConfig, SearchArea
from ..models import Listing, now_iso

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


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

        url = f"https://{self.config.site}.craigslist.org/search/{self.config.category}"
        params = {"query": query, "sort": "date"}
        if self.search_area.lat is not None and self.search_area.lon is not None:
            params["lat"] = self.search_area.lat
            params["lon"] = self.search_area.lon
            params["search_distance"] = self.search_area.radius_miles

        resp = requests.get(url, params=params, headers={"User-Agent": _USER_AGENT}, timeout=15)
        resp.raise_for_status()

        return list(self._parse_html(resp.text))

    def _parse_html(self, html_text: str) -> Iterable[Listing]:
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
                location_text=item["location"] or self.config.site,
                lat=None,
                lon=None,
                posted_at=None,
                first_seen_at=now_iso(),
            )


def _extract_price(text: str) -> float | None:
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None
