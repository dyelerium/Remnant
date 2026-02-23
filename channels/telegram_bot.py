"""Telegram bot channel — aiogram-based polling."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Aiogram-based Telegram bot adapter.
    Starts long-polling in background; forwards messages to Orchestrator.
    """

    name = "telegram"

    def __init__(self, bot_token: str, orchestrator, retriever, broadcast_fn=None) -> None:
        self._token = bot_token
        self._orchestrator = orchestrator
        self._retriever = retriever
        self._broadcast_fn = broadcast_fn
        self._dp = None
        self._bot = None
        self._task: Optional[asyncio.Task] = None  # reference to polling task

    async def start(self) -> None:
        """Start the Telegram bot polling loop (as background task)."""
        try:
            from aiogram import Bot, Dispatcher
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

            # Strip internal runtime markers ([GEN], [EXE], [PLAN], etc.)
            import re as _re
            response_text = "".join(response_parts)
            response_text = _re.sub(r"^\[GEN\] ", "", response_text)
            response_text = _re.sub(r"\[EXE\] \d+ tool\(s\) executed\n?", "", response_text)
            response_text = response_text.strip()

            if response_text:
                # Telegram max message length is 4096 chars
                for i in range(0, len(response_text), 4000):
                    await message.answer(response_text[i:i+4000])

            # Push conversation to open web UI tabs
            if self._broadcast_fn:
                try:
                    username = message.from_user.username or message.from_user.first_name or chat_id
                    await self._broadcast_fn({
                        "type": "tg_message",
                        "session_id": f"tg-{chat_id}",
                        "sender": username,
                        "chat_id": chat_id,
                        "user_message": text,
                        "response": response_text,
                    })
                except Exception as _exc:
                    logger.warning("[TELEGRAM] Broadcast failed: %s", _exc)

        logger.info("[TELEGRAM] Bot started")
        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        """Stop polling and close session. Cancels the underlying task if stored."""
        # Cancel the asyncio task first (fast path)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        # Also gracefully stop the dispatcher
        try:
            if self._dp:
                await asyncio.wait_for(self._dp.stop_polling(), timeout=5.0)
        except Exception:
            pass

        try:
            if self._bot:
                await self._bot.session.close()
        except Exception:
            pass

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
