from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from aiogram.types import Message
from openai import AsyncOpenAI


@dataclass
class VoiceTranscriber:
    api_key: str
    model: str = "gpt-4o-mini-transcribe"
    language: str = "ru"
    cost_per_min_usd: float = 0.006

    def __post_init__(self) -> None:
        self.client = AsyncOpenAI(api_key=self.api_key)

    async def transcribe_message(self, message: Message) -> tuple[str, dict]:
        if message.voice:
            file_id = message.voice.file_id
            filename = "voice.ogg"
            mime_type = "audio/ogg"
            duration_sec = float(message.voice.duration or 0)
        elif message.audio:
            file_id = message.audio.file_id
            filename = message.audio.file_name or "audio.mp3"
            mime_type = message.audio.mime_type or "audio/mpeg"
            duration_sec = float(message.audio.duration or 0)
        else:
            raise ValueError("Message does not contain voice/audio")

        tg_file = await message.bot.get_file(file_id)
        buffer = BytesIO()
        downloaded = await message.bot.download_file(tg_file.file_path, destination=buffer)

        if isinstance(downloaded, BytesIO):
            audio_io = downloaded
        else:
            audio_io = buffer

        audio_io.seek(0)
        audio_bytes = audio_io.read()
        if not audio_bytes:
            raise RuntimeError("Downloaded audio is empty")

        try:
            transcript = await self.client.audio.transcriptions.create(
                model=self.model,
                file=(filename, audio_bytes, mime_type),
                language=self.language,
            )
        except Exception:
            # Fallback: some SDK versions prefer a file-like object with .name
            fallback_file = BytesIO(audio_bytes)
            fallback_file.name = filename
            transcript = await self.client.audio.transcriptions.create(
                model=self.model,
                file=fallback_file,
                language=self.language,
            )

        text = getattr(transcript, "text", "")
        cost_usd = (duration_sec / 60.0) * float(self.cost_per_min_usd)
        meta = {
            "source": "stt",
            "model": self.model,
            "input_tokens": 0,
            "output_tokens": 0,
            "audio_seconds": duration_sec,
            "cost_usd": round(cost_usd, 8),
        }
        return text.strip(), meta
