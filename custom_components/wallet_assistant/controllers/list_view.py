from __future__ import annotations

from uuid import uuid4

from homeassistant.components.http import HomeAssistantView

from ..const import (
    API_BASE,
    DEFAULT_BARCODE_FORMAT,
    DEFAULT_VIEW,
    ITEM_TYPES,
    TYPE_LOYALTY,
)
from ..models.card import WalletItem
from ..services.storage import WalletStorage


class WalletAssistantListAPI(HomeAssistantView):
    url = API_BASE
    name = "api:wallet_assistant:list"
    requires_auth = True

    def __init__(self, hass):
        self.storage = WalletStorage(hass)

    async def get(self, request):
        items = await self.storage.get_all()
        return self.json([item.to_dict() for item in items])

    async def post(self, request):
        data = await request.json()
        user = request["hass_user"]
        if not isinstance(data, dict):
            return self.json({"error": "invalid payload"}, status_code=400)
        if not data.get("name"):
            return self.json({"error": "missing fields"}, status_code=400)

        item_type = data.get("item_type") or data.get("type") or TYPE_LOYALTY
        if item_type == "card":
            item_type = TYPE_LOYALTY
        if item_type not in ITEM_TYPES:
            return self.json({"error": "invalid item_type"}, status_code=400)

        item_data = (
            {
                "item_id": str(uuid4()),
                "name": data.get("name"),
                "code": data.get("code", ""),
                "owner": getattr(user, "name", None) or data.get("owner", ""),
                "user_id": user.id,
                "item_type": item_type,
                "format": data.get("format") or DEFAULT_BARCODE_FORMAT,
                "default_view": data.get("default_view") or DEFAULT_VIEW,
                "logo_slug": _clean_optional_string(data.get("logo_slug")),
                "expires_on": _clean_optional_string(data.get("expires_on")),
                "notes": _clean_optional_string(data.get("notes")),
                "promo_url": _clean_optional_string(data.get("promo_url")),
                "initial_value": data.get("initial_value"),
                "currency": data.get("currency", "EUR"),
            }
        )
        try:
            item = WalletItem.from_dict(item_data)
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        saved = await self.storage.add_item(item)
        return self.json(saved.to_dict())

def _clean_optional_string(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
