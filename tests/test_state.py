import time
from pathlib import Path

from dota_bot.state import StateStore


def test_dedupe_window(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.load()

    assert store.should_notify("match_found", 60) is True
    store.mark("match_found")

    assert store.should_notify("match_found", 60) is False
    assert store.should_notify("other", 60) is True


def test_dedupe_expires(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.mark("match_found")

    store._data["recent"]["match_found"] = time.time() - 3600
    assert store.should_notify("match_found", 60) is True


def test_clear_allows_next_notification(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    store.mark("match_found")

    store.clear("match_found")

    assert store.should_notify("match_found", 3600) is True


def test_stats_and_flags(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.load()

    store.bump("matches_found")
    store.bump("matches_found", 2)
    assert store.get_stat("matches_found") == 3

    store.set_flag("notifications_enabled", False)
    assert store.get_flag("notifications_enabled") is False
    assert store.get_flag("unknown_flag", True) is True


def test_state_survives_reload(tmp_path: Path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.load()
    store.bump("notifications_sent")
    store.set_last_event({"type": "match_found", "source": "foreground", "at": 123.0})
    store.save()

    reloaded = StateStore(path)
    reloaded.load()
    assert reloaded.get_stat("notifications_sent") == 1
    assert reloaded.last_event["type"] == "match_found"
