"""Craigslist search via its public RSS feed -- no auth, no API key, and it's the one
source here that officially supports being queried this way (Craigslist has historically
tolerated reasonable RSS polling; keep request volume low and cache results).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterable

import requests

from ..config import CraigslistConfig, SearchArea
from ..models import Listing, now_iso

_GEORSS_NS = "{http://www.georss.org/georss}"
_USER_AGENT = "stolen-bike-finder/1.0 (personal use, low volume)"


class CraigslistScraper:
    name = "craigslist"

    def __init__(self, config: CraigslistConfig, search_area: SearchArea):
        self.config = config
        self.search_area = search_area

    def search(self, query: str) -> Iterable[Listing]:
        if not self.config.enabled or not self.config.site:
            return []

        url = f"https://{self.config.site}.craigslist.org/search/{self.config.category}"
        params = {
            "format": "rss",
            "query": query,
            "sort": "date",
        }
        if self.search_area.lat is not None and self.search_area.lon is not None:
            params["lat"] = self.search_area.lat
            params["lon"] = self.search_area.lon
            params["search_distance"] = self.search_area.radius_miles

        resp = requests.get(url, params=params, headers={"User-Agent": _USER_AGENT}, timeout=15)
        resp.raise_for_status()

        return list(self._parse_rss(resp.text))

    def _parse_rss(self, rss_text: str) -> Iterable[Listing]:
        root = ET.fromstring(rss_text)
        channel = root.find("channel")
        if channel is None:
            return

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = item.findtext("pubDate")

            if not link:
                continue

            external_id = link.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
            price = _extract_price(title)

            point = item.find(f"{_GEORSS_NS}point")
            lat = lon = None
            if point is not None and point.text:
                parts = point.text.strip().split()
                if len(parts) == 2:
                    lat, lon = float(parts[0]), float(parts[1])

            image_url = None
            enclosure = item.find("enclosure")
            if enclosure is not None:
                image_url = enclosure.get("url")

            yield Listing(
                id=f"craigslist:{external_id}",
                source="craigslist",
                title=title,
                description=description,
                price_usd=price,
                url=link,
                image_url=image_url,
                location_text=self.config.site,
                lat=lat,
                lon=lon,
                posted_at=pub_date,
                first_seen_at=now_iso(),
            )


def _extract_price(title: str) -> float | None:
    # Craigslist RSS titles are usually "$350 - Trek FX 3 Disc (City)"
    if not title.startswith("$"):
        return None
    digits = ""
    for ch in title[1:]:
        if ch.isdigit():
            digits += ch
        elif ch in (",", "."):
            continue
        else:
            break
    return float(digits) if digits else None
