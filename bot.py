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
from dota_bot.autostart import SingleInstance, install_autostart, start_background
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
from dota_bot.pairing import PairingStore
from dota_bot.paths import find_dota_dir, steamid_from_url
from dota_bot.service import MatchService
from dota_bot.state import StateStore
from dota_bot.telegram_client import TelegramClient, TelegramError

log = logging.getLogger("dota_match_bot")


def setup_logging(level: str) -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
    )
    # Дублируем лог в файл — в фоновом режиме (pythonw, без консоли) это
    # единственный способ диагностики.
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        try:
            log_dir = PROJECT_ROOT / "logs"
            log_dir.mkdir(exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "bot.log", encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(fmt))
            root.addHandler(file_handler)
        except OSError:
            pass


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
    print("  Dota Match Bot — привязка по коду")
    print("=" * 52)

    print("\nШаг 1/5. Telegram")
    print("Используем общий бот @BflyDotaBot")
    token = config.telegram.bot_token or "8498537696:AAHQDDIRCDyNQYiiwMGQpsud47ch7bJKPXo"
    client = TelegramClient(token, timeout=config.telegram.request_timeout_seconds)
    try:
        me = client.get_me()
    except TelegramError as exc:
        print(f"Не удалось подключиться к боту: {exc}", file=sys.stderr)
        return 3
    print(f"Бот: @{me.get('username')}")

    chat_id = (
        getattr(args, "chat_id", None)
        or os.environ.get("DOTA_BOT_TELEGRAM_CHAT_ID")
        or config.telegram.chat_id
    )
    if not chat_id:
        store = PairingStore(config.pairing_path)
        store.load()
        code = store.create_code()
        print("\n" + "=" * 52)
        print(f"КОД ПРИВЯЗКИ: {code}")
        print("=" * 52)
        print("\n1. Открой @BflyDotaBot")
        print(f"2. Напиши: /start {code}")
        print("3. Код одноразовый, срока действия нет")
        print("4. Мастер ждёт подтверждение автоматически")
        print("\nЖду привязку Telegram...")
        try:
            chat_id = _wait_for_pairing(client, store, code, timeout_seconds=180)
        except TelegramError as exc:
            print(f"Не удалось дождаться Telegram: {exc}", file=sys.stderr)
            return 3
        if not chat_id:
            print(
                "Время ожидания вышло, но код остаётся действительным — "
                "просто запусти setup ещё раз, новый код не понадобится.",
                file=sys.stderr,
            )
            return 2
        print(f"Привязан chat_id: {chat_id}")
    else:
        print(f"Chat ID уже настроен: {chat_id}")

    updates: dict[str, dict] = {
        "telegram": {"bot_token": token, "chat_id": str(chat_id)}
    }

    print("\nШаг 2/5. Steam")
    print("Ключ: https://steamcommunity.com/dev/apikey — обязателен для статуса «в игре».")
    print("Вставка из буфера работает: Ctrl+V.")
    api_key = (
        getattr(args, "steam_api_key", None)
        or os.environ.get("DOTA_BOT_STEAM_API_KEY")
        or config.steam.api_key
    )
    while not api_key:
        entered = _prompt_secret("Steam API key: ")
        if entered:
            api_key = entered
        else:
            print("Ключ обязателен — вставь его сюда (Ctrl+V) и нажми Enter.")
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

    print("\nШаг 3/5. Game State Integration")
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

    print("\nШаг 4/5. Автопринятие матча")
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

    if sys.platform == "win32":
        print("\nШаг 5/5. Автозапуск")
        try:
            autostart_file = install_autostart(PROJECT_ROOT)
            print(f"Бот будет запускаться сам при входе в Windows:")
            print(f"  {autostart_file}")
        except RuntimeError as exc:
            print(f"Не удалось прописать автозапуск: {exc}", file=sys.stderr)

        if _ask_yes("Запустить бота в фоне сейчас?", default=True):
            try:
                start_background(PROJECT_ROOT)
                print("Бот запущен в фоне (без окон, логи: logs\\bot.log).")
            except RuntimeError as exc:
                print(f"Не удалось запустить в фоне: {exc}", file=sys.stderr)
                print("Запусти позже двойным кликом: start_dota_match_bot.cmd")
        print("\nГотово. Это окно можно закрыть — бот работает в фоне")
        print("и стартует сам после перезагрузки Windows.")
    return 0


def _extract_start_code(update: dict) -> tuple[str | None, str | None]:
    """Возвращает (chat_id, код) из команды /start <код> в обновлении Telegram."""
    for key in ("message", "edited_message"):
        payload = update.get(key)
        if not isinstance(payload, dict):
            continue
        parts = str(payload.get("text") or "").strip().split()
        if not parts:
            continue
        command = parts[0].split("@", 1)[0].lower()
        if command != "/start":
            continue
        chat = payload.get("chat")
        if not isinstance(chat, dict) or chat.get("id") is None:
            continue
        code = parts[1] if len(parts) > 1 else None
        return str(chat["id"]), code
    return None, None


def _wait_for_pairing(
    client: TelegramClient,
    store: PairingStore,
    code: str,
    timeout_seconds: int = 180,
) -> str | None:
    deadline = time.time() + timeout_seconds
    offset: int | None = None
    while time.time() < deadline:
        updates = client.get_updates(offset=offset, timeout=10)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = max(offset or 0, update_id + 1)
            chat_id, supplied_code = _extract_start_code(update)
            if chat_id is None:
                continue
            if supplied_code is None:
                continue
            if supplied_code != code:
                continue
            if store.claim(supplied_code, chat_id):
                try:
                    client.send_message(
                        chat_id,
                        "✅ Telegram привязан. Возвращайся в консоль — мастер продолжит настройку.",
                    )
                except TelegramError:
                    log.debug("Не удалось отправить подтверждение привязки", exc_info=True)
                return chat_id
            try:
                client.send_message(
                    chat_id,
                    "Такой код не найден или уже использован. Запусти setup ещё раз — мастер покажет действующий код.",
                )
            except TelegramError:
                pass
        store.load()
        existing_chat_id = store.get_chat_id(code)
        if existing_chat_id:
            return existing_chat_id
    return None


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
    # getpass на Windows читает клавиши через msvcrt и не даёт вставить
    # текст из буфера обмена (Ctrl+V не работает), поэтому используем
    # обычный input — вставка работает, ключ при этом виден на экране.
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


def _has_console() -> bool:
    # pythonw запускается без консоли: stdin/stdout равны None,
    # интерактивный мастер там работать не может.
    return sys.stdin is not None and sys.stdout is not None


def cmd_run(args: argparse.Namespace) -> int:
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler

    require_telegram = not args.no_telegram
    try:
        config = load_config(getattr(args, "config", None))
        validate(config, require_telegram=require_telegram)
    except ConfigError as exc:
        if not require_telegram or not _has_console():
            # Фоновый процесс (pythonw) без конфигурации: мастеру нужно
            # окно консоли, поэтому просто логируем и выходим.
            print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
            return 2
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        print("\nБот ещё не настроен — запускаю мастер настройки.\n")
        setup_args = argparse.Namespace(
            config=getattr(args, "config", None),
            token=None,
            chat_id=None,
            steam_api_key=None,
            steam_id=None,
        )
        return cmd_setup(setup_args)
    setup_logging(config.log_level)

    lock = SingleInstance(PROJECT_ROOT / "run.lock")
    if not lock.acquire():
        log.info("Экземпляр бота уже запущен — выходим")
        return 0
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
            pass
        finally:
            service.stop()
            lock.release()
        return 0

    pairing = PairingStore(config.pairing_path)
    pairing.load()

    def _build_application() -> Application:
        app = Application.builder().token(config.telegram.bot_token).build()
        app.bot_data["service"] = service
        app.bot_data["state"] = state
        app.bot_data["config"] = config
        app.bot_data["pairing"] = pairing
        app.add_handler(CallbackQueryHandler(tg_on_callback))
        app.add_handler(CommandHandler("start", tg_cmd_start))
        app.add_handler(CommandHandler("on", tg_cmd_on))
        app.add_handler(CommandHandler("off", tg_cmd_off))
        return app

    log.info("Бот запущен, ожидаю матчи")
    try:
        # Сетевые сбои Telegram (таймауты, 5xx) не должны убивать фонового
        # бота: пересоздаём Application и продолжаем опрос.
        while True:
            application = _build_application()
            try:
                application.run_polling()
                break  # штатная остановка
            except Exception as exc:
                # Любой сбой опроса (таймауты, сетевые ошибки) — повод
                # перезапустить, а не умирать молча в фоне.
                log.warning("Опрос Telegram прерван (%s) — перезапуск через 5 с", exc)
                time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        lock.release()
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
    print(f"Детекторы: accept_button=Windows gsi={config.gsi.enabled} steam={config.steam.enabled}")
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
