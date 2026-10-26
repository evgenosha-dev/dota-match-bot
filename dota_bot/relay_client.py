"""Клиент центрального релея для режима общего бота.

В этом режиме токен общего Telegram-бота хранится только на сервере релея.
Агент общается с релеем по HTTPS по личному ключу (relay.token):

- привязка: агент регистрирует код, пользователь шлёт его общему боту,
  релей связывает ключ с chat_id пользователя;
- уведомления: агент шлёт их через релей (релей пересылает от имени бота);
- команды: /on, /off, /start и нажатия кнопки «Принять матч» релей складывает
  во «входящие», агент забирает их опросом.

Благодаря этому сколько угодно пользователей может работать через одного
общего бота одновременно: getUpdates опрашивает только релей (Telegram
разрешает одного опрашивающего на токен), а отправка сообщений конфликтов
не создаёт.

RelayClient одновременно реализует интерфейс нотификатора MatchService
(send_message / edit_message / edit_message_reply_markup), поэтому в
релей-режиме его можно передать в сервис вместо TelegramClient.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class RelayError(RuntimeError):
    pass


class RelayClient:
    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ):
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    # ── транспорт ──
    def _request(self, method: str, path: str, **payload: Any) -> dict[str, Any]:
        request = httpx.Request(
            method,
            f"{self._url}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            json=payload or None,
        )
        try:
            response = self._client.send(request)
        except httpx.HTTPError as exc:
            raise RelayError(f"Релей недоступен: {exc}") from exc
        if response.status_code == 404:
            raise RelayError(f"Релей: не найдено ({path})")
        if response.status_code >= 400:
            raise RelayError(f"Релей вернул HTTP {response.status_code}: {response.text[:200]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RelayError(f"Релей вернул не-JSON: {response.text[:200]}") from exc
        if not isinstance(data, dict):
            raise RelayError("Релей вернул неожиданный ответ")
        return data

    # ── привязка ──
    def create_pairing(self) -> dict[str, Any]:
        """Регистрирует (или возвращает существующий) код привязки."""
        return self._request("POST", "/pair")

    def pairing_status(self) -> dict[str, Any]:
        return self._request("GET", "/pair")

    def wait_chat_id(self, timeout_seconds: float = 180.0) -> str | None:
        """Ждёт, пока пользователь пришлёт код общему боту."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                status = self.pairing_status()
            except RelayError as exc:
                log.debug("Статус привязки: %s", exc)
                time.sleep(3)
                continue
            chat_id = status.get("chat_id")
            if chat_id:
                return str(chat_id)
            time.sleep(2)
        return None

    # ── входящие команды ──
    def fetch_updates(self, cursor: int = 0) -> tuple[list[dict[str, Any]], int]:
        data = self._request("GET", f"/inbox?after={int(cursor)}")
        items = data.get("items") or []
        new_cursor = int(data.get("cursor", cursor))
        return [i for i in items if isinstance(i, dict)], new_cursor

    # ── нотификатор (интерфейс MatchService) ──
    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/send",
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

    def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
    ) -> bool:
        data = self._request("POST", "/edit", text=text, parse_mode=parse_mode)
        return bool(data.get("ok"))

    def edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        reply_markup: dict | None = None,
    ) -> bool:
        data = self._request(
            "POST",
            "/edit",
            reply_markup="remove" if reply_markup is None else reply_markup,
        )
        return bool(data.get("ok"))

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> bool:
        data = self._request(
            "POST",
            "/answer",
            callback_id=callback_id,
            text=text,
            alert=alert,
        )
        return bool(data.get("ok"))
