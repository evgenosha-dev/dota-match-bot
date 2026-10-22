import httpx
import pytest

from dota_bot import telegram_client as tg


class FakeResponse:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return {"ok": True, "result": {"message_id": 7}}


def test_send_message_retries_connect_errors(monkeypatch):
    calls = {"count": 0}

    def fake_request(method, url, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise httpx.ConnectTimeout("timed out")
        return FakeResponse()

    monkeypatch.setattr(tg.httpx, "request", fake_request)
    monkeypatch.setattr(tg.time, "sleep", lambda seconds: None)

    data = tg.TelegramClient("token").send_message("1", "hello")

    assert calls["count"] == 3
    assert data["ok"] is True


def test_send_message_does_not_retry_ambiguous_errors(monkeypatch):
    calls = {"count": 0}

    def fake_request(method, url, **kwargs):
        calls["count"] += 1
        raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(tg.httpx, "request", fake_request)

    with pytest.raises(tg.TelegramError):
        tg.TelegramClient("token").send_message("1", "hello")

    assert calls["count"] == 1
