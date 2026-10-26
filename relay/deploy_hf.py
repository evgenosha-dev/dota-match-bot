"""Автоматическое развёртывание релея на Hugging Face Spaces.

Использование:
    python deploy_hf.py --token HF_TOKEN [--space dota-relay] \
        --tg-token TELEGRAM_TOKEN [--owner HF_LOGIN]

Что делает:
1. Определяет владельца Space (whoami).
2. Создаёт Space (Docker SDK), если его ещё нет.
3. Загружает relay_server.py, requirements.txt, Dockerfile (без секретов!).
4. Добавляет Space secrets: TELEGRAM_TOKEN, BASE_URL, ADMIN_KEY, WEBHOOK_SECRET.
5. Ждёт сборки и проверяет /health и webhook.

Требует только стандартной библиотеки (urllib).
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RELAY_DIR = Path(__file__).parent
HF = "https://huggingface.co"


def api(method: str, url: str, token: str, body: dict | bytes | None = None,
        ctype: str = "application/json", ok=(200, 201)) -> tuple[int, bytes]:
    data: bytes | None
    if body is None:
        data = None
    elif isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
    else:
        data = body
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": ctype},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="HF access token (write)")
    ap.add_argument("--space", default="dota-relay", help="имя Space")
    ap.add_argument("--tg-token", required=True, help="токен общего Telegram-бота")
    ap.add_argument("--owner", default=None, help="HF-логин (по умолчанию whoami)")
    args = ap.parse_args()

    owner = args.owner
    if not owner:
        code, body = api("GET", f"{HF}/api/whoami-v2", args.token)
        if code != 200:
            print(f"whoami не удался: HTTP {code} {body[:300]!r}", file=sys.stderr)
            return 1
        owner = json.loads(body).get("name") or ""
    if not owner:
        print("Не удалось определить HF-логин; передай --owner", file=sys.stderr)
        return 1

    repo = f"{owner}/{args.space}"
    base_url = f"https://{owner.lower()}-{args.space.lower()}.hf.space"
    print(f"Space: {repo}")
    print(f"Адрес релея: {base_url}")

    # 1. создать Space, если нет
    code, body = api("GET", f"{HF}/api/spaces/{repo}", args.token)
    if code == 404:
        code, body = api("POST", f"{HF}/api/repos/create", args.token, {
            "type": "space",
            "name": args.space,
            "sdk": "docker",
            "license": "mit",
        })
        if code not in (200, 201):
            print(f"Создание Space не удалось: HTTP {code} {body[:500]!r}", file=sys.stderr)
            return 1
        print("Space создан")
    else:
        print("Space уже существует — обновляю файлы")

    # 2. загрузить файлы (без секретов!)
    for name in ("Dockerfile", "relay_server.py", "requirements.txt"):
        content = (RELAY_DIR / name).read_bytes()
        code, body = api(
            "POST",
            f"{HF}/api/spaces/{repo}/upload/main/{name}",
            args.token,
            body=content,
            ctype="application/octet-stream",
        )
        if code in (400, 404, 405, 422):  # raw-body не поддержан — пробуем multipart
            boundary = "----dota-relay-boundary"
            payload = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
            code, body = api(
                "POST",
                f"{HF}/api/spaces/{repo}/upload/main/{name}",
                args.token,
                body=payload,
                ctype=f"multipart/form-data; boundary={boundary}",
            )
        if code not in (200, 201):
            print(f"Загрузка {name} не удалась: HTTP {code} {body[:500]!r}", file=sys.stderr)
            return 1
        print(f"Загружен {name}")

    # 3. секреты
    secret_values = {
        "TELEGRAM_TOKEN": args.tg_token,
        "BASE_URL": base_url,
        "ADMIN_KEY": secrets.token_urlsafe(16),
        "WEBHOOK_SECRET": secrets.token_urlsafe(16),
    }
    for key, value in secret_values.items():
        code, body = api("POST", f"{HF}/api/spaces/{repo}/secrets", args.token,
                         {"key": key, "value": value})
        if code not in (200, 201):
            print(f"Секрет {key} не сохранён: HTTP {code} {body[:300]!r}", file=sys.stderr)
            return 1
    print("Секреты добавлены (в репозитории их нет)")

    # 4. перезапуск, чтобы подтянуть секреты (если Space уже был запущен)
    api("POST", f"{HF}/api/spaces/{repo}/restart", args.token, {})

    # 5. ждём сборки
    print("Жду сборку Space…", flush=True)
    deadline = time.time() + 15 * 60
    stage = ""
    while time.time() < deadline:
        code, body = api("GET", f"{HF}/api/spaces/{repo}/runtime", args.token)
        if code == 200:
            stage = (json.loads(body).get("stage") or "").upper()
            print(f"  stage: {stage}", flush=True)
            if stage == "RUNNING":
                break
        time.sleep(15)
    else:
        print("Таймаут ожидания сборки (15 мин). Проверь логи на странице Space.", file=sys.stderr)
        return 1

    # 6. проверка /health
    for attempt in range(12):
        code, body = api("GET", f"{base_url}/health", "")
        if code == 200:
            print(f"HEALTH: {body.decode('utf-8', 'replace')}")
            break
        print(f"  /health: HTTP {code}, жду…", flush=True)
        time.sleep(10)
    else:
        print("Релей не отвечает на /health — смотри логи Space.", file=sys.stderr)
        return 1

    print()
    print("ГОТОВО.")
    print(f"Адрес релея для мастера настройки и друзей: {base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
