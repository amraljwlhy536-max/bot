import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.integrations.sam_topup.errors import SamTopupError, SamTopupTimeoutError
from app.services.auth_service import AuthService
from app.services.json_storage import JsonStorage
from app.services.points_service import PointsService
from app.services.purchase_service import (
    InsufficientPointsForPurchaseError,
    InvalidQuantityError,
    ProductDisabledError,
    ProductNotFoundError,
    ProductSalePriceMissingError,
    PurchaseService,
    PurchaseSupplierError,
    PurchaseSupplierTimeoutError,
)


class FakeSupplier:
    def __init__(self, response=None, error=None):
        self.response = response or {
            "success": True,
            "orderId": "supplier-order-1",
            "productName": "Test Product",
            "quantity": 1,
            "unitPrice": 2.5,
            "totalCost": 2.5,
            "newBalance": 100,
            "deliveredData": {"code": "ABC-123"},
        }
        self.error = error
        self.calls = []

    async def create_order(self, product_id, quantity):
        self.calls.append((product_id, quantity))
        if self.error:
            raise self.error
        return self.response


def setup(tmp_path: Path, balance=1000, supplier=None, enabled=True, price=700):
    storage = JsonStorage(tmp_path / "data")
    user = AuthService(storage).register_with_email(1001, "user@example.com")
    points = PointsService(storage, {9001})
    if balance:
        points.credit(user["id"], balance, 9001)
    storage.write("products.json", {"products": [{
        "id": "p1",
        "supplier_product_id": "supplier-p1",
        "name": "Test Product",
        "price": 5.0,
        "inStock": 10,
        "description": "Test",
        "enabled": enabled,
        "sale_price_points": price,
    }]})
    supplier = supplier or FakeSupplier()
    service = PurchaseService(storage, supplier, lambda _: asyncio.sleep(0, result=True))
    return storage, user, service, supplier


def run(service, *args):
    return asyncio.run(service.purchase(*args))


def test_purchase_rejects_when_quantity_total_exceeds_balance(tmp_path):
    storage, _, service, supplier = setup(tmp_path, balance=1000)
    with pytest.raises(InsufficientPointsForPurchaseError):
        run(service, 1001, "p1", 2)
    assert supplier.calls == []
    assert storage.read("orders.json")["orders"] == []


def test_successful_purchase_with_enough_points(tmp_path):
    storage, user, service, supplier = setup(tmp_path, balance=2000)
    order = run(service, 1001, "p1", 2)
    assert order["supplier_order_id"] == "supplier-order-1"
    assert order["total_points"] == 1400
    assert order["delivered_data"] == {"code": "ABC-123"}
    assert storage.read("users.json")["users"][0]["points_balance"] == 600
    tx = storage.read("balance_transactions.json")["transactions"][-1]
    assert tx["type"] == "PURCHASE"
    assert tx["amount"] == 1400
    assert tx["balance_before"] == 2000
    assert tx["balance_after"] == 600
    assert tx["order_id"] == "supplier-order-1"
    assert len(storage.read("orders.json")["orders"]) == 1


def test_insufficient_points(tmp_path):
    storage, _, service, supplier = setup(tmp_path, balance=699)
    with pytest.raises(InsufficientPointsForPurchaseError):
        run(service, 1001, "p1", 1)
    assert supplier.calls == []
    assert storage.read("orders.json")["orders"] == []


def test_product_not_found(tmp_path):
    storage, _, service, supplier = setup(tmp_path)
    with pytest.raises(ProductNotFoundError):
        run(service, 1001, "missing", 1)
    assert supplier.calls == []


def test_disabled_product(tmp_path):
    storage, _, service, supplier = setup(tmp_path, enabled=False)
    with pytest.raises(ProductDisabledError):
        run(service, 1001, "p1", 1)
    assert supplier.calls == []


def test_product_without_sale_price(tmp_path):
    storage, _, service, supplier = setup(tmp_path)
    product = storage.read("products.json")["products"][0]
    del product["sale_price_points"]
    storage.write("products.json", {"products": [product]})
    with pytest.raises(ProductSalePriceMissingError):
        run(service, 1001, "p1", 1)
    assert supplier.calls == []


def test_out_of_stock_supplier_error(tmp_path):
    error = SamTopupError("out of stock", code="OUT_OF_STOCK", status_code=400)
    storage, _, service, supplier = setup(tmp_path, balance=1000, supplier=FakeSupplier(error=error))
    with pytest.raises(PurchaseSupplierError) as exc:
        run(service, 1001, "p1", 1)
    assert exc.value.error.code == "OUT_OF_STOCK"
    assert storage.read("users.json")["users"][0]["points_balance"] == 1000
    assert storage.read("orders.json")["orders"] == []
    assert storage.read("balance_transactions.json")["transactions"][0]["type"] == "ADMIN_CREDIT"


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "1"])
def test_invalid_quantity(tmp_path, quantity):
    _, _, service, supplier = setup(tmp_path)
    with pytest.raises(InvalidQuantityError):
        run(service, 1001, "p1", quantity)
    assert supplier.calls == []


def test_quantity_over_50(tmp_path):
    _, _, service, supplier = setup(tmp_path, price=1)
    with pytest.raises(InvalidQuantityError):
        run(service, 1001, "p1", 51)
    assert supplier.calls == []


@pytest.mark.parametrize("error", [
    SamTopupError("rate limited", code="RATE_LIMITED", status_code=429),
    SamTopupError("internal", code="INTERNAL_ERROR", status_code=500),
])
def test_supplier_rate_limit_and_internal_error_do_not_debit(tmp_path, error):
    storage, _, service, supplier = setup(tmp_path, supplier=FakeSupplier(error=error))
    with pytest.raises(PurchaseSupplierError):
        run(service, 1001, "p1", 1)
    assert storage.read("users.json")["users"][0]["points_balance"] == 1000
    assert len(storage.read("orders.json")["orders"]) == 0


def test_timeout_is_not_retried_and_is_recorded_uncertain(tmp_path):
    timeout = SamTopupTimeoutError("uncertain")
    storage, _, service, supplier = setup(tmp_path, supplier=FakeSupplier(error=timeout))
    with pytest.raises(PurchaseSupplierTimeoutError):
        run(service, 1001, "p1", 1)
    assert supplier.calls == [("supplier-p1", 1)]
    assert storage.read("users.json")["users"][0]["points_balance"] == 1000
    assert storage.read("balance_transactions.json")["transactions"][0]["type"] == "ADMIN_CREDIT"
    order = storage.read("orders.json")["orders"][0]
    assert order["status"] == "SUPPLIER_ORDER_UNCERTAIN"
    assert order["delivered_data"] is None
    assert order["supplier_order_id"] is None


def test_two_concurrent_purchases_cannot_double_spend(tmp_path):
    storage, _, service, supplier = setup(tmp_path, balance=1000)

    def attempt(_):
        try:
            return run(service, 1001, "p1", 1)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))

    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert any(isinstance(r, InsufficientPointsForPurchaseError) for r in failures)
    assert len(supplier.calls) == 1
    assert storage.read("users.json")["users"][0]["points_balance"] == 300
    assert len(storage.read("orders.json")["orders"]) == 1
    assert len([t for t in storage.read("balance_transactions.json")["transactions"] if t["type"] == "PURCHASE"]) == 1


def test_unregistered_user_is_rejected(tmp_path):
    storage, _, service, supplier = setup(tmp_path)
    with pytest.raises(Exception):
        run(service, 9999, "p1", 1)
    assert supplier.calls == []


def test_banned_user_is_rejected(tmp_path):
    storage, user, service, supplier = setup(tmp_path)
    u = storage.read("users.json")["users"][0]
    u["is_banned"] = True
    storage.write("users.json", {"users": [u]})
    with pytest.raises(Exception):
        run(service, 1001, "p1", 1)
    assert supplier.calls == []
