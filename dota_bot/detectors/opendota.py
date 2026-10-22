from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import replace
from typing import Any

import httpx

from ..gamedata import game_mode_label, lobby_type_label, region_label
from ..heroes import HeroCatalog
from ..models import EventType, MatchEvent, PlayerInfo
from .base import BaseDetector

log = logging.getLogger(__name__)

TOP_HEROES_LIMIT = 3
TOP_HEROES_TTL_SECONDS = 6 * 3600
DETAILS_DEADLINE_SECONDS = 900


class OpenDotaDetector(BaseDetector):
    """Сообщает о завершённых матчах по данным OpenDota API."""

    name = "opendota"

    def __init__(
        self,
        sink,
        account_id: int,
        base_url: str = "https://api.opendota.com/api",
        poll_interval_seconds: float = 60.0,
        hero_catalog: HeroCatalog | None = None,
        lookback_matches: int = 50,
    ):
        super().__init__(sink)
        self._account_id = account_id
        self._base_url = base_url.rstrip("/")
        self._poll_interval = max(poll_interval_seconds, 15.0)
        self._heroes = hero_catalog
        self._known_match_id: str | None = None
        self._initialized = False
        self._client = httpx.Client(timeout=30.0)
        self._lookback_matches = max(10, min(lookback_matches, 100))
        self._top_heroes_cache: dict[int, tuple[float, list[tuple[str, int]]]] = {}
        self._pending: dict[str, dict[str, Any]] = {}

    def fetch_recent_matches(self) -> list[dict[str, Any]]:
        url = f"{self._base_url}/players/{self._account_id}/recentMatches"
        response = self._client.get(url)
        if response.status_code == 404:
            log.warning("OpenDota не знает аккаунт %s", self._account_id)
            return []
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if isinstance(entry, dict)]

    def fetch_match(self, match_id: str | int) -> dict[str, Any] | None:
        try:
            response = self._client.get(f"{self._base_url}/matches/{match_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            log.warning("Не удалось получить детали матча %s: %s", match_id, exc)
            return None

    def fetch_player_matches(self, account_id: int) -> list[dict[str, Any]]:
        try:
            response = self._client.get(
                f"{self._base_url}/players/{account_id}/matches",
                params={"limit": self._lookback_matches},
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                return []
            return [entry for entry in payload if isinstance(entry, dict)]
        except Exception as exc:
            log.warning("Не удалось получить матчи игрока %s: %s", account_id, exc)
            return []

    def _hero_name(self, hero_id: Any) -> str | None:
        if not isinstance(hero_id, int) or hero_id < 0 or self._heroes is None:
            return None
        return self._heroes.name(hero_id)

    def _top_heroes(self, account_id: int) -> list[tuple[str, int]]:
        cached = self._top_heroes_cache.get(account_id)
        if cached and time.time() - cached[0] < TOP_HEROES_TTL_SECONDS:
            return cached[1]
        counter: Counter[int] = Counter()
        for entry in self.fetch_player_matches(account_id):
            hero_id = entry.get("hero_id")
            if isinstance(hero_id, int) and hero_id >= 0:
                counter[hero_id] += 1
        result: list[tuple[str, int]] = []
        for hero_id, count in counter.most_common(TOP_HEROES_LIMIT):
            name = self._hero_name(hero_id)
            if name:
                result.append((name, count))
        self._top_heroes_cache[account_id] = (time.time(), result)
        return result

    def _build_players(self, details: dict[str, Any]) -> tuple[PlayerInfo, ...]:
        raw_players = details.get("players")
        if not isinstance(raw_players, list):
            return ()
        players: list[PlayerInfo] = []
        for entry in raw_players:
            if not isinstance(entry, dict):
                continue
            slot = entry.get("player_slot")
            team = None
            if isinstance(slot, int):
                team = "radiant" if slot < 128 else "dire"
            account_id = entry.get("account_id")
            top_heroes: list[tuple[str, int]] = []
            if isinstance(account_id, int) and 0 < account_id < 2**31:
                top_heroes = self._top_heroes(account_id)
            name = entry.get("personaname") or entry.get("name")
            players.append(
                PlayerInfo(
                    name=str(name) if name else None,
                    hero=self._hero_name(entry.get("hero_id")),
                    team=team,
                    kills=entry.get("kills") if isinstance(entry.get("kills"), int) else None,
                    deaths=entry.get("deaths") if isinstance(entry.get("deaths"), int) else None,
                    assists=entry.get("assists") if isinstance(entry.get("assists"), int) else None,
                    top_heroes=tuple(top_heroes),
                )
            )
        return tuple(players)

    @staticmethod
    def _players_ready(details: dict[str, Any]) -> bool:
        raw_players = details.get("players")
        if not isinstance(raw_players, list) or not raw_players:
            return False
        return any(
            isinstance(entry, dict) and isinstance(entry.get("hero_id"), int)
            for entry in raw_players
        )

    @staticmethod
    def _victory(match: dict[str, Any]) -> bool | None:
        radiant_win = match.get("radiant_win")
        player_slot = match.get("player_slot")
        if not isinstance(radiant_win, bool) or not isinstance(player_slot, int):
            return None
        return radiant_win if player_slot < 128 else not radiant_win

    def _build_event(
        self, match: dict[str, Any], details: dict[str, Any] | None = None
    ) -> MatchEvent:
        hero_id = match.get("hero_id")
        game_mode = match.get("game_mode")
        player_slot = match.get("player_slot")
        event = MatchEvent(
            type=EventType.MATCH_FINISHED,
            source=self.name,
            detected_at=time.time(),
            match_id=str(match.get("match_id")) if match.get("match_id") else None,
            hero=self._hero_name(hero_id),
            hero_id=hero_id if isinstance(hero_id, int) else None,
            team="radiant" if isinstance(player_slot, int) and player_slot < 128 else None,
            game_mode=game_mode if isinstance(game_mode, int) else None,
            victory=self._victory(match),
            duration_seconds=match.get("duration") if isinstance(match.get("duration"), int) else None,
            kills=match.get("kills") if isinstance(match.get("kills"), int) else None,
            deaths=match.get("deaths") if isinstance(match.get("deaths"), int) else None,
            assists=match.get("assists") if isinstance(match.get("assists"), int) else None,
            gpm=match.get("gold_per_min") if isinstance(match.get("gold_per_min"), int) else None,
            xpm=match.get("xp_per_min") if isinstance(match.get("xp_per_min"), int) else None,
        )
        if not details:
            return event

        region = details.get("region")
        lobby_type = details.get("lobby_type")
        details_mode = details.get("game_mode")
        duration = details.get("duration")
        radiant_score = details.get("radiant_score")
        dire_score = details.get("dire_score")
        return replace(
            event,
            game_mode=details_mode if isinstance(details_mode, int) else event.game_mode,
            lobby_type=lobby_type if isinstance(lobby_type, int) else None,
            region=region if isinstance(region, int) else None,
            duration_seconds=duration if isinstance(duration, int) else event.duration_seconds,
            radiant_score=radiant_score if isinstance(radiant_score, int) else None,
            dire_score=dire_score if isinstance(dire_score, int) else None,
            players=self._build_players(details),
        )

    def _enrich_pending(self) -> None:
        now = time.time()
        for match_id, item in list(self._pending.items()):
            if now >= float(item["deadline"]):
                log.info("OpenDota: детали матча %s так и не распарсились", match_id)
                del self._pending[match_id]
                continue
            details = self.fetch_match(match_id)
            if not details or not self._players_ready(details):
                continue
            log.info("OpenDota: составы матча %s получены", match_id)
            self.emit(self._build_event(item["match"], details))
            del self._pending[match_id]

    def run(self) -> None:
        log.info("Слежу за матчами через OpenDota (аккаунт %s)", self._account_id)
        while not self.stopping:
            try:
                matches = self.fetch_recent_matches()
                if matches:
                    newest = max(matches, key=lambda m: m.get("start_time") or 0)
                    match_id = str(newest.get("match_id")) if newest.get("match_id") else None
                    if match_id and match_id != self._known_match_id:
                        if self._initialized:
                            log.info("OpenDota: новый матч %s", match_id)
                            details = self.fetch_match(match_id)
                            if details and not self._players_ready(details):
                                details = None
                            self.emit(self._build_event(newest, details))
                            if details is None:
                                self._pending[match_id] = {
                                    "match": newest,
                                    "deadline": time.time() + DETAILS_DEADLINE_SECONDS,
                                }
                        else:
                            log.info("OpenDota: зафиксирован последний матч %s", match_id)
                        self._known_match_id = match_id
                    self._initialized = True
                self._enrich_pending()
            except Exception as exc:
                log.warning("Ошибка опроса OpenDota: %s", exc)
            time.sleep(self._poll_interval)
