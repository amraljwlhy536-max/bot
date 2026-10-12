import asyncio
from pathlib import Path

import pytest

from app.integrations.sam_topup.errors import SamTopupError
from app.services.admin_service import AdminAuthorizationError, AdminService, AdminValidationError
from app.services.auth_service import AuthService
from app.services.json_storage import JsonStorage
from app.services.points_service import PointsService


class FakeSupplier:
    def __init__(self):
        self.products = [{
            "id": "sp1", "name": "Product 1", "price": 2.0,
            "inStock": 10, "description": "D",
        }]
        self.balance = {"success": True, "balance": 150.5}

    async def get_products(self):
        return {"success": True, "count": len(self.products), "products": self.products}

    async def get_balance(self):
        return self.balance


def setup(tmp_path: Path):
    storage = JsonStorage(tmp_path / "data")
    user = AuthService(storage).register_with_email(1001, "user@example.com")
    points = PointsService(storage, {9001})
    supplier = FakeSupplier()
    service = AdminService(storage, {9001}, points, supplier)
    return storage, user, service, supplier


def test_admin_enters_and_non_admin_is_rejected(tmp_path):
    _, user, service, _ = setup(tmp_path)
    assert 9001 in service.admin_ids
    with pytest.raises(AdminAuthorizationError):
        service.list_products(1001)
    with pytest.raises(AdminAuthorizationError):
        service.search_users(1001, "1001")


def test_non_admin_callback_cannot_bypass_server_authorization(tmp_path):
    _, _, service, _ = setup(tmp_path)
    # Callback data is not an authorization credential; the service rejects the actor.
    with pytest.raises(AdminAuthorizationError):
        service.ban_user(1001, "missing")
    with pytest.raises(AdminAuthorizationError):
        service.set_sale_price_points(1001, "missing", 100)


def test_points_credit_and_debit(tmp_path):
    storage, user, service, _ = setup(tmp_path)
    tx1 = service.credit_points(9001, user["id"], 200, "test")
    tx2 = service.debit_points(9001, user["id"], 50, "test")
    assert tx1["balance_after"] == 200
    assert tx2["balance_after"] == 150
    assert storage.read("users.json")["users"][0]["points_balance"] == 150


def test_ban_and_unban(tmp_path):
    storage, user, service, _ = setup(tmp_path)
    assert service.ban_user(9001, user["id"])["is_banned"] is True
    assert storage.read("users.json")["users"][0]["is_banned"] is True
    assert service.unban_user(9001, user["id"])["is_banned"] is False


def test_product_price_enable_disable(tmp_path):
    storage, _, service, _ = setup(tmp_path)
    storage.write("products.json", {"products": [{"id": "p1", "supplier_product_id": "sp1", "name": "P", "enabled": True}]})
    assert service.set_sale_price_points(9001, "p1", 700)["sale_price_points"] == 700
    assert service.set_product_enabled(9001, "p1", False)["enabled"] is False
    assert service.set_product_enabled(9001, "p1", True)["enabled"] is True


def test_product_sync_preserves_local_controls(tmp_path):
    storage, _, service, _ = setup(tmp_path)
    storage.write("products.json", {"products": [{
        "id": "local1", "supplier_product_id": "sp1", "name": "Old", "enabled": True, "sale_price_points": 700
    }]})
    products = asyncio.run(service.sync_products(9001))
    assert len(products) == 1
    assert products[0]["name"] == "Product 1"
    assert products[0]["enabled"] is True
    assert products[0]["sale_price_points"] == 700


def test_channels_management(tmp_path):
    storage, _, service, _ = setup(tmp_path)
    channel = service.add_channel(9001, "-1001", "example", "Example", True)
    assert channel["is_active"] is True
    assert service.set_channel_active(9001, channel["id"], False)["is_active"] is False
    assert service.set_channel_active(9001, channel["id"], True)["is_active"] is True


def test_orders_and_supplier_balance(tmp_path):
    storage, _, service, _ = setup(tmp_path)
    storage.write("orders.json", {"orders": [{
        "supplier_order_id": "o1", "product_id": "p1", "product_name": "P",
        "quantity": 1, "delivered_data": {"code": "SECRET"}, "status": "SUCCESS"
    }]})
    assert service.search_orders(9001, "o1")[0]["delivered_data"] == {"code": "SECRET"}
    assert asyncio.run(service.supplier_balance(9001))["balance"] == 150.5


def test_statistics_uses_only_existing_json_data(tmp_path):
    storage, user, service, _ = setup(tmp_path)
    storage.write("orders.json", {"orders": [{"supplier_order_id": "o1", "status": "SUCCESS"}]})
    storage.write("balance_transactions.json", {"transactions": [{
        "id": "t1", "user_id": user["id"], "type": "PURCHASE", "amount": 700,
        "balance_before": 1000, "balance_after": 300, "reason": None, "admin_id": None,
        "order_id": "o1", "created_at": "2026-01-01T00:00:00+00:00"
    }]})
    assert service.statistics(9001) == {
        "users_count": 1, "orders_count": 1, "purchase_count": 1, "total_points_used": 700
    }
