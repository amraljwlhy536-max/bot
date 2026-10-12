from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services.auth_service import AuthService
from app.services.json_storage import JsonStorage
from app.services.points_service import (
    InsufficientPointsError,
    InvalidPointsAmountError,
    PointsService,
    UnauthorizedPointsOperationError,
)


ADMIN_ID = 9001
USER_ID = "user-1"


def make_service(tmp_path: Path) -> tuple[JsonStorage, PointsService, str]:
    storage = JsonStorage(tmp_path / "data")
    user = AuthService(storage).register_with_email(1001, "user@example.com")
    service = PointsService(storage, {ADMIN_ID})
    return storage, service, user["id"]


def test_new_user_starts_with_zero_points(tmp_path: Path):
    storage = JsonStorage(tmp_path / "data")
    user = AuthService(storage).register_with_email(1001, "user@example.com")
    assert user["points_balance"] == 0


def test_credit_points(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)
    result = service.credit(user_id, 200, ADMIN_ID, "manual credit")
    assert result["type"] == "ADMIN_CREDIT"
    assert result["balance_before"] == 0
    assert result["balance_after"] == 200
    assert service.get_balance(user_id) == 200


def test_debit_points(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)
    service.credit(user_id, 700, ADMIN_ID)
    result = service.debit(user_id, 200, ADMIN_ID, "manual debit")
    assert result["type"] == "ADMIN_DEBIT"
    assert result["balance_before"] == 700
    assert result["balance_after"] == 500
    assert service.get_balance(user_id) == 500


def test_debit_larger_than_balance_is_rejected(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)
    service.credit(user_id, 100, ADMIN_ID)
    with pytest.raises(InsufficientPointsError):
        service.debit(user_id, 101, ADMIN_ID)
    assert service.get_balance(user_id) == 100
    assert storage.read("balance_transactions.json")["transactions"][-1]["balance_after"] == 100


@pytest.mark.parametrize("amount", ["10", 1.5, None, True, False])
def test_invalid_amount_is_rejected(tmp_path: Path, amount):
    _, service, user_id = make_service(tmp_path)
    with pytest.raises(InvalidPointsAmountError):
        service.credit(user_id, amount, ADMIN_ID)


@pytest.mark.parametrize("amount", [-1, -100])
def test_negative_amount_is_rejected(tmp_path: Path, amount: int):
    _, service, user_id = make_service(tmp_path)
    with pytest.raises(InvalidPointsAmountError):
        service.credit(user_id, amount, ADMIN_ID)
    assert service.get_balance(user_id) == 0


def test_non_admin_cannot_modify_balance(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)
    with pytest.raises(UnauthorizedPointsOperationError):
        service.credit(user_id, 100, 123456)
    with pytest.raises(UnauthorizedPointsOperationError):
        service.debit(user_id, 1, 123456)
    assert service.get_balance(user_id) == 0
    assert storage.read("balance_transactions.json")["transactions"] == []


def test_transaction_is_recorded_with_required_fields(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)
    service.credit(user_id, 500, ADMIN_ID, "reason text")
    transaction = storage.read("balance_transactions.json")["transactions"][0]
    assert set(transaction) == {
        "id", "user_id", "type", "amount", "balance_before", "balance_after",
        "reason", "admin_id", "order_id", "created_at",
    }
    assert transaction["user_id"] == user_id
    assert transaction["type"] == "ADMIN_CREDIT"
    assert transaction["amount"] == 500
    assert transaction["reason"] == "reason text"
    assert transaction["admin_id"] == ADMIN_ID
    assert transaction["order_id"] is None


def test_balance_before_and_after_are_correct(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)
    service.credit(user_id, 500, ADMIN_ID)
    service.debit(user_id, 200, ADMIN_ID)
    transactions = storage.read("balance_transactions.json")["transactions"]
    assert [(t["balance_before"], t["balance_after"]) for t in transactions] == [(0, 500), (500, 300)]


def test_concurrent_credits_are_serialized_and_preserved(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)

    def add_points(_: int):
        return service.credit(user_id, 1, ADMIN_ID)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add_points, range(100)))

    assert service.get_balance(user_id) == 100
    transactions = storage.read("balance_transactions.json")["transactions"]
    assert len(transactions) == 100
    assert sorted(t["balance_before"] for t in transactions) == list(range(100))
    assert sorted(t["balance_after"] for t in transactions) == list(range(1, 101))


def test_transaction_failure_rolls_back_balance(tmp_path: Path, monkeypatch):
    storage, service, user_id = make_service(tmp_path)
    original = storage._atomic_write_unlocked

    def fail_transaction_write(path, document):
        if path.name == "balance_transactions.json":
            raise OSError("simulated transaction write failure")
        return original(path, document)

    monkeypatch.setattr(storage, "_atomic_write_unlocked", fail_transaction_write)

    with pytest.raises(OSError):
        service.credit(user_id, 50, ADMIN_ID)

    assert service.get_balance(user_id) == 0
    assert storage.read("balance_transactions.json")["transactions"] == []
    assert storage.read("users.json")["users"][0]["points_balance"] == 0


def test_json_files_remain_valid_after_operations(tmp_path: Path):
    storage, service, user_id = make_service(tmp_path)
    service.credit(user_id, 100, ADMIN_ID)
    service.debit(user_id, 40, ADMIN_ID)
    users = storage.read("users.json")
    transactions = storage.read("balance_transactions.json")
    assert isinstance(users["users"], list)
    assert isinstance(transactions["transactions"], list)
