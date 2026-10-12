"""The Pro's Closet (theproscloset.com), queried through Shopify's public predictive-search
JSON endpoint (/search/suggest.json) -- the same AJAX call the site's own search-as-you-type
box makes. It's a standard, documented Shopify storefront feature, reachable with a plain
HTTPS GET and no login or browser automation.

Unlike Upway's Storefront GraphQL API (see scrapers/upway.py), this theme doesn't expose a
Storefront access token for GraphQL, and the bulk /products.json catalog endpoint has no
server-side brand/category filter -- paginating all ~7,500+ products to filter client-side
took nearly a minute in testing, far too slow to run on every refresh. Predictive search
takes a real query and returns matching products directly, at the cost of being capped at
10 results per query by Shopify itself (not something this code controls).

The Pro's Closet doesn't tag listings with an exact mileage or a maxSpeed value the way
Upway does, so mileage_miles and class_3_confirmed fall back to matching.py's text-guess
heuristics against the title, same as Craigslist.
"""
from __future__ import annotations

from typing import Iterable

import requests

from ..config import StoreConfig
from ..models import Listing, now_iso

_SEARCH_URL = "https://www.theproscloset.com/search/suggest.json"
_RESULT_LIMIT = 10  # hard cap enforced by Shopify's predictive search, regardless of what's requested


class ProsClosetScraper:
    name = "pros_closet"

    def __init__(self, config: StoreConfig):
        self.config = config

    def search(self, query: str) -> Iterable[Listing]:
        if not self.config.enabled:
            return []

        resp = requests.get(
            _SEARCH_URL,
            params={
                "q": f"{query} electric",
                "resources[type]": "product",
                "resources[limit]": _RESULT_LIMIT,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        products = data.get("resources", {}).get("results", {}).get("products", [])
        return list(self._parse_products(products))

    def _parse_products(self, products: list[dict]) -> Iterable[Listing]:
        for product in products:
            tags = product.get("tags") or []
            if not any("electric" in tag.lower() or tag.lower() == "ebike" for tag in tags):
                continue  # predictive search does loose text matching; this isn't a hard filter elsewhere

            price = product.get("price")
            image = (product.get("featured_image") or {}).get("url") or product.get("image")

            yield Listing(
                id=f"pros_closet:{product.get('id')}",
                source="pros_closet",
                title=product.get("title", ""),
                description=product.get("body", "") or "",
                price_usd=float(price) if price else None,
                url=f"https://www.theproscloset.com{product.get('url', '')}",
                image_url=image,
                location_text="ships nationwide",
                lat=None,
                lon=None,
                posted_at=None,
                first_seen_at=now_iso(),
                delivery_available=True,  # online marketplace, not local pickup
            )
