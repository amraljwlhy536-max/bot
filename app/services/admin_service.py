from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.services.json_storage import JsonStorage
from app.services.points_service import PointsService


class AdminAuthorizationError(PermissionError):
    pass


class AdminValidationError(ValueError):
    pass


class AdminService:
    USERS_FILE = "users.json"
    PRODUCTS_FILE = "products.json"
    ORDERS_FILE = "orders.json"
    CHANNELS_FILE = "required_channels.json"
    TRANSACTIONS_FILE = "balance_transactions.json"

    def __init__(self, storage: JsonStorage, admin_ids: set[int] | frozenset[int], points_service: PointsService, supplier_client: Any):
        self.storage = storage
        self.admin_ids = {int(x) for x in admin_ids}
        self.points_service = points_service
        self.supplier_client = supplier_client

    def require_admin(self, telegram_id: int) -> None:
        if int(telegram_id) not in self.admin_ids:
            raise AdminAuthorizationError("Admin permission required")

    def search_users(self, admin_id: int, query: str) -> list[dict[str, Any]]:
        self.require_admin(admin_id)
        q = str(query).strip().casefold()
        if not q:
            return []
        users = self.storage.read(self.USERS_FILE)["users"]
        return [
            user for user in users
            if q in str(user.get("id", "")).casefold()
            or q in str(user.get("telegram_id", "")).casefold()
            or q in str(user.get("email", "")).casefold()
            or q in str(user.get("username", "")).casefold()
        ]

    def get_user(self, admin_id: int, user_id: str) -> dict[str, Any] | None:
        self.require_admin(admin_id)
        return self.storage.find_by_id(self.USERS_FILE, user_id)

    def ban_user(self, admin_id: int, user_id: str) -> dict[str, Any]:
        self.require_admin(admin_id)
        return self._set_banned(user_id, True)

    def unban_user(self, admin_id: int, user_id: str) -> dict[str, Any]:
        self.require_admin(admin_id)
        return self._set_banned(user_id, False)

    def _set_banned(self, user_id: str, banned: bool) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] | None = None

        def operation(documents: dict[str, dict[str, list[Any]]]) -> None:
            nonlocal result
            users = documents[self.USERS_FILE]["users"]
            user = next((u for u in users if u.get("id") == user_id), None)
            if user is None:
                raise AdminValidationError("User not found")
            user["is_banned"] = banned
            user["updated_at"] = now
            result = user.copy()

        self.storage.atomic_update([self.USERS_FILE], operation)
        assert result is not None
        return result

    def credit_points(self, admin_id: int, user_id: str, amount: int, reason: str | None = None) -> dict[str, Any]:
        self.require_admin(admin_id)
        return self.points_service.credit(user_id, amount, admin_id, reason)

    def debit_points(self, admin_id: int, user_id: str, amount: int, reason: str | None = None) -> dict[str, Any]:
        self.require_admin(admin_id)
        return self.points_service.debit(user_id, amount, admin_id, reason)

    def list_products(self, admin_id: int) -> list[dict[str, Any]]:
        self.require_admin(admin_id)
        return self.storage.read(self.PRODUCTS_FILE)["products"]

    async def sync_products(self, admin_id: int) -> list[dict[str, Any]]:
        self.require_admin(admin_id)
        response = await self.supplier_client.get_products()
        supplier_products = response.get("products")
        if not isinstance(supplier_products, list):
            raise AdminValidationError("Supplier products response is invalid")

        def operation(documents: dict[str, dict[str, list[Any]]]) -> list[dict[str, Any]]:
            products = documents[self.PRODUCTS_FILE]["products"]
            by_supplier_id = {p.get("supplier_product_id"): p for p in products}
            for supplier in supplier_products:
                supplier_id = supplier.get("id")
                if not isinstance(supplier_id, str) or not supplier_id:
                    continue
                local = by_supplier_id.get(supplier_id)
                if local is None:
                    local = {
                        "id": str(uuid4()),
                        "supplier_product_id": supplier_id,
                    }
                    products.append(local)
                for source_key, target_key in (
                    ("name", "name"),
                    ("price", "price"),
                    ("inStock", "inStock"),
                    ("description", "description"),
                ):
                    if source_key in supplier:
                        local[target_key] = supplier[source_key]
            return products

        return self.storage.atomic_update([self.PRODUCTS_FILE], operation)

    def set_product_enabled(self, admin_id: int, product_id: str, enabled: bool) -> dict[str, Any]:
        self.require_admin(admin_id)
        return self._update_product(product_id, {"enabled": enabled})

    def set_sale_price_points(self, admin_id: int, product_id: str, amount: int) -> dict[str, Any]:
        self.require_admin(admin_id)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise AdminValidationError("sale_price_points must be a positive integer")
        return self._update_product(product_id, {"sale_price_points": amount})

    def _update_product(self, product_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        def operation(documents: dict[str, dict[str, list[Any]]]) -> None:
            nonlocal result
            products = documents[self.PRODUCTS_FILE]["products"]
            product = next((p for p in products if p.get("id") == product_id), None)
            if product is None:
                raise AdminValidationError("Product not found")
            product.update(updates)
            result = product.copy()

        self.storage.atomic_update([self.PRODUCTS_FILE], operation)
        assert result is not None
        return result

    def add_channel(self, admin_id: int, chat_id: str, username: str | None, title: str, is_required: bool) -> dict[str, Any]:
        self.require_admin(admin_id)
        if not chat_id.strip() or not title.strip():
            raise AdminValidationError("chat_id and title are required")
        now = datetime.now(timezone.utc).isoformat()
        channel = {
            "id": str(uuid4()),
            "chat_id": chat_id.strip(),
            "username": username.strip().lstrip("@") if username and username.strip() else None,
            "title": title.strip(),
            "is_required": bool(is_required),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        self.storage.append(self.CHANNELS_FILE, channel)
        return channel

    def list_channels(self, admin_id: int) -> list[dict[str, Any]]:
        self.require_admin(admin_id)
        return self.storage.read(self.CHANNELS_FILE)["channels"]

    def set_channel_active(self, admin_id: int, channel_id: str, active: bool) -> dict[str, Any]:
        self.require_admin(admin_id)
        result: dict[str, Any] | None = None
        now = datetime.now(timezone.utc).isoformat()
        def operation(documents: dict[str, dict[str, list[Any]]]) -> None:
            nonlocal result
            channels = documents[self.CHANNELS_FILE]["channels"]
            channel = next((c for c in channels if c.get("id") == channel_id), None)
            if channel is None:
                raise AdminValidationError("Channel not found")
            channel["is_active"] = active
            channel["updated_at"] = now
            result = channel.copy()

        self.storage.atomic_update([self.CHANNELS_FILE], operation)
        assert result is not None
        return result

    def list_orders(self, admin_id: int) -> list[dict[str, Any]]:
        self.require_admin(admin_id)
        return self.storage.read(self.ORDERS_FILE)["orders"]

    def search_orders(self, admin_id: int, query: str) -> list[dict[str, Any]]:
        self.require_admin(admin_id)
        q = str(query).strip().casefold()
        if not q:
            return []
        orders = self.storage.read(self.ORDERS_FILE)["orders"]
        return [
            order for order in orders
            if q in str(order.get("supplier_order_id", "")).casefold()
            or q in str(order.get("product_id", "")).casefold()
            or q in str(order.get("product_name", "")).casefold()
            or q in str(order.get("status", "")).casefold()
        ]

    def get_order(self, admin_id: int, supplier_order_id: str) -> dict[str, Any] | None:
        self.require_admin(admin_id)
        return self.storage.find(self.ORDERS_FILE, lambda o: o.get("supplier_order_id") == supplier_order_id)

    async def supplier_balance(self, admin_id: int) -> dict[str, Any]:
        self.require_admin(admin_id)
        return await self.supplier_client.get_balance()

    def statistics(self, admin_id: int) -> dict[str, Any]:
        self.require_admin(admin_id)
        users = self.storage.read(self.USERS_FILE)["users"]
        orders = self.storage.read(self.ORDERS_FILE)["orders"]
        transactions = self.storage.read(self.TRANSACTIONS_FILE)["transactions"]
        purchases = [t for t in transactions if t.get("type") == "PURCHASE"]
        total_points_used = sum(
            t.get("amount") for t in purchases
            if isinstance(t.get("amount"), int) and not isinstance(t.get("amount"), bool)
        )
        return {
            "users_count": len(users),
            "orders_count": len(orders),
            "purchase_count": len(purchases),
            "total_points_used": total_points_used,
        }
