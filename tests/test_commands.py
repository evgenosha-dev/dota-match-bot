import bot
from dota_bot import commands
from dota_bot.config import BotConfig
from dota_bot.telegram_client import TelegramError


def test_connect_notifier_retries_transient_failures(monkeypatch):
    class FlakyTelegram:
        attempts = 0

        def __init__(self, token, timeout=10.0):
            pass

        def get_me(self):
            FlakyTelegram.attempts += 1
            if FlakyTelegram.attempts < 3:
                raise TelegramError("timed out")
            return {"username": "bot"}

    monkeypatch.setattr(bot, "TelegramClient", FlakyTelegram)
    monkeypatch.setattr(bot.time, "sleep", lambda seconds: None)

    notifier = bot.connect_notifier(BotConfig())

    assert isinstance(notifier, FlakyTelegram)
    assert FlakyTelegram.attempts == 3


def test_telegram_handlers_are_not_cli_functions():
    assert bot.tg_cmd_start is commands.cmd_start
    assert bot.tg_cmd_on is commands.cmd_on
    assert callable(bot.cmd_test)
    for removed in ("cmd_test", "cmd_status", "cmd_help"):
        assert not hasattr(commands, removed)
