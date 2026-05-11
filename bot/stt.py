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

    def __post_init__(self) -> None:
        self.client = AsyncOpenAI(api_key=self.api_key)

    async def transcribe_message(self, message: Message) -> str:
        if message.voice:
            file_id = message.voice.file_id
            filename = "voice.ogg"
            mime_type = "audio/ogg"
        elif message.audio:
            file_id = message.audio.file_id
            filename = message.audio.file_name or "audio.mp3"
            mime_type = message.audio.mime_type or "audio/mpeg"
        else:
            raise ValueError("Message does not contain voice/audio")

        tg_file = await message.bot.get_file(file_id)
        audio_stream = await message.bot.download_file(tg_file.file_path)
        if not isinstance(audio_stream, BytesIO):
            raise RuntimeError("Failed to download audio from Telegram")

        payload = (filename, audio_stream.getvalue(), mime_type)
        transcript = await self.client.audio.transcriptions.create(
            model=self.model,
            file=payload,
            language=self.language,
        )

        text = getattr(transcript, "text", "")
        return text.strip()
