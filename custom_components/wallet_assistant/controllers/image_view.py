"""Upload device photos for use as local card images."""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from homeassistant.components.http import HomeAssistantView

from ..const import API_BASE

MAX_IMAGE_BYTES = 10 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)


def image_extension(data: bytes) -> str:
    """Determine the stored extension from content, never the supplied filename."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError("Choose a JPEG, PNG, GIF or WebP image")


def store_image(directory: Path, data: bytes) -> str:
    """Write within the integration's image folder using a generated filename."""
    extension = image_extension(data)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.{extension}"
    destination = directory / filename
    try:
        with destination.open("xb") as image:
            image.write(data)
    except OSError:
        destination.unlink(missing_ok=True)
        raise
    return f"/local/wallet_assistant/images/{filename}"


class WalletAssistantImageAPI(HomeAssistantView):
    # Two path segments prevent /{item_id} from capturing a binary upload,
    # regardless of the order in which Home Assistant registers the views.
    url = f"{API_BASE}/images/upload"
    name = "api:wallet_assistant:image"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def post(self, request):
        if request.content_length and request.content_length > MAX_IMAGE_BYTES:
            return self.json({"error": "Image must be 10 MB or smaller"}, status_code=413)
        data = bytearray()
        async for chunk in request.content.iter_chunked(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                return self.json({"error": "Image must be 10 MB or smaller"}, status_code=413)
        try:
            path = await self.hass.async_add_executor_job(
                store_image,
                Path(self.hass.config.path("www", "wallet_assistant", "images")),
                bytes(data),
            )
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        except OSError:
            _LOGGER.exception("Unable to store card image")
            return self.json({"error": "Unable to store image in Home Assistant"}, status_code=500)
        return self.json({"path": path}, status_code=201)
