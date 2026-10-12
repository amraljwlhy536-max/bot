from pathlib import Path

import pytest

from app.services.json_storage import DEFAULT_FILES, JsonStorage, JsonValidationError


@pytest.fixture
def storage(tmp_path: Path) -> JsonStorage:
    return JsonStorage(tmp_path)


def test_initialization_creates_data_directory_and_all_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    storage = JsonStorage(data_dir)
    assert storage.data_dir.exists()
    for filename, key in DEFAULT_FILES.items():
        assert (data_dir / filename).read_text(encoding="utf-8").strip() == f'{{\n  "{key}": []\n}}'


def test_initialization_does_not_delete_existing_data(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    users = data_dir / "users.json"
    users.write_text('{"users": [{"id": "existing"}]}', encoding="utf-8")
    JsonStorage(data_dir)
    assert users.read_text(encoding="utf-8") == '{"users": [{"id": "existing"}]}'


def test_read_write_and_atomic_replacement(storage: JsonStorage, tmp_path: Path):
    storage.write("users.json", {"users": [{"id": "u1", "username": "test"}]})
    assert storage.read("users.json") == {"users": [{"id": "u1", "username": "test"}]}
    assert not list(tmp_path.glob(".users.json.*.tmp"))


def test_append_and_find_by_id(storage: JsonStorage):
    storage.append("users.json", {"id": "u1", "username": "test"})
    assert storage.find_by_id("users.json", "u1") == {"id": "u1", "username": "test"}


def test_update(storage: JsonStorage):
    storage.append("users.json", {"id": "u1", "points_balance": 10})
    result = storage.update("users.json", lambda item: item.get("id") == "u1", {"points_balance": 20})
    assert result is not None
    assert result["points_balance"] == 20
    assert storage.find_by_id("users.json", "u1")["points_balance"] == 20


def test_delete(storage: JsonStorage):
    storage.append("users.json", {"id": "u1"})
    storage.append("users.json", {"id": "u2"})
    assert storage.delete("users.json", lambda item: item.get("id") == "u1") is True
    assert storage.read("users.json") == {"users": [{"id": "u2"}]}
    assert storage.delete("users.json", lambda item: item.get("id") == "missing") is False


def test_invalid_json_is_rejected(storage: JsonStorage):
    path = storage.data_dir / "users.json"
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(JsonValidationError):
        storage.read("users.json")


def test_invalid_shape_is_rejected(storage: JsonStorage):
    with pytest.raises(JsonValidationError):
        storage.write("users.json", {"products": []})


def test_non_object_item_is_rejected(storage: JsonStorage):
    with pytest.raises(JsonValidationError):
        storage.write("users.json", {"users": ["invalid"]})


def test_filename_cannot_escape_data_directory(storage: JsonStorage):
    with pytest.raises(ValueError):
        storage.read("../users.json")
