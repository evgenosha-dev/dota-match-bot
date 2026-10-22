from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    """Минимальный синхронный клиент Telegram Bot API."""

    def __init__(self, token: str, timeout: float = 10.0):
        if not token:
            raise TelegramError("Не указан Telegram-токен")
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._timeout = timeout

    def _request(self, method: str, path: str, *, timeout: float | None = None, **kwargs: Any) -> httpx.Response:
        """Выполняет запрос, повторяя только ошибки установки соединения."""
        url = self._base_url + path
        last_error: Exception | None = None
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                return httpx.request(method, url, timeout=timeout or self._timeout, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                log.warning(
                    "Ошибка соединения с Telegram (%d/%d): %s",
                    attempt,
                    CONNECT_ATTEMPTS,
                    exc,
                )
                if attempt < CONNECT_ATTEMPTS:
                    time.sleep(0.5 * attempt)
            except httpx.HTTPError as exc:
                raise TelegramError(f"Сетевая ошибка Telegram: {exc}") from exc
        raise TelegramError(f"Не удалось соединиться с Telegram: {last_error}") from last_error

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = self._request("POST", "/sendMessage", json=payload)
        if response.status_code != 200:
            raise TelegramError(f"Telegram вернул {response.status_code}: {response.text[:300]}")
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram отклонил сообщение: {data.get('description')}")
        return data

    def get_me(self) -> dict[str, Any]:
        response = self._request("GET", "/getMe")
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram вернул ошибку: {data.get('description')}")
        return data.get("result", {})

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        response = self._request("GET", "/getUpdates", params=params, timeout=timeout + 15)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram вернул ошибку: {data.get('description')}")
        result = data.get("result")
        return result if isinstance(result, list) else []

    def find_chat_id(self, timeout_seconds: int = 60) -> str | None:
        """Ищет chat_id последнего чата, который написал боту."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            for update in self.get_updates(timeout=20):
                chat = self._extract_chat(update)
                if chat:
                    return chat
            if timeout_seconds <= 20:
                break
        return None

    @staticmethod
    def _extract_chat(update: dict[str, Any]) -> str | None:
        for key in ("message", "edited_message", "channel_post", "callback_query"):
            payload = update.get(key)
            if not isinstance(payload, dict):
                continue
            chat = payload.get("chat")
            if isinstance(chat, dict) and chat.get("id") is not None:
                return str(chat["id"])
            sender = payload.get("from")
            if isinstance(sender, dict) and sender.get("id") is not None:
                return str(sender["id"])
        return None

    def edit_message_reply_markup(
        self, chat_id: str, message_id: int, reply_markup: dict[str, Any] | None = None
    ) -> bool:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        payload["reply_markup"] = reply_markup if reply_markup is not None else {}
        response = self._request("POST", "/editMessageReplyMarkup", json=payload)
        if response.status_code != 200:
            return False
        return bool(response.json().get("ok"))

    def edit_message(self, chat_id: str, message_id: int, text: str, parse_mode: str = "HTML") -> bool:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        response = self._request("POST", "/editMessageText", json=payload)
        if response.status_code != 200:
            return False
        data = response.json()
        return bool(data.get("ok"))
