"""Local image upload regressions without a Home Assistant installation."""
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import make_mocked_request


def load_view():
    path = Path(__file__).resolve().parents[1] / "custom_components/wallet_assistant/controllers/image_view.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.level or node.module.startswith("homeassistant"))
    )]

    class HomeAssistantView:
        def json(self, data, status_code=200):
            return SimpleNamespace(data=data, status=status_code)

    namespace = {"HomeAssistantView": HomeAssistantView, "API_BASE": "/api/wallet_assistant"}
    exec(compile(tree, str(path), "exec"), namespace)
    return SimpleNamespace(**namespace)


module = load_view()
PNG = b"\x89PNG\r\n\x1a\nimage-data"


class Request:
    def __init__(self, chunks, content_length=None):
        self.content_length = content_length

        async def iter_chunked(size):
            for chunk in chunks:
                yield chunk

        self.content = SimpleNamespace(iter_chunked=iter_chunked)


class ImageUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_route_is_not_captured_by_item_route(self):
        with TemporaryDirectory() as config:
            async def executor(function, *args):
                return function(*args)

            view = module.WalletAssistantImageAPI(SimpleNamespace(
                config=SimpleNamespace(path=lambda *parts: str(Path(config).joinpath(*parts))),
                async_add_executor_job=executor,
            ))
            item_handler = AsyncMock(side_effect=AssertionError("Image routed to JSON item handler"))
            app = web.Application()
            # Deliberately put the dynamic route first to reproduce the collision.
            app.router.add_post("/api/wallet_assistant/{item_id}", item_handler)
            app.router.add_post(view.url, view.post)
            request = make_mocked_request("POST", view.url, payload=Request([PNG]).content)
            match = await app.router.resolve(request)
            response = await match.handler(request)
            self.assertEqual(response.status, 201)
            stored = Path(config) / "www" / response.data["path"].removeprefix("/local/")
            self.assertEqual(stored.read_bytes(), PNG)
            item_handler.assert_not_called()

    def test_extensions_and_rejected_content(self):
        for content, extension in [
            (PNG, "png"), (b"\xff\xd8\xffdata", "jpg"),
            (b"GIF89adata", "gif"), (b"RIFF1234WEBPdata", "webp"),
        ]:
            self.assertEqual(module.image_extension(content), extension)
        for content in [b"", b"<svg onload='alert(1)'/>", b"not an image"]:
            with self.assertRaises(ValueError):
                module.image_extension(content)

    async def test_upload_writes_generated_local_path_without_overwriting(self):
        with TemporaryDirectory() as config:
            async def executor(function, *args):
                return function(*args)

            hass = SimpleNamespace(
                config=SimpleNamespace(path=lambda *parts: str(Path(config).joinpath(*parts))),
                async_add_executor_job=executor,
            )
            view = module.WalletAssistantImageAPI(hass)
            self.assertTrue(view.requires_auth)
            paths = []
            for _ in range(2):
                response = await view.post(Request([PNG[:5], PNG[5:]]))
                self.assertEqual(response.status, 201)
                path = response.data["path"]
                self.assertRegex(path, r"^/local/wallet_assistant/images/[0-9a-f]{32}\.png$")
                self.assertEqual((Path(config) / "www" / path.removeprefix("/local/")).read_bytes(), PNG)
                paths.append(path)
            self.assertNotEqual(*paths)

    async def test_invalid_and_oversized_uploads_do_not_write_files(self):
        executor = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        with TemporaryDirectory() as config:
            view = module.WalletAssistantImageAPI(SimpleNamespace(
                config=SimpleNamespace(path=lambda *parts: str(Path(config).joinpath(*parts))),
                async_add_executor_job=executor,
            ))
            self.assertEqual((await view.post(Request([b"<svg/>"]))).status, 400)
            executor.reset_mock()
            for request in [
                Request([], module.MAX_IMAGE_BYTES + 1),
                Request([b"x" * module.MAX_IMAGE_BYTES, b"x"]),
            ]:
                self.assertEqual((await view.post(request)).status, 413)
            executor.assert_not_called()
            self.assertEqual(list(Path(config).iterdir()), [])

    async def test_filesystem_error_returns_actionable_response(self):
        view = module.WalletAssistantImageAPI(SimpleNamespace(
            config=SimpleNamespace(path=lambda *args: "unused"),
            async_add_executor_job=AsyncMock(side_effect=OSError("full")),
        ))
        with self.assertLogs(level="ERROR"):
            response = await view.post(Request([PNG]))
        self.assertEqual(response.status, 500)
        self.assertIn("Unable to store image", response.data["error"])


if __name__ == "__main__":
    unittest.main()
