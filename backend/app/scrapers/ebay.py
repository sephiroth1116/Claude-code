"""eBay Browse API search, using the official client-credentials OAuth flow.

Note: eBay is a national marketplace, not a local classifieds site, so "radius" doesn't
apply the way it does for Craigslist -- most listings ship, they aren't pickup-only. This
is included because it's a real source of resold bikes/parts, but treat distance_miles as
unavailable/approximate here (we surface itemLocation city/state, not a filtered radius).
Requires EBAY_APP_ID and EBAY_CERT_ID env vars from a registered eBay developer app
(https://developer.ebay.com/my/keys).
"""
from __future__ import annotations

import time
from typing import Iterable

import requests

from ..config import EbayConfig
from ..models import Listing, now_iso

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_BICYCLE_CATEGORY_ID = "7294"

_token_cache: dict[str, object] = {"token": None, "expires_at": 0}


class EbayScraper:
    name = "ebay"

    def __init__(self, config: EbayConfig, app_id: str | None, cert_id: str | None):
        self.config = config
        self.app_id = app_id
        self.cert_id = cert_id

    def search(self, query: str) -> Iterable[Listing]:
        if not self.config.enabled:
            return []
        if not self.app_id or not self.cert_id:
            raise RuntimeError(
                "eBay source is enabled but EBAY_APP_ID / EBAY_CERT_ID are not set in the environment."
            )

        token = self._get_token()
        resp = requests.get(
            _SEARCH_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "q": query,
                "category_ids": _BICYCLE_CATEGORY_ID,
                "limit": "50",
                "sort": "newlyListed",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        return list(self._parse_items(data.get("itemSummaries", [])))

    def _get_token(self) -> str:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]  # type: ignore[return-value]

        resp = requests.post(
            _TOKEN_URL,
            auth=(self.app_id, self.cert_id),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        _token_cache["token"] = payload["access_token"]
        _token_cache["expires_at"] = time.time() + payload.get("expires_in", 7200)
        return _token_cache["token"]  # type: ignore[return-value]

    def _parse_items(self, items: list[dict]) -> Iterable[Listing]:
        for item in items:
            item_id = item.get("itemId", "")
            price = item.get("price", {})
            location = item.get("itemLocation", {})
            location_text = ", ".join(
                filter(None, [location.get("city"), location.get("stateOrProvince")])
            )
            image = item.get("image", {}).get("imageUrl")

            yield Listing(
                id=f"ebay:{item_id}",
                source="ebay",
                title=item.get("title", ""),
                description=item.get("shortDescription", ""),
                price_usd=float(price["value"]) if price.get("value") else None,
                url=item.get("itemWebUrl", ""),
                image_url=image,
                location_text=location_text or "unknown location",
                lat=None,
                lon=None,
                posted_at=item.get("itemCreationDate"),
                first_seen_at=now_iso(),
            )
