from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


class JsonStorageError(Exception):
    """Base exception for JSON storage errors."""


class JsonValidationError(JsonStorageError):
    """Raised when a JSON storage file does not match its expected shape."""


DEFAULT_FILES: dict[str, str] = {
    "users.json": "users",
    "products.json": "products",
    "orders.json": "orders",
    "balance_transactions.json": "transactions",
    "required_channels.json": "channels",
}


class JsonStorage:
    """Centralized JSON storage using file locking and atomic replacement."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_files()

    def _path(self, filename: str) -> Path:
        path = self.data_dir / filename
        if path.parent != self.data_dir:
            raise ValueError("filename must refer to a file directly inside data_dir")
        return path

    @contextmanager
    def _lock(self, path: Path) -> Iterator[None]:
        lock_path = path.with_name(f".{path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock_file:
            try:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except ImportError:
                import msvcrt
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write("0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _validate_document(document: Any, expected_key: str | None = None) -> dict[str, list[Any]]:
        if not isinstance(document, dict):
            raise JsonValidationError("JSON root must be an object")
        if len(document) != 1:
            raise JsonValidationError("JSON root must contain exactly one collection key")
        key, value = next(iter(document.items()))
        if expected_key is not None and key != expected_key:
            raise JsonValidationError(f"Expected collection key '{expected_key}'")
        if not isinstance(value, list):
            raise JsonValidationError("Collection value must be a list")
        if any(not isinstance(item, dict) for item in value):
            raise JsonValidationError("Every collection item must be an object")
        return document

    def _read_unlocked(self, path: Path, expected_key: str | None = None) -> dict[str, list[Any]]:
        if not path.exists():
            raise FileNotFoundError(path)
        try:
            with path.open("r", encoding="utf-8") as file:
                document = json.load(file)
        except json.JSONDecodeError as exc:
            raise JsonValidationError(f"Invalid JSON in {path.name}: {exc}") from exc
        return self._validate_document(document, expected_key)

    def _atomic_write_unlocked(self, path: Path, document: dict[str, list[Any]]) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(document, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _initialize_files(self) -> None:
        for filename, collection_key in DEFAULT_FILES.items():
            path = self._path(filename)
            with self._lock(path):
                if not path.exists():
                    self._atomic_write_unlocked(path, {collection_key: []})

    @contextmanager
    def _locks(self, filenames: list[str]) -> Iterator[dict[str, Path]]:
        """Acquire multiple JSON-file locks in a stable order to avoid deadlocks."""
        unique = sorted(set(filenames))
        paths = {filename: self._path(filename) for filename in unique}
        files = []
        try:
            for filename in unique:
                path = paths[filename]
                lock_path = path.with_name(f".{path.name}.lock")
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_file = lock_path.open("a+")
                files.append(lock_file)
                try:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                except ImportError:
                    import msvcrt
                    lock_file.seek(0, os.SEEK_END)
                    if lock_file.tell() == 0:
                        lock_file.write("0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            yield paths
        finally:
            for lock_file in reversed(files):
                try:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                except ImportError:
                    import msvcrt
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                lock_file.close()

    def atomic_update(
        self,
        filenames: list[str],
        callback: Callable[[dict[str, dict[str, list[Any]]]], Any],
    ) -> Any:
        """Update multiple JSON documents under one lock scope with rollback on failure."""
        if not filenames:
            raise ValueError("filenames must not be empty")

        with self._locks(filenames) as paths:
            documents: dict[str, dict[str, list[Any]]] = {}
            originals: dict[str, dict[str, list[Any]]] = {}
            for filename, path in paths.items():
                expected_key = DEFAULT_FILES.get(filename)
                document = self._read_unlocked(path, expected_key)
                documents[filename] = document
                originals[filename] = json.loads(json.dumps(document))

            result = callback(documents)

            written: list[str] = []
            try:
                for filename in sorted(paths):
                    if documents[filename] != originals[filename]:
                        self._atomic_write_unlocked(paths[filename], documents[filename])
                        written.append(filename)
            except Exception:
                for filename in written:
                    self._atomic_write_unlocked(paths[filename], originals[filename])
                raise

            return result

    def read(self, filename: str) -> dict[str, list[Any]]:
        path = self._path(filename)
        expected_key = DEFAULT_FILES.get(filename)
        with self._lock(path):
            return self._read_unlocked(path, expected_key)

    def write(self, filename: str, document: dict[str, list[Any]]) -> None:
        path = self._path(filename)
        expected_key = DEFAULT_FILES.get(filename)
        document = self._validate_document(document, expected_key)
        with self._lock(path):
            self._atomic_write_unlocked(path, document)

    def append(self, filename: str, item: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise JsonValidationError("Item must be an object")
        path = self._path(filename)
        expected_key = DEFAULT_FILES.get(filename)
        with self._lock(path):
            document = self._read_unlocked(path, expected_key)
            collection = next(iter(document.values()))
            collection.append(item)
            self._atomic_write_unlocked(path, document)
        return item

    def append_unique(
        self,
        filename: str,
        item: dict[str, Any],
        conflict_predicate: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any]:
        """Append an item only if no existing item matches the conflict predicate.

        The conflict check and append occur under the same file lock, making the
        operation safe against concurrent writers using this storage service.
        """
        if not isinstance(item, dict):
            raise JsonValidationError("Item must be an object")
        path = self._path(filename)
        expected_key = DEFAULT_FILES.get(filename)
        with self._lock(path):
            document = self._read_unlocked(path, expected_key)
            collection = next(iter(document.values()))
            if any(conflict_predicate(existing) for existing in collection):
                raise ValueError("unique_constraint_violation")
            collection.append(item)
            self._atomic_write_unlocked(path, document)
        return item

    def update(
        self,
        filename: str,
        predicate: Callable[[dict[str, Any]], bool],
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(updates, dict):
            raise JsonValidationError("updates must be an object")
        path = self._path(filename)
        expected_key = DEFAULT_FILES.get(filename)
        with self._lock(path):
            document = self._read_unlocked(path, expected_key)
            collection = next(iter(document.values()))
            for item in collection:
                if predicate(item):
                    item.update(updates)
                    self._atomic_write_unlocked(path, document)
                    return item
        return None

    def delete(self, filename: str, predicate: Callable[[dict[str, Any]], bool]) -> bool:
        path = self._path(filename)
        expected_key = DEFAULT_FILES.get(filename)
        with self._lock(path):
            document = self._read_unlocked(path, expected_key)
            collection = next(iter(document.values()))
            original_length = len(collection)
            collection[:] = [item for item in collection if not predicate(item)]
            deleted = len(collection) != original_length
            if deleted:
                self._atomic_write_unlocked(path, document)
            return deleted

    def find(
        self,
        filename: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any] | None:
        path = self._path(filename)
        expected_key = DEFAULT_FILES.get(filename)
        with self._lock(path):
            document = self._read_unlocked(path, expected_key)
            collection = next(iter(document.values()))
            return next((item for item in collection if predicate(item)), None)

    def find_by_id(self, filename: str, item_id: Any) -> dict[str, Any] | None:
        return self.find(filename, lambda item: item.get("id") == item_id)
