# TG Cognitive Distortion Bot (MVP)

Telegram-бот для разбора когнитивных искажений. Работает с текстом и голосовыми сообщениями.

## Что умеет

- `/razbor` — полный разбор ситуации (3-5 минут)
- `/sos` — короткий протокол деэскалации (90 секунд)
- `/checkin` — ежедневный чек-ин (настроение/стресс/энергия)
- `/stats` — статистика по сессиям
- `/cancel` — отмена текущего сценария

## Особенности

- Объективный режим по умолчанию
- Формат: `факты -> интерпретации -> искажения -> прямой вывод -> шаг`
- Поддержка voice/audio через OpenAI STT

## Переменные окружения

```env
BOT_TOKEN=твой_токен_из_BotFather
DB_PATH=data/bot.db
OPENAI_API_KEY=твой_openai_api_key
STT_MODEL=gpt-4o-mini-transcribe
STT_LANGUAGE=ru
```

## Локальный запуск

```bash
cd "/Users/ivan/Documents/temp bot/tg-cognitive-bot"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m bot.main
```

## Деплой на Render (24/7)

В проект уже добавлен `render.yaml` (Blueprint), поэтому деплой максимально простой.

1. Создай GitHub-репозиторий и залей туда папку проекта.
2. В Render: `New +` -> `Blueprint`.
3. Выбери этот GitHub-репозиторий.
4. Render прочитает `render.yaml` и создаст Worker.
5. В `Environment` у сервиса заполни:
   - `BOT_TOKEN`
   - `OPENAI_API_KEY`
6. Нажми `Deploy`.

После старта в логах должна быть нормальная поллинг-работа без ошибок.

## Важное

- `DB_PATH` в Render уже направлен в `/var/data/bot.db` (persist disk), данные не потеряются при рестарте.
- Если `OPENAI_API_KEY` не задан, бот продолжит работать, но попросит отправлять текст вместо голосовых.

## Структура

- `bot/main.py` — handlers, FSM, команды, прием голосовых
- `bot/cognitive.py` — логика разбора
- `bot/stt.py` — транскрибация voice/audio
- `bot/db.py` — SQLite
- `render.yaml` — деплой-конфиг Render
