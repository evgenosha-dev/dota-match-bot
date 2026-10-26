from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class PairingStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, Any] = {"codes": {}}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("Не удалось прочитать pairing: %s", exc)
            return
        if isinstance(raw, dict):
            self._data.update(raw)
            self._data.setdefault("codes", {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            log.debug("Не удалось сохранить pairing: %s", exc)

    def create_code(self, ttl_seconds: int | None = None) -> str:
        """Создаёт код привязки. По умолчанию срока действия нет.

        Если уже есть неиспользованный код — возвращает его же, чтобы
        повторный запуск setup не инвалидировал ранее показанный код.
        Явный ttl_seconds задаёт срок действия (для тестов).
        """
        codes = self._data.setdefault("codes", {})
        now = time.time()
        codes = {
            k: v
            for k, v in codes.items()
            if isinstance(v, dict)
            and (v.get("expires") is None or float(v["expires"]) > now)
        }
        self._data["codes"] = codes
        for code, entry in codes.items():
            if isinstance(entry, dict) and not entry.get("used"):
                return code
        while True:
            code = str(100000 + secrets.randbelow(900000))
            if code not in codes:
                break
        entry: dict[str, Any] = {"chat_id": None, "used": False}
        if ttl_seconds is not None:
            entry["expires"] = now + ttl_seconds
        codes[code] = entry
        self.save()
        return code

    def claim(self, code: str, chat_id: str) -> bool:
        codes = self._data.setdefault("codes", {})
        entry = codes.get(code)
        if not isinstance(entry, dict):
            return False
        if entry.get("used"):
            return False
        expires = entry.get("expires")
        if expires is not None and float(expires) < time.time():
            codes.pop(code, None)
            self.save()
            return False
        entry["chat_id"] = str(chat_id)
        entry["used"] = True
        self.save()
        return True

    def get_chat_id(self, code: str) -> str | None:
        codes = self._data.get("codes") or {}
        entry = codes.get(code)
        if not isinstance(entry, dict) or not entry.get("used"):
            return None
        expires = entry.get("expires")
        if expires is not None and float(expires) < time.time():
            return None
        chat_id = entry.get("chat_id")
        return str(chat_id) if chat_id else None
