"""Upway.co, queried through Shopify's public Storefront GraphQL API.

Upway's storefront is a Shopify Hydrogen app (a "headless" JS storefront), which means its
search results aren't server-rendered HTML -- they're fetched client-side, after the page
loads, from Shopify's own documented Storefront API. Every Hydrogen storefront ships a
public, intentionally client-readable access token in its page source for exactly this
purpose; this module just calls that same API directly over plain HTTPS instead of running
a browser to let Upway's own JS call it for us. No scraping, no automation, no login.

This replaced an earlier attempt to use Shopify's newer UCP/MCP "agentic commerce" protocol,
which authenticated fine but whose tools/call endpoint returned "Tool not found" for every
request tried -- a bug on Shopify's side as of this writing, not something fixable here.

Bonus over every other source in this project: Upway tags each listing with its own
maxSpeed:20mph / maxSpeed:28mph value and a product.mileage metafield with the exact miles
ridden, so class_3_confirmed and mileage_miles come from Upway's own data here, not a text
guess against prose (see matching.py for how the two approaches combine).
"""
from __future__ import annotations

from typing import Iterable

import requests

from ..config import StoreConfig
from ..models import Listing, now_iso

_SHOP_DOMAIN = "upway-us1.myshopify.com"
_API_VERSION = "2024-10"
_GRAPHQL_URL = f"https://{_SHOP_DOMAIN}/api/{_API_VERSION}/graphql.json"
# Public client-side Storefront API token, read out of upway.co's own page source -- every
# Hydrogen storefront ships one of these for its own frontend to use; it carries no write
# access and isn't a secret (see module docstring).
_STOREFRONT_TOKEN = "80b5092f50f4573e9255aeda65427ec9"

_MAX_RESULTS = 50

_SEARCH_QUERY = """
query Search($query: String!, $first: Int!) {
  products(first: $first, query: $query, sortKey: CREATED_AT, reverse: true) {
    edges {
      node {
        id
        title
        handle
        description
        tags
        featuredImage { url }
        priceRange { minVariantPrice { amount } }
        metafields(identifiers: [{namespace: "product", key: "mileage"}]) { value }
      }
    }
  }
}
"""


class UpwayScraper:
    name = "upway"

    def __init__(self, config: StoreConfig):
        self.config = config

    def search(self, query: str) -> Iterable[Listing]:
        if not self.config.enabled:
            return []

        # Upway's own vendor field is always "Upway" (they're the seller of record), so
        # brand filtering has to go through full-text search against title/tags instead of
        # a vendor: filter. product_type:Bike keeps accessories (helmets, locks, lights)
        # that happen to mention a brand name out of the results.
        search_text = f"{query} available_for_sale:true product_type:Bike"

        resp = requests.post(
            _GRAPHQL_URL,
            json={"query": _SEARCH_QUERY, "variables": {"query": search_text, "first": _MAX_RESULTS}},
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Storefront-Access-Token": _STOREFRONT_TOKEN,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"Upway Storefront API error: {data['errors']}")

        edges = data.get("data", {}).get("products", {}).get("edges", [])
        return list(self._parse_products(edges))

    def _parse_products(self, edges: list[dict]) -> Iterable[Listing]:
        for edge in edges:
            node = edge["node"]
            tags = node.get("tags") or []

            mileage = None
            metafields = node.get("metafields") or []
            if metafields and metafields[0] and metafields[0].get("value"):
                try:
                    mileage = float(metafields[0]["value"])
                except (TypeError, ValueError):
                    mileage = None

            price = (node.get("priceRange") or {}).get("minVariantPrice", {}).get("amount")
            product_id = node["id"].rsplit("/", 1)[-1]

            yield Listing(
                id=f"upway:{product_id}",
                source="upway",
                title=node.get("title", ""),
                description=node.get("description", "") or "",
                price_usd=float(price) if price else None,
                url=f"https://upway.co/products/{node.get('handle', '')}",
                image_url=(node.get("featuredImage") or {}).get("url"),
                location_text="ships nationwide",
                lat=None,
                lon=None,
                posted_at=None,
                first_seen_at=now_iso(),
                mileage_miles=mileage,
                delivery_available=True,  # Upway is a nationwide online marketplace, not local pickup
                class_3_confirmed="maxSpeed:28mph" in tags,
            )
