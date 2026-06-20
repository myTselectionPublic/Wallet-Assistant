from __future__ import annotations

from homeassistant.components.http import HomeAssistantView

from ..const import API_BASE
from ..services.promotion_platforms import PromotionPlatformRegistry


class WalletAssistantPromotionsAPI(HomeAssistantView):
    url = f"{API_BASE}/promotions/search"
    name = "api:wallet_assistant:promotions"
    requires_auth = True

    def __init__(self, hass):
        self.registry = PromotionPlatformRegistry(hass)

    async def get(self, request):
        query = str(request.query.get("q", "")).strip()
        promotions = await self.registry.async_search(query)
        return self.json({"promotions": [promotion.to_dict() for promotion in promotions]})
