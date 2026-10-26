from dota_bot.commands import command_reply, normalize_command
from dota_bot.state import StateStore


def test_normalize_command_strips_bot_mention():
    assert normalize_command("/on@MyDotaBot") == "/on"
    assert normalize_command("/OFF extra words") == "/off"
    assert normalize_command("  /start 42") == "/start"
    assert normalize_command("привет") == ""
    assert normalize_command("") == ""


def test_command_reply_on_off_toggles_flag(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.load()
    state.set_flag("notifications_enabled", True)

    assert command_reply(state, "/off") == "🔕 Уведомления выключены."
    assert state.get_flag("notifications_enabled") is False

    assert command_reply(state, "/on") == "🔔 Уведомления включены."
    assert state.get_flag("notifications_enabled") is True


def test_command_reply_start_returns_help(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.load()
    reply = command_reply(state, "/start")
    assert reply and "/on" in reply


def test_command_reply_unknown_is_none(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.load()
    assert command_reply(state, "/что-то") is None
    assert command_reply(state, "просто текст") is None
