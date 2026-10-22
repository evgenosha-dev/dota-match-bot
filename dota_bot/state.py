from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class StateStore:
    """Хранит последние события, чтобы не присылать дубликаты уведомлений."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, Any] = {"recent": {}, "last_event": None, "stats": {}}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Не удалось прочитать состояние %s: %s", self.path, exc)
            return
        if isinstance(raw, dict):
            self._data.update(raw)
            self._data.setdefault("recent", {})
            self._data.setdefault("stats", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Не удалось сохранить состояние %s: %s", self.path, exc)

    @property
    def last_event(self) -> dict[str, Any] | None:
        value = self._data.get("last_event")
        return value if isinstance(value, dict) else None

    def set_last_event(self, payload: dict[str, Any]) -> None:
        self._data["last_event"] = payload

    def should_notify(self, key: str, window_seconds: float) -> bool:
        recent = self._data.setdefault("recent", {})
        now = time.time()
        previous = recent.get(key)
        if isinstance(previous, (int, float)) and now - float(previous) < window_seconds:
            return False
        return True

    def mark(self, key: str) -> None:
        recent = self._data.setdefault("recent", {})
        recent[key] = time.time()
        cutoff = time.time() - 86400
        self._data["recent"] = {k: v for k, v in recent.items() if isinstance(v, (int, float)) and v >= cutoff}

    def clear(self, key: str) -> None:
        recent = self._data.get("recent")
        if isinstance(recent, dict):
            recent.pop(key, None)

    def bump(self, counter: str, amount: int = 1) -> None:
        stats = self._data.setdefault("stats", {})
        stats[counter] = int(stats.get(counter, 0)) + amount

    def get_stat(self, counter: str) -> int:
        return int(self._data.get("stats", {}).get(counter, 0))

    def set_flag(self, name: str, value: bool) -> None:
        self._data["flags"] = {**self._data.get("flags", {}), name: value}

    def get_flag(self, name: str, default: bool = True) -> bool:
        return bool(self._data.get("flags", {}).get(name, default))
