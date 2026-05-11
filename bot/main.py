from __future__ import annotations

import asyncio
import os
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv

from bot.cognitive import analyze_case
from bot.db import Storage
from bot.states import CheckinStates, RazborStates, SosStates
from bot.stt import VoiceTranscriber


router = Router()
STORAGE: Storage
TRANSCRIBER: VoiceTranscriber | None = None


RUS_NUMBERS = {
    "ноль": 0,
    "нуль": 0,
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/razbor"), KeyboardButton(text="/sos")],
            [KeyboardButton(text="/checkin"), KeyboardButton(text="/stats")],
        ],
        resize_keyboard=True,
    )


async def extract_user_text(message: Message) -> str | None:
    if message.text and message.text.strip():
        return message.text.strip()

    if message.voice or message.audio:
        if TRANSCRIBER is None:
            await message.answer(
                "Голосовые доступны после подключения OPENAI_API_KEY в .env. Пока отправь текстом."
            )
            return None

        await message.answer("Принял голосовое, расшифровываю...")
        try:
            text = await TRANSCRIBER.transcribe_message(message)
        except Exception:
            await message.answer("Не удалось распознать голосовое. Попробуй еще раз или отправь текст.")
            return None

        if not text:
            await message.answer("Не удалось получить текст из аудио. Попробуй запись четче.")
            return None

        await message.answer(f"Распознанный текст: {text}")
        return text

    await message.answer("Нужен текст или голосовое сообщение.")
    return None


def parse_score(raw: str | None) -> int | None:
    if raw is None:
        return None

    text = raw.strip().lower()
    if not text:
        return None

    if text.isdigit():
        value = int(text)
        if 0 <= value <= 10:
            return value

    if text in RUS_NUMBERS:
        return RUS_NUMBERS[text]

    match = re.search(r"\b(10|[0-9])\b", text)
    if match:
        value = int(match.group(1))
        if 0 <= value <= 10:
            return value

    return None


def format_list(items: list[str]) -> str:
    if not items:
        return "- нет данных"
    return "\n".join(f"- {item}" for item in items)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    STORAGE.get_stats(message.from_user.id)
    await message.answer(
        "Я бот по когнитивным искажениям. Работаю в честном режиме: без подлизывания, с опорой на факты и прямой вывод.\n\n"
        "Можно писать текстом или голосом.\n\n"
        "Команды:\n"
        "/razbor — полный разбор за 3-5 минут\n"
        "/sos — короткий протокол на 90 секунд\n"
        "/checkin — ежедневный чек-ин\n"
        "/stats — личная статистика\n"
        "/cancel — отменить текущий сценарий",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Сценарий остановлен. Выбери следующий режим.", reply_markup=main_menu())


@router.message(Command("razbor"))
async def cmd_razbor(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RazborStates.situation)
    await message.answer("Опиши ситуацию одним-двумя предложениями.")


@router.message(RazborStates.situation)
async def razbor_situation(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return

    await state.update_data(situation=user_text)
    await state.set_state(RazborStates.thought)
    await message.answer("Какая автоматическая мысль возникла в этот момент?")


@router.message(RazborStates.thought)
async def razbor_thought(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return

    await state.update_data(thought=user_text)
    await state.set_state(RazborStates.emotion_before)
    await message.answer("Оцени интенсивность эмоции сейчас от 0 до 10 (цифрой).")


@router.message(RazborStates.emotion_before)
async def razbor_emotion_before(message: Message, state: FSMContext) -> None:
    raw = await extract_user_text(message)
    if raw is None:
        return

    score = parse_score(raw)
    if score is None:
        await message.answer("Нужна оценка от 0 до 10.")
        return

    data = await state.get_data()
    situation = data["situation"]
    thought = data["thought"]

    result = analyze_case(situation=situation, thought=thought)
    distortions = result["distortions"]
    distortion_titles = [d.title for d in distortions]
    questions = "\n".join(f"- {q}" for q in result["questions"])

    await state.update_data(
        emotion_before=score,
        distortions=distortion_titles,
        reframe=result["reframe"],
        action=result["action"],
    )

    await state.set_state(RazborStates.emotion_after)
    await message.answer(
        "Объективный разбор:\n"
        f"Факты:\n{format_list(result['facts'])}\n\n"
        f"Интерпретации/догадки:\n{format_list(result['interpretations'])}\n\n"
        f"Искажения: {', '.join(distortion_titles)}\n"
        f"Прямой вывод: {result['hard_truth']}\n\n"
        f"Проверочные вопросы:\n{questions}\n\n"
        f"Рабочая формулировка:\n{result['reframe']}\n\n"
        f"Следующий шаг на 10-30 минут:\n{result['action']}\n\n"
        "После шага оцени эмоцию снова от 0 до 10.",
    )


@router.message(RazborStates.emotion_after)
async def razbor_emotion_after(message: Message, state: FSMContext) -> None:
    raw = await extract_user_text(message)
    if raw is None:
        return

    score = parse_score(raw)
    if score is None:
        await message.answer("Нужна оценка от 0 до 10.")
        return

    data = await state.get_data()
    STORAGE.save_session(
        tg_id=message.from_user.id,
        mode="razbor",
        situation=data["situation"],
        thought=data["thought"],
        distortions=data["distortions"],
        reframe=data["reframe"],
        action=data["action"],
        emotion_before=data["emotion_before"],
        emotion_after=score,
    )
    await state.clear()

    delta = data["emotion_before"] - score
    await message.answer(
        f"Сохранено. Изменение эмоции: {data['emotion_before']} -> {score} (Δ {delta}).\n"
        "Если хочешь, повторим разбор на следующей ситуации.",
        reply_markup=main_menu(),
    )


@router.message(Command("sos"))
async def cmd_sos(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SosStates.trigger)
    await message.answer("Что тебя сейчас зацепило? Опиши коротко.")


@router.message(SosStates.trigger)
async def sos_trigger(message: Message, state: FSMContext) -> None:
    user_text = await extract_user_text(message)
    if user_text is None:
        return

    await state.update_data(trigger=user_text)
    await state.set_state(SosStates.emotion_before)
    await message.answer("Накал эмоции сейчас (0-10)?")


@router.message(SosStates.emotion_before)
async def sos_emotion_before(message: Message, state: FSMContext) -> None:
    raw = await extract_user_text(message)
    if raw is None:
        return

    score = parse_score(raw)
    if score is None:
        await message.answer("Нужна оценка от 0 до 10.")
        return

    await state.update_data(emotion_before=score)
    await state.set_state(SosStates.thought)
    await message.answer("Какая мысль крутится сильнее всего прямо сейчас?")


@router.message(SosStates.thought)
async def sos_thought(message: Message, state: FSMContext) -> None:
    thought = await extract_user_text(message)
    if thought is None:
        return

    data = await state.get_data()
    trigger = data["trigger"]

    result = analyze_case(situation=trigger, thought=thought)
    distortion_titles = [d.title for d in result["distortions"]]

    await state.update_data(
        thought=thought,
        distortions=distortion_titles,
        reframe=result["reframe"],
        action=result["action"],
    )
    await state.set_state(SosStates.emotion_after)

    await message.answer(
        "SOS протокол (90 секунд):\n"
        "1) Стоп: 3 медленных выдоха (выдох длиннее вдоха).\n"
        "2) Назови эмоцию одним словом.\n"
        "3) Отдели факт от догадки.\n"
        f"4) Искажения: {', '.join(distortion_titles)}\n"
        f"5) Прямой вывод: {result['hard_truth']}\n"
        f"6) Рабочая мысль: {result['reframe']}\n"
        f"7) Микрошаг: {result['action']}\n\n"
        "Теперь снова оцени накал эмоции от 0 до 10.",
    )


@router.message(SosStates.emotion_after)
async def sos_emotion_after(message: Message, state: FSMContext) -> None:
    raw = await extract_user_text(message)
    if raw is None:
        return

    score = parse_score(raw)
    if score is None:
        await message.answer("Нужна оценка от 0 до 10.")
        return

    data = await state.get_data()
    STORAGE.save_session(
        tg_id=message.from_user.id,
        mode="sos",
        situation=data["trigger"],
        thought=data["thought"],
        distortions=data["distortions"],
        reframe=data["reframe"],
        action=data["action"],
        emotion_before=data["emotion_before"],
        emotion_after=score,
    )
    await state.clear()

    delta = data["emotion_before"] - score
    await message.answer(
        f"Готово. Накал: {data['emotion_before']} -> {score} (Δ {delta}).",
        reply_markup=main_menu(),
    )


@router.message(Command("checkin"))
async def cmd_checkin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CheckinStates.mood)
    await message.answer("Чек-ин: настроение сейчас 0-10?")


@router.message(CheckinStates.mood)
async def checkin_mood(message: Message, state: FSMContext) -> None:
    raw = await extract_user_text(message)
    if raw is None:
        return

    score = parse_score(raw)
    if score is None:
        await message.answer("Нужна оценка от 0 до 10.")
        return
    await state.update_data(mood=score)
    await state.set_state(CheckinStates.stress)
    await message.answer("Стресс сейчас 0-10?")


@router.message(CheckinStates.stress)
async def checkin_stress(message: Message, state: FSMContext) -> None:
    raw = await extract_user_text(message)
    if raw is None:
        return

    score = parse_score(raw)
    if score is None:
        await message.answer("Нужна оценка от 0 до 10.")
        return
    await state.update_data(stress=score)
    await state.set_state(CheckinStates.energy)
    await message.answer("Энергия сейчас 0-10?")


@router.message(CheckinStates.energy)
async def checkin_energy(message: Message, state: FSMContext) -> None:
    raw = await extract_user_text(message)
    if raw is None:
        return

    score = parse_score(raw)
    if score is None:
        await message.answer("Нужна оценка от 0 до 10.")
        return
    await state.update_data(energy=score)
    await state.set_state(CheckinStates.note)
    await message.answer("Короткая заметка о дне (или '-' если без комментария).")


@router.message(CheckinStates.note)
async def checkin_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user_text = await extract_user_text(message)
    if user_text is None:
        return

    note = user_text.strip()
    if note == "-":
        note = ""

    STORAGE.save_checkin(
        tg_id=message.from_user.id,
        mood=data["mood"],
        stress=data["stress"],
        energy=data["energy"],
        note=note,
    )
    await state.clear()
    await message.answer("Чек-ин сохранен.", reply_markup=main_menu())


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    stats = STORAGE.get_stats(message.from_user.id)

    top = "\n".join(f"- {name}: {count}" for name, count in stats["top_distortions"]) or "- пока нет данных"

    await message.answer(
        "Твоя статистика:\n"
        f"Сессий разбора/SOS: {stats['sessions_total']}\n"
        f"Средняя эмоция до: {stats['avg_before']}\n"
        f"Средняя эмоция после: {stats['avg_after']}\n"
        f"Среднее снижение: {stats['avg_delta']}\n\n"
        f"Чек-инов: {stats['checkins_total']}\n"
        f"Среднее настроение: {stats['avg_mood']}\n"
        f"Средний стресс: {stats['avg_stress']}\n"
        f"Средняя энергия: {stats['avg_energy']}\n\n"
        f"Топ искажений:\n{top}"
    )


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer("Неизвестная команда. Используй /start для меню.")


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

    global TRANSCRIBER
    if stt_api_key:
        TRANSCRIBER = VoiceTranscriber(api_key=stt_api_key, model=stt_model, language=stt_language)

    dp = Dispatcher()
    dp.include_router(router)

    bot = Bot(token=token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
