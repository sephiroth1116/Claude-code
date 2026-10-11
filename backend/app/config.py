"""Loads config.yaml (shopping wishlist, search area, source toggles) plus secrets from env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("BIKE_FINDER_CONFIG", REPO_ROOT / "config.yaml"))


@dataclass
class WishlistConfig:
    """What counts as a match -- this replaced the old per-bike stolen-bike profile once
    the project repurposed from "find my stolen bike" to "find a bike worth buying"."""

    brands: list[str] = field(default_factory=list)  # OR-matched: any one of these qualifies
    require_class_3: bool = False  # hard filter: listing text must confirm Class 3 / 28mph
    max_price_usd: float | None = None
    keywords: list[str] = field(default_factory=list)  # extra positive signals, e.g. ["ebike"]


@dataclass
class SearchArea:
    lat: float | None = None
    lon: float | None = None
    radius_miles: float = 25
    extra_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)


@dataclass
class CraigslistConfig:
    enabled: bool = True
    site: str = ""
    category: str = "bia"  # bicycles for sale


@dataclass
class EbayConfig:
    enabled: bool = False


@dataclass
class FacebookConfig:
    enabled: bool = False


@dataclass
class UcpStoreConfig:
    """A Shopify store reachable via the UCP/MCP shopping protocol (see scrapers/ucp.py).
    Upway and The Pro's Closet both run on this; any other Shopify-based bike shop with a
    reachable /.well-known/ucp endpoint can be added the same way."""

    enabled: bool = False
    domain: str = ""  # e.g. "upway.co" -- scraper resolves the real MCP endpoint from here


@dataclass
class EmailAlertConfig:
    enabled: bool = False
    to: str = ""


@dataclass
class AppConfig:
    wishlist: WishlistConfig
    search: SearchArea
    craigslist: CraigslistConfig
    ebay: EbayConfig
    facebook: FacebookConfig
    upway: UcpStoreConfig
    pros_closet: UcpStoreConfig
    email_alert: EmailAlertConfig
    refresh_interval_minutes: int = 30

    # secrets, never stored in config.yaml
    ebay_app_id: str | None = None
    ebay_cert_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"No config.yaml found at {CONFIG_PATH}. Copy config.example.yaml to config.yaml "
            "and fill in your bike's details first."
        )

    raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}

    wishlist_raw = raw.get("wishlist", {})
    wishlist = WishlistConfig(
        brands=[b for b in wishlist_raw.get("brands", []) if b],
        require_class_3=bool(wishlist_raw.get("require_class_3", False)),
        max_price_usd=wishlist_raw.get("max_price_usd"),
        keywords=[k for k in wishlist_raw.get("keywords", []) if k],
    )

    search_raw = raw.get("search", {})
    search = SearchArea(
        lat=search_raw.get("lat"),
        lon=search_raw.get("lon"),
        radius_miles=search_raw.get("radius_miles", 25),
        extra_keywords=search_raw.get("extra_keywords", []) or [],
        exclude_keywords=search_raw.get("exclude_keywords", []) or [],
    )

    sources_raw = raw.get("sources", {})
    craigslist = CraigslistConfig(**sources_raw.get("craigslist", {}))
    ebay = EbayConfig(**{k: v for k, v in sources_raw.get("ebay", {}).items() if k == "enabled"})
    facebook = FacebookConfig(
        **{k: v for k, v in sources_raw.get("facebook", {}).items() if k == "enabled"}
    )
    upway_raw = sources_raw.get("upway", {})
    upway = UcpStoreConfig(enabled=upway_raw.get("enabled", False), domain=upway_raw.get("domain", "upway.co"))
    pros_closet_raw = sources_raw.get("pros_closet", {})
    pros_closet = UcpStoreConfig(
        enabled=pros_closet_raw.get("enabled", False),
        domain=pros_closet_raw.get("domain", "theproscloset.com"),
    )

    alerts_raw = raw.get("alerts", {}).get("email", {})
    email_alert = EmailAlertConfig(enabled=alerts_raw.get("enabled", False), to=alerts_raw.get("to", ""))

    return AppConfig(
        wishlist=wishlist,
        search=search,
        craigslist=craigslist,
        ebay=ebay,
        facebook=facebook,
        upway=upway,
        pros_closet=pros_closet,
        email_alert=email_alert,
        refresh_interval_minutes=raw.get("refresh_interval_minutes", 30),
        ebay_app_id=os.environ.get("EBAY_APP_ID"),
        ebay_cert_id=os.environ.get("EBAY_CERT_ID"),
        smtp_host=os.environ.get("SMTP_HOST"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER"),
        smtp_pass=os.environ.get("SMTP_PASS"),
    )
