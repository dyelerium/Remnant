"""Telegram bot channel — aiogram-based polling."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Aiogram-based Telegram bot adapter.
    Starts long-polling in background; forwards messages to Orchestrator.
    """

    name = "telegram"

    def __init__(self, bot_token: str, orchestrator, retriever) -> None:
        self._token = bot_token
        self._orchestrator = orchestrator
        self._retriever = retriever
        self._dp = None
        self._bot = None

    async def start(self) -> None:
        """Start the Telegram bot polling loop (as background task)."""
        try:
            from aiogram import Bot, Dispatcher
            from aiogram.filters import Command
            from aiogram import types
        except ImportError:
            logger.warning("[TELEGRAM] aiogram not installed — Telegram channel disabled")
            return

        self._bot = Bot(token=self._token)
        self._dp = Dispatcher()

        @self._dp.message()
        async def handle_message(message: types.Message):
            text = message.text or ""
            chat_id = str(message.chat.id)

            try:
                chunks = self._retriever.retrieve(text)
                memory_context = self._retriever.format_for_prompt(chunks)
            except Exception:
                memory_context = ""

            response_parts = []
            async for chunk in self._orchestrator.handle(
                message=text,
                session_id=f"tg-{chat_id}",
                channel="telegram",
                memory_context=memory_context,
            ):
                response_parts.append(chunk)

            response_text = "".join(response_parts).strip()
            if response_text:
                # Telegram max message length is 4096 chars
                for i in range(0, len(response_text), 4000):
                    await message.answer(response_text[i:i+4000])

        logger.info("[TELEGRAM] Bot started")
        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        if self._dp:
            await self._dp.stop_polling()
        if self._bot:
            await self._bot.session.close()

    async def send_message(
        self,
        chat_id: str,
        message: str,
        metadata: Optional[dict] = None,
    ) -> None:
        if not self._bot:
            logger.warning("[TELEGRAM] Bot not started")
            return
        await self._bot.send_message(chat_id=int(chat_id), text=message)
