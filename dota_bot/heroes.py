from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

HEROES_URL = "https://api.opendota.com/api/constants/heroes"
CACHE_TTL_SECONDS = 86400 * 7


class HeroCatalog:
    """Кэширует соответствие hero_id -> название героя."""

    def __init__(self, cache_path: Path | str, timeout: float = 15.0):
        self._cache_path = Path(cache_path)
        self._timeout = timeout
        self._heroes: dict[int, str] = {}
        self._loaded_at = 0.0

    def name(self, hero_id: int | None) -> str | None:
        if hero_id is None:
            return None
        heroes = self._load()
        return heroes.get(int(hero_id))

    def _load(self) -> dict[int, str]:
        now = time.time()
        if self._heroes and now - self._loaded_at < CACHE_TTL_SECONDS:
            return self._heroes
        cached = self._read_cache()
        if cached is not None:
            self._heroes = cached
            self._loaded_at = now
            return cached
        fetched = self._fetch()
        if fetched:
            self._heroes = fetched
            self._loaded_at = now
            self._write_cache(fetched)
        return self._heroes

    def _read_cache(self) -> dict[int, str] | None:
        if not self._cache_path.exists():
            return None
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, dict):
            return None
        heroes: dict[int, str] = {}
        for key, value in raw.items():
            try:
                heroes[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
        return heroes or None

    def _write_cache(self, heroes: dict[int, str]) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({str(k): v for k, v in heroes.items()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.debug("Не удалось записать кэш героев: %s", exc)

    def _fetch(self) -> dict[int, str]:
        try:
            response = httpx.get(HEROES_URL, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            log.warning("Не удалось получить список героев: %s", exc)
            return {}
        heroes: dict[int, str] = {}
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                hero_id = entry.get("id")
                name = entry.get("localized_name") or entry.get("name")
                if isinstance(hero_id, int) and isinstance(name, str):
                    heroes[hero_id] = name
        return heroes
