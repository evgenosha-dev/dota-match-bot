import json
from pathlib import Path

from bot import _extract_start_code, _wait_for_pairing
from dota_bot.config import load_config
from dota_bot.pairing import PairingStore


class PairingTelegram:
    def __init__(self, updates: list[list[dict]]):
        self._updates = updates
        self.calls = 0
        self.sent: list[tuple[str, str]] = []

    def get_updates(self, offset=None, timeout=0):
        index = min(self.calls, len(self._updates) - 1)
        self.calls += 1
        return self._updates[index]

    def send_message(self, chat_id: str, text: str, **kwargs):
        self.sent.append((str(chat_id), text))
        return {"ok": True, "result": {"message_id": 1}}


def test_pairing_code_is_six_digits_and_single_use(tmp_path: Path):
    store = PairingStore(tmp_path / "pairing.json")
    store.load()
    code = store.create_code(ttl_seconds=180)

    assert len(code) == 6
    assert code.isdigit()
    assert store.claim(code, "12345") is True
    assert store.claim(code, "67890") is False

    data = json.loads((tmp_path / "pairing.json").read_text(encoding="utf-8"))
    assert data["codes"][code]["chat_id"] == "12345"
    assert data["codes"][code]["used"] is True


def test_expired_pairing_code_cannot_be_used(tmp_path: Path, monkeypatch):
    store = PairingStore(tmp_path / "pairing.json")
    store.load()
    monkeypatch.setattr("dota_bot.pairing.time.time", lambda: 1000.0)
    code = store.create_code(ttl_seconds=10)
    monkeypatch.setattr("dota_bot.pairing.time.time", lambda: 1011.0)

    assert store.claim(code, "12345") is False


def test_extract_start_code_from_telegram_update():
    update = {
        "update_id": 5,
        "message": {
            "text": "/start@BflyDotaBot 123456",
            "chat": {"id": 4242},
        },
    }

    assert _extract_start_code(update) == ("4242", "123456")
    assert _extract_start_code({"update_id": 6}) == (None, None)


def test_wait_for_pairing_claims_expected_code(tmp_path: Path):
    store = PairingStore(tmp_path / "pairing.json")
    store.load()
    code = store.create_code(ttl_seconds=180)
    update = {
        "update_id": 10,
        "message": {"text": f"/start {code}", "chat": {"id": 4242}},
    }
    client = PairingTelegram([[update]])

    assert _wait_for_pairing(client, store, code, timeout_seconds=5) == "4242"
    assert client.sent == [
        ("4242", "✅ Telegram привязан. Возвращайся в консоль — мастер продолжит настройку.")
    ]


def test_wait_for_pairing_ignores_other_active_code(tmp_path: Path):
    store = PairingStore(tmp_path / "pairing.json")
    store.load()
    expected = store.create_code(ttl_seconds=180)
    other = store.create_code(ttl_seconds=180)
    wrong_update = {
        "update_id": 20,
        "message": {"text": f"/start {other}", "chat": {"id": 1111}},
    }
    correct_update = {
        "update_id": 21,
        "message": {"text": f"/start {expected}", "chat": {"id": 2222}},
    }
    client = PairingTelegram([[wrong_update], [correct_update]])

    assert _wait_for_pairing(client, store, expected, timeout_seconds=5) == "2222"
    assert store.get_chat_id(other) is None
    assert store.get_chat_id(expected) == "2222"


def test_pairing_path_resolves_relative_to_config(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    config = load_config(config_path)

    assert config.state_path == (tmp_path / "state.json").resolve()
    assert config.pairing_path == (tmp_path / "pairing.json").resolve()
