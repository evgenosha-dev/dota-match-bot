import time
from dataclasses import replace
from pathlib import Path

from dota_bot.config import BotConfig
from dota_bot.models import EventType, MatchEvent, PlayerInfo
from dota_bot.service import MatchService
from dota_bot.state import StateStore


class FakeTelegram:
    def __init__(self):
        self.sent: list[str] = []
        self.edited: list[tuple[int, str]] = []
        self.markups: list[dict | None] = []
        self.markup_edits: list[int] = []

    def send_message(self, chat_id, text, parse_mode="HTML", reply_markup=None):
        self.sent.append(text)
        self.markups.append(reply_markup)
        return {"ok": True, "result": {"message_id": 1000 + len(self.sent)}}

    def edit_message(self, chat_id, message_id, text, parse_mode="HTML"):
        self.edited.append((message_id, text))
        return True

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.markup_edits.append(message_id)
        return True


def make_service(tmp_path: Path) -> tuple[MatchService, FakeTelegram]:
    config = BotConfig()
    config.state_path = tmp_path / "state.json"
    config.telegram.chat_id = "1"
    state = StateStore(config.state_path)
    state.load()
    telegram = FakeTelegram()
    return MatchService(config, telegram, state), telegram


def found_event(source: str = "accept_button") -> MatchEvent:
    return MatchEvent(type=EventType.MATCH_FOUND, source=source, detected_at=time.time())


def test_duplicate_match_found_is_suppressed(tmp_path: Path):
    service, telegram = make_service(tmp_path)
    service.config.telegram.notify_match_finished = False

    service.handle_event(found_event())
    service.handle_event(found_event())

    assert len(telegram.sent) == 1


def test_foreground_source_does_not_notify(tmp_path: Path):
    service, telegram = make_service(tmp_path)

    service.handle_event(found_event("foreground"))

    assert telegram.sent == []


def test_remote_control_adds_accept_keyboard(tmp_path: Path):
    service, telegram = make_service(tmp_path)
    service.config.remote_control.enabled = True

    service.handle_event(found_event())

    assert telegram.markups[0]["inline_keyboard"][0][0]["callback_data"] == "accept_match"
    assert "Принять матч" in telegram.sent[0]


def test_no_keyboard_when_remote_control_disabled(tmp_path: Path):
    service, telegram = make_service(tmp_path)

    service.handle_event(found_event())

    assert telegram.markups == [None]


def test_started_event_removes_accept_keyboard(tmp_path: Path):
    service, telegram = make_service(tmp_path)
    service.config.remote_control.enabled = True

    service.handle_event(found_event())
    service.handle_event(
        MatchEvent(
            type=EventType.MATCH_STARTED,
            source="gsi",
            detected_at=time.time(),
            match_id="7898755227",
            hero="Invoker",
        )
    )

    assert telegram.markup_edits == [1001]


def test_started_event_enriches_existing_message(tmp_path: Path):
    service, telegram = make_service(tmp_path)

    service.handle_event(found_event())
    service._last_found_message = None
    service.handle_event(
        MatchEvent(
            type=EventType.MATCH_STARTED,
            source="gsi",
            detected_at=time.time(),
            match_id="7898755227",
            hero="Invoker",
            team="radiant",
        )
    )

    assert len(telegram.sent) == 1
    assert len(telegram.edited) == 0


def test_started_event_without_prior_notification(tmp_path: Path):
    service, telegram = make_service(tmp_path)

    service.handle_event(
        MatchEvent(
            type=EventType.MATCH_STARTED,
            source="gsi",
            detected_at=time.time(),
            match_id="7898755227",
            hero="Invoker",
        )
    )

    assert len(telegram.sent) == 0


def test_finished_event_is_deduplicated_by_match_id(tmp_path: Path):
    service, telegram = make_service(tmp_path)
    event = MatchEvent(
        type=EventType.MATCH_FINISHED,
        source="opendota",
        detected_at=time.time(),
        match_id="7898755227",
        victory=True,
    )

    service.handle_event(event)
    service.handle_event(event)

    assert len(telegram.sent) == 1


def test_found_is_suppressed_until_match_starts(tmp_path: Path):
    service, telegram = make_service(tmp_path)
    service.config.dedupe_window_seconds = 0
    service.config.telegram.notify_match_finished = False

    service._match_started_at = None
    service._in_match = False
    service.handle_event(found_event())
    service.handle_event(found_event())
    service.handle_event(found_event("steam"))

    assert len(telegram.sent) == 1

    service.handle_event(
        MatchEvent(
            type=EventType.MATCH_STARTED,
            source="gsi",
            detected_at=time.time(),
            match_id="1",
            hero="Invoker",
        )
    )
    service.handle_event(found_event())

    assert len(telegram.sent) == 1

    service.handle_event(
        MatchEvent(
            type=EventType.MATCH_FINISHED,
            source="gsi",
            detected_at=time.time(),
            match_id="1",
            victory=True,
        )
    )
    service._match_started_at = time.time() - 600
    service.handle_event(found_event())

    assert len(telegram.sent) == 2


def test_finished_message_is_enriched_with_lineups(tmp_path: Path):
    service, telegram = make_service(tmp_path)
    basic = MatchEvent(
        type=EventType.MATCH_FINISHED,
        source="gsi",
        detected_at=time.time(),
        match_id="42",
        victory=True,
    )
    rich = replace(
        basic,
        source="opendota",
        region=8,
        game_mode=22,
        lobby_type=7,
        players=(
            PlayerInfo(
                name="Me",
                hero="Invoker",
                team="radiant",
                kills=8,
                deaths=2,
                assists=11,
                top_heroes=(("Invoker", 12), ("Puck", 9)),
            ),
            PlayerInfo(name="Enemy", hero="Axe", team="dire"),
        ),
    )

    service.handle_event(basic)
    service.handle_event(rich)

    assert len(telegram.sent) == 1
    assert len(telegram.edited) == 1
    text = telegram.edited[0][1]
    assert "Стокгольм" in text
    assert "All Draft" in text
    assert "Invoker×12" in text
    assert "Axe" in text


def test_disabled_notifications_suppress_events(tmp_path: Path):
    service, telegram = make_service(tmp_path)
    service.state.set_flag("notifications_enabled", False)

    service.handle_event(found_event())

    assert telegram.sent == []


def test_status_counters(tmp_path: Path):
    service, telegram = make_service(tmp_path)

    service.handle_event(found_event())
    service.handle_event(
        MatchEvent(
            type=EventType.MATCH_FINISHED,
            source="opendota",
            detected_at=time.time(),
            match_id="1",
            victory=False,
        )
    )
    status = service.status()

    assert status["matches_found"] == 1
    assert status["matches_finished"] == 1
    assert status["notifications_sent"] == 2
    assert status["last_event"]["type"] == "match_finished"
