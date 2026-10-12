from pathlib import Path

import pytest

from app.services.auth_service import AuthService, EmailAlreadyUsedError, InvalidEmailError
from app.services.json_storage import JsonStorage


def make_service(tmp_path: Path) -> AuthService:
    return AuthService(JsonStorage(tmp_path / "data"))


def test_new_user_and_valid_email(tmp_path: Path):
    service = make_service(tmp_path)
    user = service.register_with_email(1001, " User@Example.COM ")

    assert user["telegram_id"] == 1001
    assert user["email"] == "user@example.com"
    assert user["points_balance"] == 0
    assert user["is_banned"] is False


def test_invalid_email(tmp_path: Path):
    service = make_service(tmp_path)

    with pytest.raises(InvalidEmailError):
        service.register_with_email(1001, "not-an-email")


def test_email_used_by_another_telegram_account(tmp_path: Path):
    service = make_service(tmp_path)
    service.register_with_email(1001, "user@example.com")

    with pytest.raises(EmailAlreadyUsedError):
        service.register_with_email(1002, "USER@example.com")

    assert service.get_user_by_telegram_id(1002) is None
    assert service.get_user_by_telegram_id(1001)["email"] == "user@example.com"


def test_existing_user_is_reused_without_email_request(tmp_path: Path):
    service = make_service(tmp_path)
    first = service.register_with_email(1001, "user@example.com")
    existing = service.get_user_by_telegram_id(1001)

    assert existing == first


def test_restart_keeps_user(tmp_path: Path):
    data_dir = tmp_path / "data"
    first_service = AuthService(JsonStorage(data_dir))
    first = first_service.register_with_email(1001, "user@example.com")

    restarted_service = AuthService(JsonStorage(data_dir))
    second = restarted_service.get_user_by_telegram_id(1001)

    assert second == first


def test_user_data_is_not_lost_when_other_user_is_registered(tmp_path: Path):
    service = make_service(tmp_path)
    first = service.register_with_email(1001, "one@example.com")
    second = service.register_with_email(1002, "two@example.com")

    assert service.get_user_by_telegram_id(1001) == first
    assert service.get_user_by_telegram_id(1002) == second
    assert len(service.storage.read("users.json")["users"]) == 2


def test_duplicate_registration_for_same_telegram_id_does_not_create_second_user(tmp_path: Path):
    service = make_service(tmp_path)
    first = service.register_with_email(1001, "one@example.com")
    second = service.register_with_email(1001, "two@example.com")

    assert second == first
    assert len(service.storage.read("users.json")["users"]) == 1
