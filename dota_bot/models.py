from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    MATCH_FOUND = "match_found"
    MATCH_STARTED = "match_started"
    MATCH_FINISHED = "match_finished"


@dataclass(frozen=True)
class PlayerInfo:
    name: str | None = None
    hero: str | None = None
    team: str | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    top_heroes: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class MatchEvent:
    type: EventType
    source: str
    detected_at: float
    match_id: str | None = None
    hero: str | None = None
    hero_id: int | None = None
    team: str | None = None
    game_state: str | None = None
    game_mode: int | None = None
    lobby_type: int | None = None
    region: int | None = None
    radiant_score: int | None = None
    dire_score: int | None = None
    victory: bool | None = None
    duration_seconds: int | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    gpm: int | None = None
    xpm: int | None = None
    players: tuple[PlayerInfo, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        if self.type is EventType.MATCH_FINISHED and self.match_id:
            return f"{self.type.value}:{self.match_id}"
        if self.type is EventType.MATCH_STARTED and self.match_id:
            return f"{self.type.value}:{self.match_id}"
        return f"{self.type.value}:{self.source}"
