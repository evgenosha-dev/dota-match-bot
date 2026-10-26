import httpx
import pytest

from dota_bot.config import BotConfig
from dota_bot.relay_client import RelayClient, RelayError
from dota_bot.state import StateStore


def make_client(handler) -> RelayClient:
    transport = httpx.MockTransport(handler)
    return RelayClient(
        "https://relay.example.com/", "tok123", client=httpx.Client(transport=transport)
    )


def test_pair_create_posts_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"code": "ABCD-2345", "chat_id": None, "bot_username": "b"})

    client = make_client(handler)
    data = client.create_pairing()
    assert data["code"] == "ABCD-2345"
    assert seen["auth"] == "Bearer tok123"
    assert seen["path"] == "/pair"


def test_pair_status_returns_chat_id_when_bound():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json={"code": "ABCD-2345", "chat_id": "42"})

    client = make_client(handler)
    assert client.pairing_status()["chat_id"] == "42"


def test_fetch_updates_parses_items_and_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "after=7" in str(request.url)
        return httpx.Response(
            200,
            json={
                "cursor": 9,
                "items": [{"id": 8, "type": "command", "text": "/on"}],
            },
        )

    client = make_client(handler)
    items, cursor = client.fetch_updates(7)
    assert cursor == 9
    assert items[0]["type"] == "command"


def test_http_error_raises_relay_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = make_client(handler)
    with pytest.raises(RelayError):
        client.fetch_updates(0)


def test_notifier_interface_send_and_edit():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.read()))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    client = make_client(handler)
    result = client.send_message("42", "текст", reply_markup={"inline_keyboard": []})
    assert result["result"]["message_id"] == 5
    assert client.edit_message("42", 5, "новый текст") is True
    assert client.edit_message_reply_markup("42", 5) is True
    assert client.answer_callback("cb1", "ответ", alert=True) is True
    paths = [c[1] for c in calls]
    assert "/send" in paths and "/edit" in paths and "/answer" in paths


def test_relay_config_defaults_and_env(monkeypatch):
    config = BotConfig()
    assert config.relay.enabled is False
    monkeypatch.setenv("DOTA_BOT_RELAY_URL", "https://x.example.com/")
    monkeypatch.setenv("DOTA_BOT_RELAY_TOKEN", "abc")
    from dota_bot.config import load_config

    loaded = load_config()
    assert loaded.relay.enabled is True
    assert loaded.relay.url == "https://x.example.com"
    assert loaded.relay.token == "abc"


def test_state_cursor_roundtrip(tmp_path):
    state = StateStore(tmp_path / "state.json")
    state.load()
    state.set_flag("relay_cursor", 123)
    state.save()
    again = StateStore(tmp_path / "state.json")
    again.load()
    assert again.get_value("relay_cursor") == 123
