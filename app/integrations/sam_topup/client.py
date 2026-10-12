from __future__ import annotations

from typing import Any

import httpx

from app.integrations.sam_topup.errors import SamTopupError, SamTopupTimeoutError


class SamTopupClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 20.0, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client = client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def get_balance(self) -> dict[str, Any]:
        return await self._get_json("/balance")

    async def get_products(self) -> dict[str, Any]:
        return await self._get_json("/products")

    async def _get_json(self, path: str) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owned = self._client is None
        try:
            try:
                response = await client.get(
                    f"{self.base_url}{path}",
                    headers={"x-api-key": self.api_key},
                )
            except (httpx.TimeoutException, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                raise SamTopupTimeoutError("Supplier request timed out") from exc
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if response.is_success and isinstance(payload, dict) and payload.get("success") is True:
                return payload
            code = payload.get("errorCode") if isinstance(payload, dict) else None
            if code is None and isinstance(payload, dict):
                code = payload.get("code")
            raise SamTopupError("Supplier request failed", code=code, status_code=response.status_code, details=payload if isinstance(payload, dict) else {})
        finally:
            if owned:
                await client.aclose()

    async def create_order(self, product_id: str, quantity: int) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        owned = self._client is None
        try:
            try:
                response = await client.post(
                    f"{self.base_url}/order",
                    headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                    json={"productId": product_id, "quantity": quantity},
                )
            except (httpx.TimeoutException, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                raise SamTopupTimeoutError("Supplier order outcome is uncertain") from exc

            try:
                payload = response.json()
            except ValueError:
                payload = None

            if response.is_success and isinstance(payload, dict) and payload.get("success") is True:
                return payload

            code = payload.get("errorCode") if isinstance(payload, dict) else None
            if code is None and isinstance(payload, dict):
                code = payload.get("code")
            details = payload if isinstance(payload, dict) else {}
            raise SamTopupError("Supplier order failed", code=code, status_code=response.status_code, details=details)
        finally:
            if owned:
                await client.aclose()
