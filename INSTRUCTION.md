# Инструкция для нового пользователя

Установка из Git на Windows занимает около пяти минут. Редактировать код не нужно.

## Требования

- Windows 10/11;
- [Git](https://git-scm.com/download/win);
- [Python 3.10+](https://www.python.org/downloads/) — при установке оставь галочку **Add Python to PATH**.

## 1) Установить и настроить

Открой PowerShell и выполни:

```powershell
git clone https://github.com/evgenosha-dev/dota-match-bot.git
cd dota-match-bot
.\install.bat
```

Установщик создаст `.venv`, установит зависимости и запустит мастер настройки.

## 2) Привязать Telegram

1. Мастер покажет **одноразовый шестизначный код**.
2. Открой [@BflyDotaBot](https://t.me/BflyDotaBot).
3. Отправь команду `/start КОД`, например `/start 123456`.
4. Возвращаться в консоль не нужно: мастер сам дождётся подтверждения и сохранит твой `chat_id`.

Если код не сработал, просто запусти настройку ещё раз — будет создан новый код.

## 3) Настроить Dota 2

Мастер предложит три необязательных шага:

1. **Steam** — ключ [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey)
   и ссылку на профиль. Рекомендуется: так появятся уведомления о результатах матчей.
2. **GSI** — если Dota 2 установлена, мастер запишет конфиг сам.
   После этого **перезапусти Dota 2**.
3. **Автопринятие** — кнопка «Принять матч» прямо в уведомлении Telegram.

Любой шаг можно пропустить, нажав `Enter`.

## 4) Запустить бота

```powershell
start_dota_match_bot.cmd
```

Или напрямую:

```powershell
.\.venv\Scripts\python.exe bot.py run
```

Окно можно свернуть. Как только Dota 2 найдёт матч, придёт уведомление. По завершении матча бот пришлёт результат и составы команд.

## 5) Изменить настройки позже

```powershell
.\.venv\Scripts\python.exe bot.py setup
```

## Без установщика

Если `install.bat` запускать не хочешь:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py setup
```

## Автозапуск при включении ПК

Нажми `Win+R` → введи `shell:startup` → создай ярлык:

- Программа: `C:\путь\к\dota-match-bot\.venv\Scripts\pythonw.exe`
- Аргументы: `bot.py run`
- Рабочая папка: `C:\путь\к\dota-match-bot`

## Советы

- Включи в Dota 2: **Настройки → Параметры → Доп. параметры → Выводить Dota 2 на передний план при поиске матча**.
- `config.json`, `state.json` и `pairing.json` личные и не попадают в Git.
- Для проверки уведомлений выполни:

```powershell
.\.venv\Scripts\python.exe bot.py test
```

