from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _app_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_STATE_PATH = PROJECT_ROOT / "state.json"
DEFAULT_PAIRING_PATH = PROJECT_ROOT / "pairing.json"
STEAM_ID64_BASE = 76561197960265728
DOTA_APP_ID = 570


class ConfigError(RuntimeError):
    pass


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    notify_match_found: bool = True
    notify_match_finished: bool = True
    request_timeout_seconds: float = 10.0


@dataclass
class ForegroundConfig:
    enabled: bool = True
    process_names: list[str] = field(default_factory=lambda: ["dota2.exe"])
    poll_interval_ms: int = 250
    confirmation_delay_ms: int = 750
    cooldown_seconds: float = 120.0


@dataclass
class GsiConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 9977
    dota_dir: str | None = None
    write_config: bool = False
    config_filename: str = "gamestate_integration_dota_match_bot.cfg"


@dataclass
class SteamConfig:
    enabled: bool = False
    api_key: str = ""
    steamid64: str | None = None
    account_id: int | None = None
    poll_interval_seconds: float = 10.0
    base_url: str = "https://api.steampowered.com"


@dataclass
class OpenDotaConfig:
    enabled: bool = False
    base_url: str = "https://api.opendota.com/api"
    poll_interval_seconds: float = 60.0


@dataclass
class RemoteControlConfig:
    enabled: bool = False
    process_name: str = "dota2.exe"
    focus_window: bool = True


@dataclass
class BotConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    foreground: ForegroundConfig = field(default_factory=ForegroundConfig)
    gsi: GsiConfig = field(default_factory=GsiConfig)
    steam: SteamConfig = field(default_factory=SteamConfig)
    opendota: OpenDotaConfig = field(default_factory=OpenDotaConfig)
    remote_control: RemoteControlConfig = field(default_factory=RemoteControlConfig)
    dedupe_window_seconds: float = 90.0
    state_path: Path = Path("state.json")
    pairing_path: Path = Path("pairing.json")
    log_level: str = "INFO"

    @property
    def resolved_steamid64(self) -> str | None:
        if self.steam.steamid64:
            return str(self.steam.steamid64)
        if self.steam.account_id:
            return str(STEAM_ID64_BASE + int(self.steam.account_id))
        return None

    @property
    def resolved_account_id(self) -> int | None:
        if self.steam.account_id:
            return int(self.steam.account_id)
        if self.steam.steamid64:
            return int(self.steam.steamid64) - STEAM_ID64_BASE
        return None


def _build(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"Ожидался объект для {cls.__name__}, получен {type(data).__name__}")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Неизвестные параметры в {cls.__name__}: {', '.join(sorted(unknown))}")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = data[f.name]
    return cls(**kwargs)


def _from_dict(data: dict[str, Any]) -> BotConfig:
    known = {f.name for f in fields(BotConfig)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Неизвестные секции конфига: {', '.join(sorted(unknown))}")
    config = BotConfig()
    for name in known:
        if name not in data:
            continue
        section = data[name]
        current = getattr(config, name)
        if is_dataclass(current) and isinstance(section, dict):
            setattr(config, name, _build(type(current), section))
        else:
            setattr(config, name, section)
    return config


def load_config(path: Path | str | None = None) -> BotConfig:
    resolved = Path(path or os.environ.get("DOTA_BOT_CONFIG") or DEFAULT_CONFIG_PATH)
    data: dict[str, Any] = {}
    if resolved.exists():
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Некорректный JSON в {resolved}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"Ожидался JSON-объект в {resolved}")
        data = raw

    config = _from_dict(data)
    _apply_env(config)
    base = resolved.parent if path or os.environ.get("DOTA_BOT_CONFIG") else PROJECT_ROOT
    config.state_path = _resolve_data_path(config.state_path, base)
    config.pairing_path = _resolve_data_path(config.pairing_path, base)
    return config


def _resolve_data_path(value: Path | str, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _apply_env(config: BotConfig) -> None:
    token = os.environ.get("DOTA_BOT_TELEGRAM_TOKEN")
    if token:
        config.telegram.bot_token = token
    chat_id = os.environ.get("DOTA_BOT_TELEGRAM_CHAT_ID")
    if chat_id:
        config.telegram.chat_id = chat_id
    api_key = os.environ.get("DOTA_BOT_STEAM_API_KEY")
    if api_key:
        config.steam.api_key = api_key
        config.steam.enabled = True
    steamid = os.environ.get("DOTA_BOT_STEAM_ID64")
    if steamid:
        config.steam.steamid64 = steamid
    account_id = os.environ.get("DOTA_BOT_STEAM_ACCOUNT_ID")
    if account_id:
        config.steam.account_id = int(account_id)
    log_level = os.environ.get("DOTA_BOT_LOG_LEVEL")
    if log_level:
        config.log_level = log_level.upper()
    state_path = os.environ.get("DOTA_BOT_STATE_PATH")
    if state_path:
        config.state_path = Path(state_path)
    pairing_path = os.environ.get("DOTA_BOT_PAIRING_PATH")
    if pairing_path:
        config.pairing_path = Path(pairing_path)


def update_config_file(path: Path | str | None, updates: dict[str, Any]) -> Path:
    """Точечно обновляет секции config.json, сохраняя остальные настройки."""
    resolved = Path(path or os.environ.get("DOTA_BOT_CONFIG") or DEFAULT_CONFIG_PATH)
    data: dict[str, Any] = {}
    if resolved.exists():
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Некорректный JSON в {resolved}: {exc}") from exc
        if isinstance(raw, dict):
            data = raw
    for section, values in updates.items():
        if not isinstance(values, dict):
            data[section] = values
            continue
        current = data.get(section)
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(values)
        data[section] = merged
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return resolved


def validate(config: BotConfig, require_telegram: bool = True, check_detectors: bool = True) -> None:
    problems: list[str] = []
    if require_telegram:
        if not config.telegram.bot_token:
            problems.append("Не задан telegram.bot_token")
        if not config.telegram.chat_id:
            problems.append("Не задан telegram.chat_id")
    if check_detectors and config.steam.enabled:
        if not config.steam.api_key:
            problems.append("Включён steam-детектор, но не задан steam.api_key")
        if not config.resolved_steamid64:
            problems.append(
                "Включён steam-детектор, но не задан steam.steamid64 или steam.account_id "
                "(получи значение командой 'bot.py resolve <ссылка на профиль Steam>')"
            )
    if check_detectors and config.opendota.enabled and config.resolved_account_id is None:
        problems.append("Включён opendota-детектор, но не задан account_id")
    if check_detectors and config.foreground.poll_interval_ms < 50:
        problems.append("foreground.poll_interval_ms должен быть не меньше 50")
    if check_detectors and config.remote_control.enabled and not config.remote_control.process_name.strip():
        problems.append("Включено remote_control, но не задан remote_control.process_name")
    if problems:
        raise ConfigError("Ошибки конфигурации: " + "; ".join(problems))
