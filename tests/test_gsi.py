import queue

from dota_bot.detectors.gsi import GsiDetector, parse_gsi_payload

PAYLOAD = {
    "provider": {"name": "Dota 2", "appid": 570, "version": 1234},
    "map": {
        "name": "dota",
        "matchid": "7898755227",
        "game_time": 300,
        "clock_time": 300,
        "game_state": "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
        "paused": False,
        "win_team": "none",
    },
    "player": {
        "steamid": "76561197960287930",
        "name": "Player",
        "activity": "playing",
        "kills": 5,
        "deaths": 1,
        "assists": 7,
        "team_name": "radiant",
        "gold": 12000,
        "gpm": 610,
        "xpm": 700,
    },
    "hero": {"id": 1, "name": "Anti-Mage", "level": 12, "health": 1800, "mana": 400},
}


def test_parse_gsi_payload():
    snapshot = parse_gsi_payload(PAYLOAD)

    assert snapshot.match_id == "7898755227"
    assert snapshot.game_state == "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"
    assert snapshot.team == "radiant"
    assert snapshot.hero == "Anti-Mage"
    assert snapshot.hero_id == 1
    assert snapshot.kills == 5
    assert snapshot.gpm == 610
    assert snapshot.in_match is True
    assert snapshot.finished is False


def test_parse_gsi_payload_handles_empty_input():
    snapshot = parse_gsi_payload({})

    assert snapshot.match_id is None
    assert snapshot.game_state is None
    assert snapshot.in_match is False


def test_detector_emits_started_and_finished():
    sink: queue.Queue = queue.Queue()
    detector = GsiDetector(sink, host="127.0.0.1", port=0)

    detector._handle_payload(PAYLOAD)
    started = sink.get_nowait()
    assert started.type.value == "match_started"
    assert started.hero == "Anti-Mage"
    assert started.match_id == "7898755227"

    finished_payload = {
        "map": {"matchid": "7898755227", "game_state": "DOTA_GAMERULES_STATE_POST_GAME", "win_team": "radiant"},
        "player": {"team_name": "radiant", "kills": 10, "deaths": 2, "assists": 12, "gpm": 700},
        "hero": {"id": 1, "name": "Anti-Mage"},
    }
    detector._handle_payload(finished_payload)
    finished = sink.get_nowait()
    assert finished.type.value == "match_finished"
    assert finished.victory is True
    assert finished.kills == 10


def test_detector_does_not_repeat_started_event():
    sink: queue.Queue = queue.Queue()
    detector = GsiDetector(sink, host="127.0.0.1", port=0)

    detector._handle_payload(PAYLOAD)
    detector._handle_payload(PAYLOAD)

    assert sink.qsize() == 1


def test_detector_resets_on_new_match():
    sink: queue.Queue = queue.Queue()
    detector = GsiDetector(sink, host="127.0.0.1", port=0)

    detector._handle_payload(PAYLOAD)
    sink.get_nowait()

    new_match = dict(PAYLOAD)
    new_match["map"] = dict(PAYLOAD["map"], matchid="1111111111")
    detector._handle_payload(new_match)

    event = sink.get_nowait()
    assert event.match_id == "1111111111"


def test_parse_extracts_player_steamid():
    from dota_bot.detectors.gsi import parse_gsi_payload

    payload = {
        "map": {"matchid": "123", "game_state": "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"},
        "player": {"team_name": "radiant", "name": "X", "steamid": "389318685"},
        "hero": {"name": "npc_dota_hero_axe", "id": 2},
    }
    snapshot = parse_gsi_payload(payload)
    assert snapshot.steam_id == "389318685"
    assert snapshot.in_match is True


def test_own_match_detection():
    from dota_bot.detectors.gsi import GsiDetector, GsiSnapshot

    own = GsiDetector(queue.Queue(), account_id=389318685)
    foreign = GsiDetector(queue.Queue(), account_id=389318685)
    unknown = GsiDetector(queue.Queue())

    snap_own = GsiSnapshot(received_at=0.0, steam_id="389318685")
    snap_foreign = GsiSnapshot(received_at=0.0, steam_id="111222333")
    snap_no_player = GsiSnapshot(received_at=0.0, steam_id=None)

    assert own._is_own_match(snap_own) is True
    assert own._is_own_match(snap_foreign) is False
    assert own._is_own_match(snap_no_player) is True  # нет данных — старое поведение
    assert foreign._is_own_match(snap_foreign) is False
    assert unknown._is_own_match(snap_foreign) is True  # без account_id не проверяем
