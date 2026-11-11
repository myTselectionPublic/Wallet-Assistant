from __future__ import annotations

from datetime import date
from asyncio import Lock
import json
import os
from typing import List, Optional
from uuid import uuid4

from homeassistant.helpers.dispatcher import async_dispatcher_send

from ..const import SIGNAL_ITEMS_UPDATED, TYPE_VOUCHER
from ..models.card import WalletItem, money
from .path_utils import get_storage_path


class WalletStorage:
    _instance = None

    def __new__(cls, hass):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, hass):
        if self._initialized:
            return
        self.hass = hass
        self.file_path = get_storage_path(hass)
        self.items: List[WalletItem] = []
        self._save_lock = Lock()
        self._initialized = True

    async def load(self) -> None:
        raw_list = await self.hass.async_add_executor_job(self._read_items)
        self.items = [WalletItem.from_dict(item) for item in raw_list if item.get("item_id")]

    async def add_item(self, item: WalletItem) -> WalletItem:
        self.items.append(item)
        await self._save_items()
        return item

    async def update_item(self, user_id: str, item_id: str, new_data: dict) -> Optional[WalletItem]:
        for idx, item in enumerate(self.items):
            if item.item_id == item_id and item.user_id == user_id:
                if item.spending and new_data.get("item_type", item.item_type) != TYPE_VOUCHER:
                    raise ValueError("Remove voucher spending before changing its type")
                if item.spending and new_data.get("currency", item.currency) != item.currency:
                    raise ValueError("Remove voucher spending before changing its currency")
                merged = {**item.to_dict(), **new_data, "item_id": item.item_id, "user_id": item.user_id}
                updated_item = WalletItem.from_dict(merged)
                self.items[idx] = updated_item
                await self._save_items()
                return updated_item
        return None

    async def record_spending(self, item, data):
        if item.item_type != TYPE_VOUCHER or item.initial_value is None:
            raise ValueError("Enable balance tracking on this voucher first")
        if data.get("action") == "remove_spending":
            spending = [entry for entry in item.spending if entry["id"] != data.get("spending_id")]
            if len(spending) == len(item.spending):
                raise ValueError("Spending entry not found")
        else:
            amount = money(data.get("amount"))
            if amount <= 0 or amount > money(item.remaining_balance):
                raise ValueError("Spending must be greater than zero and no more than the remaining balance")
            spent_on = data.get("spent_on") or date.today().isoformat()
            try:
                date.fromisoformat(spent_on)
            except (TypeError, ValueError):
                raise ValueError("Invalid spending date") from None
            note = data.get("note", "")
            if not isinstance(note, str):
                raise ValueError("Invalid spending note")
            spending = [*item.spending, {"id": str(uuid4()), "amount": float(amount), "spent_on": spent_on, "note": note.strip()}]
        return await self.update_item(item.user_id, item.item_id, {"spending": spending})

    async def delete_item(self, user_id: str, item_id: str) -> bool:
        original_len = len(self.items)
        self.items = [item for item in self.items if not (item.item_id == item_id and item.user_id == user_id)]
        if len(self.items) < original_len:
            await self._save_items()
            return True
        return False

    async def get_all(self) -> List[WalletItem]:
        return self.items

    async def get_item_by_id(self, item_id: str) -> Optional[WalletItem]:
        return next((item for item in self.items if item.item_id == item_id), None)

    def get_user_items(self, user_id: str) -> List[WalletItem]:
        return [item for item in self.items if item.user_id == user_id]

    def count_expiring_vouchers(self, days: int) -> int:
        today = date.today()
        count = 0
        for item in self.items:
            if item.item_type != TYPE_VOUCHER or not item.expires_on:
                continue
            expiry = _parse_date(item.expires_on)
            if expiry is None:
                continue
            if 0 <= (expiry - today).days <= days:
                count += 1
        return count

    async def _save_items(self) -> None:
        async with self._save_lock:
            data = [item.to_dict() for item in self.items]
            await self.hass.async_add_executor_job(self._write_items, data)
            async_dispatcher_send(self.hass, SIGNAL_ITEMS_UPDATED)

    def _read_items(self) -> list[dict]:
        if not os.path.exists(self.file_path):
            return []

        with open(self.file_path, "r", encoding="utf-8") as file:
            try:
                return json.load(file) or []
            except json.JSONDecodeError:
                return []

    def _write_items(self, data: list[dict]) -> None:
        with open(self.file_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

def _parse_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
