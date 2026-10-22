from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from dota_bot.commands import (
    cmd_off as tg_cmd_off,
    cmd_on as tg_cmd_on,
    cmd_start as tg_cmd_start,
    on_callback as tg_on_callback,
)
from dota_bot.config import (
    ConfigError,
    BotConfig,
    STEAM_ID64_BASE,
    load_config,
    update_config_file,
    validate,
)
from dota_bot.detectors.gsi import write_gsi_config
from dota_bot.formatting import format_test_message
from dota_bot.paths import find_dota_dir, steamid_from_url
from dota_bot.service import MatchService
from dota_bot.state import StateStore
from dota_bot.telegram_client import TelegramClient, TelegramError

log = logging.getLogger("dota_match_bot")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def load_or_fail(
    args: argparse.Namespace,
    require_telegram: bool = True,
    check_detectors: bool = True,
) -> BotConfig:
    try:
        config = load_config(getattr(args, "config", None))
        validate(config, require_telegram=require_telegram, check_detectors=check_detectors)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        raise SystemExit(2)
    return config


def build_state(config: BotConfig) -> StateStore:
    state = StateStore(config.state_path)
    state.load()
    return state


def cmd_setup(args: argparse.Namespace) -> int:
    try:
        config = load_config(getattr(args, "config", None))
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    setup_logging(config.log_level)

    print("=" * 52)
    print("  Dota Match Bot — первичная настройка")
    print("=" * 52)

    if config.telegram.bot_token:
        print(f"Текущий токен: ...{config.telegram.bot_token[-6:]}")
    print("\nШаг 1/4. Telegram")
    print("Создай бота у @BotFather (/newbot) и вставь токен.")
    token = (
        getattr(args, "token", None)
        or os.environ.get("DOTA_BOT_TELEGRAM_TOKEN")
        or _prompt_secret("Токен (Enter — оставить текущий): ")
        or config.telegram.bot_token
    )
    if not token:
        print("Токен не указан.", file=sys.stderr)
        return 2

    client = TelegramClient(token, timeout=config.telegram.request_timeout_seconds)
    try:
        me = client.get_me()
    except TelegramError as exc:
        print(f"Telegram отклонил токен: {exc}", file=sys.stderr)
        return 3
    print(f"Бот подключён: @{me.get('username')} ({me.get('first_name')})")

    chat_id = (
        getattr(args, "chat_id", None)
        or os.environ.get("DOTA_BOT_TELEGRAM_CHAT_ID")
        or config.telegram.chat_id
    )
    if chat_id:
        print(f"Chat ID: {chat_id}")
    else:
        print("Теперь напиши этому боту любое сообщение в Telegram.")
        input("Нажми Enter, когда отправишь: ")
        chat_id = client.find_chat_id(timeout_seconds=45)
        if not chat_id:
            chat_id = _prompt("Chat ID (оставь пустым, чтобы пропустить): ")
    if not chat_id:
        print("Chat ID не найден. Укажи его в config.json вручную.", file=sys.stderr)
        return 2
    print(f"Chat ID: {chat_id}")

    updates: dict[str, dict] = {
        "telegram": {"bot_token": token, "chat_id": str(chat_id)}
    }

    print("\nШаг 2/4. Steam (необязательно)")
    print("Ключ: https://steamcommunity.com/dev/apikey — нужен для статуса «в игре».")
    api_key = (
        getattr(args, "steam_api_key", None)
        or os.environ.get("DOTA_BOT_STEAM_API_KEY")
        or _prompt_secret("Steam API key (Enter — пропустить): ")
        or config.steam.api_key
    )
    profile = (
        getattr(args, "steam_id", None)
        or os.environ.get("DOTA_BOT_STEAM_ID64")
        or _prompt("Ссылка на профиль Steam / steamid64 / account_id (Enter — пропустить): ")
        or config.steam.steamid64
        or (str(config.steam.account_id) if config.steam.account_id else "")
    )
    steamid64, account_id = _resolve_steam_profile(
        str(profile), api_key, config.steam.base_url
    )
    if steamid64 and account_id:
        if api_key:
            error = _steam_key_error(api_key, steamid64, config.steam.base_url)
            if error:
                print(f"Steam не принял ключ: {error}")
                api_key = ""
        steam_updates = {
            "steamid64": steamid64,
            "account_id": account_id,
            "api_key": api_key,
            "enabled": bool(api_key),
        }
        updates["steam"] = steam_updates
        updates["opendota"] = {"enabled": True}
        print(f"Steam ID: {steamid64} (account_id {account_id})")
    elif api_key:
        updates["steam"] = {"api_key": api_key}
        print("Steam ID не определён — Steam-детектор останется выключенным.")
    else:
        print("Пропущено: останутся только GSI и детектор окна Dota 2.")

    print("\nШаг 3/4. Game State Integration")
    if sys.platform != "win32":
        print("GSI доступен только на Windows — пропущено.")
    else:
        dota_dir = config.gsi.dota_dir or find_dota_dir()
        if dota_dir:
            try:
                gsi_path = write_gsi_config(
                    str(dota_dir),
                    config.gsi.host,
                    config.gsi.port,
                    config.gsi.config_filename,
                )
                updates["gsi"] = {"enabled": True}
                print(f"GSI-конфиг записан: {gsi_path}")
                print("Перезапусти Dota 2, чтобы она начала отправлять данные.")
            except OSError as exc:
                print(f"Не удалось записать GSI-конфиг: {exc}")
        else:
            print("Dota 2 не найдена. Позже выполни: dota-match-bot.exe gsi-setup")

    print("\nШаг 4/4. Автопринятие матча")
    if sys.platform == "win32":
        if _ask_yes("Включить кнопку «Принять матч» в Telegram?", default=True):
            updates["remote_control"] = {"enabled": True}
        else:
            updates["remote_control"] = {"enabled": False}
    else:
        print("Автопринятие работает только на Windows — пропущено.")

    path = update_config_file(getattr(args, "config", None), updates)
    print(f"\nНастройки сохранены: {path}")

    try:
        validate(load_config(getattr(args, "config", None)))
    except ConfigError as exc:
        print(f"Проверка конфигурации: {exc}", file=sys.stderr)
        return 2

    try:
        client.send_message(str(chat_id), format_test_message())
        print("Тестовое уведомление отправлено в Telegram.")
    except TelegramError as exc:
        print(f"Не удалось отправить тест: {exc}", file=sys.stderr)
        return 3
    return 0


def _ask_yes(text: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _prompt(f"{text} [{hint}]: ").lower()
    if not answer:
        return default
    return answer in {"y", "yes", "д", "да"}


def _resolve_steam_profile(
    value: str, api_key: str, base_url: str
) -> tuple[str | None, int | None]:
    value = value.strip()
    if not value:
        return None, None
    steamid = steamid_from_url(value)
    if not steamid and value.isdigit() and len(value) <= 10:
        account_id = int(value)
        return str(STEAM_ID64_BASE + account_id), account_id
    if not steamid and api_key:
        vanity = value.rstrip("/").split("/")[-1]
        try:
            response = httpx.get(
                f"{base_url}/ISteamUser/ResolveVanityURL/v1/",
                params={"key": api_key, "vanityurl": vanity},
                timeout=15.0,
            )
            response.raise_for_status()
            resolved = (response.json().get("response") or {}).get("steamid")
            if isinstance(resolved, str) and resolved.isdigit():
                steamid = resolved
        except (httpx.HTTPError, ValueError):
            steamid = None
    if steamid:
        return steamid, int(steamid) - STEAM_ID64_BASE
    return None, None


def _steam_key_error(api_key: str, steamid64: str, base_url: str) -> str | None:
    try:
        response = httpx.get(
            f"{base_url}/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": api_key, "steamids": steamid64},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        return str(exc)
    if response.status_code == 403:
        return "ключ отклонён (403)"
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    players = (response.json().get("response") or {}).get("players") or []
    if not players:
        return "профиль не найден или скрыт"
    return None


def _prompt(text: str) -> str:
    try:
        return input(text).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _prompt_secret(text: str) -> str:
    try:
        import getpass

        return getpass.getpass(text).strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:
        return _prompt(text)


def connect_notifier(config: BotConfig, attempts: int = 3) -> TelegramClient:
    token = config.telegram.bot_token
    timeout = config.telegram.request_timeout_seconds
    last_error: TelegramError | None = None
    for attempt in range(1, attempts + 1):
        try:
            probe = TelegramClient(token, timeout=min(timeout, 5.0))
            me = probe.get_me()
            log.info("Telegram-бот @%s подключён", me.get("username"))
            return TelegramClient(token, timeout=timeout)
        except TelegramError as exc:
            last_error = exc
            log.warning("Не удалось подключиться к Telegram (%d/%d): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    raise last_error or TelegramError("Не удалось подключиться к Telegram")


def cmd_run(args: argparse.Namespace) -> int:
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler

    require_telegram = not args.no_telegram
    try:
        config = load_config(getattr(args, "config", None))
        validate(config, require_telegram=require_telegram)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        if not require_telegram:
            return 2
        print("\nБот ещё не настроен — запускаю мастер настройки.\n")
        setup_args = argparse.Namespace(
            config=getattr(args, "config", None),
            token=None,
            chat_id=None,
            steam_api_key=None,
            steam_id=None,
        )
        code = cmd_setup(setup_args)
        if code != 0:
            return code
        try:
            config = load_config(getattr(args, "config", None))
            validate(config)
        except ConfigError as exc2:
            print(f"Ошибка конфигурации: {exc2}", file=sys.stderr)
            return 2
    setup_logging(config.log_level)
    state = build_state(config)

    notifier: TelegramClient | None = None
    if not args.no_telegram:
        try:
            notifier = connect_notifier(config)
        except TelegramError as exc:
            print(f"Не удалось подключиться к Telegram: {exc}", file=sys.stderr)
            return 3

    service = MatchService(config, notifier, state)
    service.start()

    thread = threading.Thread(target=service.run_forever, name="match-service", daemon=True)
    thread.start()

    if args.no_telegram:
        log.info("Режим без Telegram: детекторы работают, уведомления только в лог")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            service.stop()
            return 0

    application = Application.builder().token(config.telegram.bot_token).build()
    application.bot_data["service"] = service
    application.bot_data["state"] = state
    application.bot_data["config"] = config
    application.add_handler(CallbackQueryHandler(tg_on_callback))
    application.add_handler(CommandHandler("start", tg_cmd_start))
    application.add_handler(CommandHandler("on", tg_cmd_on))
    application.add_handler(CommandHandler("off", tg_cmd_off))
    log.info("Бот запущен, ожидаю матчи")
    try:
        application.run_polling()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    config = load_or_fail(args)
    setup_logging(config.log_level)
    try:
        client = TelegramClient(
            config.telegram.bot_token, timeout=config.telegram.request_timeout_seconds
        )
        client.send_message(config.telegram.chat_id, format_test_message())
    except TelegramError as exc:
        print(f"Не удалось отправить тест: {exc}", file=sys.stderr)
        return 3
    print("Тестовое уведомление отправлено.")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    config = load_or_fail(args, require_telegram=False)
    setup_logging(config.log_level)
    state = build_state(config)
    service = MatchService(config, None, state)
    service.start()
    print("Детекторы запущены. События будут выводиться в лог. Ctrl+C — выход.")
    try:
        service.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 0


def cmd_gsi_setup(args: argparse.Namespace) -> int:
    config = load_or_fail(args, require_telegram=False)
    setup_logging(config.log_level)
    dota_dir = args.dota_dir or config.gsi.dota_dir or find_dota_dir()
    if not dota_dir:
        print(
            "Каталог Dota 2 не найден. Укажи его явно: --dota-dir 'C:/.../dota 2 beta'",
            file=sys.stderr,
        )
        return 2
    path = write_gsi_config(
        str(dota_dir),
        config.gsi.host,
        config.gsi.port,
        config.gsi.config_filename,
    )
    print(f"GSI-конфиг записан: {path}")
    print(f"Убедись, что в config.json включено: gsi.enabled = true, порт {config.gsi.port}")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    config = load_or_fail(args, require_telegram=False, check_detectors=False)
    setup_logging(config.log_level)
    steamid = steamid_from_url(args.value)
    if steamid:
        print(steamid)
        return 0
    if config.steam.api_key and "/" in args.value:
        vanity = args.value.rstrip("/").split("/")[-1]
        try:
            response = httpx.get(
                f"{config.steam.base_url}/ISteamUser/ResolveVanityURL/v1/",
                params={"key": config.steam.api_key, "vanityurl": vanity},
                timeout=15.0,
            )
            payload = response.json()
            resolved = (payload.get("response") or {}).get("steamid")
            if resolved:
                print(resolved)
                return 0
        except httpx.HTTPError as exc:
            print(f"Не удалось разрешить vanity-URL: {exc}", file=sys.stderr)
            return 3
    print("Не удалось определить steamid64", file=sys.stderr)
    return 2


def cmd_status(args: argparse.Namespace) -> int:
    config = load_or_fail(args, require_telegram=False, check_detectors=False)
    setup_logging(config.log_level)
    state = build_state(config)
    last = state.last_event
    print(f"Детекторы: foreground={config.foreground.enabled} gsi={config.gsi.enabled} steam={config.steam.enabled}")
    print(f"Найдено матчей: {state.get_stat('matches_found')}")
    print(f"Завершено матчей: {state.get_stat('matches_finished')}")
    print(f"Отправлено уведомлений: {state.get_stat('notifications_sent')}")
    if last:
        age = int(time.time() - float(last.get("at", 0)))
        print(f"Последнее событие: {last.get('type')} ({last.get('source')}, {age} с назад)")
    else:
        print("Событий пока не было.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="путь к config.json")

    parser = argparse.ArgumentParser(
        prog="dota-match-bot",
        description="Telegram-бот, оповещающий о найденном матче в Dota 2",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="запустить агент и Telegram-бота", parents=[common])
    run.add_argument("--no-telegram", action="store_true", help="только детекторы, без Telegram")
    run.set_defaults(func=cmd_run)

    test = sub.add_parser("test", help="отправить тестовое уведомление", parents=[common])
    test.set_defaults(func=cmd_test)

    detect = sub.add_parser("detect", help="только детекторы, события в лог", parents=[common])
    detect.set_defaults(func=cmd_detect)

    gsi = sub.add_parser("gsi-setup", help="записать GSI-конфиг в каталог Dota 2", parents=[common])
    gsi.add_argument("--dota-dir", help="путь к каталогу dota 2 beta")
    gsi.set_defaults(func=cmd_gsi_setup)

    resolve = sub.add_parser("resolve", help="определить steamid64 по ссылке профиля", parents=[common])
    resolve.add_argument("value", help="ссылка на профиль Steam или vanity-имя")
    resolve.set_defaults(func=cmd_resolve)

    setup = sub.add_parser(
        "setup",
        help="мастер настройки: Telegram, Steam, GSI, автопринятие",
        parents=[common],
    )
    setup.add_argument("--token", help="токен от @BotFather")
    setup.add_argument("--chat-id", help="chat_id получателя (если известен — вводить не нужно)")
    setup.add_argument("--steam-api-key", help="ключ Steam Web API")
    setup.add_argument("--steam-id", help="ссылка на профиль Steam, steamid64 или account_id")
    setup.set_defaults(func=cmd_setup)

    status = sub.add_parser("status", help="показать состояние из state.json", parents=[common])
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw)
    if not getattr(args, "func", None):
        args = parser.parse_args(["run", *raw])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
