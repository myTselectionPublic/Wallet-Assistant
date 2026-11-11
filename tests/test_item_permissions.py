"""Edit/delete permissions of wallet items (no Home Assistant install needed)."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "wallet_assistant"


def load_item_view():
    """Execute item_view.py definitions with HA classes replaced by stubs."""
    tree = ast.parse((COMPONENT / "controllers" / "item_view.py").read_text(encoding="utf-8"))
    body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]

    class HomeAssistantView:
        def json(self, data, status_code=200):
            return SimpleNamespace(data=data, status=status_code)

    namespace = {
        "HomeAssistantView": HomeAssistantView,
        "API_BASE": "/api/wallet_assistant",
        "WalletStorage": lambda hass: None,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), "item_view.py", "exec"), namespace)
    return SimpleNamespace(**namespace)


module = load_item_view()

OWNER = SimpleNamespace(id="owner", is_admin=False)
OTHER = SimpleNamespace(id="other", is_admin=False)
ADMIN = SimpleNamespace(id="admin", is_admin=True)


def make_view(item):
    view = module.WalletAssistantItemAPI(None)
    view.storage = SimpleNamespace(
        get_item_by_id=AsyncMock(return_value=item),
        update_item=AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"ok": True})),
        delete_item=AsyncMock(return_value=True),
    )
    return view


class Request(dict):
    def __init__(self, user, payload=None):
        super().__init__(hass_user=user)
        self.json = AsyncMock(return_value=payload if payload is not None else {})


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item = SimpleNamespace(item_id="i1", user_id="owner")

    def test_can_manage_item(self):
        self.assertTrue(module.can_manage_item(OWNER, self.item))
        self.assertTrue(module.can_manage_item(ADMIN, self.item))
        self.assertFalse(module.can_manage_item(OTHER, self.item))

    async def test_other_user_cannot_edit_or_delete(self):
        view = make_view(self.item)
        self.assertEqual((await view.put(Request(OTHER, {"name": "x"}), "i1")).status, 403)
        self.assertEqual((await view.delete(Request(OTHER), "i1")).status, 403)
        view.storage.update_item.assert_not_called()
        view.storage.delete_item.assert_not_called()

    async def test_admin_edit_keeps_ownership(self):
        view = make_view(self.item)
        payload = {"name": "New", "user_id": "admin", "owner": "Admin", "item_id": "hack"}
        response = await view.put(Request(ADMIN, payload), "i1")

        self.assertEqual(response.status, 200)
        view.storage.update_item.assert_awaited_once_with("owner", "i1", {"name": "New"})

    async def test_admin_delete_uses_owner_id(self):
        view = make_view(self.item)
        response = await view.delete(Request(ADMIN), "i1")

        self.assertEqual(response.data, {"status": "deleted"})
        view.storage.delete_item.assert_awaited_once_with("owner", "i1")

    async def test_owner_edit_still_works(self):
        view = make_view(self.item)
        await view.put(Request(OWNER, {"notes": "n"}), "i1")
        view.storage.update_item.assert_awaited_once_with("owner", "i1", {"notes": "n"})

    async def test_missing_item_returns_404(self):
        view = make_view(None)
        self.assertEqual((await view.put(Request(ADMIN, {"name": "x"}), "i1")).status, 404)


if __name__ == "__main__":
    unittest.main()
