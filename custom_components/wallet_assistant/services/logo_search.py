"""Optional Logo.dev typeahead, available on the free Community plan.

Only the Search API is used. The secret key never leaves the backend;
no brand lookups, subscriptions, or paid-only endpoints are called.
"""

from __future__ import annotations

import asyncio
from collections import deque
import time

import aiohttp

from ..const import CONF_LOGO_DEV_SEARCH_KEY, DOMAIN
from .logo_cache import normalize_logo_slug


def get_logo_search_key(hass) -> str:
    entries = hass.config_entries.async_entries(DOMAIN)
    options = entries[0].options if entries else {}
    return str(options.get(CONF_LOGO_DEV_SEARCH_KEY) or "").strip()


def normalize_logo_query(value) -> str:
    query = " ".join(str(value or "").split()).lower()
    if not 3 <= len(query) <= 100 or any(char in query for char in "/:\\"):
        return ""
    return query


class LogoSearch:
    """Bounded query cache, shared request limit, and quota backoff."""

    def __init__(self, hass, session_factory=None):
        self.hass = hass
        self._session_factory = session_factory
        self._lock = asyncio.Lock()
        self._key = ""
        self._cache = {}
        self._requests = deque()
        self._retry_at = 0
        self._status = "unavailable"

    async def async_search(self, query: str) -> dict:
        query = normalize_logo_query(query)
        if not query:
            return {"status": "ok", "results": []}
        async with self._lock:
            key = get_logo_search_key(self.hass)
            if not key:
                return {"status": "disabled", "results": []}
            if key != self._key:
                self._key = key
                self._cache.clear()
                self._requests.clear()
                self._retry_at = 0
            now = time.monotonic()
            cached = self._cache.get(query)
            if cached and now - cached[0] < 86400:
                return {"status": "ok", "results": cached[1]}
            if now < self._retry_at:
                return {"status": self._status, "results": []}
            while self._requests and now - self._requests[0] >= 60:
                self._requests.popleft()
            if len(self._requests) >= 60:
                return {"status": "rate_limited", "results": []}
            self._requests.append(now)
            try:
                session = self._get_session()
                async with session.get(
                    "https://api.logo.dev/v2/search",
                    params={"q": query, "method": "typeahead", "limit": 5},
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        self._status = {
                            401: "invalid_key", 403: "invalid_key",
                            402: "credits_exhausted", 429: "rate_limited",
                        }.get(response.status, "unavailable")
                        self._retry_at = time.monotonic() + (900 if response.status == 402 else 60)
                        return {"status": self._status, "results": []}
                    payload = await response.json()
                    candidates = payload["data"]
                    if not isinstance(candidates, list):
                        raise ValueError("Invalid search response")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
                # Do not expose remote errors, URLs, headers, or secret keys.
                self._status = "unavailable"
                self._retry_at = time.monotonic() + 60
                return {"status": "unavailable", "results": []}

            results = []
            domains = set()
            for candidate in candidates[:25]:
                if not isinstance(candidate, dict):
                    continue
                domain = normalize_logo_slug(candidate.get("domain"))
                if domain and domain not in domains:
                    domains.add(domain)
                    results.append({"name": str(candidate.get("name") or domain)[:150], "domain": domain})
                    if len(results) == 5:
                        break
            if len(self._cache) >= 200:
                self._cache.pop(next(iter(self._cache)))
            self._cache[query] = (time.monotonic(), results)
            return {"status": "ok", "results": results}

    def _get_session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return async_get_clientsession(self.hass)
