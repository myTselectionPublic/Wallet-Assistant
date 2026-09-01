from __future__ import annotations

from homeassistant.components.http import HomeAssistantView

from ..const import API_BASE
from ..services.promotion_platforms import (
    PromotionPlatformRegistry,
    get_promotion_platform_configs,
)


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


class WalletAssistantPromotionStatusAPI(HomeAssistantView):
    url = f"{API_BASE}/promotions/status"
    name = "api:wallet_assistant:promotion_status"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass
        self.registry = PromotionPlatformRegistry(hass)

    async def get(self, request):
        await self.registry.async_load()
        cache = self.registry.cached_data
        platform_urls = {
            config.platform_id: config.base_url
            for config in get_promotion_platform_configs(self.hass)
            if config.enabled
        }
        platforms = [
            {
                **platform,
                "platform_url": platform_urls.get(
                    str(platform.get("platform_id", "")),
                    "",
                ),
            }
            for platform in cache.get("platforms", [])
        ]
        return self.json(
            {
                "updated_at": cache.get("updated_at", ""),
                "platforms": platforms,
            }
        )
