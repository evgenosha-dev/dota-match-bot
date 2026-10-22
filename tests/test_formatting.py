import time

from dota_bot.formatting import format_match_finished, format_match_found
from dota_bot.models import EventType, MatchEvent, PlayerInfo


def test_match_found_message_from_foreground():
    event = MatchEvent(type=EventType.MATCH_FOUND, source="foreground", detected_at=time.time())
    text = format_match_found(event)

    assert "Матч найден" in text
    assert "Принять" in text


def test_match_found_message_contains_details():
    event = MatchEvent(
        type=EventType.MATCH_STARTED,
        source="gsi",
        detected_at=time.time(),
        hero="Invoker",
        team="radiant",
        match_id="7898755227",
    )
    text = format_match_found(event)

    assert "Invoker" in text
    assert "Свет" in text
    assert "7898755227" in text


def test_hero_name_is_escaped():
    event = MatchEvent(
        type=EventType.MATCH_STARTED,
        source="gsi",
        detected_at=time.time(),
        hero="A & B <script>",
    )
    text = format_match_found(event)

    assert "&amp;" in text
    assert "<script>" not in text


def test_match_finished_message():
    event = MatchEvent(
        type=EventType.MATCH_FINISHED,
        source="opendota",
        detected_at=time.time(),
        victory=True,
        hero="Invoker",
        kills=8,
        deaths=2,
        assists=11,
        duration_seconds=1970,
        gpm=620,
    )
    text = format_match_finished(event)

    assert "Победа" in text
    assert "8/2/11" in text
    assert "32:50" in text
    assert "620" in text


def test_defeat_message():
    event = MatchEvent(
        type=EventType.MATCH_FINISHED,
        source="opendota",
        detected_at=time.time(),
        victory=False,
    )
    assert "Поражение" in format_match_finished(event)


def test_finished_message_with_lineups_and_favorite_heroes():
    event = MatchEvent(
        type=EventType.MATCH_FINISHED,
        source="opendota",
        detected_at=time.time(),
        match_id="42",
        region=8,
        game_mode=22,
        lobby_type=7,
        radiant_score=31,
        dire_score=24,
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
            PlayerInfo(name="Enemy", hero="Axe", team="dire", kills=4, deaths=7, assists=3),
        ),
    )
    text = format_match_finished(event)

    assert "Стокгольм" in text
    assert "All Draft" in text
    assert "Ranked" in text
    assert "Свет" in text and "Тьма" in text
    assert "Invoker×12" in text
    assert "8/2/11" in text
    assert "Axe" in text
