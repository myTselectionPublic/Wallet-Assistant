"""Free-plan logo search, credentials, caching, and failure behaviour."""

import asyncio
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_logo_cache import logo_cache


search_module = importlib.import_module(f"{logo_cache.__package__}.logo_search")


class Response:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {"data": [
            {"name": "IKEA", "domain": "ikea.com", "logo_url": "https://untrusted.invalid/sk_private"},
        ]}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self):
        self.calls = []
        self.response = Response()

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class LogoSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.options = {"logo_dev_search_key": "sk_private"}
        self.hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_entries=lambda domain: [SimpleNamespace(options=self.options)],
        ))
        self.session = Session()
        self.search = search_module.LogoSearch(self.hass, lambda: self.session)

    async def test_typeahead_uses_only_search_with_server_credentials_and_caches(self):
        first, second = await asyncio.gather(
            self.search.async_search("  IKE "), self.search.async_search("ike"),
        )
        self.assertEqual(first, {"status": "ok", "results": [{"name": "IKEA", "domain": "ikea.com"}]})
        self.assertEqual(first, second)
        self.assertNotIn("sk_private", str(first))
        self.assertEqual(len(self.session.calls), 1)
        url, options = self.session.calls[0]
        self.assertEqual(url, "https://api.logo.dev/v2/search")
        self.assertEqual(options["headers"], {"Authorization": "Bearer sk_private"})
        self.assertEqual(options["params"], {"q": "ike", "method": "typeahead", "limit": 5})
        self.assertFalse(options["allow_redirects"])

    async def test_disabled_short_and_image_path_queries_do_not_call_api(self):
        for query in ("", "ab", "/local/ikea.png", "https://example.com", "x" * 101):
            self.assertEqual((await self.search.async_search(query))["results"], [])
        self.options.clear()
        self.assertEqual((await self.search.async_search("ike"))["status"], "disabled")
        self.assertEqual(self.session.calls, [])

    async def test_quota_auth_rate_and_network_errors_back_off_without_exposing_details(self):
        for status, payload, expected in (
            (402, None, "credits_exhausted"), (429, None, "rate_limited"),
            (401, None, "invalid_key"), (403, None, "invalid_key"),
            (500, None, "unavailable"), (200, ValueError("sk_private"), "unavailable"),
            (200, {"data": {}}, "unavailable"),
        ):
            with self.subTest(status=status, expected=expected):
                self.search = search_module.LogoSearch(self.hass, lambda: self.session)
                self.session.calls.clear()
                self.session.response = Response(status, payload)
                first = await self.search.async_search("ike")
                second = await self.search.async_search("other")
                self.assertEqual(first, {"status": expected, "results": []})
                self.assertEqual(first, second)
                self.assertNotIn("sk_private", str(first))
                self.assertEqual(len(self.session.calls), 1)

    async def test_key_change_resets_backoff_and_cache(self):
        self.session.response = Response(402)
        await self.search.async_search("ike")
        self.options["logo_dev_search_key"] = "sk_replacement"
        self.session.response = Response()
        self.assertEqual((await self.search.async_search("ike"))["status"], "ok")
        self.assertEqual(self.session.calls[-1][1]["headers"]["Authorization"], "Bearer sk_replacement")

    async def test_cache_expires_and_local_limit_prevents_extra_requests(self):
        with patch.object(search_module.time, "monotonic", return_value=0):
            await self.search.async_search("ike")
        with patch.object(search_module.time, "monotonic", return_value=86401):
            await self.search.async_search("ike")
            for index in range(59):
                await self.search.async_search(f"brand{index}")
            self.assertEqual((await self.search.async_search("extra"))["status"], "rate_limited")
        self.assertEqual(len(self.session.calls), 61)

    async def test_only_valid_unique_domains_are_returned(self):
        self.session.response = Response(payload={"data": [
            None, {"domain": "https://invalid.com"},
            {"name": "IKEA", "domain": "IKEA.COM"},
            {"name": "Duplicate", "domain": "ikea.com"},
        ]})
        self.assertEqual((await self.search.async_search("ike"))["results"], [{"name": "IKEA", "domain": "ikea.com"}])


if __name__ == "__main__":
    unittest.main()
