import logging
from dataclasses import dataclass
from typing import Protocol

import httpx


class _TelegramRequestFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "api.telegram.org/bot" not in record.getMessage()


logging.getLogger("httpx").addFilter(_TelegramRequestFilter())


class NotificationError(RuntimeError):
    pass


class Notifier(Protocol):
    async def send(self, recipient: str, message: str, idempotency_key: str) -> None: ...


@dataclass(slots=True)
class DryRunNotifier:
    messages: list[tuple[str, str, str]]

    async def send(self, recipient: str, message: str, idempotency_key: str) -> None:
        self.messages.append((recipient, message, idempotency_key))


class TelegramNotifier:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=35)

    async def username(self) -> str:
        response = await self._client.get(f"https://api.telegram.org/bot{self._token}/getMe")
        if response.is_error:
            raise NotificationError(f"Telegram bot lookup failed: {response.status_code}")
        return str(response.json()["result"]["username"])

    async def updates(self, offset: int | None = None) -> list[dict[str, object]]:
        params: dict[str, object] = {"timeout": 25, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        response = await self._client.get(
            f"https://api.telegram.org/bot{self._token}/getUpdates", params=params
        )
        if response.is_error:
            raise NotificationError(f"Telegram polling failed: {response.status_code}")
        return list(response.json().get("result", []))

    async def send(self, recipient: str, message: str, idempotency_key: str) -> None:
        response = await self._client.post(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            json={"chat_id": recipient, "text": message},
        )
        if response.is_error:
            raise NotificationError(f"Telegram delivery failed: {response.status_code}")


class ResendNotifier:
    def __init__(
        self,
        api_key: str,
        sender: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = sender
        self._client = client or httpx.AsyncClient(timeout=10)

    async def send(self, recipient: str, message: str, idempotency_key: str) -> None:
        response = await self._client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Idempotency-Key": idempotency_key,
            },
            json={
                "from": self._sender,
                "to": [recipient],
                "subject": "Transit arrival update",
                "text": message,
            },
        )
        if response.is_error:
            raise NotificationError(f"Email delivery failed: {response.status_code}")
