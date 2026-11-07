"""Logo cache behaviour (no Home Assistant install needed)."""

import importlib
import os
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "wallet_assistant"


def _load_logo_cache():
    """Import services/logo_cache.py without running the HA package __init__."""
    package = "wa_logo_test"
    if f"{package}.services.logo_cache" in sys.modules:
        return sys.modules[f"{package}.services.logo_cache"]
    root = ModuleType(package)
    root.__path__ = [str(COMPONENT)]
    services = ModuleType(f"{package}.services")
    services.__path__ = [str(COMPONENT / "services")]
    sys.modules[package] = root
    sys.modules[f"{package}.services"] = services
    return importlib.import_module(f"{package}.services.logo_cache")


logo_cache = _load_logo_cache()


def webp(payload_size):
    """A minimal byte string with a correct RIFF/WebP size header."""
    payload = b"WEBP" + b"x" * payload_size
    return b"RIFF" + len(payload).to_bytes(4, "little") + payload


WEBP = webp(40_000)  # bigger than one 16 KB network chunk


class FakeResponse:
    """Delivers the body in 16 KB chunks, like aiohttp's StreamReader."""

    CHUNK = 16384

    def __init__(self, status=200, body=WEBP, content_type="image/webp"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.content = SimpleNamespace(read=self._read)
        self._body = body
        self._pos = 0

    async def _read(self, limit):
        size = min(limit, self.CHUNK)
        chunk = self._body[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.response


def make_hass(directory):
    async def executor(func, *args):
        return func(*args)

    return SimpleNamespace(
        config=SimpleNamespace(path=lambda name: os.path.join(directory, name)),
        async_add_executor_job=executor,
    )


class SlugValidationTests(unittest.TestCase):
    def test_accepts_domains(self):
        self.assertEqual(logo_cache.normalize_logo_slug(" Google.com "), "google.com")
        self.assertEqual(logo_cache.normalize_logo_slug("my-shop.co.uk"), "my-shop.co.uk")

    def test_rejects_paths_urls_and_traversal(self):
        for value in ("", None, "/local/a.png", "https://x.com", "../etc", "a/b", "a..b", ".com"):
            self.assertIsNone(logo_cache.normalize_logo_slug(value), value)

    def test_size_is_clamped(self):
        self.assertEqual(logo_cache.normalize_logo_size("9999"), 256)
        self.assertEqual(logo_cache.normalize_logo_size("1"), 32)
        self.assertEqual(logo_cache.normalize_logo_size("65"), 128)
        self.assertEqual(logo_cache.normalize_logo_size("abc"), 64)


class LogoCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = make_hass(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_downloads_once_then_serves_from_disk(self):
        session = FakeSession()
        cache = logo_cache.LogoCache(self.hass, session_factory=lambda: session)

        first = await cache.async_get("google.com", 64)
        second = await cache.async_get("google.com", 64)

        self.assertEqual(first, WEBP)
        self.assertEqual(second, first)
        self.assertEqual(len(session.calls), 1)
        self.assertIn("img.logo.dev/google.com", session.calls[0])
        self.assertTrue(os.path.exists(cache.cache_file("google.com", 64)))

    async def test_falls_back_to_stale_copy_when_logo_dev_is_down(self):
        good = FakeSession()
        cache = logo_cache.LogoCache(self.hass, session_factory=lambda: good)
        await cache.async_get("google.com", 64)

        path = cache.cache_file("google.com", 64)
        old = time.time() - logo_cache.LOGO_CACHE_MAX_AGE_SECONDS - 10
        os.utime(path, (old, old))

        down = FakeSession(error=OSError("unreachable"))
        cache._session_factory = lambda: down
        self.assertEqual(await cache.async_get("google.com", 64), WEBP)
        self.assertEqual(len(down.calls), 1)

    async def test_non_image_response_is_not_cached(self):
        session = FakeSession(FakeResponse(status=200, body=b"<html>", content_type="text/html"))
        cache = logo_cache.LogoCache(self.hass, session_factory=lambda: session)

        self.assertIsNone(await cache.async_get("unknown.example", 64))
        self.assertFalse(os.path.exists(cache.cache_file("unknown.example", 64)))

    async def test_oversized_response_is_rejected(self):
        body = b"x" * (logo_cache.LOGO_MAX_BYTES + 10)
        session = FakeSession(FakeResponse(body=body))
        cache = logo_cache.LogoCache(self.hass, session_factory=lambda: session)

        self.assertIsNone(await cache.async_get("big.example", 64))

    async def test_truncated_cached_file_is_downloaded_again(self):
        session = FakeSession()
        cache = logo_cache.LogoCache(self.hass, session_factory=lambda: session)
        path = cache.cache_file("shop.example.com", 64)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(WEBP[:16384])  # what the old code could store

        self.assertEqual(await cache.async_get("shop.example.com", 64), WEBP)
        self.assertEqual(len(session.calls), 1)
        with open(path, "rb") as file:
            self.assertEqual(file.read(), WEBP)

    async def test_truncated_download_is_rejected(self):
        session = FakeSession(FakeResponse(body=WEBP[:20000]))
        cache = logo_cache.LogoCache(self.hass, session_factory=lambda: session)

        self.assertIsNone(await cache.async_get("cut.example", 64))
        self.assertFalse(os.path.exists(cache.cache_file("cut.example", 64)))

    async def test_full_cache_serves_but_does_not_store(self):
        session = FakeSession()
        cache = logo_cache.LogoCache(self.hass, session_factory=lambda: session)
        os.makedirs(cache.directory, exist_ok=True)
        for index in range(3):
            with open(os.path.join(cache.directory, f"{index}.webp"), "wb") as file:
                file.write(WEBP)

        original = logo_cache.LOGO_CACHE_MAX_FILES
        logo_cache.LOGO_CACHE_MAX_FILES = 3
        try:
            self.assertEqual(await cache.async_get("new.example", 64), WEBP)
        finally:
            logo_cache.LOGO_CACHE_MAX_FILES = original
        self.assertFalse(os.path.exists(cache.cache_file("new.example", 64)))


if __name__ == "__main__":
    unittest.main()
