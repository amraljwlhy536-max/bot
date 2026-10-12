from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.integrations.sam_topup.errors import SamTopupError, SamTopupTimeoutError
from app.services.admin_service import AdminAuthorizationError, AdminService
from app.services.auth_service import AuthService, EmailAlreadyUsedError, InvalidEmailError
from app.services.json_storage import JsonStorage, JsonValidationError
from app.services.points_service import PointsService, UnauthorizedPointsOperationError
from app.services.purchase_service import PurchaseService, PurchaseSupplierError


ADMIN = 9001
USER = 1001
OTHER = 1002


class Supplier:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def create_order(self, product_id, quantity):
        self.calls.append((product_id, quantity))
        if self.error:
            raise self.error
        return {
            "success": True,
            "orderId": "ord-review",
            "productName": "Review Product",
            "quantity": quantity,
            "unitPrice": 2.0,
            "totalCost": 2.0 * quantity,
            "deliveredData": {"code": "DELIVERED"},
        }

    async def get_products(self):
        return {"success": True, "products": [{"id": "sp2", "name": "Synced"}]}

    async def get_balance(self):
        return {"success": True, "balance": 123.45}


class Channels:
    async def __call__(self, telegram_id):
        return True


def env(tmp_path: Path, balance=1000, supplier=None):
    storage = JsonStorage(tmp_path / "data")
    auth = AuthService(storage)
    user = auth.register_with_email(USER, "user@example.com")
    auth.register_with_email(OTHER, "other@example.com")
    points = PointsService(storage, {ADMIN})
    if balance:
        points.credit(user["id"], balance, ADMIN)
    storage.write("products.json", {"products": [{
        "id": "p1", "supplier_product_id": "sp1", "name": "P1",
        "enabled": True, "sale_price_points": 700, "inStock": 10,
        "description": "d",
    }]})
    supplier = supplier or Supplier()
    purchase = PurchaseService(storage, supplier, Channels())
    admin = AdminService(storage, {ADMIN}, points, supplier)
    return storage, auth, points, purchase, admin, supplier


def run(coro):
    return asyncio.run(coro)


def test_authentication_and_email_isolation(tmp_path):
    storage = JsonStorage(tmp_path / "data")
    auth = AuthService(storage)
    user = auth.register_with_email(USER, " User@Example.com ")
    assert user["points_balance"] == 0
    assert auth.get_user_by_telegram_id(USER)["id"] == user["id"]
    with pytest.raises(InvalidEmailError):
        auth.normalize_email("not-an-email")
    with pytest.raises(EmailAlreadyUsedError):
        auth.register_with_email(OTHER, "user@example.com")


def test_admin_and_non_admin_service_authorization(tmp_path):
    storage, auth, points, purchase, admin, _ = env(tmp_path)
    uid = auth.get_user_by_telegram_id(USER)["id"]
    assert admin.get_user(ADMIN, uid)["id"] == uid
    with pytest.raises(AdminAuthorizationError):
        admin.get_user(123, uid)
    with pytest.raises(UnauthorizedPointsOperationError):
        points.credit(uid, 1, 123)


def test_product_and_stock_related_purchase_validation(tmp_path):
    storage, auth, points, purchase, admin, supplier = env(tmp_path)
    with pytest.raises(Exception):
        run(purchase.purchase(USER, "missing", 1))
    product = storage.read("products.json")["products"][0]
    product["enabled"] = False
    storage.write("products.json", {"products": [product]})
    with pytest.raises(Exception):
        run(purchase.purchase(USER, "p1", 1))
    product["enabled"] = True
    product.pop("sale_price_points")
    storage.write("products.json", {"products": [product]})
    with pytest.raises(Exception):
        run(purchase.purchase(USER, "p1", 1))
    assert supplier.calls == []


def test_supplier_error_statuses_do_not_debit(tmp_path):
    for status, code in [(429, "RATE_LIMITED"), (500, "INTERNAL_ERROR"), (402, "INSUFFICIENT_BALANCE"), (409, "OUT_OF_STOCK")]:
        storage, auth, points, purchase, admin, supplier = env(
            tmp_path / str(status), supplier=Supplier(SamTopupError("safe supplier error", code=code, status_code=status))
        )
        with pytest.raises(PurchaseSupplierError):
            run(purchase.purchase(USER, "p1", 1))
        uid = auth.get_user_by_telegram_id(USER)["id"]
        assert points.get_balance(uid) == 1000
        assert storage.read("orders.json")["orders"] == []


def test_timeout_is_uncertain_and_not_retried(tmp_path):
    storage, auth, points, purchase, admin, supplier = env(
        tmp_path, supplier=Supplier(SamTopupTimeoutError("uncertain"))
    )
    with pytest.raises(Exception):
        run(purchase.purchase(USER, "p1", 1))
    assert supplier.calls == [("sp1", 1)]
    uid = auth.get_user_by_telegram_id(USER)["id"]
    assert points.get_balance(uid) == 1000
    order = storage.read("orders.json")["orders"][0]
    assert order["status"] == "SUPPLIER_ORDER_UNCERTAIN"
    assert all(t["type"] != "PURCHASE" for t in storage.read("balance_transactions.json")["transactions"])


def test_concurrent_purchases_do_not_double_spend(tmp_path):
    storage, auth, points, purchase, admin, supplier = env(tmp_path)
    def attempt(_):
        try:
            return run(purchase.purchase(USER, "p1", 1))
        except Exception as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sum(isinstance(x, dict) for x in results) == 1
    assert len(supplier.calls) == 1
    uid = auth.get_user_by_telegram_id(USER)["id"]
    assert points.get_balance(uid) == 300


def test_json_corruption_is_never_replaced_with_empty_data(tmp_path):
    data = tmp_path / "data"
    storage = JsonStorage(data)
    original = {"users": [{"id": "u1", "points_balance": 9}]}
    storage.write("users.json", original)
    path = data / "users.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(JsonValidationError):
        storage.read("users.json")
    assert path.read_text(encoding="utf-8") == "{bad"


def test_missing_file_is_not_silently_recreated_after_initialization(tmp_path):
    storage = JsonStorage(tmp_path / "data")
    path = storage.data_dir / "users.json"
    path.unlink()
    with pytest.raises(FileNotFoundError):
        storage.read("users.json")


def test_concurrent_json_writes_preserve_all_records(tmp_path):
    storage = JsonStorage(tmp_path / "data")
    def add(i):
        storage.append("users.json", {"id": str(i)})
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add, range(100)))
    users = storage.read("users.json")["users"]
    assert len(users) == 100
    assert {u["id"] for u in users} == {str(i) for i in range(100)}


def test_secrets_not_in_user_error_response_and_safe_logging_source():
    from pathlib import Path
    user_source = Path("app/handlers/user.py").read_text(encoding="utf-8")
    admin_source = Path("app/handlers/admin.py").read_text(encoding="utf-8")
    assert "BOT_TOKEN" not in user_source
    assert "SAM_API_KEY" not in user_source
    assert "BOT_TOKEN" not in admin_source
    assert "SAM_API_KEY" not in admin_source
    assert "logger.exception" in user_source
    assert "logger.exception" in admin_source


def test_user_order_isolation_uses_purchase_transaction_owner(tmp_path):
    storage, auth, points, purchase, admin, supplier = env(tmp_path)
    users = storage.read("users.json")["users"]
    uid1 = next(u["id"] for u in users if u["telegram_id"] == USER)
    uid2 = next(u["id"] for u in users if u["telegram_id"] == OTHER)
    storage.append("orders.json", {"supplier_order_id": "o1", "product_id": "p1"})
    storage.append("orders.json", {"supplier_order_id": "o2", "product_id": "p1"})
    storage.append("balance_transactions.json", {"id":"t1","user_id":uid1,"type":"PURCHASE","amount":1,"balance_before":2,"balance_after":1,"reason":None,"admin_id":None,"order_id":"o1","created_at":"now"})
    storage.append("balance_transactions.json", {"id":"t2","user_id":uid2,"type":"PURCHASE","amount":1,"balance_before":2,"balance_after":1,"reason":None,"admin_id":None,"order_id":"o2","created_at":"now"})
    owned = {t["order_id"] for t in storage.read("balance_transactions.json")["transactions"] if t["type"] == "PURCHASE" and t["user_id"] == uid1}
    assert owned == {"o1"}
