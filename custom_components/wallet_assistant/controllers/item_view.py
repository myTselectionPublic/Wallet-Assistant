from __future__ import annotations

from homeassistant.components.http import HomeAssistantView

from ..const import API_BASE
from ..services.storage import WalletStorage

# Ownership never changes through an edit, also when an admin edits.
PROTECTED_FIELDS = frozenset({"item_id", "user_id", "owner"})


def can_manage_item(user, item) -> bool:
    """Owners manage their own items; Home Assistant admins manage all items."""
    return item.user_id == user.id or bool(getattr(user, "is_admin", False))


class WalletAssistantItemAPI(HomeAssistantView):
    url = f"{API_BASE}/{{item_id}}"
    name = "api:wallet_assistant:item"
    requires_auth = True

    def __init__(self, hass):
        self.storage = WalletStorage(hass)

    async def delete(self, request, item_id):
        user = request["hass_user"]

        if not user.id or not item_id:
            return self.json({"error": "missing user_id or item_id"}, status_code=400)

        item = await self.storage.get_item_by_id(item_id)
        if not item:
            return self.json({"error": "item not found"}, status_code=404)

        if not can_manage_item(user, item):
            return self.json({"error": "not allowed to delete this item"}, status_code=403)

        # The owner's id is used so an admin can delete items of other users.
        deleted = await self.storage.delete_item(item.user_id, item_id)
        return self.json({"status": "deleted" if deleted else "not found"})

    async def put(self, request, item_id):
        data = await request.json()
        user = request["hass_user"]

        if not user.id or not item_id:
            return self.json({"error": "missing user_id or item_id"}, status_code=400)
        if not isinstance(data, dict):
            return self.json({"error": "invalid payload"}, status_code=400)

        item = await self.storage.get_item_by_id(item_id)
        if not item:
            return self.json({"error": "item not found"}, status_code=404)

        if not can_manage_item(user, item):
            return self.json({"error": "not allowed to edit this item"}, status_code=403)

        changes = {key: value for key, value in data.items() if key not in PROTECTED_FIELDS}
        updated = await self.storage.update_item(item.user_id, item_id, changes)
        if updated:
            return self.json(updated.to_dict())
        return self.json({"error": "item not found"}, status_code=404)
