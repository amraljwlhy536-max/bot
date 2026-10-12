from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.services.json_storage import JsonStorage


class PointsError(ValueError):
    """Base exception for points operations."""


class UnauthorizedPointsOperationError(PointsError):
    """Raised when the caller is not an admin."""


class UserNotFoundError(PointsError):
    """Raised when the target user does not exist."""


class InvalidPointsAmountError(PointsError):
    """Raised when the points amount is invalid."""


class InsufficientPointsError(PointsError):
    """Raised when a debit would make the balance negative."""


class PointsService:
    USERS_FILE = "users.json"
    TRANSACTIONS_FILE = "balance_transactions.json"
    CREDIT_TYPE = "ADMIN_CREDIT"
    DEBIT_TYPE = "ADMIN_DEBIT"

    def __init__(self, storage: JsonStorage, admin_ids: set[int] | frozenset[int]):
        self.storage = storage
        self.admin_ids = {int(admin_id) for admin_id in admin_ids}

    def get_balance(self, user_id: str) -> int:
        user = self.storage.find_by_id(self.USERS_FILE, user_id)
        if user is None:
            raise UserNotFoundError("User not found")
        return self._read_balance(user)

    def credit(self, user_id: str, amount: int, admin_id: int, reason: str | None = None) -> dict[str, Any]:
        return self._change_balance(user_id, amount, admin_id, self.CREDIT_TYPE, reason)

    def debit(self, user_id: str, amount: int, admin_id: int, reason: str | None = None) -> dict[str, Any]:
        return self._change_balance(user_id, amount, admin_id, self.DEBIT_TYPE, reason)

    def _change_balance(
        self,
        user_id: str,
        amount: int,
        admin_id: int,
        transaction_type: str,
        reason: str | None,
    ) -> dict[str, Any]:
        self._require_admin(admin_id)
        self._validate_amount(amount)
        if transaction_type not in {self.CREDIT_TYPE, self.DEBIT_TYPE}:
            raise ValueError("Unsupported transaction type")

        now = datetime.now(timezone.utc).isoformat()
        transaction: dict[str, Any] | None = None

        def operation(documents: dict[str, dict[str, list[Any]]]) -> dict[str, Any]:
            nonlocal transaction
            users = documents[self.USERS_FILE]["users"]
            user = next((item for item in users if item.get("id") == user_id), None)
            if user is None:
                raise UserNotFoundError("User not found")

            balance_before = self._read_balance(user)
            if transaction_type == self.DEBIT_TYPE and amount > balance_before:
                raise InsufficientPointsError("Insufficient points")

            balance_after = (
                balance_before + amount
                if transaction_type == self.CREDIT_TYPE
                else balance_before - amount
            )
            if balance_after < 0:
                raise InsufficientPointsError("Insufficient points")

            user["points_balance"] = balance_after
            user["updated_at"] = now
            transaction = {
                "id": str(uuid4()),
                "user_id": user_id,
                "type": transaction_type,
                "amount": amount,
                "balance_before": balance_before,
                "balance_after": balance_after,
                "reason": reason,
                "admin_id": admin_id,
                "order_id": None,
                "created_at": now,
            }
            documents[self.TRANSACTIONS_FILE]["transactions"].append(transaction)
            return transaction

        self.storage.atomic_update(
            [self.USERS_FILE, self.TRANSACTIONS_FILE],
            operation,
        )
        assert transaction is not None
        return transaction

    def _require_admin(self, admin_id: int) -> None:
        if int(admin_id) not in self.admin_ids:
            raise UnauthorizedPointsOperationError("Admin permission required")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise InvalidPointsAmountError("Amount must be a positive integer")

    @staticmethod
    def _read_balance(user: dict[str, Any]) -> int:
        balance = user.get("points_balance")
        if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
            raise InvalidPointsAmountError("User points_balance must be a non-negative integer")
        return balance
