from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv

from bot.coach import ProductCoach
from bot.cognitive import analyze_case
from bot.db import Storage
from bot.product import build_decision_audit, build_plan
from bot.states import (
    AdminAccessStates,
    AdminBroadcastStates,
    AdminPanelStates,
    AuditStates,
    CrisisStates,
    PlanStates,
    RealityStates,
    RazborStates,
)
from bot.stt import VoiceTranscriber


router = Router()
STORAGE: Storage
TRANSCRIBER: VoiceTranscriber | None = None
COACH: ProductCoach
ADMIN_TG_ID: int | None = None
ADMIN_NOTIFY_HOURS: set[int] = {9, 21}
ADMIN_TZ = timezone.utc
COST_ALERT_SPIKE_PCT = 50.0
COST_ALERT_MIN_BASE_USD = 0.25
COST_SPIKE_WINDOW_HOURS = 6
BROADCAST_ENABLED = False
BROADCAST_HOURS: set[int] = {10}
BROADCAST_TEXT = ""
BROADCAST_INCLUDE_BLOCKED = False
BROADCAST_MAX_RETRIES = 2

BTN_RAZBOR = "🧠 Разбор ситуации"
BTN_PLAN = "🗺️ План действий"
BTN_AUDIT = "🧪 Проверка решения"
BTN_REALITY = "🧭 Reality Check"
BTN_STATS = "📊 Мой прогресс"
BTN_ADMIN_PANEL = "🛠 Админ-панель"
BTN_ADMIN_BROADCAST = "📣 Рассылка"
BTN_ADMIN_USERS = "👥 Пользователи"
BTN_ADMIN_EVENTS = "🧾 События"
BTN_ADMIN_COST = "💸 Расходы"
BTN_ADMIN_STATUS = "📈 Статус/KPI"
BTN_ADMIN_RUNS = "📦 История рассылок"
BTN_ADMIN_ADMINS = "🔐 Админы"
BTN_ADMIN_HELP = "❓ Help админки"
BTN_BACK_MAIN = "⬅️ В меню"
BTN_BROADCAST_SEND = "✅ Отправить"
BTN_BROADCAST_EDIT = "✏️ Изменить контент"
BTN_BROADCAST_CANCEL = "🛑 Отмена"

PANIC_MARKERS = (
    "паник", "задыха", "сердце", "сердцеби", "тряс", "не могу дышать", "приступ", "пульс", "сильная тревога",
)

GENERIC_ACTION_MARKERS = (
    "4-7-8",
    "дыхатель",
    "подыш",
    "медитац",
    "выпей воды",
    "прогуляйся",
    "запиши чувства",
    "проверь физические причины",
)

SMALLTALK_MARKERS = (
    "привет", "хай", "hello", "hi", "добрый день", "доброе утро", "добрый вечер", "йо",
)

LOW_SIGNAL_MARKERS = (
    "норм", "ок", "ясно", "пон", "хз", "лол", "test", "тест", "проверка",
)

PLAN_ROUTE_MARKERS = (
    "план", "цель", "дедлайн", "срок", "по шагам", "дорожн", "как успеть", "расписание",
)

AUDIT_ROUTE_MARKERS = (
    "стоит ли", "делать или нет", "выбор", "риск", "go/no-go", "go no-go", "решение", "правильно ли",
)

HARM_MARKERS = (
    "изнасил", "принужд", "заставлю", "насильно", "отомщу", "убью", "покалеч", "шантаж",
)

SUICIDE_RISK_MARKERS = (
    "спрыгну", "спрыгнуть", "прыгну", "прыгнуть", "с крыши", "суицид", "поконч", "уйти из жизни",
    "не хочу жить", "не вижу смысла жить", "умереть", "убить себя",
)

MENU_ALIAS_MAP: dict[str, str] = {
    BTN_RAZBOR.lower(): "razbor",
    "разбор ситуации": "razbor",
    "разбор": "razbor",
    "/razbor": "razbor",
    BTN_PLAN.lower(): "plan",
    "план действий": "plan",
    "план": "plan",
    "/plan": "plan",
    BTN_AUDIT.lower(): "audit",
    "аудит решения": "audit",
    "проверка решения": "audit",
    "аудит": "audit",
    "/audit": "audit",
    BTN_REALITY.lower(): "reality",
    "reality check": "reality",
    "reality": "reality",
    "реалити чек": "reality",
    "проверка реальности": "reality",
    "/reality": "reality",
    "/reality_check": "reality",
    BTN_STATS.lower(): "stats",
    "мой прогресс": "stats",
    "прогресс": "stats",
    "/stats": "stats",
    BTN_ADMIN_PANEL.lower(): "admin_panel",
    "админ панель": "admin_panel",
    "/admin": "admin_panel",
    "/admin_panel": "admin_panel",
    "/helpa": "helpa",
}

BROADCAST_SEGMENTS: dict[str, tuple[str, str]] = {
    "1": ("all", "Все"),
    "2": ("active_24h", "Активные 24ч"),
    "3": ("active_7d", "Активные 7д"),
    "4": ("power_7d", "Активные 5+ сессий за 7д"),
}


def main_menu(include_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_RAZBOR), KeyboardButton(text=BTN_PLAN)],
        [KeyboardButton(text=BTN_AUDIT), KeyboardButton(text=BTN_REALITY)],
        [KeyboardButton(text=BTN_STATS)],
    ]
    if include_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN_PANEL)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Опиши ситуацию или отправь голосовое.",
    )


def admin_panel_menu(include_root_actions: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_ADMIN_BROADCAST), KeyboardButton(text=BTN_ADMIN_RUNS)],
        [KeyboardButton(text=BTN_ADMIN_USERS), KeyboardButton(text=BTN_ADMIN_EVENTS)],
        [KeyboardButton(text=BTN_ADMIN_COST), KeyboardButton(text=BTN_ADMIN_STATUS)],
        [KeyboardButton(text=BTN_ADMIN_ADMINS), KeyboardButton(text=BTN_ADMIN_HELP)],
    ]
    if include_root_actions:
        rows.append([KeyboardButton(text="➕ Выдать админку"), KeyboardButton(text="➖ Снять админку")])
        rows.append([KeyboardButton(text="🧹 Очистка логов")])
    rows.append([KeyboardButton(text=BTN_BACK_MAIN)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выбери действие в админке.",
    )


def broadcast_segment_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1) 👥 Все"), KeyboardButton(text="2) ⚡ Активные 24ч")],
            [KeyboardButton(text="3) 🔥 Активные 7д"), KeyboardButton(text="4) 💎 5+ сессий за 7д")],
            [KeyboardButton(text=BTN_BROADCAST_CANCEL)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери сегмент рассылки.",
    )


def broadcast_confirm_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BROADCAST_SEND), KeyboardButton(text=BTN_BROADCAST_EDIT)],
            [KeyboardButton(text=BTN_BROADCAST_CANCEL)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Подтверди рассылку.",
    )


def section(title: str, body: str) -> str:
    return f"{title}\n{body}".strip()


def _normalize_menu_text(text: str) -> str:
    cleaned = (text or "").lower().strip()
    cleaned = re.sub(r"[^\wа-яё/\s]+", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _resolve_menu_action(text: str) -> str | None:
    normalized = _normalize_menu_text(text)
    return MENU_ALIAS_MAP.get(normalized)


def _to_int(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip())
    except (TypeError, ValueError):
        return default


def _to_float(value: str | None, default: float) -> float:
    try:
        return float((value or "").strip())
    except (TypeError, ValueError):
        return default


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return default


def _parse_hours(value: str | None, default: set[int]) -> set[int]:
    if not value:
        return default
    result: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
        except ValueError:
            continue
        if 0 <= hour <= 23:
            result.add(hour)
    return result or default


def _pop_usage_meta(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    meta = payload.pop("__meta", None)
    return meta if isinstance(meta, dict) else None


def _record_usage_meta(tg_id: int, meta: dict[str, Any] | None) -> None:
    if not meta:
        return
    STORAGE.record_usage_event(
        tg_id=tg_id,
        source=str(meta.get("source", "unknown")),
        model=str(meta.get("model", "unknown")),
        cost_usd=float(meta.get("cost_usd", 0) or 0),
        input_tokens=int(meta.get("input_tokens", 0) or 0),
        output_tokens=int(meta.get("output_tokens", 0) or 0),
        audio_seconds=float(meta.get("audio_seconds", 0) or 0),
    )


def _is_admin(tg_id: int | None) -> bool:
    if tg_id is None:
        return False
    if ADMIN_TG_ID is not None and tg_id == ADMIN_TG_ID:
        return True
    try:
        return STORAGE.is_admin_tg_id(tg_id)
    except Exception:
        return False


def _is_root_admin(tg_id: int | None) -> bool:
    return bool(ADMIN_TG_ID is not None and tg_id is not None and tg_id == ADMIN_TG_ID)


def _broadcast_segment_label(segment: str) -> str:
    labels = {
        "all": "Все",
        "active_24h": "Активные 24ч",
        "active_7d": "Активные 7д",
        "power_7d": "Активные 5+ сессий за 7д",
    }
    return labels.get(segment, segment)


def _admin_help_text() -> str:
    return "\n\n".join(
        [
            section(
                "🛠 Админ-команды",
                (
                    "• /admin (или /admin_panel) — панель действий\n"
                    "• /admin_status — статус мониторинга и KPI\n"
                    "• /admin_users — кто активен/кто блокнул\n"
                    "• /admin_events — клики и действия\n"
                    "• /admin_cost — расходы LLM/STT\n"
                    "• /admin_broadcast — рассылка (текст/фото/голос) с предпросмотром\n"
                    "• /admin_runs — последние запуски рассылки\n"
                    "• /admin_cleanup — очистка старых логов"
                ),
            ),
            section(
                "📣 Как делать рассылку",
                (
                    "1) /admin_broadcast\n"
                    "2) Выбери сегмент аудитории\n"
                    "3) Пришли контент (текст, фото, фото+подпись, голос, аудио)\n"
                    "4) Проверь предпросмотр и нажми «✅ Отправить»"
                ),
            ),
            section(
                "🔐 Доступы",
                (
                    "• /admin_grant — выдать админку (root)\n"
                    "• /admin_revoke — снять админку (root)\n"
                    "• /admin_list — список админов"
                ),
            ),
        ]
    )


def _admin_panel_text() -> str:
    return "\n\n".join(
        [
            "🛠 Админ-панель",
            "Здесь можно управлять рассылками и смотреть метрики.\n"
            "Используй кнопки ниже: так быстрее и без ошибок в командах.",
        ]
    )


def _extract_choice_digit(text: str) -> str | None:
    match = re.match(r"^\s*(\d+)", text or "")
    if match:
        return match.group(1)
    return None


def _broadcast_segment_overview() -> str:
    lines: list[str] = []
    for idx, (segment, label) in (
        ("1", ("all", "Все")),
        ("2", ("active_24h", "Активные 24ч")),
        ("3", ("active_7d", "Активные 7д")),
        ("4", ("power_7d", "Активные 5+ сессий за 7д")),
    ):
        count = len(STORAGE.get_broadcast_targets(include_blocked=False, segment=segment))
        lines.append(f"{idx}) {label}: {count}")
    return "\n".join(lines)


def _touch_user(tg_id: int | None, event_type: str = "", payload: str = "") -> None:
    if tg_id is None:
        return
    STORAGE.mark_user_active(tg_id)
    if event_type:
        STORAGE.log_user_event(tg_id=tg_id, event_type=event_type, payload=payload)


def _extract_broadcast_payload(message: Message) -> dict[str, str] | None:
    if message.text and message.text.strip():
        return {"kind": "text", "text": message.text.strip()}
    if message.photo:
        photo_id = message.photo[-1].file_id
        caption = (message.caption or "").strip()
        return {"kind": "photo", "file_id": photo_id, "caption": caption}
    if message.voice:
        caption = (message.caption or "").strip()
        return {"kind": "voice", "file_id": message.voice.file_id, "caption": caption}
    if message.audio:
        caption = (message.caption or "").strip()
        return {"kind": "audio", "file_id": message.audio.file_id, "caption": caption}
    return None


def _broadcast_payload_preview(payload: dict[str, str]) -> str:
    kind = payload.get("kind", "text")
    if kind == "text":
        return (payload.get("text", "") or "").strip()[:500]
    if kind == "photo":
        caption = (payload.get("caption", "") or "").strip()
        if caption:
            return f"🖼 Фото + подпись:\n{caption[:450]}"
        return "🖼 Фото без подписи"
    if kind == "voice":
        caption = (payload.get("caption", "") or "").strip()
        if caption:
            return f"🎙 Голосовое + подпись:\n{caption[:450]}"
        return "🎙 Голосовое без подписи"
    if kind == "audio":
        caption = (payload.get("caption", "") or "").strip()
        if caption:
            return f"🎵 Аудио + подпись:\n{caption[:450]}"
        return "🎵 Аудио без подписи"
    return "Неподдерживаемый формат"


def _broadcast_payload_log_preview(payload: dict[str, str]) -> str:
    kind = payload.get("kind", "text")
    if kind == "text":
        return (payload.get("text", "") or "").strip()[:280]
    if kind == "photo":
        caption = (payload.get("caption", "") or "").strip()
        return f"photo:{caption[:260]}" if caption else "photo"
    if kind == "voice":
        caption = (payload.get("caption", "") or "").strip()
        return f"voice:{caption[:260]}" if caption else "voice"
    if kind == "audio":
        caption = (payload.get("caption", "") or "").strip()
        return f"audio:{caption[:260]}" if caption else "audio"
    return kind[:280]


async def _safe_send_user_payload(
    bot: Bot,
    tg_id: int,
    payload: dict[str, str],
    max_retries: int = 0,
) -> tuple[bool, bool, str, int]:
    retries = max(0, int(max_retries))
    attempts = 0
    last_error = ""
    kind = payload.get("kind", "text")
    for attempt in range(1, retries + 2):
        attempts = attempt
        try:
            if kind == "text":
                await bot.send_message(tg_id, payload.get("text", ""))
            elif kind == "photo":
                await bot.send_photo(
                    tg_id,
                    photo=payload.get("file_id", ""),
                    caption=payload.get("caption") or None,
                )
            elif kind == "voice":
                await bot.send_voice(
                    tg_id,
                    voice=payload.get("file_id", ""),
                    caption=payload.get("caption") or None,
                )
            elif kind == "audio":
                await bot.send_audio(
                    tg_id,
                    audio=payload.get("file_id", ""),
                    caption=payload.get("caption") or None,
                )
            else:
                raise ValueError(f"unsupported broadcast payload kind: {kind}")
            STORAGE.mark_delivery_ok(tg_id)
            return True, False, "", attempts
        except TelegramForbiddenError as exc:
            err = str(exc)[:240]
            STORAGE.mark_delivery_failed(tg_id, err, blocked=True)
            return False, True, err, attempts
        except TelegramBadRequest as exc:
            err = str(exc)[:240]
            blocked = "chat not found" in err.lower() or "bot was blocked" in err.lower()
            STORAGE.mark_delivery_failed(tg_id, err, blocked=blocked)
            return False, blocked, err, attempts
        except Exception as exc:
            last_error = str(exc)[:240]
            if attempt <= retries:
                await asyncio.sleep(0.25 * attempt)
                continue
            STORAGE.mark_delivery_failed(tg_id, last_error, blocked=False)
            return False, False, last_error, attempts
    STORAGE.mark_delivery_failed(tg_id, last_error, blocked=False)
    return False, False, last_error, attempts


async def _run_broadcast(
    bot: Bot,
    payload: dict[str, str],
    include_blocked: bool = False,
    segment: str = "all",
    created_by_tg_id: int = 0,
    max_retries: int = 0,
) -> dict[str, int | str]:
    targets = STORAGE.get_broadcast_targets(include_blocked=include_blocked, segment=segment)
    run_id = STORAGE.create_broadcast_run(
        created_by_tg_id=created_by_tg_id,
        segment=segment,
        include_blocked=include_blocked,
        text_preview=_broadcast_payload_log_preview(payload),
        total_targets=len(targets),
    )
    sent = 0
    blocked = 0
    failed = 0
    retry_count = 0
    errors: Counter[str] = Counter()
    for tg_id in targets:
        ok, is_blocked, err_text, attempts = await _safe_send_user_payload(
            bot,
            tg_id,
            payload,
            max_retries=max_retries,
        )
        retry_count += max(0, attempts - 1)
        if ok:
            sent += 1
            STORAGE.log_broadcast_attempt(run_id, tg_id, attempts, "sent", "")
        elif is_blocked:
            blocked += 1
            STORAGE.log_broadcast_attempt(run_id, tg_id, attempts, "blocked", err_text)
            if err_text:
                errors.update([err_text[:80]])
        else:
            failed += 1
            STORAGE.log_broadcast_attempt(run_id, tg_id, attempts, "failed", err_text)
            if err_text:
                errors.update([err_text[:80]])
        await asyncio.sleep(0.04)
    top_error = ""
    if errors:
        err, cnt = errors.most_common(1)[0]
        top_error = f"{err} ({cnt})"
    STORAGE.finish_broadcast_run(
        run_id=run_id,
        sent_count=sent,
        blocked_count=blocked,
        failed_count=failed,
        retry_count=retry_count,
        error_top=top_error,
    )
    return {
        "run_id": run_id,
        "targets": len(targets),
        "sent": sent,
        "blocked": blocked,
        "failed": failed,
        "retries": retry_count,
        "segment": segment,
        "error_top": top_error,
    }


async def extract_user_text(message: Message) -> str | None:
    if message.from_user:
        if message.text and message.text.strip():
            _touch_user(message.from_user.id, "incoming:text", f"len={len(message.text.strip())}")
        elif message.voice:
            _touch_user(message.from_user.id, "incoming:voice", f"duration={message.voice.duration or 0}")
        elif message.audio:
            _touch_user(message.from_user.id, "incoming:audio", f"duration={message.audio.duration or 0}")

    if message.text and message.text.strip():
        return message.text.strip()

    if message.voice or message.audio:
        if TRANSCRIBER is None:
            await message.answer(
                "🎙️ Голосовые сейчас выключены.\n"
                "Отправь текстом или добавь `OPENAI_API_KEY` в `.env`."
            )
            return None

        await message.answer("🎙️ Расшифровываю голосовое...")
        try:
            text, meta = await TRANSCRIBER.transcribe_message(message)
            _record_usage_meta(message.from_user.id, meta)
        except Exception as exc:
            error_text = str(exc).replace("\n", " ")[:160]
            await message.answer(
                "Не распознал голосовое.\n"
                f"Тех.деталь: `{error_text}`\n"
                "Запиши короче/четче или отправь текст."
            )
            return None

        if not text:
            await message.answer("Пустая расшифровка. Повтори запись или отправь текст.")
            return None

        await message.answer(f"📝 Распознанный текст:\n{text}")
        return text

    await message.answer("Нужен текст или голосовое.")
    return None


def format_list(items: list[str]) -> str:
    if not items:
        return "• нет данных"
    return "\n".join(f"• {item}" for item in items)


def top_items(items: list[str], n: int = 3) -> list[str]:
    cleaned = [str(i).strip() for i in items if str(i).strip()]
    return cleaned[:n]


def _sanitize_style(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"\bПользователь\b", "Ты", cleaned)
    cleaned = re.sub(r"\bпользователь\b", "ты", cleaned)
    cleaned = re.sub(r"\bЧеловек\b", "Ты", cleaned)
    cleaned = re.sub(r"\bчеловек\b", "ты", cleaned)
    return cleaned


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _is_panic_context(text: str) -> bool:
    return _contains_any(text, PANIC_MARKERS)


def _is_generic_action(action: str) -> bool:
    return _contains_any(action, GENERIC_ACTION_MARKERS)


def _contextual_action_candidates(user_text: str) -> list[str]:
    lowered = user_text.lower()
    actions: list[str] = []

    if any(k in lowered for k in ("сообщение", "переписк", "конфликт", "начальник", "коллег", "клиент", "договор")):
        actions.append("Сформулируй цель разговора в 1 фразу и отправь короткое сообщение с конкретным следующим шагом.")
        actions.append("Отдели 2 факта от 2 интерпретаций и опирайся в ответе только на факты.")

    if any(k in lowered for k in ("срок", "дедлайн", "задач", "проект", "не успева")):
        actions.append("Разбей задачу на минимальный блок на 25 минут и закрой его без отвлечений.")
        actions.append("Зафиксируй дедлайн и критерий готовности, чтобы не крутить мысль по кругу.")

    if any(k in lowered for k in ("деньг", "оплат", "долг", "бюджет", "зарплат")):
        actions.append("Зафиксируй цифры: сумма, срок, ответственный, и отправь подтверждение второй стороне.")
        actions.append("Выбери один финансовый шаг на сегодня: запрос, перенос срока или частичная оплата.")

    if any(k in lowered for k in ("отношен", "парень", "девушк", "муж", "жена", "расстав")):
        actions.append("Назови свою границу в 1 фразе и предложи формат разговора без взаимных обвинений.")
        actions.append("Определи, что для тебя неприемлемо сейчас, и сообщи это прямо и спокойно.")

    if not actions:
        actions.append("Выдели 1 проверяемый факт, 1 риск и 1 действие, которое можно сделать сегодня.")
        actions.append("Сформулируй следующий шаг так, чтобы его можно было завершить за 30 минут.")

    unique: list[str] = []
    seen: set[str] = set()
    for action in actions:
        key = action.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)
    return unique


def _make_actions_contextual(user_text: str, actions: list[str], limit: int) -> list[str]:
    proposed = top_items(actions, 6)
    panic_mode = _is_panic_context(user_text)

    prepared: list[str] = []
    seen: set[str] = set()
    for action in proposed:
        action_clean = _sanitize_style(action)
        if not panic_mode and _is_generic_action(action_clean):
            continue
        key = action_clean.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        prepared.append(action_clean)

    if len(prepared) < limit:
        for extra in _contextual_action_candidates(user_text):
            if len(prepared) >= limit:
                break
            key = extra.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            prepared.append(extra)

    return prepared[:limit]


def _normalize_text_tokens(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9-]+", text.lower(), flags=re.IGNORECASE)


def _is_smalltalk_or_low_signal(text: str) -> bool:
    lowered = text.lower().strip()
    tokens = _normalize_text_tokens(text)
    if not tokens:
        return True
    if len(tokens) <= 2 and any(marker in lowered for marker in SMALLTALK_MARKERS):
        return True
    if len(tokens) <= 4 and any(marker in lowered for marker in LOW_SIGNAL_MARKERS):
        return True
    return False


def _looks_like_followup(text: str) -> bool:
    lowered = text.lower()
    tokens = _normalize_text_tokens(text)
    if len(tokens) < 3:
        return False
    markers = (
        "почему", "с какого", "в смысле", "объясни", "не понял", "это не так",
        "а если", "тогда", "то есть", "поясни", "как это",
    )
    return any(m in lowered for m in markers)


def _wants_history_reference(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "на основе прошлого", "на основе прошлых", "как выше", "как раньше",
        "из прошлого", "по прошлому", "учти прошл", "контекст прошлого",
    )
    return any(m in lowered for m in markers)


def _auto_route_mode_for_free_text(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in AUDIT_ROUTE_MARKERS):
        return "audit"
    if any(marker in lowered for marker in PLAN_ROUTE_MARKERS):
        return "plan"
    return "razbor"


def _looks_like_reality_input(text: str) -> bool:
    lowered = text.lower()
    signals = 0
    if any(k in lowered for k in ("лет", "возраст")):
        signals += 1
    if any(k in lowered for k in ("доход", "зарабат", "зарплат")):
        signals += 1
    if any(k in lowered for k in ("расход", "трачу", "обязател")):
        signals += 1
    if any(k in lowered for k in ("долг", "кредит")):
        signals += 1
    if any(k in lowered for k in ("цель", "12 месяц", "год", "через год")):
        signals += 1
    if any(k in lowered for k in ("работ", "фриланс", "бизнес", "без работы")):
        signals += 1
    if any(k in lowered for k in ("мешает", "проблем", "что происходит")):
        signals += 1

    words = _normalize_text_tokens(text)
    return signals >= 4 and len(words) >= 45


def _pick_variant(tg_id: int, key: str, variants: list[str]) -> str:
    if not variants:
        return ""
    salt = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    idx = abs(hash(f"{tg_id}:{key}:{salt}")) % len(variants)
    return variants[idx]


def _low_signal_reply(tg_id: int, mode: str | None = None) -> str:
    if mode == "plan":
        return _pick_variant(
            tg_id,
            "low_plan",
            [
                "Для плана дай 3 вещи: цель, ограничение (время/ресурс), срок.",
                "Чтобы собрать план, напиши цель, рамки по времени/ресурсам и дедлайн.",
                "Мало фактов для плана. Нужны цель, ограничение и срок.",
            ],
        )
    if mode == "audit":
        return _pick_variant(
            tg_id,
            "low_audit",
            [
                "Для аудита дай: ситуацию и действие, которое хочешь сделать.",
                "Чтобы проверить решение, пришли ситуацию и выбранный шаг.",
                "Мало фактов для аудита: опиши ситуацию и планируемое действие.",
            ],
        )
    return _pick_variant(
        tg_id,
        "low_general",
        [
            "Пока это слишком общий запрос.\nДай коротко: что произошло, что уже сделал, какой результат тебе нужен.",
            "Чтобы помочь по делу, нужны 3 пункта: факт, твои действия, желаемый итог.",
            "Пока мало конкретики. Напиши: что случилось, что уже пробовал, чего хочешь добиться.",
        ],
    )


def _is_harm_intent(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in HARM_MARKERS)


def _harm_intent_reply(tg_id: int) -> str:
    return _pick_variant(
        tg_id,
        "harm_reply",
        [
            "Я не помогаю с причинением вреда или принуждением.\nЕсли хочешь, разберу ситуацию так, чтобы снизить ущерб и выбрать безопасное решение.",
            "С вредом и принуждением не помогаю.\nМогу помочь найти безопасный и рабочий выход.",
            "С этим не помогу.\nЕсли цель — решить проблему без вреда, разберем по шагам.",
        ],
    )


def _is_suicide_risk(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SUICIDE_RISK_MARKERS)


def _crisis_reply() -> str:
    return "\n\n".join(
        [
            "Я рядом. Сейчас главное — безопасность на ближайшие 10 минут.",
            "Сделай это прямо сейчас:\n"
            "• Отойди от крыши/окна/опасного места.\n"
            "• Напиши или позвони одному живому человеку рядом: «Мне сейчас очень плохо, побудь со мной».\n"
            "• Позвони 112 (или местный номер) и скажи прямо, что есть риск причинить себе вред.",
            "Если готов, отвечай 3 пункта: что накрыло, кто рядом, что делаем в ближайшие 10 минут.",
        ]
    )


async def _handle_crisis_message(message: Message, state: FSMContext, user_text: str) -> bool:
    if not _is_suicide_risk(user_text):
        return False

    current = await state.get_state()
    user_context = build_user_context_text(message.from_user.id)
    llm = await COACH.crisis_support(user_text=user_text, user_context=user_context) if COACH.enabled else None
    if llm:
        _record_usage_meta(message.from_user.id, _pop_usage_meta(llm))

    STORAGE.save_session(
        tg_id=message.from_user.id,
        mode="crisis",
        situation=user_text,
        thought="Кризисный сигнал",
        distortions=["Туннельное мышление", "Катастрофизация"],
        reframe="Сначала безопасность, потом разбор.",
        action="Отойти от опасного места, связаться с человеком, позвонить 112.",
        emotion_before=None,
        emotion_after=None,
    )

    # First crisis response: explicit safety protocol.
    if current != CrisisStates.followup.state:
        await state.set_state(CrisisStates.followup)
        if llm:
            steps = llm.get("next_10_min") if isinstance(llm.get("next_10_min"), list) else []
            first_step = top_items(_make_actions_contextual(user_text, steps, 3), 1)
            question = _sanitize_style(str(llm.get("followup_question", ""))).strip() or "Кто сейчас рядом с тобой физически?"
            await state.update_data(
                last_crisis_step=first_step[0] if first_step else "",
                last_crisis_question=question,
            )
            text = "\n\n".join(
                [
                    _sanitize_style(str(llm.get("what_i_heard", "Я с тобой. Сейчас приоритет — безопасность."))),
                    "План на 10 минут:\n" + format_list(top_items(_make_actions_contextual(user_text, steps, 3), 3)),
                    f"Кому написать: {_sanitize_style(str(llm.get('one_person_to_contact', 'человеку рядом, которому доверяешь')))}",
                    f"Текст: {_sanitize_style(str(llm.get('one_message_template', 'Мне сейчас очень плохо, побудь со мной.')))}",
                    "Если риск высокий прямо сейчас — звони 112.",
                ]
            )
            await message.answer(text)
        else:
            await message.answer(_crisis_reply())
        return True

    # Follow-up crisis response: no static repeat, only next concrete step.
    if llm:
        step = _sanitize_style(str(llm.get("one_small_step_now", ""))).strip() or "Сядь в безопасное место и напиши одному человеку: «Мне нужна помощь сейчас»."
        question = _sanitize_style(str(llm.get("followup_question", ""))).strip() or "Кто сейчас рядом с тобой физически?"
        data = await state.get_data()
        prev_step = str(data.get("last_crisis_step", "")).strip()
        prev_q = str(data.get("last_crisis_question", "")).strip()
        if prev_step and step == prev_step:
            step = _pick_variant(
                message.from_user.id,
                "crisis_alt_step",
                [
                    "Убери от себя все опасные предметы и напиши: «Мне нужна помощь прямо сейчас».",
                    "Выйди в более безопасное место (свет, люди) и оставайся на связи с кем-то живым.",
                    "Сделай 1 звонок человеку, который точно поднимет трубку, и оставайся на линии.",
                ],
            )
        if prev_q and question == prev_q:
            question = _pick_variant(
                message.from_user.id,
                "crisis_alt_q",
                [
                    "Ты сейчас один или с кем-то?",
                    "Что из этого ты уже сделал: отошел от опасного места, написал человеку, позвонил?",
                    "Назови один контакт, с кем можешь быть на связи ближайшие 10 минут.",
                ],
            )
        await state.update_data(last_crisis_step=step, last_crisis_question=question)
        await message.answer(
            "Принял. Идем шаг за шагом.\n"
            f"Сейчас сделай одно: {step}\n"
            f"Ответь коротко: {question}\n"
            "Если есть риск сделать себе вред — сразу 112."
        )
    else:
        await message.answer(
            "Принял. Сейчас один шаг: отойди от опасного места и напиши человеку рядом, что тебе нужна помощь сейчас.\n"
            "Если есть риск навредить себе — сразу 112."
        )
    return True


def _basic_context_gate(mode: str, text: str) -> str | None:
    if _is_suicide_risk(text):
        return None

    words = _normalize_text_tokens(text)
    if len(words) < 6:
        return "need_context_min"

    # If user gave enough detail in one message, don't force rigid templates.
    if len(words) >= 20:
        return None

    lowered = text.lower()
    if mode == "razbor":
        # Medium detailed messages should pass when they contain event + intent signals.
        if len(words) >= 12 and any(
            token in lowered
            for token in (
                "произош", "случил", "написал", "сказал", "сделал", "получил", "было", "есть",
                "проблем", "ситуац", "отношен", "деньг", "конфликт", "вчера", "сегодня", "недел",
                "месяц", "поссор", "разъех", "план", "цель", "как быть", "помоги",
            )
        ):
            return None
        has_fact = any(
            token in lowered
            for token in (
                "произош", "случил", "написал", "сказал", "сделал", "получил",
                "было", "есть", "проблем", "ситуац", "отношен", "деньг", "конфликт",
                "вчера", "сегодня", "недел", "месяц", "год", "разъех", "поссор",
            )
        )
        has_goal = any(
            token in lowered
            for token in (
                "хочу", "цель", "итог", "нужно", "добиться", "что делать", "помоги",
                "как быть", "совет", "оцени", "разобрать",
            )
        )
        if not (has_fact or has_goal):
            return "need_razbor_structure"

    if mode == "plan":
        # Plan flow should accept natural text if enough detail exists.
        if len(words) < 8:
            return "need_plan_input"
        if len(words) >= 12 and any(token in lowered for token in ("цель", "хочу", "срок", "время", "ресурс", "дедлайн", "нужно")):
            return None

    if mode == "audit":
        # Audit flow should accept natural text if decision intent is visible.
        if len(words) < 8:
            return "need_audit_input"
        if len(words) >= 10 and any(
            token in lowered
            for token in ("стоит", "делать", "решение", "выбор", "риск", "сделаю", "планирую", "go", "no-go")
        ):
            return None

    return None


def _gate_message(tg_id: int, code: str) -> str:
    variants = {
        "need_context_min": [
            "Фактов мало. Дай 3 пункта: что произошло, что уже сделал, какой результат тебе нужен.",
            "Пока мало данных. Напиши: факт ситуации, что уже пробовал, какой итог нужен.",
            "Чтобы разобрать по делу, добавь: событие, твои действия, целевой результат.",
        ],
        "need_razbor_structure": [
            "Не хватает фактов для точного разбора.\nНапиши: факт события + что уже сделал + какой итог хочешь получить.",
            "Вижу запрос, но мало опоры на факты.\nДобавь: что случилось, что сделал, чего хочешь добиться.",
            "Дай каркас: факт, твой шаг, желаемый итог. Разберу по делу.",
        ],
        "need_plan_input": [
            "Для плана мало фактов.\nДай хотя бы цель и одно ограничение (время/ресурс).",
            "Чтобы составить план, нужны минимум цель и ограничения.",
            "Мало данных для плана. Напиши цель + рамки по времени или ресурсам.",
        ],
        "need_audit_input": [
            "Для аудита мало фактов.\nНапиши: ситуацию и какое действие ты рассматриваешь.",
            "Чтобы проверить решение, нужны 2 вещи: ситуация и выбранный шаг.",
            "Мало данных для аудита. Дай ситуацию и действие, которое хочешь сделать.",
        ],
    }
    return _pick_variant(tg_id, f"gate:{code}", variants.get(code, ["Нужно больше фактов."]))


def _context_reply_from_llm(payload: dict[str, Any], source_text: str) -> str | None:
    # If the user already sent a long detailed context, ignore LLM false negatives.
    if len(_normalize_text_tokens(source_text)) >= 20:
        return None

    enough = payload.get("enough_context")
    if enough is not False:
        return None

    # If LLM gave usable substance, don't hard-block the user with "not enough context".
    hard_truth = str(payload.get("hard_truth", "") or "").strip()
    core = str(payload.get("problem_core", "") or payload.get("decision_summary", "") or "").strip()
    if len(_normalize_text_tokens(source_text)) >= 10 and (hard_truth or core):
        blocks = []
        if core:
            blocks.append(section("🧠 Что уже видно", _sanitize_style(core)))
        if hard_truth:
            blocks.append(section("⚡ Прямой вывод", _sanitize_style(hard_truth)))
        blocks.append("Для точности добавь 1-2 факта (цифры/срок/что уже сделал).")
        return "\n\n".join(blocks)

    missing = payload.get("missing_context")
    questions = payload.get("clarifying_questions")
    missing_items = top_items(missing, 3) if isinstance(missing, list) else []
    question_items = top_items(questions, 2) if isinstance(questions, list) else []

    blocks = ["Пока не хватило фактов для точного разбора."]
    if missing_items:
        blocks.append("Добавь это:\n" + format_list(missing_items))
    if question_items:
        blocks.append("Ответь коротко:\n" + format_list(question_items))
    return "\n\n".join(blocks)


def _quoted_reply_context(message: Message) -> str:
    if not message.reply_to_message:
        return ""
    quoted = (
        (message.reply_to_message.text or message.reply_to_message.caption or "")
        .strip()
    )
    if not quoted:
        return ""
    return quoted[:700]


def _compose_with_last_context(
    tg_id: int,
    user_text: str,
    mode_hint: str | None = None,
    quoted_context: str = "",
) -> str:
    include_modes: tuple[str, ...] | None = None
    if mode_hint in {"razbor", "plan", "audit"}:
        include_modes = (mode_hint,)

    last = STORAGE.get_last_session(
        tg_id,
        include_modes=include_modes,
        exclude_modes=("crisis",),
        max_age_hours=72,
    )

    if not last and mode_hint is not None:
        last = STORAGE.get_last_session(
            tg_id,
            include_modes=("razbor", "plan", "audit"),
            exclude_modes=("crisis",),
            max_age_hours=72,
        )

    chunks: list[str] = []

    if last:
        prev_parts = [str(last.get("situation") or "").strip(), str(last.get("thought") or "").strip()]
        prev = "\n".join(p for p in prev_parts if p).strip()
        if prev:
            chunks.append(f"Контекст прошлой сессии:\n{prev}")

    if quoted_context:
        chunks.append(f"Цитата из предыдущего сообщения:\n{quoted_context}")

    if not chunks:
        return user_text

    chunks.append(f"Новый запрос:\n{user_text}")
    return "\n\n".join(chunks)


def _distortion_names_from_llm(payload: dict[str, Any]) -> list[str]:
    distortions = payload.get("distortions") or payload.get("likely_distortions") or []
    names: list[str] = []
    for item in distortions:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                names.append(name)
    return names[:3]


def _distortion_block(payload: dict[str, Any]) -> str:
    distortions = payload.get("distortions") or []
    lines: list[str] = []
    for item in distortions[:3]:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip() or "Искажение"
            why = str(item.get("why", "")).strip()[:90]
            evidence = str(item.get("evidence", "")).strip()[:80]
            text = f"• {name}"
            if why:
                text += f": {why}"
            if evidence:
                text += f" | факт: {evidence}"
            lines.append(text)
        elif isinstance(item, str):
            lines.append(f"• {item[:90]}")

    return "\n".join(lines) if lines else "• не выявлено"


def _reality_intake_prompt() -> str:
    return (
        "🧭 Reality Check\n"
        "Это глубокий аудит траектории (горизонт 30–90 дней).\n"
        "Ответь одним сообщением (текстом или голосом). Можно свободно, но лучше по структуре:\n"
        "1) Возраст\n"
        "2) Город/страна\n"
        "3) Доход в месяц\n"
        "4) Обязательные расходы в месяц\n"
        "5) Долги (сумма + платеж)\n"
        "6) Чем занимаешься/статус работы\n"
        "7) Часов работы в неделю\n"
        "8) Цель на 12 месяцев\n"
        "9) 3 главные проблемы\n"
        "10) Что уже пробовал\n"
        "11) Что мешает\n"
        "12) Сколько часов в неделю готов вкладывать в изменения"
    )


def _reality_profile_value(profile: dict[str, Any], key: str) -> str:
    value = profile.get(key)
    if value is None:
        return "не указано"
    text = str(value).strip()
    return text if text else "не указано"


def _reality_profile_list(profile: dict[str, Any], key: str) -> list[str]:
    raw = profile.get(key)
    if not isinstance(raw, list):
        return []
    return top_items([str(x).strip() for x in raw if str(x).strip()], 3)


def _reality_context_missing_reply(tg_id: int, missing: list[str]) -> str:
    missing_top = top_items(missing, 5)
    base = _pick_variant(
        tg_id,
        "reality_missing",
        [
            "Для жесткого Reality Check пока мало входа.",
            "Профиль сырой, точный разбор сейчас будет мимо.",
            "Сейчас данных недостаточно для объективного Reality Check.",
        ],
    )
    return "\n\n".join(
        [
            base,
            "Добавь одним сообщением:\n" + format_list(missing_top if missing_top else ["возраст", "доход", "расходы", "цель", "главные проблемы"]),
            "Сразу после этого дам полный разбор.",
        ]
    )


def _format_reality_check(payload: dict[str, Any]) -> str:
    profile_raw = payload.get("profile")
    profile = profile_raw if isinstance(profile_raw, dict) else {}

    problems = _reality_profile_list(profile, "main_problems")
    self_deception = top_items(payload.get("self_deception", []) if isinstance(payload.get("self_deception"), list) else [], 3)
    improve_first = top_items(payload.get("improve_first", []) if isinstance(payload.get("improve_first"), list) else [], 3)
    plan_7d = top_items(payload.get("plan_7d", []) if isinstance(payload.get("plan_7d"), list) else [], 3)
    weekly_metrics = top_items(payload.get("weekly_metrics", []) if isinstance(payload.get("weekly_metrics"), list) else [], 3)

    return "\n\n".join(
        [
            section(
                "📌 Профиль",
                (
                    f"• Возраст: {_reality_profile_value(profile, 'age')}\n"
                    f"• Локация: {_reality_profile_value(profile, 'location')}\n"
                    f"• Доход: {_reality_profile_value(profile, 'income_monthly')}\n"
                    f"• Расходы: {_reality_profile_value(profile, 'expenses_monthly')}\n"
                    f"• Долги: {_reality_profile_value(profile, 'debt')}\n"
                    f"• Работа: {_reality_profile_value(profile, 'work_status')}\n"
                    f"• Часы/нед: {_reality_profile_value(profile, 'work_hours_week')}\n"
                    f"• Цель 12м: {_reality_profile_value(profile, 'goal_12m')}\n"
                    f"• Проблемы: {', '.join(problems) if problems else 'не указано'}\n"
                    f"• Время на изменения: {_reality_profile_value(profile, 'time_for_change_hours_week')}"
                ),
            ),
            section("🧱 Где ты сейчас", _sanitize_style(str(payload.get("where_you_are_now", "нет данных")))),
            section("⚡ Жесткий вывод", _sanitize_style(str(payload.get("hard_truth", "нет данных")))),
            section("🪞 Где самообман", format_list(self_deception if self_deception else ["не выделен"])),
            section("🎯 Что улучшать первым", format_list(improve_first if improve_first else ["не выделено"])),
            section("📅 План на 7 дней", format_list(plan_7d if plan_7d else ["не сформирован"])),
            section("📈 Метрики на неделю", format_list(weekly_metrics if weekly_metrics else ["не заданы"])),
            section("➡️ Первый шаг за 24 часа", _sanitize_style(str(payload.get("first_step_24h", "не указан")))),
        ]
    )


def build_user_context_text(tg_id: int) -> str:
    profile = STORAGE.get_user_context(tg_id)
    if profile["sessions_total"] == 0:
        return "Истории пока нет."

    parts = [
        f"История сессий: {profile['sessions_total']}",
        f"Частые искажения: {', '.join(profile['top_distortions']) if profile['top_distortions'] else 'нет'}",
        f"Частые режимы: {', '.join(profile['frequent_modes']) if profile['frequent_modes'] else 'нет'}",
        f"Повторяющиеся темы: {', '.join(profile['recurring_topics']) if profile['recurring_topics'] else 'нет'}",
        f"Среднее снижение эмоции: {profile['avg_emotion_delta'] if profile['avg_emotion_delta'] is not None else 'нет данных'}",
    ]
    if profile["recent_actions"]:
        parts.append("Последние шаги: " + " | ".join(profile["recent_actions"][:3]))

    return "; ".join(parts)


def _admin_cost_summary_text() -> str:
    h12 = STORAGE.get_usage_summary_last_hours(12)
    h24 = STORAGE.get_usage_summary_last_hours(24)
    daily = STORAGE.get_usage_daily(7)
    daily_lines = "\n".join(
        f"• {row['day']}: ${row['cost_usd']:.4f} ({row['events']} вызовов)"
        for row in daily[:5]
    ) or "• нет данных"

    return "\n\n".join(
        [
            section(
                "💸 Burn Rate (12ч)",
                (
                    f"• Cost: ${float(h12['cost_usd']):.4f}\n"
                    f"• LLM in/out tokens: {int(h12['input_tokens'])}/{int(h12['output_tokens'])}\n"
                    f"• STT seconds: {float(h12['audio_seconds']):.1f}\n"
                    f"• Events: {int(h12['events'])}"
                ),
            ),
            section(
                "📊 Burn Rate (24ч)",
                (
                    f"• Cost: ${float(h24['cost_usd']):.4f}\n"
                    f"• LLM in/out tokens: {int(h24['input_tokens'])}/{int(h24['output_tokens'])}\n"
                    f"• STT seconds: {float(h24['audio_seconds']):.1f}\n"
                    f"• Events: {int(h24['events'])}"
                ),
            ),
            section("🗓 Последние дни", daily_lines),
        ]
    )


def _admin_status_text() -> str:
    kpi = STORAGE.get_admin_delivery_kpi_last_days(7)
    runs = STORAGE.get_recent_broadcast_runs_summary(1)
    last_run = runs[0] if runs else None
    last_run_line = "нет"
    if last_run:
        last_run_line = (
            f"#{last_run['id']} {last_run['segment']} | sent {last_run['sent_count']}/{last_run['total_targets']} "
            f"| blocked {last_run['blocked_count']} | failed {last_run['failed_count']}"
        )
    return "\n".join(
        [
            "Админ-мониторинг активен.",
            f"• ADMIN_TG_ID: {'настроен' if ADMIN_TG_ID else 'не задан'}",
            f"• Часы сводки: {', '.join(str(h).zfill(2) for h in sorted(ADMIN_NOTIFY_HOURS))}",
            f"• Таймзона: {ADMIN_TZ}",
            f"• Порог spike: {COST_ALERT_SPIKE_PCT:.0f}%",
            f"• Окно spike: {COST_SPIKE_WINDOW_HOURS}ч",
            f"• Авто-рассылка: {'вкл' if BROADCAST_ENABLED else 'выкл'}",
            f"• Часы авто-рассылки: {', '.join(str(h).zfill(2) for h in sorted(BROADCAST_HOURS))}",
            f"• Текст авто-рассылки: {'задан' if BROADCAST_TEXT else 'не задан'}",
            f"• Ретраи рассылки: {BROADCAST_MAX_RETRIES}",
            f"• Delivery 7д: runs={kpi['runs_count']}, sent={kpi['sent_count']}, blocked={kpi['blocked_count']}, failed={kpi['failed_count']}, retries={kpi['retry_count']}",
            f"• Последний запуск: {last_run_line}",
        ]
    )


def _mode_label(mode: str) -> str:
    labels = {
        "razbor": "Разбор",
        "plan": "План",
        "reply": "Переписка",
        "audit": "Проверка решения",
        "reality": "Reality Check",
        "crisis": "Кризис-помощь",
    }
    return labels.get(mode, mode)


def _progress_bar(current: int, target: int, width: int = 10) -> str:
    if target <= 0:
        target = 1
    filled = min(width, int((current / target) * width))
    return "■" * filled + "□" * (width - filled)


def _next_level_progress(sessions_total: int) -> tuple[int, int, int]:
    current_level = max(1, (sessions_total // 5) + 1)
    level_start = (current_level - 1) * 5
    to_next = 5
    inside = sessions_total - level_start
    return current_level, inside, to_next


def _weekly_challenge(progress: dict, top_modes: list[tuple[str, int]]) -> str:
    if progress["active_days_7d"] < 4:
        return "Выйти на 4 активных дня за 7 дней."
    if progress["sessions_7d"] < 7:
        return "Сделать 7 разборов за неделю (сейчас меньше)."
    return "Удержать серию и закрыть 1 сложный кейс через «Аудит решения»."


def _progress_next_step(progress: dict) -> str:
    if progress["sessions_total"] == 0:
        return "Сделай первый разбор: 1 факт, 1 твой шаг, 1 желаемый итог."
    if progress["active_days_7d"] < 3:
        return "Закрепи ритм: еще 2 коротких сессии в разные дни этой недели."
    mode_counts = progress.get("mode_counts", {})
    if mode_counts.get("audit", 0) == 0:
        return "Добавь «Аудит решения» перед следующим рискованным действием."
    if mode_counts.get("plan", 0) == 0:
        return "Добавь «План действий» на одну текущую проблему, чтобы снизить хаос."
    return "Возьми последнюю проблему и доведи ее до результата: разбор -> план -> аудит."


def _build_spike_alert(now_utc: datetime) -> str | None:
    current_start = now_utc - timedelta(hours=COST_SPIKE_WINDOW_HOURS)
    prev_start = now_utc - timedelta(hours=COST_SPIKE_WINDOW_HOURS * 2)
    prev_end = current_start

    current_cost = STORAGE.get_cost_between(current_start, now_utc)
    prev_cost = STORAGE.get_cost_between(prev_start, prev_end)

    threshold = 1 + (COST_ALERT_SPIKE_PCT / 100.0)
    if prev_cost < COST_ALERT_MIN_BASE_USD or current_cost < prev_cost * threshold:
        return None

    delta_pct = ((current_cost - prev_cost) / prev_cost) * 100.0 if prev_cost else 0.0
    return (
        "🚨 COST SPIKE\n"
        f"Окно: последние {COST_SPIKE_WINDOW_HOURS}ч vs предыдущие {COST_SPIKE_WINDOW_HOURS}ч\n"
        f"Было: ${prev_cost:.4f}\n"
        f"Стало: ${current_cost:.4f}\n"
        f"Рост: {delta_pct:.1f}%"
    )


async def admin_monitor_loop(bot: Bot) -> None:
    if ADMIN_TG_ID is None and not BROADCAST_ENABLED:
        return

    while True:
        try:
            now_local = datetime.now(ADMIN_TZ)
            if ADMIN_TG_ID is not None and now_local.hour in ADMIN_NOTIFY_HOURS and now_local.minute < 10:
                summary_key = f"summary:{now_local.date().isoformat()}:{now_local.hour}"
                if not STORAGE.has_alert_been_sent(summary_key):
                    await bot.send_message(ADMIN_TG_ID, _admin_cost_summary_text())
                    STORAGE.mark_alert_sent(summary_key, payload="daily_summary")

            if ADMIN_TG_ID is not None:
                now_utc = datetime.now(timezone.utc)
                spike_key = f"spike:{now_utc.strftime('%Y-%m-%d-%H')}"
                if not STORAGE.has_alert_been_sent(spike_key):
                    spike_text = _build_spike_alert(now_utc)
                    if spike_text:
                        await bot.send_message(ADMIN_TG_ID, spike_text)
                        STORAGE.mark_alert_sent(spike_key, payload=spike_text)

            if BROADCAST_ENABLED and BROADCAST_TEXT and now_local.hour in BROADCAST_HOURS and now_local.minute < 10:
                b_key = f"broadcast:{now_local.date().isoformat()}:{now_local.hour}"
                if not STORAGE.has_alert_been_sent(b_key):
                    report = await _run_broadcast(
                        bot,
                        payload={"kind": "text", "text": BROADCAST_TEXT},
                        include_blocked=BROADCAST_INCLUDE_BLOCKED,
                        segment="all",
                        created_by_tg_id=ADMIN_TG_ID or 0,
                        max_retries=BROADCAST_MAX_RETRIES,
                    )
                    STORAGE.mark_alert_sent(
                        b_key,
                        payload=(
                            f"run_id={report['run_id']} targets={report['targets']} sent={report['sent']} "
                            f"blocked={report['blocked']} failed={report['failed']} retries={report['retries']}"
                        ),
                    )
                    if ADMIN_TG_ID is not None:
                        await bot.send_message(
                            ADMIN_TG_ID,
                            section(
                                "📬 Авто-рассылка отправлена",
                                (
                                    f"• Run: #{report['run_id']}\n"
                                    f"• Целей: {report['targets']}\n"
                                    f"• Доставлено: {report['sent']}\n"
                                    f"• Блок: {report['blocked']}\n"
                                    f"• Ошибки: {report['failed']}\n"
                                    f"• Повторы: {report['retries']}"
                                ),
                            ),
                        )
        except Exception:
            # Monitoring should never stop the bot.
            pass

        await asyncio.sleep(300)


async def send_quick_analysis(message: Message, user_text: str, state: FSMContext | None = None) -> None:
    if state and await _handle_crisis_message(message, state, user_text):
        return
    if _is_suicide_risk(user_text):
        await message.answer(_crisis_reply())
        return

    use_history = _wants_history_reference(user_text) or _looks_like_followup(user_text)
    quoted_context = _quoted_reply_context(message)
    effective_text = (
        _compose_with_last_context(
            message.from_user.id,
            user_text,
            mode_hint="razbor",
            quoted_context=quoted_context,
        )
        if use_history or quoted_context
        else user_text
    )

    if _is_harm_intent(effective_text):
        await message.answer(_harm_intent_reply(message.from_user.id))
        return

    if _is_smalltalk_or_low_signal(user_text) and not use_history:
        await message.answer(_low_signal_reply(message.from_user.id))
        return

    gate_text = _basic_context_gate("razbor", effective_text)
    if gate_text and _looks_like_followup(user_text):
        last = STORAGE.get_last_session(
            message.from_user.id,
            include_modes=("razbor", "plan", "audit"),
            exclude_modes=("crisis",),
            max_age_hours=72,
        )
        if last and (last.get("situation") or last.get("thought")):
            prev = f"{last.get('situation', '')}\n{last.get('thought', '')}".strip()
            if prev:
                effective_text = f"Контекст прошлого сообщения:\n{prev}\n\nУточнение:\n{user_text}"
                gate_text = None
    if gate_text:
        await message.answer(_gate_message(message.from_user.id, gate_text))
        return

    if _looks_like_reality_input(effective_text):
        await message.answer(
            "Это уже уровень полного жизненного аудита, не локального кейса.\n"
            "Запусти `🧭 Reality Check` (или /reality), и дам жесткий системный разбор на 30–90 дней."
        )
        return

    user_context = build_user_context_text(message.from_user.id)
    llm = await COACH.case_analysis(user_text=effective_text, user_context=user_context) if COACH.enabled else None

    if llm:
        _record_usage_meta(message.from_user.id, _pop_usage_meta(llm))
        llm_context_reply = _context_reply_from_llm(llm, effective_text)
        if llm_context_reply:
            await message.answer(llm_context_reply)
            return
        distortions = _distortion_names_from_llm(llm)
        actions_15m_raw = llm.get("actions_15m") if isinstance(llm.get("actions_15m"), list) else []
        actions_24h_raw = llm.get("actions_24h") if isinstance(llm.get("actions_24h"), list) else []
        actions_15m = _make_actions_contextual(effective_text, actions_15m_raw, 3)
        actions_24h = _make_actions_contextual(effective_text, actions_24h_raw, 2)
        reality_score = max(0, min(100, int(llm.get("reality_score_0_100", 0) or 0)))
        killer_block = "\n".join(
            [
                f"• Reality Score: {reality_score}/100",
                f"• Цена иллюзии (30д): {_sanitize_style(str(llm.get('cost_of_illusion_30d', 'не указана')))}",
                f"• Главная отмазка: {_sanitize_style(str(llm.get('main_excuse', 'не выделена')))}",
                f"• Контрфакт: {_sanitize_style(str(llm.get('counter_fact', 'не выделен')))}",
            ]
        )
        non_negotiable = _sanitize_style(str(llm.get("non_negotiable_step_24h", ""))).strip()

        STORAGE.save_session(
            tg_id=message.from_user.id,
            mode="razbor",
            situation=effective_text,
            thought=_sanitize_style(str(llm.get("problem_core", ""))),
            distortions=distortions,
            reframe=_sanitize_style(str(llm.get("reframe", ""))),
            action="; ".join(actions_15m[:2]) if actions_15m else "",
            emotion_before=None,
            emotion_after=None,
        )

        text = "\n\n".join(
            [
                section("🧠 Суть проблемы", _sanitize_style(str(llm.get("problem_core", "нет")))),
                section("🪓 Анти-самообман", killer_block),
                section("🔎 Где искажения", _distortion_block(llm)),
                section("⚡ Прямой вывод", _sanitize_style(str(llm.get("hard_truth", "нет")))),
                section("⏱ Что делать сейчас", format_list(top_items(actions_15m, 3))),
                section("📅 Что сделать сегодня", format_list(top_items(actions_24h, 2))),
                section("🔒 Шаг без компромиссов (24ч)", non_negotiable or "не указан"),
            ]
        )
        _touch_user(message.from_user.id, "result:razbor", "llm")
        await message.answer(text)
        return

    result = analyze_case(situation=effective_text, thought=effective_text)
    distortions = [d.title for d in result["distortions"]]
    STORAGE.save_session(
        tg_id=message.from_user.id,
        mode="razbor",
        situation=effective_text,
        thought=effective_text,
        distortions=distortions,
        reframe=result["reframe"],
        action=result["action"],
        emotion_before=None,
        emotion_after=None,
    )

    fallback_text = "\n\n".join(
        [
            section("🧠 Базовый разбор", "AI временно недоступен."),
            section("🔎 Искажения", ", ".join(distortions)),
            section("⚡ Прямой вывод", result["hard_truth"]),
            section("✅ Следующий шаг", result["action"][:220]),
        ]
    )
    _touch_user(message.from_user.id, "result:razbor", "fallback")
    await message.answer(fallback_text)


async def _run_plan_flow(message: Message, state: FSMContext, user_text: str) -> None:
    if await _handle_crisis_message(message, state, user_text):
        return

    use_history = _wants_history_reference(user_text) or _looks_like_followup(user_text)
    quoted_context = _quoted_reply_context(message)
    effective_text = (
        _compose_with_last_context(
            message.from_user.id,
            user_text,
            mode_hint="plan",
            quoted_context=quoted_context,
        )
        if use_history or quoted_context
        else user_text
    )

    if _is_harm_intent(effective_text):
        await message.answer(_harm_intent_reply(message.from_user.id))
        return
    if _is_smalltalk_or_low_signal(user_text) and not use_history:
        await message.answer(_low_signal_reply(message.from_user.id, "plan"))
        return

    gate_text = _basic_context_gate("plan", effective_text)
    if gate_text:
        await message.answer(_gate_message(message.from_user.id, gate_text))
        return

    user_context = build_user_context_text(message.from_user.id)
    llm = await COACH.action_plan(user_text=effective_text, user_context=user_context) if COACH.enabled else None
    if llm:
        _record_usage_meta(message.from_user.id, _pop_usage_meta(llm))
        llm_context_reply = _context_reply_from_llm(llm, effective_text)
        if llm_context_reply:
            await message.answer(llm_context_reply)
            return
        distortions = _distortion_names_from_llm(llm)
        plan_24h_raw = llm.get("plan_24h") if isinstance(llm.get("plan_24h"), list) else []
        plan_7d_raw = llm.get("plan_7d") if isinstance(llm.get("plan_7d"), list) else []
        plan_30d_raw = llm.get("plan_30d") if isinstance(llm.get("plan_30d"), list) else []
        no_compromise_raw = llm.get("no_compromise_rules") if isinstance(llm.get("no_compromise_rules"), list) else []
        plan_24h = _make_actions_contextual(effective_text, plan_24h_raw, 3)
        plan_7d = _make_actions_contextual(effective_text, plan_7d_raw, 2)
        plan_30d = _make_actions_contextual(effective_text, plan_30d_raw, 2)
        no_compromise = top_items([_sanitize_style(str(x)) for x in no_compromise_raw], 3)
        first_step = _make_actions_contextual(effective_text, [str(llm.get("first_step", ""))], 1)[0]
        failure_trigger = _sanitize_style(str(llm.get("failure_trigger", "не указан"))).strip()
        recovery_if_failed = _sanitize_style(str(llm.get("recovery_if_failed", "не указано"))).strip()
        checkpoint_metric = _sanitize_style(str(llm.get("checkpoint_metric", "не указана"))).strip()

        STORAGE.save_session(
            tg_id=message.from_user.id,
            mode="plan",
            situation=effective_text,
            thought=_sanitize_style(str(llm.get("goal_interpretation", ""))),
            distortions=distortions,
            reframe=_sanitize_style(str(llm.get("hard_truth", ""))),
            action="; ".join(plan_24h[:2]) if plan_24h else "",
            emotion_before=None,
            emotion_after=None,
        )

        await state.clear()
        text = "\n\n".join(
            [
                section("🎯 Цель", _sanitize_style(str(llm.get("goal_interpretation", "нет")))),
                section("🧱 Главный блокер", _sanitize_style(str(llm.get("main_blocker", "нет")))),
                section("⚡ Прямой вывод", _sanitize_style(str(llm.get("hard_truth", "нет")))),
                section("📅 План на 24 часа", format_list(top_items(plan_24h, 3))),
                section("🗓 План на 7 дней", format_list(top_items(plan_7d, 2))),
                section("🧭 План на 30 дней", format_list(top_items(plan_30d, 2))),
                section("🔒 Правила без компромиссов", format_list(no_compromise if no_compromise else ["не заданы"])),
                section("💥 Точка срыва", failure_trigger or "не указана"),
                section("🛠 Восстановление после срыва", recovery_if_failed or "не указано"),
                section("📏 Метрика контроля", checkpoint_metric or "не указана"),
                section("🚀 Первый шаг", _sanitize_style(first_step)),
            ]
        )
        _touch_user(message.from_user.id, "result:plan", "llm")
        await message.answer(text, reply_markup=main_menu(_is_admin(message.from_user.id)))
        return

    plan = build_plan(goal="Цель из сообщения", situation=effective_text, thought=effective_text)
    STORAGE.save_session(
        tg_id=message.from_user.id,
        mode="plan",
        situation=effective_text,
        thought=effective_text,
        distortions=plan.distortions,
        reframe=plan.hard_truth,
        action=plan.plan_today,
        emotion_before=None,
        emotion_after=None,
    )
    await state.clear()
    fallback_text = "\n\n".join(
        [
            section("⚠️ Резервный режим", "AI-план временно недоступен."),
            section("🔎 Искажения", ", ".join(plan.distortions)),
            section("⚡ Прямой вывод", plan.hard_truth),
            section("⏱ На сегодня", plan.plan_today[:220]),
        ]
    )
    _touch_user(message.from_user.id, "result:plan", "fallback")
    await message.answer(fallback_text, reply_markup=main_menu(_is_admin(message.from_user.id)))


async def _run_audit_flow(message: Message, state: FSMContext, user_text: str) -> None:
    if await _handle_crisis_message(message, state, user_text):
        return

    use_history = _wants_history_reference(user_text) or _looks_like_followup(user_text)
    quoted_context = _quoted_reply_context(message)
    effective_text = (
        _compose_with_last_context(
            message.from_user.id,
            user_text,
            mode_hint="audit",
            quoted_context=quoted_context,
        )
        if use_history or quoted_context
        else user_text
    )

    if _is_harm_intent(effective_text):
        await message.answer(_harm_intent_reply(message.from_user.id))
        return
    if _is_smalltalk_or_low_signal(user_text) and not use_history:
        await message.answer(_low_signal_reply(message.from_user.id, "audit"))
        return

    gate_text = _basic_context_gate("audit", effective_text)
    if gate_text:
        await message.answer(_gate_message(message.from_user.id, gate_text))
        return

    user_context = build_user_context_text(message.from_user.id)
    llm = await COACH.decision_audit(user_text=effective_text, user_context=user_context) if COACH.enabled else None
    if llm:
        _record_usage_meta(message.from_user.id, _pop_usage_meta(llm))
        llm_context_reply = _context_reply_from_llm(llm, effective_text)
        if llm_context_reply:
            await message.answer(llm_context_reply)
            return
        distortions = _distortion_names_from_llm(llm)
        risks = llm.get("risks") if isinstance(llm.get("risks"), list) else []
        irreversible_risks = llm.get("irreversible_risks") if isinstance(llm.get("irreversible_risks"), list) else []
        go = llm.get("go_criteria") if isinstance(llm.get("go_criteria"), list) else []
        no_go = llm.get("no_go_criteria") if isinstance(llm.get("no_go_criteria"), list) else []
        must_be_true = llm.get("what_must_be_true_before_action") if isinstance(llm.get("what_must_be_true_before_action"), list) else []
        verdict = _sanitize_style(str(llm.get("verdict", "HOLD"))).strip().upper()
        confidence = max(0, min(100, int(llm.get("confidence_0_100", 0) or 0)))

        STORAGE.save_session(
            tg_id=message.from_user.id,
            mode="audit",
            situation=effective_text,
            thought=_sanitize_style(str(llm.get("decision_summary", ""))),
            distortions=distortions,
            reframe=_sanitize_style(str(llm.get("hard_truth", ""))),
            action=_sanitize_style(str(llm.get("best_next_step", ""))),
            emotion_before=None,
            emotion_after=None,
        )

        await state.clear()
        text = "\n\n".join(
            [
                section("🧪 Суть решения", _sanitize_style(str(llm.get("decision_summary", "нет")))),
                section("⚡ Прямой вывод", _sanitize_style(str(llm.get("hard_truth", "нет")))),
                section("🧷 Вердикт", f"• {verdict}\n• Уверенность: {confidence}/100"),
                section("⚠️ Риски", format_list(top_items(risks, 3))),
                section("☠️ Необратимые риски", format_list(top_items(irreversible_risks, 2))),
                section("📌 Что должно быть истинно до шага", format_list(top_items(must_be_true, 3))),
                section("✅ GO / 🛑 NO-GO", format_list(top_items(go, 1) + top_items(no_go, 1))),
                section("➡️ Лучший следующий шаг", _sanitize_style(str(llm.get("best_next_step", "нет")))),
            ]
        )
        _touch_user(message.from_user.id, "result:audit", "llm")
        await message.answer(text, reply_markup=main_menu(_is_admin(message.from_user.id)))
        return

    result = build_decision_audit(situation=effective_text, selected_action=effective_text)
    STORAGE.save_session(
        tg_id=message.from_user.id,
        mode="audit",
        situation=effective_text,
        thought=effective_text,
        distortions=result.distortions,
        reframe=result.hard_truth,
        action=result.next_step,
        emotion_before=None,
        emotion_after=None,
    )
    await state.clear()
    fallback_text = "\n\n".join(
        [
            section("⚠️ Резервный режим", "AI-аудит временно недоступен."),
            section("⚡ Прямой вывод", result.hard_truth),
            section("⚠️ Риски", format_list(top_items(result.risks, 3))),
            section("➡️ Следующий шаг", result.next_step),
        ]
    )
    _touch_user(message.from_user.id, "result:audit", "fallback")
    await message.answer(fallback_text, reply_markup=main_menu(_is_admin(message.from_user.id)))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    _touch_user(message.from_user.id, "cmd:start")
    STORAGE.get_stats(message.from_user.id)
    text = "\n\n".join(
        [
            "Когнитивный Навигатор",
            "Я не успокаиваю. Я вскрываю, где ты врешь себе, и даю шаг, который двигает к результату.",
            "Отправь текст или голосовое:\n"
            "1) факт\n"
            "2) что уже сделал\n"
            "3) какой итог нужен",
            "🧠 Разбор — один кейс\n"
            "🧭 Reality Check — аудит траектории\n"
            "🗺️ План — шаги до результата\n"
            "🧪 Проверка — GO / NO-GO\n"
            "📊 Прогресс — реальные изменения",
        ]
    )
    await message.answer(text, reply_markup=main_menu(_is_admin(message.from_user.id)))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    _touch_user(message.from_user.id, "cmd:cancel")
    await message.answer("Остановил текущий сценарий.", reply_markup=main_menu(_is_admin(message.from_user.id)))


@router.message(
    F.text.in_([BTN_RAZBOR, BTN_PLAN, BTN_AUDIT, BTN_REALITY, BTN_STATS])
    | F.text.lower().in_(
        [
            "разбор",
            "разбор ситуации",
            "план",
            "план действий",
            "аудит",
            "аудит решения",
            "проверка решения",
            "reality check",
            "reality",
            "реалити чек",
            "проверка реальности",
            "прогресс",
            "мой прогресс",
        ]
    )
)
async def menu_button_router(message: Message, state: FSMContext) -> None:
    await state.clear()
    action = _resolve_menu_action(message.text or "")
    _touch_user(message.from_user.id, "click:menu", action or (message.text or "")[:80])
    if action == "razbor":
        await cmd_razbor(message, state)
        return
    if action == "plan":
        await cmd_plan(message, state)
        return
    if action == "audit":
        await cmd_audit(message, state)
        return
    if action == "reality":
        await cmd_reality(message, state)
        return
    if action == "stats":
        await cmd_stats(message)
        return
    if action == "admin_panel" and _is_admin(message.from_user.id):
        await cmd_admin_panel(message, state)


@router.message(
    F.text.in_(
        [
            BTN_ADMIN_BROADCAST,
            BTN_ADMIN_RUNS,
            BTN_ADMIN_USERS,
            BTN_ADMIN_EVENTS,
            BTN_ADMIN_COST,
            BTN_ADMIN_STATUS,
            BTN_ADMIN_ADMINS,
            BTN_ADMIN_HELP,
            "➕ Выдать админку",
            "➖ Снять админку",
            "🧹 Очистка логов",
            BTN_BACK_MAIN,
        ]
    )
)
async def admin_button_router(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    choice = (message.text or "").strip()
    if choice == BTN_BACK_MAIN:
        await state.clear()
        await message.answer("Вернул обычное меню.", reply_markup=main_menu(True))
        return
    if choice == BTN_ADMIN_BROADCAST:
        await cmd_admin_broadcast(message, state)
        return
    if choice == BTN_ADMIN_RUNS:
        await cmd_admin_runs(message)
        return
    if choice == BTN_ADMIN_USERS:
        await cmd_admin_users(message)
        return
    if choice == BTN_ADMIN_EVENTS:
        await cmd_admin_events(message)
        return
    if choice == BTN_ADMIN_COST:
        await cmd_admin_cost(message)
        return
    if choice == BTN_ADMIN_STATUS:
        await cmd_admin_status(message)
        return
    if choice == BTN_ADMIN_ADMINS:
        await cmd_admin_list(message)
        return
    if choice == BTN_ADMIN_HELP:
        await cmd_helpa(message)
        return
    if choice == "➕ Выдать админку":
        if _is_root_admin(message.from_user.id):
            await cmd_admin_grant(message, state)
        else:
            await message.answer("Только root-админ может выдавать админку.")
        return
    if choice == "➖ Снять админку":
        if _is_root_admin(message.from_user.id):
            await cmd_admin_revoke(message, state)
        else:
            await message.answer("Только root-админ может снимать админку.")
        return
    if choice == "🧹 Очистка логов":
        if _is_root_admin(message.from_user.id):
            await cmd_admin_cleanup(message)
        else:
            await message.answer("Только root-админ может чистить логи.")


@router.message(Command("razbor"))
@router.message(F.text == BTN_RAZBOR)
async def cmd_razbor(message: Message, state: FSMContext) -> None:
    await state.clear()
    _touch_user(message.from_user.id, "cmd:razbor")
    await state.set_state(RazborStates.intake)
    await message.answer(
        "🧠 Разбор = один конкретный кейс (горизонт 24–72ч).\n"
        "Пришли одним сообщением:\n"
        "1) Что произошло (факты)\n"
        "2) Что уже сделал\n"
        "3) Какой результат тебе нужен"
    )


@router.message(RazborStates.intake)
async def razbor_intake(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return
    await state.clear()
    await send_quick_analysis(message, user_text, state)


@router.message(CrisisStates.followup)
async def crisis_followup(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return

    if await _handle_crisis_message(message, state, user_text):
        return

    await state.clear()
    await message.answer("Принял. Перехожу к разбору.")
    await send_quick_analysis(message, user_text, state)


@router.message(Command("plan"))
@router.message(F.text == BTN_PLAN)
async def cmd_plan(message: Message, state: FSMContext) -> None:
    await state.clear()
    _touch_user(message.from_user.id, "cmd:plan")
    await state.set_state(PlanStates.intake)
    await message.answer(
        "🗺️ План действий = как дойти до цели по шагам.\n"
        "Пришли одним сообщением:\n"
        "1) Цель\n"
        "2) Ограничения (время/ресурс)\n"
        "3) Срок\n"
        "4) Что уже пробовал (если есть)"
    )


@router.message(PlanStates.intake)
async def plan_intake(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return
    await _run_plan_flow(message, state, user_text)


@router.message(Command("audit"))
@router.message(F.text == BTN_AUDIT)
async def cmd_audit(message: Message, state: FSMContext) -> None:
    await state.clear()
    _touch_user(message.from_user.id, "cmd:audit")
    await state.set_state(AuditStates.intake)
    await message.answer(
        "🧪 Проверка решения = идти в действие или тормозить.\n"
        "Пришли:\n"
        "1) Ситуацию\n"
        "2) Какое действие ты собираешься сделать\n"
        "3) Что рискуешь потерять при ошибке"
    )


@router.message(AuditStates.intake)
async def audit_intake(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return
    await _run_audit_flow(message, state, user_text)


@router.message(Command("reality"))
@router.message(Command("reality_check"))
@router.message(F.text == BTN_REALITY)
async def cmd_reality(message: Message, state: FSMContext) -> None:
    await state.clear()
    _touch_user(message.from_user.id, "cmd:reality")
    await state.set_state(RealityStates.intake)
    await message.answer(_reality_intake_prompt())


@router.message(RealityStates.intake)
async def reality_intake(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return

    if await _handle_crisis_message(message, state, user_text):
        return

    previous = STORAGE.get_reality_profile(message.from_user.id) or {}
    prev_profile = previous.get("profile", {}) if isinstance(previous.get("profile"), dict) else {}
    prev_profile_context = str(prev_profile) if prev_profile else ""
    user_context = build_user_context_text(message.from_user.id)

    llm = await COACH.reality_check(
        user_text=user_text,
        user_context=user_context,
        profile_context=prev_profile_context,
    ) if COACH.enabled else None

    if llm:
        _record_usage_meta(message.from_user.id, _pop_usage_meta(llm))
        enough = llm.get("enough_context")
        missing = llm.get("missing_data", [])
        if enough is False:
            await message.answer(_reality_context_missing_reply(message.from_user.id, missing if isinstance(missing, list) else []))
            return

        profile_raw = llm.get("profile")
        profile = profile_raw if isinstance(profile_raw, dict) else {}
        quality = int(llm.get("profile_quality", 0) or 0)
        STORAGE.save_reality_profile(
            tg_id=message.from_user.id,
            source_text=user_text,
            profile=profile,
            profile_quality=quality,
            check_payload=llm,
        )
        STORAGE.save_session(
            tg_id=message.from_user.id,
            mode="reality",
            situation=user_text,
            thought=_sanitize_style(str(llm.get("where_you_are_now", ""))),
            distortions=top_items([str(x) for x in llm.get("self_deception", [])], 3) if isinstance(llm.get("self_deception"), list) else [],
            reframe=_sanitize_style(str(llm.get("hard_truth", ""))),
            action=_sanitize_style(str(llm.get("first_step_24h", ""))),
            emotion_before=None,
            emotion_after=None,
        )
        await state.clear()
        await message.answer(
            _format_reality_check(llm),
            reply_markup=main_menu(_is_admin(message.from_user.id)),
        )
        return

    await state.clear()
    await message.answer(
        "Reality Check сейчас временно недоступен (LLM off). Добавь `OPENAI_API_KEY` и повтори /reality.",
        reply_markup=main_menu(_is_admin(message.from_user.id)),
    )


@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def cmd_stats(message: Message) -> None:
    _touch_user(message.from_user.id, "cmd:stats")
    stats = STORAGE.get_stats(message.from_user.id)
    progress = STORAGE.get_progress_snapshot(message.from_user.id)
    reality = STORAGE.get_reality_profile(message.from_user.id) or {}
    mode_rank = sorted(progress["mode_counts"].items(), key=lambda x: x[1], reverse=True)[:3]
    mode_rank_text = format_list([f"{_mode_label(m)}: {c}" for m, c in mode_rank]) if mode_rank else "• пока нет"
    top_dist_text = ", ".join(progress["top_distortions"]) if progress["top_distortions"] else "нет явного паттерна"
    badges_text = " • ".join(progress["badges"]) if progress["badges"] else "пока нет"
    recent_focus = format_list(
        [f"{_mode_label(item['mode'])}: {item['line']}" for item in progress["recent_focus"][:3]]
    ) if progress["recent_focus"] else "• пока нет данных"

    level, inside, target = _next_level_progress(progress["sessions_total"])
    bar = _progress_bar(inside, target)
    score = int(progress["sessions_total"] * 10 + progress["streak_days"] * 5 + progress["active_days_7d"] * 7)
    discipline = max(0, min(100, int(progress["active_days_7d"] * 11 + min(progress["streak_days"], 7) * 3)))
    avg_delta = stats["avg_delta"] if stats["avg_delta"] is not None else "нет данных"
    challenge = _weekly_challenge(progress, mode_rank)
    next_step = _progress_next_step(progress)
    reality_quality = int(reality.get("profile_quality", 0) or 0)
    reality_updated = str(reality.get("updated_at", "") or "нет")
    target_daily_sessions = max(1, 7 - int(progress["sessions_7d"]))
    weekly_pace = "в норме" if progress["sessions_7d"] >= 7 else "ниже цели"

    text = "\n\n".join(
        [
            section(
                "🏆 Сводка прогресса",
                (
                    f"• Очки: {score}\n"
                    f"• Уровень {level}: {bar} ({inside}/{target})\n"
                    f"• Серия: {progress['streak_days']} дн.\n"
                    f"• Индекс дисциплины: {discipline}/100"
                ),
            ),
            section(
                "📅 Неделя",
                (
                    f"• Сессий: {progress['sessions_7d']}\n"
                    f"• Активных дней: {progress['active_days_7d']}/7\n"
                    f"• Среднее снижение эмоции: {avg_delta}\n"
                    f"• Темп: {weekly_pace}"
                ),
            ),
            section("🎯 Рабочие режимы", mode_rank_text),
            section("🧠 Главный паттерн искажений", top_dist_text),
            section("🛠 Последний фокус", recent_focus),
            section(
                "🧭 Reality профиль",
                (
                    f"• Качество профиля: {reality_quality}/100\n"
                    f"• Обновлен: {reality_updated}"
                ),
            ),
            section("📌 Приоритет на сегодня", next_step),
            section(
                "🎯 Цель до конца недели",
                (
                    f"• {challenge}\n"
                    f"• Чтобы выйти в ритм: минимум {target_daily_sessions} сесс. до конца недели"
                ),
            ),
            section("🎖 Бейджи", badges_text),
        ]
    )
    await message.answer(text)


@router.message(Command("admin_cost"))
async def cmd_admin_cost(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_cost")
    await message.answer(_admin_cost_summary_text(), reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))


@router.message(Command("admin_status"))
async def cmd_admin_status(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_status")
    await message.answer(_admin_status_text(), reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))


@router.message(Command("helpa"))
async def cmd_helpa(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:helpa")
    await message.answer(_admin_help_text(), reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))


@router.message(Command("admin"))
@router.message(Command("admin_panel"))
async def cmd_admin_panel(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_panel")
    await state.clear()
    await state.set_state(AdminPanelStates.waiting_action)
    await message.answer(_admin_panel_text(), reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))


@router.message(Command("admin_users"))
async def cmd_admin_users(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_users")
    overview = STORAGE.get_admin_user_overview()
    rows = STORAGE.get_admin_user_rows(limit=20)
    lines = []
    for row in rows:
        status = "🔴 блок" if int(row.get("is_blocked") or 0) == 1 else "🟢 ок"
        last_seen = str(row.get("last_seen_at") or "нет")
        last_event = str(row.get("last_event") or "нет")
        lines.append(f"• {row['tg_id']} | {status} | seen: {last_seen} | evt: {last_event}")
    details = "\n".join(lines) if lines else "• нет данных"
    text = "\n\n".join(
        [
            section(
                "👥 Пользователи",
                (
                    f"• Всего: {overview['users_total']}\n"
                    f"• Активны 24ч: {overview['active_24h']}\n"
                    f"• Активны 7д: {overview['active_7d']}\n"
                    f"• Заблокировали бота: {overview['blocked_total']}"
                ),
            ),
            section("🧾 Последние пользователи", details),
        ]
    )
    await message.answer(text, reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))


@router.message(Command("admin_events"))
async def cmd_admin_events(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_events")
    events = STORAGE.get_recent_user_events(limit=30, event_prefix="click:")
    lines = [
        f"• {e['created_at']} | {e['tg_id']} | {e['event_type']} | {str(e.get('payload') or '')[:60]}"
        for e in events
    ]
    text = section("🕹 Последние клики", "\n".join(lines) if lines else "• нет данных")
    await message.answer(text, reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))


@router.message(Command("admin_runs"))
async def cmd_admin_runs(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_runs")
    runs = STORAGE.get_recent_broadcast_runs_summary(limit=5)
    if not runs:
        await message.answer("Рассылок пока не было.", reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))
        return
    lines = []
    for run in runs:
        total = int(run["total_targets"] or 0)
        sent = int(run["sent_count"] or 0)
        blocked = int(run["blocked_count"] or 0)
        failed = int(run["failed_count"] or 0)
        sent_pct = (sent / total * 100.0) if total else 0.0
        blocked_pct = (blocked / total * 100.0) if total else 0.0
        failed_pct = (failed / total * 100.0) if total else 0.0
        lines.append(
            f"• #{run['id']} | {_broadcast_segment_label(run['segment'])} | sent {sent}/{total} ({sent_pct:.0f}%) "
            f"| block {blocked} ({blocked_pct:.0f}%) | fail {failed} ({failed_pct:.0f}%) | retry {run['retry_count']}"
        )
    await message.answer(
        section("📬 Последние рассылки", "\n".join(lines)),
        reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)),
    )


@router.message(Command("admin_list"))
async def cmd_admin_list(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_list")
    admins = STORAGE.list_admins(limit=50)
    if not admins:
        await message.answer("Список админов пуст.", reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))
        return
    lines = []
    for row in admins:
        marker = " (root)" if _is_root_admin(row["tg_id"]) else ""
        lines.append(f"• {row['tg_id']}{marker} | by: {row.get('granted_by') or '-'} | {row.get('created_at') or '-'}")
    await message.answer(section("🔐 Админы", "\n".join(lines)), reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))


@router.message(Command("admin_grant"))
async def cmd_admin_grant(message: Message, state: FSMContext) -> None:
    if not _is_root_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_grant")
    await state.clear()
    await state.set_state(AdminAccessStates.waiting_grant_tg_id)
    await message.answer(
        "Пришли `tg_id`, которому выдать админку.",
        reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)),
    )


@router.message(AdminAccessStates.waiting_grant_tg_id)
async def admin_grant_input(message: Message, state: FSMContext) -> None:
    if not _is_root_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой `tg_id`.")
        return
    target = int(raw)
    STORAGE.grant_admin(tg_id=target, granted_by=message.from_user.id, note="manual grant")
    await state.clear()
    await message.answer(
        f"Выдал админку: `{target}`",
        reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)),
    )


@router.message(Command("admin_revoke"))
async def cmd_admin_revoke(message: Message, state: FSMContext) -> None:
    if not _is_root_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_revoke")
    await state.clear()
    await state.set_state(AdminAccessStates.waiting_revoke_tg_id)
    await message.answer(
        "Пришли `tg_id`, у которого снять админку.",
        reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)),
    )


@router.message(AdminAccessStates.waiting_revoke_tg_id)
async def admin_revoke_input(message: Message, state: FSMContext) -> None:
    if not _is_root_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой `tg_id`.")
        return
    target = int(raw)
    if _is_root_admin(target):
        await message.answer("Нельзя снять root-админа из бота.")
        await state.clear()
        return
    STORAGE.revoke_admin(tg_id=target)
    await state.clear()
    await message.answer(
        f"Снял админку: `{target}`",
        reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)),
    )


@router.message(Command("admin_cleanup"))
async def cmd_admin_cleanup(message: Message) -> None:
    if not _is_root_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_cleanup")
    report = STORAGE.cleanup_old_admin_data(keep_days=45)
    await message.answer(
        section(
            "🧹 Очистка логов",
            (
                f"• Период хранения: {report['keep_days']} дн.\n"
                f"• Удалено user_events: {report['deleted_user_events']}\n"
                f"• Удалено usage_events: {report['deleted_usage_events']}\n"
                f"• Удалено admin_alerts: {report['deleted_admin_alerts']}\n"
                f"• Удалено broadcast_attempts: {report['deleted_broadcast_attempts']}"
            ),
        ),
        reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)),
    )


@router.message(Command("admin_broadcast"))
async def cmd_admin_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    _touch_user(message.from_user.id, "cmd:admin_broadcast")
    await state.clear()
    await state.set_state(AdminBroadcastStates.waiting_segment)
    await message.answer(
        "📣 Мастер рассылки — Шаг 1/3\n"
        "Выбери сегмент аудитории:\n\n"
        f"{_broadcast_segment_overview()}\n\n"
        "Можно нажать кнопку или отправить цифру 1-4.",
        reply_markup=broadcast_segment_menu(),
    )


async def _admin_command_in_state(message: Message, state: FSMContext) -> bool:
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return False
    cmd = text.split()[0].split("@")[0].lower()
    await state.clear()
    if cmd == "/cancel":
        await cmd_cancel(message, state)
        return True
    if cmd == "/helpa":
        await cmd_helpa(message)
        return True
    if cmd == "/admin":
        await cmd_admin_panel(message, state)
        return True
    if cmd == "/admin_panel":
        await cmd_admin_panel(message, state)
        return True
    if cmd == "/admin_users":
        await cmd_admin_users(message)
        return True
    if cmd == "/admin_events":
        await cmd_admin_events(message)
        return True
    if cmd == "/admin_cost":
        await cmd_admin_cost(message)
        return True
    if cmd == "/admin_status":
        await cmd_admin_status(message)
        return True
    if cmd == "/admin_runs":
        await cmd_admin_runs(message)
        return True
    if cmd == "/admin_broadcast":
        await cmd_admin_broadcast(message, state)
        return True
    if cmd == "/admin_grant":
        await cmd_admin_grant(message, state)
        return True
    if cmd == "/admin_revoke":
        await cmd_admin_revoke(message, state)
        return True
    if cmd == "/admin_list":
        await cmd_admin_list(message)
        return True
    if cmd == "/admin_cleanup":
        await cmd_admin_cleanup(message)
        return True
    return False


@router.message(AdminBroadcastStates.waiting_segment)
async def admin_broadcast_segment(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    if await _admin_command_in_state(message, state):
        return
    raw_choice = (message.text or "").strip()
    if raw_choice == BTN_BROADCAST_CANCEL:
        await state.clear()
        await message.answer("Ок, рассылку отменил.", reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))
        return

    choice = _extract_choice_digit(raw_choice) or raw_choice
    picked = BROADCAST_SEGMENTS.get(choice)
    if not picked:
        await message.answer("Неверный выбор. Нажми кнопку сегмента или отправь цифру 1-4.")
        return
    segment, label = picked
    targets = STORAGE.get_broadcast_targets(include_blocked=False, segment=segment)
    await state.update_data(broadcast_segment=segment, broadcast_label=label, broadcast_targets=len(targets))
    await state.set_state(AdminBroadcastStates.waiting_text)
    await message.answer(
        "📣 Мастер рассылки — Шаг 2/3\n"
        f"Сегмент: {label}\n"
        f"Оценка аудитории: {len(targets)}\n\n"
        "Пришли контент одним сообщением:\n"
        "• текст\n"
        "• фото\n"
        "• фото + подпись\n"
        "• голосовое/аудио (можно с подписью)\n\n"
        "Если передумал: «🛑 Отмена»",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=BTN_BROADCAST_CANCEL)]],
            resize_keyboard=True,
            input_field_placeholder="Отправь контент рассылки.",
        ),
    )


@router.message(AdminBroadcastStates.waiting_text)
async def admin_broadcast_text(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    if await _admin_command_in_state(message, state):
        return
    if (message.text or "").strip() == BTN_BROADCAST_CANCEL:
        await state.clear()
        await message.answer("Ок, рассылку отменил.", reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))
        return
    payload = _extract_broadcast_payload(message)
    if payload is None:
        await message.answer("Неподдерживаемый формат. Нужны: текст, фото, фото+подпись, голосовое или аудио.")
        return
    data = await state.get_data()
    segment = str(data.get("broadcast_segment", "all"))
    label = str(data.get("broadcast_label", _broadcast_segment_label(segment)))
    targets = int(data.get("broadcast_targets", 0))
    preview = _broadcast_payload_preview(payload)
    await state.update_data(broadcast_payload=payload)
    await state.set_state(AdminBroadcastStates.waiting_confirm)
    await message.answer(
        section(
            "📣 Мастер рассылки — Шаг 3/3 (предпросмотр)",
            (
                f"• Сегмент: {label}\n"
                f"• Получателей: {targets}\n\n"
                f"{preview}\n\n"
                "Проверь контент и нажми:\n"
                f"• {BTN_BROADCAST_SEND}\n"
                f"• {BTN_BROADCAST_EDIT}\n"
                f"• {BTN_BROADCAST_CANCEL}"
            ),
        )
        ,
        reply_markup=broadcast_confirm_menu(),
    )


@router.message(AdminBroadcastStates.waiting_confirm)
async def admin_broadcast_confirm(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    if await _admin_command_in_state(message, state):
        return
    decision_raw = (message.text or "").strip()
    decision = decision_raw.lower()

    if decision_raw == BTN_BROADCAST_EDIT:
        await state.set_state(AdminBroadcastStates.waiting_text)
        await message.answer(
            "Пришли новый контент для этой же рассылки.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=BTN_BROADCAST_CANCEL)]],
                resize_keyboard=True,
                input_field_placeholder="Отправь новый контент.",
            ),
        )
        return

    if decision_raw == BTN_BROADCAST_CANCEL:
        await state.clear()
        await message.answer("Ок, рассылку отменил.", reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))
        return

    if decision not in {"да", "yes", "y", "send", "ok"} and decision_raw != BTN_BROADCAST_SEND:
        await message.answer(
            f"Нажми {BTN_BROADCAST_SEND}, {BTN_BROADCAST_EDIT} или {BTN_BROADCAST_CANCEL}.",
            reply_markup=broadcast_confirm_menu(),
        )
        return
    data = await state.get_data()
    payload_raw = data.get("broadcast_payload")
    payload = payload_raw if isinstance(payload_raw, dict) else {}
    segment = str(data.get("broadcast_segment", "all"))
    if not payload:
        await state.clear()
        await message.answer("Нет контента рассылки. Запусти /admin_broadcast снова.", reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)))
        return
    await message.answer("Запускаю рассылку…")
    report = await _run_broadcast(
        message.bot,
        payload=payload,
        include_blocked=False,
        segment=segment,
        created_by_tg_id=message.from_user.id,
        max_retries=BROADCAST_MAX_RETRIES,
    )
    total_targets = int(report["targets"] or 0)
    sent_count = int(report["sent"] or 0)
    blocked_count = int(report["blocked"] or 0)
    failed_count = int(report["failed"] or 0)
    sent_pct = (sent_count / total_targets * 100.0) if total_targets else 0.0
    blocked_pct = (blocked_count / total_targets * 100.0) if total_targets else 0.0
    failed_pct = (failed_count / total_targets * 100.0) if total_targets else 0.0
    top_errors_rows = STORAGE.get_broadcast_top_errors(int(report["run_id"]), limit=3)
    top_errors_text = "• нет"
    if top_errors_rows:
        top_errors_text = "\n".join(
            f"• {str(row.get('error_text') or '').strip()[:90]} ({int(row.get('cnt') or 0)})"
            for row in top_errors_rows
        )
    await state.clear()
    await message.answer(
        section(
            "📣 Рассылка завершена",
            (
                f"• Run: #{report['run_id']}\n"
                f"• Сегмент: {_broadcast_segment_label(str(report['segment']))}\n"
                f"• Целей: {report['targets']}\n"
                f"• Доставлено: {report['sent']} ({sent_pct:.1f}%)\n"
                f"• Блок: {report['blocked']} ({blocked_pct:.1f}%)\n"
                f"• Ошибки: {report['failed']} ({failed_pct:.1f}%)\n"
                f"• Повторы: {report['retries']}\n"
                f"• Топ-ошибка: {report['error_top'] or 'нет'}\n\n"
                f"Топ причин ошибок:\n{top_errors_text}\n\n"
                "Дальше:\n"
                "• /admin_runs — история запусков\n"
                "• /admin_users — статус аудитории"
            ),
        )
        ,
        reply_markup=admin_panel_menu(_is_root_admin(message.from_user.id)),
    )


@router.message(AdminPanelStates.waiting_action)
async def admin_panel_action(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    if await _admin_command_in_state(message, state):
        return
    choice = (message.text or "").strip()
    choice_digit = _extract_choice_digit(choice) or choice
    root_mode = _is_root_admin(message.from_user.id)

    if choice in {BTN_BACK_MAIN, "0"}:
        await state.clear()
        await message.answer("Вышел из админ-панели.", reply_markup=main_menu(True))
        return

    if choice in {BTN_ADMIN_USERS, "1"} or choice_digit == "1":
        await state.clear()
        await cmd_admin_users(message)
        return
    if choice in {BTN_ADMIN_EVENTS, "2"} or choice_digit == "2":
        await state.clear()
        await cmd_admin_events(message)
        return
    if choice in {BTN_ADMIN_COST, "3"} or choice_digit == "3":
        await state.clear()
        await cmd_admin_cost(message)
        return
    if choice in {BTN_ADMIN_STATUS, "4"} or choice_digit == "4":
        await state.clear()
        await cmd_admin_status(message)
        return
    if choice in {BTN_ADMIN_BROADCAST, "5"} or choice_digit == "5":
        await state.clear()
        await cmd_admin_broadcast(message, state)
        return
    if choice in {"➕ Выдать админку", "6"} or choice_digit == "6":
        if not root_mode:
            await message.answer("Только root-админ может выдавать админку.")
            return
        await state.clear()
        await cmd_admin_grant(message, state)
        return
    if choice in {"➖ Снять админку", "7"} or choice_digit == "7":
        if not root_mode:
            await message.answer("Только root-админ может снимать админку.")
            return
        await state.clear()
        await cmd_admin_revoke(message, state)
        return
    if choice in {BTN_ADMIN_ADMINS, "8"} or choice_digit == "8":
        await state.clear()
        await cmd_admin_list(message)
        return
    if choice in {"🧹 Очистка логов", "9"} or choice_digit == "9":
        if not root_mode:
            await message.answer("Только root-админ может чистить логи.")
            return
        await state.clear()
        await cmd_admin_cleanup(message)
        return
    if choice in {BTN_ADMIN_HELP, "10"} or choice_digit == "10":
        await state.clear()
        await cmd_helpa(message)
        return
    if choice in {BTN_ADMIN_RUNS}:
        await state.clear()
        await cmd_admin_runs(message)
        return
    await message.answer(
        "Не понял действие. Нажми кнопку из админ-панели.",
        reply_markup=admin_panel_menu(root_mode),
    )


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    _touch_user(message.from_user.id, "cmd:unknown", (message.text or "")[:80])
    await message.answer("Не знаю такую команду. Нажми кнопку ниже или используй /start.", reply_markup=main_menu(_is_admin(message.from_user.id)))


@router.message()
async def fallback_auto_analyze(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state:
        return

    user_text = await extract_user_text(message)
    if user_text is None:
        return

    if await _handle_crisis_message(message, state, user_text):
        return

    if _is_harm_intent(user_text):
        await message.answer(_harm_intent_reply(message.from_user.id), reply_markup=main_menu(_is_admin(message.from_user.id)))
        return

    menu_action = _resolve_menu_action(user_text)
    if menu_action == "stats":
        await cmd_stats(message)
        return
    if menu_action == "admin_panel" and _is_admin(message.from_user.id):
        await cmd_admin_panel(message, state)
        return
    if menu_action == "helpa" and _is_admin(message.from_user.id):
        await cmd_helpa(message)
        return
    if menu_action == "plan":
        await cmd_plan(message, state)
        return
    if menu_action == "audit":
        await cmd_audit(message, state)
        return
    if menu_action == "reality":
        await cmd_reality(message, state)
        return
    if menu_action == "razbor":
        await cmd_razbor(message, state)
        return

    if _is_smalltalk_or_low_signal(user_text):
        await message.answer(
            "Чтобы я помог по делу, пришли 3 строки:\n"
            "1) факт\n2) что уже сделал\n3) какой результат нужен",
            reply_markup=main_menu(_is_admin(message.from_user.id)),
        )
        return

    route_mode = _auto_route_mode_for_free_text(user_text)
    if route_mode == "plan":
        _touch_user(message.from_user.id, "auto_route", "plan")
        await _run_plan_flow(message, state, user_text)
        return
    if route_mode == "audit":
        _touch_user(message.from_user.id, "auto_route", "audit")
        await _run_audit_flow(message, state, user_text)
        return

    await send_quick_analysis(message, user_text, state)


async def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не найден в переменных окружения.")

    db_path = os.getenv("DB_PATH", "data/bot.db")

    global STORAGE
    STORAGE = Storage(db_path)

    stt_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    stt_model = os.getenv("STT_MODEL", "gpt-4o-mini-transcribe")
    stt_language = os.getenv("STT_LANGUAGE", "ru")
    stt_cost_per_min = _to_float(os.getenv("STT_COST_PER_MIN"), 0.006)

    global TRANSCRIBER
    if stt_api_key:
        TRANSCRIBER = VoiceTranscriber(
            api_key=stt_api_key,
            model=stt_model,
            language=stt_language,
            cost_per_min_usd=stt_cost_per_min,
        )

    global COACH
    analysis_model = os.getenv("ANALYSIS_MODEL", "gpt-4.1-mini")
    llm_input_cost = _to_float(os.getenv("LLM_INPUT_COST_PER_1M"), 0.4)
    llm_output_cost = _to_float(os.getenv("LLM_OUTPUT_COST_PER_1M"), 1.6)
    COACH = ProductCoach(
        api_key=stt_api_key or None,
        model=analysis_model,
        input_cost_per_1m=llm_input_cost,
        output_cost_per_1m=llm_output_cost,
    )

    global ADMIN_TG_ID, ADMIN_NOTIFY_HOURS, COST_ALERT_SPIKE_PCT, COST_ALERT_MIN_BASE_USD, COST_SPIKE_WINDOW_HOURS, ADMIN_TZ
    global BROADCAST_ENABLED, BROADCAST_HOURS, BROADCAST_TEXT, BROADCAST_INCLUDE_BLOCKED, BROADCAST_MAX_RETRIES
    ADMIN_TG_ID = _to_int(os.getenv("ADMIN_TG_ID"), 0) or None
    ADMIN_NOTIFY_HOURS = _parse_hours(os.getenv("ADMIN_NOTIFY_HOURS"), {9, 21})
    COST_ALERT_SPIKE_PCT = _to_float(os.getenv("COST_ALERT_SPIKE_PCT"), 50.0)
    COST_ALERT_MIN_BASE_USD = _to_float(os.getenv("COST_ALERT_MIN_BASE_USD"), 0.25)
    COST_SPIKE_WINDOW_HOURS = _to_int(os.getenv("COST_SPIKE_WINDOW_HOURS"), 6)
    BROADCAST_ENABLED = _to_bool(os.getenv("BROADCAST_ENABLED"), False)
    BROADCAST_HOURS = _parse_hours(os.getenv("BROADCAST_HOURS"), {10})
    BROADCAST_TEXT = (os.getenv("BROADCAST_TEXT") or "").strip()
    BROADCAST_INCLUDE_BLOCKED = _to_bool(os.getenv("BROADCAST_INCLUDE_BLOCKED"), False)
    BROADCAST_MAX_RETRIES = _to_int(os.getenv("BROADCAST_MAX_RETRIES"), 2)

    tz_name = (os.getenv("ADMIN_TIMEZONE") or "Europe/Prague").strip()
    try:
        ADMIN_TZ = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        ADMIN_TZ = timezone.utc

    if ADMIN_TG_ID is not None:
        STORAGE.grant_admin(tg_id=ADMIN_TG_ID, granted_by=ADMIN_TG_ID, note="root bootstrap")

    dp = Dispatcher()
    dp.include_router(router)

    bot = Bot(token=token)
    monitor_task = asyncio.create_task(admin_monitor_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor_task


if __name__ == "__main__":
    asyncio.run(main())
