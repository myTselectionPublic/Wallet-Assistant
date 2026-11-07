from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from ..const import API_BASE, DOMAIN
from ..services.logo_cache import LogoCache, normalize_logo_size, normalize_logo_slug


class WalletAssistantLogoAPI(HomeAssistantView):
    """Serve Logo.dev logos through the local cache."""

    url = f"{API_BASE}/logo"
    name = "api:wallet_assistant:logo"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    @property
    def cache(self) -> LogoCache:
        data = self.hass.data.setdefault(DOMAIN, {})
        if "logo_cache" not in data:
            data["logo_cache"] = LogoCache(self.hass)
        return data["logo_cache"]

    async def get(self, request):
        slug = normalize_logo_slug(request.query.get("slug"))
        if slug is None:
            return self.json({"error": "invalid slug"}, status_code=400)
        size = normalize_logo_size(request.query.get("size"))

        data = await self.cache.async_get(slug, size)
        if data is None:
            return self.json({"error": "logo unavailable"}, status_code=404)

        return web.Response(
            body=data,
            content_type=_sniff_image_type(data),
            headers={"Cache-Control": "private, max-age=86400"},
        )


def _sniff_image_type(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/webp"
