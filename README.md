# TG Cognitive Distortion Bot

Telegram-бот для качественного разбора ситуаций и решений.

## Что умеет

- `/razbor` или кнопка `🧠 Разбор ситуации`
- `/plan` или кнопка `🗺️ План действий`
- `/audit` или кнопка `🧪 Проверка решения`
- `/stats` или кнопка `📊 Мой прогресс`
- `/reality` или кнопка `🧭 Reality Check`

## Что улучшено

- Стиль ответов: структурированные карточки с понятными секциями.
- Голосовые: поддержка voice/audio с более надежной обработкой и диагностикой ошибок.
- Персональная память: учет истории пользователя, частых искажений и повторяющихся тем.
- Четкое разделение глубины:
  - Разбор — локальный кейс (24–72ч)
  - Reality Check — системный аудит траектории (30–90д)
- Авто-разбор: можно отправить текст/голос без команды.
- Скрытая админ-аналитика расходов: учет LLM/STT затрат + авто-уведомления.
- Скрытая админ-аналитика пользователей: кто активен, кто блокнул, что нажимают.
- Ручная и ежедневная авто-рассылка.

## Переменные окружения

```env
BOT_TOKEN=твой_токен_из_BotFather
DB_PATH=data/bot.db
OPENAI_API_KEY=твой_openai_api_key
STT_MODEL=gpt-4o-mini-transcribe
STT_LANGUAGE=ru
ANALYSIS_MODEL=gpt-4.1-mini
ADMIN_TG_ID=123456789
ADMIN_NOTIFY_HOURS=09,21
ADMIN_TIMEZONE=Europe/Prague
COST_ALERT_SPIKE_PCT=50
COST_ALERT_MIN_BASE_USD=0.25
COST_SPIKE_WINDOW_HOURS=6
BROADCAST_ENABLED=0
BROADCAST_HOURS=10
BROADCAST_TEXT=
BROADCAST_INCLUDE_BLOCKED=0
BROADCAST_MAX_RETRIES=2
LLM_INPUT_COST_PER_1M=0.4
LLM_OUTPUT_COST_PER_1M=1.6
STT_COST_PER_MIN=0.006
```

## Скрытый админ-контроль расходов

- Пользователи это не видят.
- Бот шлет тебе в личку (по `ADMIN_TG_ID`) сводку 2 раза в день (`ADMIN_NOTIFY_HOURS`).
- Если расход за последние N часов вырос на `COST_ALERT_SPIKE_PCT` относительно предыдущего окна, приходит отдельный alert.
- Ручные команды для админа:
  - `/helpa` — список всех админ-команд
  - `/admin_panel` — панель управления (выбор цифрой)
  - `/admin_cost` — расходы
  - `/admin_status` — статус + KPI
  - `/admin_users` — кто активен/заблокирован
  - `/admin_events` — кто что нажимает
  - `/admin_runs` — последние запуски рассылки
  - `/admin_broadcast` — рассылка вручную (текст/фото/фото+подпись/голос, с предпросмотром и подтверждением)
  - `/admin_cleanup` — очистка старых логов
  - `/admin_list` — список админов
  - `/admin_grant` — выдать админку (root)
  - `/admin_revoke` — снять админку (root)

## Ежедневная рассылка

- `BROADCAST_ENABLED=1` — включить.
- `BROADCAST_HOURS=10` или `09,21` — часы по `ADMIN_TIMEZONE`.
- `BROADCAST_TEXT=...` — текст рассылки.
- `BROADCAST_INCLUDE_BLOCKED=0` — обычно `0`, чтобы не слать тем, кто уже заблокировал бота.
- `BROADCAST_MAX_RETRIES=2` — сколько ретраев на временных ошибках доставки.

## Локальный запуск

```bash
cd "/Users/ivan/Documents/tg-cognitive-bot"
source .venv/bin/activate
python -m bot.main
```

## Авто-деплой на VPS (GitHub Actions)

После добавления workflow `.github/workflows/deploy-vps.yml` деплой идет автоматически при пуше в `main`.

Нужно один раз добавить Secrets в GitHub (Repo -> Settings -> Secrets and variables -> Actions):

- `VPS_HOST` — IP сервера (например `212.43.150.197`)
- `VPS_USER` — `root`
- `VPS_SSH_KEY` — приватный SSH-ключ для входа на VPS

Проверка деплоя:

- вкладка `Actions` в GitHub -> workflow `Deploy To VPS`
- на сервере: `cd /root/tg-cognitive-bot && docker compose ps`
