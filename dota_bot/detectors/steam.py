from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..config import DOTA_APP_ID
from ..models import EventType, MatchEvent
from .base import BaseDetector

log = logging.getLogger(__name__)


class SteamDetector(BaseDetector):
    """Отслеживает статус «в игре» через Steam Web API."""

    name = "steam"

    def __init__(
        self,
        sink,
        api_key: str,
        steamid64: str,
        base_url: str = "https://api.steampowered.com",
        poll_interval_seconds: float = 10.0,
    ):
        super().__init__(sink)
        self._api_key = api_key
        self._steamid64 = steamid64
        self._base_url = base_url.rstrip("/")
        self._poll_interval = max(poll_interval_seconds, 3.0)
        self._in_game = False
        self._client = httpx.Client(timeout=15.0)

    def fetch_summary(self) -> dict[str, Any]:
        url = f"{self._base_url}/ISteamUser/GetPlayerSummaries/v2/"
        response = self._client.get(
            url, params={"key": self._api_key, "steamids": self._steamid64}
        )
        if response.status_code == 403:
            raise PermissionError(
                "Steam отклонил API-ключ (403). Проверьте ключ и ограничения по IP."
            )
        response.raise_for_status()
        payload = response.json()
        players = (payload.get("response") or {}).get("players") or []
        if not players:
            return {}
        return players[0] if isinstance(players[0], dict) else {}

    @staticmethod
    def is_dota_game(summary: dict[str, Any]) -> bool:
        game_id = summary.get("game_id")
        if isinstance(game_id, int) and game_id == DOTA_APP_ID:
            return True
        extra = summary.get("game_extra_info")
        if isinstance(extra, str) and "dota" in extra.lower():
            return True
        return False

    def run(self) -> None:
        log.info("Опрос Steam Web API для аккаунта %s", self._steamid64)
        while not self.stopping:
            try:
                summary = self.fetch_summary()
                in_game = self.is_dota_game(summary)
                if in_game and not self._in_game:
                    log.info("Steam: аккаунт вошёл в Dota 2")
                    self.emit(
                        MatchEvent(
                            type=EventType.MATCH_FOUND,
                            source=self.name,
                            detected_at=time.time(),
                            extra={"game_extra_info": summary.get("game_extra_info")},
                        )
                    )
                elif not in_game and self._in_game:
                    log.info("Steam: аккаунт вышел из Dota 2")
                self._in_game = in_game
            except PermissionError as exc:
                log.error("%s", exc)
            except Exception as exc:
                log.warning("Ошибка опроса Steam: %s", exc)
            time.sleep(self._poll_interval)
