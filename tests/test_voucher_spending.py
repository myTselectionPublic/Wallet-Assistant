"""Voucher accounting and permissions, with only Home Assistant interfaces stubbed."""

import ast
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_item_permissions import ADMIN, OWNER, OTHER, Request, make_view


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "wallet_assistant"


def load_module(path, **namespace):
    tree = ast.parse((COMPONENT / path).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and
        (node.level or (node.module or "").startswith("homeassistant"))
    )]
    module = ModuleType(f"voucher_test_{Path(path).stem}")
    sys.modules[module.__name__] = module
    module.__dict__.update(namespace)
    exec(compile(tree, path, "exec"), module.__dict__)
    return module


model = load_module("models/card.py", DEFAULT_BARCODE_FORMAT="CODE128", DEFAULT_VIEW="barcode",
                    DEFAULT_VIEWS={"barcode", "qr"}, ITEM_TYPES={"loyalty", "voucher", "promotion"}, TYPE_LOYALTY="loyalty")
storage_module = load_module("services/storage.py", WalletItem=model.WalletItem, money=model.money,
                             TYPE_VOUCHER="voucher", SIGNAL_ITEMS_UPDATED="updated",
                             async_dispatcher_send=Mock(), get_storage_path=lambda hass: "unused")
list_module = load_module("controllers/list_view.py", HomeAssistantView=type(make_view(None)).__mro__[1],
                          API_BASE="/api/wallet_assistant", WalletItem=model.WalletItem,
                          WalletStorage=lambda hass: None, DEFAULT_BARCODE_FORMAT="CODE128",
                          DEFAULT_VIEW="barcode", TYPE_LOYALTY="loyalty", ITEM_TYPES={"loyalty", "voucher", "promotion"})


def voucher(**changes):
    return model.WalletItem.from_dict({"item_id": "v1", "user_id": "owner", "name": "Gift",
                                      "item_type": "voucher", "initial_value": "25.00", **changes})


class VoucherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        storage_module.WalletStorage._instance = None
        self.storage = storage_module.WalletStorage(None)
        self.storage._save_items = AsyncMock()
        self.storage.items = [voucher()]

    async def spend(self, amount, **data):
        item = await self.storage.get_item_by_id("v1")
        return await self.storage.record_spending(item, {"amount": amount, "spent_on": "2026-11-11", **data})

    def test_legacy_items_have_no_tracking(self):
        item = model.WalletItem.from_dict({"name": "Existing card"})
        self.assertEqual(item.item_type, "loyalty")
        self.assertIsNone(item.remaining_balance)
        self.assertIsNone(voucher(initial_value=None).remaining_balance)

    async def test_decimal_arithmetic_and_roundtrip(self):
        self.storage.items = [voucher(initial_value="0.30")]
        await self.spend("0.10", note="First purchase")
        item = await self.spend("0.20")
        self.assertEqual(item.remaining_balance, 0)
        restored = model.WalletItem.from_dict(item.to_dict())
        self.assertEqual(restored.remaining_balance, 0)
        self.assertEqual(restored.spending[0]["note"], "First purchase")
        self.assertEqual(len({entry["id"] for entry in restored.spending}), 2)

    async def test_spending_survives_storage_reload(self):
        with TemporaryDirectory() as directory:
            self.storage.file_path = str(Path(directory) / "wallet.json")
            self.storage.hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=lambda fn, *args: fn(*args)))
            self.storage._save_items = storage_module.WalletStorage._save_items.__get__(self.storage)
            await self.spend("8.75", note="Lunch")
            self.storage.items = []
            await self.storage.load()
            self.assertEqual(self.storage.items[0].remaining_balance, 16.25)
            self.assertEqual(self.storage.items[0].spending[0]["note"], "Lunch")

    async def test_invalid_amounts_and_dates_do_not_change_balance(self):
        for amount in ["-1", "0", "25.01", "0.001", "NaN", "Infinity", None, {}, True]:
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                await self.spend(amount)
        with self.assertRaises(ValueError):
            await self.spend("1", spent_on="not-a-date")
        self.assertEqual(self.storage.items[0].remaining_balance, 25)
        self.storage._save_items.assert_not_awaited()

    async def test_remove_entry_restores_balance(self):
        item = await self.spend("7.45")
        self.assertEqual(item.remaining_balance, 17.55)
        restored = await self.storage.record_spending(item, {"action": "remove_spending", "spending_id": item.spending[0]["id"]})
        self.assertEqual(restored.remaining_balance, 25)
        self.assertEqual(restored.spending, [])
        with self.assertRaises(ValueError):
            await self.storage.record_spending(restored, {"action": "remove_spending", "spending_id": "missing"})

    async def test_edit_cannot_discard_or_exceed_spending(self):
        await self.spend("10")
        for changes in [{"initial_value": "9.99"}, {"initial_value": None}, {"item_type": "loyalty"}, {"currency": "USD"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await self.storage.update_item("owner", "v1", changes)
        updated = await self.storage.update_item("owner", "v1", {"name": "Updated", "initial_value": 30})
        self.assertEqual(updated.remaining_balance, 20)
        self.assertEqual(len(updated.spending), 1)

    async def test_untracked_and_loyalty_items_reject_spending(self):
        for item in [voucher(initial_value=None), voucher(item_type="loyalty")]:
            with self.assertRaises(ValueError):
                await self.storage.record_spending(item, {"amount": 1})

    async def test_concurrent_spending_cannot_overdraw(self):
        results = await asyncio.gather(self.spend(20), self.spend(20), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual(self.storage.items[0].remaining_balance, 5)

    async def test_spending_api_permissions_and_errors(self):
        view = make_view(self.storage.items[0])
        view.storage = self.storage
        for user in [OWNER, ADMIN]:
            response = await view.post(Request(user, {"action": "record_spending", "amount": 1}), "v1")
            self.assertEqual(response.status, 200)
        self.assertEqual((await view.post(Request(OTHER, {"action": "record_spending", "amount": 1}), "v1")).status, 403)
        self.assertEqual((await view.post(Request(OWNER, {"action": "record_spending", "amount": 100}), "v1")).status, 400)
        self.assertEqual((await view.post(Request(OWNER, {"action": "record_spending", "amount": 1}), "missing")).status, 404)

    async def test_put_cannot_replace_history_or_balance(self):
        view = make_view(self.storage.items[0])
        await view.put(Request(OWNER, {"notes": "Hi", "spending": [], "remaining_balance": 999}), "v1")
        view.storage.update_item.assert_awaited_once_with("owner", "v1", {"notes": "Hi"})

    async def test_create_voucher_validates_tracking_fields(self):
        view = list_module.WalletAssistantListAPI(None)
        view.storage = self.storage
        for value in ["-1", "NaN", "1.234"]:
            response = await view.post(Request(OWNER, {"name": "Gift", "item_type": "voucher", "initial_value": value}))
            self.assertEqual(response.status, 400)
        response = await view.post(Request(OWNER, {"name": "Gift", "item_type": "voucher", "initial_value": "12.50", "currency": "USD"}))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data["remaining_balance"], 12.5)
        self.assertEqual(response.data["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
