from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    pass


class ProviderNotConnected(ProviderError):
    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"provider '{provider_name}' is not connected")


class RpcError(ProviderError):
    def __init__(self, error: dict[str, Any]) -> None:
        self.code = error.get("code")
        self.message = error.get("message")
        self.data = error.get("data")
        super().__init__(f"RPC error {self.code}: {self.message}")


class RpcHttpError(ProviderError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")
