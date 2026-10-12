from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.integrations.sam_topup.errors import SamTopupError, SamTopupTimeoutError
from app.services.json_storage import JsonStorage


class PurchaseError(ValueError):
    pass


class UserNotFoundForPurchaseError(PurchaseError):
    pass


class UserBannedError(PurchaseError):
    pass


class RequiredChannelsIncompleteError(PurchaseError):
    pass


class ProductNotFoundError(PurchaseError):
    pass


class ProductDisabledError(PurchaseError):
    pass


class ProductSalePriceMissingError(PurchaseError):
    pass


class InvalidQuantityError(PurchaseError):
    pass


class InsufficientPointsForPurchaseError(PurchaseError):
    pass


class PurchaseSupplierError(PurchaseError):
    def __init__(self, error: SamTopupError):
        super().__init__(str(error))
        self.error = error


class PurchaseSupplierTimeoutError(PurchaseError):
    def __init__(self, error: SamTopupTimeoutError):
        super().__init__(str(error))
        self.error = error


class PurchaseService:
    USERS_FILE = "users.json"
    PRODUCTS_FILE = "products.json"
    ORDERS_FILE = "orders.json"
    TRANSACTIONS_FILE = "balance_transactions.json"
    PURCHASE_TYPE = "PURCHASE"
    UNCERTAIN_STATUS = "SUPPLIER_ORDER_UNCERTAIN"
    SUCCESS_STATUS = "SUCCESS"
    MAX_QUANTITY = 50

    def __init__(self, storage: JsonStorage, supplier_client: Any, required_channels_checker: Callable[[int], Awaitable[Any]]):
        self.storage = storage
        self.supplier_client = supplier_client
        self.required_channels_checker = required_channels_checker

    async def purchase(self, telegram_id: int, product_id: str, quantity: int) -> dict[str, Any]:
        user_snapshot = self.storage.find(self.USERS_FILE, lambda u: u.get("telegram_id") == telegram_id)
        if user_snapshot is None:
            raise UserNotFoundForPurchaseError("User is not registered")
        if user_snapshot.get("is_banned") is True:
            raise UserBannedError("User is banned")

        channels_result = await self.required_channels_checker(telegram_id)
        channels_complete = channels_result if isinstance(channels_result, bool) else bool(getattr(channels_result, "is_complete", False))
        if not channels_complete:
            raise RequiredChannelsIncompleteError("Required channels are incomplete")

        self._validate_quantity(quantity)
        product_snapshot = self.storage.find_by_id(self.PRODUCTS_FILE, product_id)
        self._validate_product(product_snapshot)

        sale_price = product_snapshot["sale_price_points"]
        total_points = sale_price * quantity

        # The storage lock is held while the supplier call is made. This intentionally
        # serializes purchases that can affect the same JSON balance and prevents two
        # concurrent requests from spending the same points.
        def operation(documents: dict[str, dict[str, list[Any]]]) -> dict[str, Any]:
            users = documents[self.USERS_FILE]["users"]
            products = documents[self.PRODUCTS_FILE]["products"]
            orders = documents[self.ORDERS_FILE]["orders"]
            transactions = documents[self.TRANSACTIONS_FILE]["transactions"]

            user = next((u for u in users if u.get("telegram_id") == telegram_id), None)
            if user is None:
                raise UserNotFoundForPurchaseError("User is not registered")
            if user.get("is_banned") is True:
                raise UserBannedError("User is banned")

            product = next((p for p in products if p.get("id") == product_id), None)
            self._validate_product(product)
            balance = user.get("points_balance")
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
                raise PurchaseError("Invalid user points balance")
            if balance < total_points:
                raise InsufficientPointsForPurchaseError("Insufficient points")

            try:
                supplier_response = _run_async_in_sync(self.supplier_client.create_order(
                    product["supplier_product_id"], quantity
                ))
            except SamTopupTimeoutError as exc:
                orders.append({
                    "supplier_order_id": None,
                    "product_id": product["id"],
                    "product_name": product.get("name"),
                    "quantity": quantity,
                    "supplier_unit_price_usdt": None,
                    "supplier_total_cost_usdt": None,
                    "sale_price_points": sale_price,
                    "total_points": total_points,
                    "delivered_data": None,
                    "status": self.UNCERTAIN_STATUS,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                return {
                    "supplier_order_id": None,
                    "product_id": product["id"],
                    "product_name": product.get("name"),
                    "quantity": quantity,
                    "supplier_unit_price_usdt": None,
                    "supplier_total_cost_usdt": None,
                    "sale_price_points": sale_price,
                    "total_points": total_points,
                    "delivered_data": None,
                    "status": self.UNCERTAIN_STATUS,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            except SamTopupError as exc:
                raise PurchaseSupplierError(exc) from exc

            supplier_order_id = supplier_response.get("orderId")
            if supplier_order_id is None:
                raise PurchaseError("Successful supplier response did not contain orderId")

            now = datetime.now(timezone.utc).isoformat()
            user["points_balance"] = balance - total_points
            user["updated_at"] = now

            order = {
                "supplier_order_id": supplier_order_id,
                "product_id": product["id"],
                "product_name": supplier_response.get("productName", product.get("name")),
                "quantity": quantity,
                "supplier_unit_price_usdt": supplier_response.get("unitPrice"),
                "supplier_total_cost_usdt": supplier_response.get("totalCost"),
                "sale_price_points": sale_price,
                "total_points": total_points,
                "delivered_data": supplier_response.get("deliveredData"),
                "status": self.SUCCESS_STATUS,
                "created_at": now,
            }
            orders.append(order)
            transactions.append({
                "id": str(uuid4()),
                "user_id": user["id"],
                "type": self.PURCHASE_TYPE,
                "amount": total_points,
                "balance_before": balance,
                "balance_after": balance - total_points,
                "reason": None,
                "admin_id": None,
                "order_id": supplier_order_id,
                "created_at": now,
            })
            return order

        result = self.storage.atomic_update(
            [self.USERS_FILE, self.PRODUCTS_FILE, self.ORDERS_FILE, self.TRANSACTIONS_FILE],
            operation,
        )
        if isinstance(result, dict) and result.get("status") == self.UNCERTAIN_STATUS:
            raise PurchaseSupplierTimeoutError(SamTopupTimeoutError("Supplier order outcome is uncertain"))
        return result

    @staticmethod
    def _validate_quantity(quantity: int) -> None:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise InvalidQuantityError("Quantity must be a positive integer")
        if quantity > PurchaseService.MAX_QUANTITY:
            raise InvalidQuantityError("Quantity cannot exceed 50")

    @staticmethod
    def _validate_product(product: dict[str, Any] | None) -> None:
        if product is None:
            raise ProductNotFoundError("Product not found")
        if product.get("enabled") is not True:
            raise ProductDisabledError("Product is disabled")
        price = product.get("sale_price_points")
        if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            raise ProductSalePriceMissingError("Product sale_price_points is missing or invalid")
        supplier_id = product.get("supplier_product_id")
        if not isinstance(supplier_id, str) or not supplier_id:
            raise ProductNotFoundError("Product supplier_product_id is missing")


def _run_async_in_sync(awaitable):
    # The callback is executed under a synchronous file lock. Run the async supplier
    # request in a worker thread so the caller's event loop remains available.
    import asyncio
    import threading

    result: list[Any] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]
