from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from ..models import EventType, MatchEvent
from .base import BaseDetector

log = logging.getLogger(__name__)

GSI_CONFIG_SUBDIR = ("game", "dota", "cfg", "gamestate_integration")

IN_MATCH_STATES = frozenset(
    {
        "DOTA_GAMERULES_STATE_WAIT_FOR_PLAYERS_TO_LOAD",
        "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
        "DOTA_GAMERULES_STATE_HERO_SELECTION",
        "DOTA_GAMERULES_STATE_STRATEGY_TIME",
        "DOTA_GAMERULES_STATE_PRE_GAME",
        "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
    }
)

FINISHED_STATES = frozenset({"DOTA_GAMERULES_STATE_POST_GAME"})
TERMINAL_STATES = frozenset({"DOTA_GAMERULES_STATE_DISCONNECT"})


@dataclass(frozen=True)
class GsiSnapshot:
    received_at: float
    match_id: str | None = None
    game_state: str | None = None
    win_team: str | None = None
    team: str | None = None
    hero: str | None = None
    hero_id: int | None = None
    player_name: str | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    gpm: int | None = None
    xpm: int | None = None
    clock_time: int | None = None

    @property
    def in_match(self) -> bool:
        return (self.game_state or "") in IN_MATCH_STATES

    @property
    def finished(self) -> bool:
        return (self.game_state or "") in FINISHED_STATES

    @property
    def terminated(self) -> bool:
        return (self.game_state or "") in TERMINAL_STATES


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_gsi_payload(payload: dict[str, Any], received_at: float | None = None) -> GsiSnapshot:
    """Разбирает payload Game State Integration от Dota 2."""
    data = payload if isinstance(payload, dict) else {}
    game_map = data.get("map") if isinstance(data.get("map"), dict) else {}
    player = data.get("player") if isinstance(data.get("player"), dict) else {}
    hero = data.get("hero") if isinstance(data.get("hero"), dict) else {}

    match_id = _as_str(game_map.get("matchid"))
    game_state = _as_str(game_map.get("game_state"))
    win_team = _as_str(game_map.get("win_team"))
    team = _as_str(player.get("team_name"))
    hero_name = _as_str(hero.get("name"))

    return GsiSnapshot(
        received_at=received_at if received_at is not None else time.time(),
        match_id=match_id,
        game_state=game_state,
        win_team=win_team,
        team=team,
        hero=hero_name,
        hero_id=_as_int(hero.get("id")),
        player_name=_as_str(player.get("name")),
        kills=_as_int(player.get("kills")),
        deaths=_as_int(player.get("deaths")),
        assists=_as_int(player.get("assists")),
        gpm=_as_int(player.get("gpm")),
        xpm=_as_int(player.get("xpm")),
        clock_time=_as_int(game_map.get("clock_time")),
    )


class _GsiRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not isinstance(payload, dict):
            payload = {}
        handler: Callable[[dict[str, Any]], None] | None = getattr(self.server, "on_payload", None)
        if handler is not None:
            try:
                handler(payload)
            except Exception:
                log.exception("Ошибка обработки GSI-пакета")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        return


class GsiServer:
    """Локальный HTTP-сервер, принимающий пакеты GSI от Dota 2."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9977, on_payload: Callable[[dict[str, Any]], None] | None = None):
        self.host = host
        self.port = port
        self._on_payload = on_payload
        self._httpd: ThreadingHTTPServer | None = None
        self._thread = None

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self.host, self.port), _GsiRequestHandler)
        httpd.on_payload = self._on_payload  # type: ignore[attr-defined]
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="gsi-server", daemon=True)
        self._thread.start()
        log.info("GSI-сервер слушает http://%s:%s", self.host, self.port)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None


def build_gsi_config_text(host: str, port: int, interval: str = "1.0") -> str:
    return (
        '"Dota Match Bot GSI Configuration"\n'
        "{\n"
        f'    "uri"               "http://{host}:{port}/"\n'
        '    "timeout"           "5.0"\n'
        f'    "buffer"            "{interval}"\n'
        f'    "throttle"          "{interval}"\n'
        '    "heartbeat"         "30.0"\n'
        "    \"data\"\n"
        "    {\n"
        '        "provider"   "1"\n'
        '        "map"        "1"\n'
        '        "player"     "1"\n'
        '        "hero"       "1"\n'
        '        "abilities"  "0"\n'
        '        "items"      "0"\n'
        '        "wearables"  "0"\n'
        '        "auth"       "0"\n'
        '        "draft"      "0"\n'
        '        "buildings"  "0"\n'
        '        "allplayers" "0"\n'
        "    }\n"
        "}\n"
    )


def write_gsi_config(dota_dir: str, host: str, port: int, filename: str, interval: str = "1.0") -> str:
    directory = Path(dota_dir).joinpath(*GSI_CONFIG_SUBDIR)
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / filename
    config_path.write_text(build_gsi_config_text(host, port, interval), encoding="utf-8")
    return str(config_path)


class GsiDetector(BaseDetector):
    """Детектор матча по данным Game State Integration."""

    name = "gsi"

    def __init__(self, sink, host: str = "127.0.0.1", port: int = 9977):
        super().__init__(sink)
        self._snapshot: GsiSnapshot | None = None
        self._in_match = False
        self._notified_started = False
        self._notified_finished = False
        self._current_match_id: str | None = None
        self._last_state: str | None = None
        self._seen_payload = False
        self._server = GsiServer(host, port, on_payload=self._handle_payload)

    @property
    def snapshot(self) -> GsiSnapshot | None:
        return self._snapshot

    def _handle_payload(self, payload: dict[str, Any]) -> None:
        snapshot = parse_gsi_payload(payload)
        self._snapshot = snapshot
        if not self._seen_payload:
            self._seen_payload = True
            log.info("GSI: подключение установлено, Dota 2 присылает данные")
        if snapshot.game_state and snapshot.game_state != self._last_state:
            self._last_state = snapshot.game_state
            log.info("GSI: состояние игры %s (match_id=%s)", snapshot.game_state, snapshot.match_id or "-")
        self._process(snapshot)

    def _reset_if_new_match(self, snapshot: GsiSnapshot) -> None:
        if snapshot.match_id and self._current_match_id and snapshot.match_id != self._current_match_id:
            self._notified_started = False
            self._notified_finished = False
            self._in_match = False

    def _process(self, snapshot: GsiSnapshot) -> None:
        self._reset_if_new_match(snapshot)
        if snapshot.match_id:
            self._current_match_id = snapshot.match_id

        if snapshot.in_match:
            if not self._notified_started:
                self._notified_started = True
                self._in_match = True
                hero_name = snapshot.hero
                if hero_name and hero_name.startswith("npc_dota_hero_"):
                    hero_name = None
                self.emit(
                    MatchEvent(
                        type=EventType.MATCH_STARTED,
                        source=self.name,
                        detected_at=time.time(),
                        match_id=snapshot.match_id,
                        hero=hero_name,
                        hero_id=snapshot.hero_id,
                        team=snapshot.team,
                        game_state=snapshot.game_state,
                        kills=snapshot.kills,
                        deaths=snapshot.deaths,
                        assists=snapshot.assists,
                        gpm=snapshot.gpm,
                        xpm=snapshot.xpm,
                    )
                )
            return

        if (snapshot.finished or snapshot.terminated) and self._in_match and not self._notified_finished:
            self._notified_finished = True
            victory: bool | None = None
            if snapshot.win_team and snapshot.win_team != "none" and snapshot.team:
                victory = snapshot.win_team.lower() == snapshot.team.lower()
            hero_name = snapshot.hero
            if hero_name and hero_name.startswith("npc_dota_hero_"):
                hero_name = None
            self.emit(
                MatchEvent(
                    type=EventType.MATCH_FINISHED,
                    source=self.name,
                    detected_at=time.time(),
                    match_id=snapshot.match_id,
                    hero=hero_name,
                    hero_id=snapshot.hero_id,
                    team=snapshot.team,
                    game_state=snapshot.game_state,
                    victory=victory,
                    duration_seconds=snapshot.clock_time,
                    kills=snapshot.kills,
                    deaths=snapshot.deaths,
                    assists=snapshot.assists,
                    gpm=snapshot.gpm,
                    xpm=snapshot.xpm,
                )
            )

    def run(self) -> None:
        try:
            self._server.start()
        except OSError as exc:
            log.error("Не удалось запустить GSI-сервер: %s", exc)
            return
        while not self.stopping:
            time.sleep(0.2)

    def stop(self) -> None:
        super().stop()
        self._server.stop()
