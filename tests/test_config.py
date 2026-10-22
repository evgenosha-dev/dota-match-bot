import json
from pathlib import Path

import pytest

from dota_bot.config import ConfigError, load_config, update_config_file, validate


def write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_defaults_are_applied(tmp_path: Path):
    path = write_config(tmp_path, {"telegram": {"bot_token": "abc:def", "chat_id": "42"}})
    config = load_config(path)

    assert config.telegram.bot_token == "abc:def"
    assert config.foreground.enabled is True
    assert config.foreground.process_names == ["dota2.exe"]
    assert config.gsi.enabled is False
    assert config.remote_control.enabled is False
    assert config.remote_control.process_name == "dota2.exe"
    assert config.state_path == (tmp_path / "state.json").resolve()


def test_unknown_section_is_rejected(tmp_path: Path):
    path = write_config(tmp_path, {"unknown": {}})
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "unknown" in str(excinfo.value)


def test_unknown_option_is_rejected(tmp_path: Path):
    path = write_config(tmp_path, {"foreground": {"pol_interval": 10}})
    with pytest.raises(ConfigError):
        load_config(path)


def test_env_overrides_file(tmp_path: Path, monkeypatch):
    path = write_config(tmp_path, {"telegram": {"bot_token": "file:token", "chat_id": "1"}})
    monkeypatch.setenv("DOTA_BOT_TELEGRAM_TOKEN", "env:token")
    monkeypatch.setenv("DOTA_BOT_TELEGRAM_CHAT_ID", "999")

    config = load_config(path)

    assert config.telegram.bot_token == "env:token"
    assert config.telegram.chat_id == "999"


def test_steamid_conversion(tmp_path: Path):
    path = write_config(tmp_path, {"steam": {"account_id": 123456}})
    config = load_config(path)

    assert config.resolved_steamid64 == str(76561197960265728 + 123456)
    assert config.resolved_account_id == 123456


def test_steamid64_to_account_id(tmp_path: Path):
    path = write_config(tmp_path, {"steam": {"steamid64": "76561197960287930"}})
    config = load_config(path)

    assert config.resolved_account_id == 76561197960287930 - 76561197960265728


def test_validate_requires_telegram(tmp_path: Path):
    path = write_config(tmp_path, {})
    config = load_config(path)

    with pytest.raises(ConfigError):
        validate(config, require_telegram=True)

    validate(config, require_telegram=False)


def test_validate_requires_steam_credentials(tmp_path: Path):
    path = write_config(tmp_path, {"steam": {"enabled": True}})
    config = load_config(path)

    with pytest.raises(ConfigError) as excinfo:
        validate(config, require_telegram=False)
    assert "steam.api_key" in str(excinfo.value)


def test_update_config_file_merges_sections(tmp_path: Path):
    path = write_config(tmp_path, {"steam": {"api_key": "key", "enabled": True}, "log_level": "INFO"})

    update_config_file(path, {"telegram": {"bot_token": "a:b", "chat_id": "42"}})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["telegram"] == {"bot_token": "a:b", "chat_id": "42"}
    assert data["steam"] == {"api_key": "key", "enabled": True}
    assert data["log_level"] == "INFO"


def test_update_config_file_creates_missing_file(tmp_path: Path):
    path = tmp_path / "config.json"

    update_config_file(path, {"telegram": {"chat_id": "42"}})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["telegram"]["chat_id"] == "42"
