"""Local cache for logos fetched from Logo.dev.

Logos are downloaded once by Home Assistant and stored on disk, so the
dashboard keeps showing them when Logo.dev is slow, down or rate limited.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Callable, Optional
from urllib.parse import quote

from ..const import (
    LOGO_ALLOWED_SIZES,
    LOGO_CACHE_DIR,
    LOGO_CACHE_MAX_AGE_SECONDS,
    LOGO_CACHE_MAX_FILES,
    LOGO_DEV_PUBLISHABLE_KEY,
    LOGO_DEFAULT_SIZE,
    LOGO_MAX_BYTES,
    LOGO_MAX_SIZE,
    LOGO_MIN_SIZE,
)

_LOGGER = logging.getLogger(__name__)

# Domain names and simple brand slugs only: never a URL or a path.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?$")
_FETCH_TIMEOUT_SECONDS = 10


def normalize_logo_slug(value) -> Optional[str]:
    """Return a lower-case Logo.dev slug, or None when it is not one."""
    slug = str(value or "").strip().lower()
    if not slug or ".." in slug or not _SLUG_RE.match(slug):
        return None
    return slug


def normalize_logo_size(value) -> int:
    """Snap to a few fixed sizes so the cache cannot grow per requested pixel."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return LOGO_DEFAULT_SIZE
    size = max(LOGO_MIN_SIZE, min(LOGO_MAX_SIZE, size))
    return next(allowed for allowed in LOGO_ALLOWED_SIZES if allowed >= size)


def is_complete_image(data: Optional[bytes]) -> bool:
    """Reject empty data and WebP files whose RIFF size does not match (truncated)."""
    if not data:
        return False
    if data[:4] == b"RIFF" and len(data) >= 12:
        return int.from_bytes(data[4:8], "little") + 8 == len(data)
    return True


def logo_dev_url(slug: str, size: int) -> str:
    return (
        f"https://img.logo.dev/{quote(slug, safe='')}"
        f"?token={LOGO_DEV_PUBLISHABLE_KEY}&size={size}&format=webp&retina=true"
    )


class LogoCache:
    """Disk cache with stale fallback; one shared instance per HA instance."""

    def __init__(self, hass, session_factory: Optional[Callable] = None):
        self.hass = hass
        self.directory = hass.config.path(LOGO_CACHE_DIR)
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = {}

    def cache_file(self, slug: str, size: int) -> str:
        digest = hashlib.sha256(f"{slug}|{size}".encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.directory, f"{digest}.webp")

    async def async_get(self, slug: str, size: int) -> Optional[bytes]:
        """Return logo bytes: fresh cache, else download, else stale cache."""
        path = self.cache_file(slug, size)
        lock = self._locks.setdefault(path, asyncio.Lock())
        async with lock:
            cached, age = await self.hass.async_add_executor_job(_read_file, path)
            if not is_complete_image(cached):
                cached = None
            if cached is not None and age < LOGO_CACHE_MAX_AGE_SECONDS:
                return cached

            downloaded = await self._async_download(slug, size)
            if downloaded is not None:
                await self.hass.async_add_executor_job(
                    _write_file, self.directory, path, downloaded, LOGO_CACHE_MAX_FILES
                )
                return downloaded

            if cached is not None:
                _LOGGER.debug("Logo.dev unavailable for %s; serving cached copy", slug)
            return cached

    async def _async_download(self, slug: str, size: int) -> Optional[bytes]:
        session = self._get_session()
        try:
            async with session.get(
                logo_dev_url(slug, size),
                timeout=_timeout(_FETCH_TIMEOUT_SECONDS),
                allow_redirects=True,
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                if response.status != 200 or not content_type.startswith("image/"):
                    _LOGGER.debug(
                        "Logo.dev returned status=%s type=%s for %s",
                        response.status,
                        content_type,
                        slug,
                    )
                    return None
                # read(n) returns at most n bytes *of what has arrived so far*,
                # so keep reading until EOF to avoid caching truncated images.
                data = bytearray()
                while True:
                    chunk = await response.content.read(65536)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > LOGO_MAX_BYTES:
                        _LOGGER.debug("Logo for %s is larger than %s bytes", slug, LOGO_MAX_BYTES)
                        return None
        except Exception as err:  # noqa: BLE001 - any network failure falls back to cache
            _LOGGER.debug("Unable to download logo for %s: %s", slug, err)
            return None

        data = bytes(data)
        if not is_complete_image(data):
            return None
        return data

    def _get_session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return async_get_clientsession(self.hass)


def _timeout(seconds: int):
    try:
        import aiohttp

        return aiohttp.ClientTimeout(total=seconds)
    except ImportError:  # pragma: no cover - only without aiohttp (tests)
        return seconds


def _read_file(path: str) -> tuple[Optional[bytes], float]:
    try:
        with open(path, "rb") as file:
            data = file.read()
        return data, time.time() - os.path.getmtime(path)
    except OSError:
        return None, float("inf")


def _write_file(directory: str, path: str, data: bytes, max_files: int) -> None:
    os.makedirs(directory, exist_ok=True)
    if not os.path.exists(path):
        try:
            count = sum(1 for name in os.listdir(directory) if name.endswith(".webp"))
        except OSError:
            count = 0
        if count >= max_files:
            # Disk guard: keep serving the logo, but do not grow the cache further.
            _LOGGER.debug("Logo cache full (%s files); not storing %s", count, path)
            return
    temp_path = f"{path}.tmp"
    with open(temp_path, "wb") as file:
        file.write(data)
    os.replace(temp_path, path)
