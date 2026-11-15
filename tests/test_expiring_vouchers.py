"""Expiry filtering and sensor attributes without a Home Assistant install."""

from datetime import date, timedelta
import json
import re
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import Mock
from urllib.parse import quote

from test_startup import load_definitions


class ExpiringVouchersTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.today = date(2026, 11, 15)
        self.clock = Mock()
        self.clock.today.return_value = self.today
        storage_module = load_definitions("services/storage.py", {"WalletStorage", "_parse_date"}, {
            "date": self.clock, "List": list, "Optional": Optional,
            "WalletItem": object, "TYPE_VOUCHER": "voucher",
        })
        self.clock.fromisoformat = date.fromisoformat
        self.storage = object.__new__(storage_module.WalletStorage)
        self.storage.items = []
        logo_module = load_definitions("services/logo_cache.py", {"normalize_logo_slug", "logo_dev_url"}, {
            "Optional": Optional, "quote": quote, "LOGO_DEV_PUBLISHABLE_KEY": "pk_default",
            "_SLUG_RE": re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?$"),
        })
        self.sensor_module = load_definitions("sensor.py", {"ExpiringVouchersSensor"}, {
            "SensorEntity": object, "callback": lambda fn: fn,
            "WalletStorage": lambda hass: self.storage,
            "DOMAIN": "wallet_assistant", "DEFAULT_EXPIRY_WARNING_DAYS": 14,
            "SIGNAL_ITEMS_UPDATED": "items_updated",
            "async_dispatcher_connect": Mock(return_value=Mock()),
            "CONF_ALLOW_EXTERNAL_LOGOS": "allow_external_logos",
            "get_logo_dev_api_key": lambda options: options.get("logo_dev_api_key", "pk_default"),
            "normalize_logo_slug": logo_module.normalize_logo_slug,
            "logo_dev_url": logo_module.logo_dev_url,
        })
        self.entry = SimpleNamespace(entry_id="test", options={})
        self.sensor = self.sensor_module.ExpiringVouchersSensor(None, self.entry)

    def voucher(self, name, days=0, **changes):
        return SimpleNamespace(
            **{"item_id": name, "name": name, "owner": "Alice", "item_type": "voucher",
               "expires_on": (self.today + timedelta(days=days)).isoformat(),
               "remaining_balance": None, "currency": "EUR", "code": "private", "logo_slug": None,
               "spending": [{"note": "private"}], **changes}
        )

    def test_window_boundaries_sorting_and_exclusions(self):
        last = self.voucher("Last day", 14)
        first = self.voucher("Today")
        self.storage.items = [
            last, self.voucher("Too late", 15), self.voucher("Expired", -1),
            self.voucher("No expiry", expires_on=None), self.voucher("Invalid", expires_on="invalid"),
            self.voucher("Invalid type", expires_on=123),
            self.voucher("Loyalty", item_type="loyalty"),
            self.voucher("Promotion", item_type="promotion"), first,
        ]
        self.assertEqual(self.storage.get_expiring_vouchers(14), [first, last])
        self.assertEqual(self.storage.count_expiring_vouchers(14), 2)
        self.assertEqual(self.storage.get_expiring_vouchers(0), [first])

    async def test_initial_state_poll_and_dispatch_keep_details_and_count_in_sync(self):
        self.assertEqual(self.sensor.extra_state_attributes, {"warning_days": 14, "expiring_vouchers": []})
        self.storage.items = [self.voucher("Gift", 1, remaining_balance=12.5)]
        self.sensor.hass = object()
        self.sensor.async_on_remove = Mock()
        self.sensor.async_write_ha_state = Mock()
        await self.sensor.async_added_to_hass()
        attributes = self.sensor.extra_state_attributes
        self.assertEqual(self.sensor._attr_native_value, 1)
        self.assertEqual(json.loads(json.dumps(attributes))["expiring_vouchers"], [{
            "item_id": "Gift", "name": "Gift", "owner": "Alice",
            "logo_url": None,
            "expires_on": "2026-11-16", "remaining_balance": 12.5, "currency": "EUR",
        }])
        self.sensor_module.async_dispatcher_connect.assert_called_once_with(
            self.sensor.hass, "items_updated", self.sensor._handle_items_updated,
        )
        self.storage.items = [self.voucher("Untracked", 2), self.voucher("Spent", 2, remaining_balance=0)]
        self.sensor._handle_items_updated()
        self.sensor.async_write_ha_state.assert_called_once_with()
        self.assertEqual(self.sensor._attr_native_value, 2)
        self.assertEqual([item["remaining_balance"] for item in self.sensor.extra_state_attributes["expiring_vouchers"]], [None, 0])
        self.clock.today.return_value += timedelta(days=20)
        await self.sensor.async_update()
        self.assertEqual(self.sensor._attr_native_value, 0)
        self.assertEqual(self.sensor.extra_state_attributes["expiring_vouchers"], [])
        self.assertEqual(len(attributes["expiring_vouchers"]), 1)

    async def test_logo_urls_for_markdown_follow_options(self):
        self.entry.options["logo_dev_api_key"] = "pk_custom"
        self.storage.items = [self.voucher("Brand", logo_slug=" IKEA.COM ")]
        await self.sensor.async_update()
        self.assertEqual(
            self.sensor.extra_state_attributes["expiring_vouchers"][0]["logo_url"],
            "https://img.logo.dev/ikea.com?token=pk_custom&size=32&format=webp&retina=true",
        )
        for logo in ("/local/images/voucher.png", "/local/wallet_assistant/images/upload.webp"):
            with self.subTest(logo=logo):
                self.assertEqual(self.sensor._logo_url(logo), logo)
        for logo in (None, "", "/local/../secret", "/local/images\\secret", "http://example.com/logo.png",
                     "https://example.com/logo.png", "javascript:alert(1)"):
            with self.subTest(logo=logo):
                self.assertIsNone(self.sensor._logo_url(logo))
        self.entry.options["allow_external_logos"] = True
        self.assertEqual(self.sensor._logo_url("https://example.com/logo.png"), "https://example.com/logo.png")
        self.assertIsNone(self.sensor._logo_url("https://example.com/invalid logo.png"))


if __name__ == "__main__":
    unittest.main()
