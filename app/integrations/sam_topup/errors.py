from __future__ import annotations


class SamTopupError(Exception):
    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class SamTopupTimeoutError(SamTopupError):
    pass
