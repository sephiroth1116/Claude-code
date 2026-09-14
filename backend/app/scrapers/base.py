"""Shared interface for scrapers: each one yields Listing objects for a given search."""
from __future__ import annotations

from typing import Iterable, Protocol

from ..models import Listing


class Scraper(Protocol):
    name: str

    def search(self, query: str) -> Iterable[Listing]: ...
