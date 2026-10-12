from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.services.json_storage import JsonStorage


_EMAIL_ADAPTER = TypeAdapter(EmailStr)


class InvalidEmailError(ValueError):
    """Raised when the supplied email address is invalid."""


class EmailAlreadyUsedError(ValueError):
    """Raised when an email is already linked to another Telegram account."""


class AuthService:
    USERS_FILE = "users.json"

    def __init__(self, storage: JsonStorage):
        self.storage = storage

    @staticmethod
    def normalize_email(value: str) -> str:
        if not isinstance(value, str):
            raise InvalidEmailError("Email must be a string")

        cleaned = value.strip().casefold()
        if not cleaned:
            raise InvalidEmailError("Email is required")

        try:
            validated = _EMAIL_ADAPTER.validate_python(cleaned)
        except ValidationError as exc:
            raise InvalidEmailError("Invalid email address") from exc

        return str(validated)

    def get_user_by_telegram_id(self, telegram_id: int) -> dict[str, Any] | None:
        return self.storage.find(
            self.USERS_FILE,
            lambda user: user.get("telegram_id") == telegram_id,
        )

    def register_with_email(self, telegram_id: int, email: str) -> dict[str, Any]:
        normalized_email = self.normalize_email(email)
        users_document = self.storage.read(self.USERS_FILE)
        for user in users_document["users"]:
            if user.get("email") == normalized_email and user.get("telegram_id") != telegram_id:
                raise EmailAlreadyUsedError("Email is already linked to another Telegram account")

        # The JSON storage lock is file-based, so the final duplicate check and append
        # must happen atomically to avoid two concurrent registrations using the same email.
        now = datetime.now(timezone.utc).isoformat()
        user = {
            "id": str(uuid4()),
            "telegram_id": telegram_id,
            "email": normalized_email,
            "username": None,
            "first_name": None,
            "points_balance": 0,
            "is_banned": False,
            "created_at": now,
            "updated_at": now,
        }

        try:
            return self.storage.append_unique(
                self.USERS_FILE,
                user,
                lambda existing: existing.get("email") == normalized_email
                or existing.get("telegram_id") == telegram_id,
            )
        except ValueError as exc:
            if str(exc) == "unique_constraint_violation":
                existing = self.get_user_by_telegram_id(telegram_id)
                if existing is not None:
                    return existing
                raise EmailAlreadyUsedError(
                    "Email is already linked to another Telegram account"
                ) from exc
            raise
