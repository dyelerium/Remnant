"""Telegram bot channel — aiogram-based polling."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Aiogram-based Telegram bot adapter.
    Starts long-polling in background; forwards messages to Orchestrator.
    """

    name = "telegram"

    def __init__(self, bot_token: str, orchestrator, retriever, broadcast_fn=None, redis_client=None, project_manager=None) -> None:
        self._token = bot_token
        self._orchestrator = orchestrator
        self._retriever = retriever
        self._broadcast_fn = broadcast_fn
        self._redis = redis_client
        self._project_manager = project_manager
        self._dp = None
        self._bot = None
        self._task: Optional[asyncio.Task] = None  # reference to polling task

    async def _get_active_project(self, chat_id: str) -> Optional[str]:
        """Return the active project_id for this Telegram chat, or None."""
        if not self._redis:
            return None
        try:
            key = f"remnant:tg:{chat_id}:project"
            val = await asyncio.get_event_loop().run_in_executor(
                None, self._redis.r.get, key
            )
            if val:
                return val.decode() if isinstance(val, bytes) else val
        except Exception as _e:
            logger.debug("[TELEGRAM] Failed to read active project: %s", _e)
        return None

    async def _set_active_project(self, chat_id: str, project_id: Optional[str]) -> None:
        """Set or clear the active project for this Telegram chat."""
        if not self._redis:
            return
        try:
            key = f"remnant:tg:{chat_id}:project"
            if project_id:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._redis.r.set(key, project_id)
                )
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._redis.r.delete, key
                )
        except Exception as _e:
            logger.warning("[TELEGRAM] Failed to set active project: %s", _e)

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

            # --- /clear command: wipe Redis session history ---
            if text.strip().lower() == "/clear":
                if self._redis:
                    key = f"remnant:session:history:tg-{chat_id}"
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._redis.r.delete, key
                    )
                await message.answer("✓ Chat cleared — conversation history reset.")
                return

            # --- list projects ---
            if re.search(r"\b(list|show|what|my)\b.{0,20}\bprojects?\b", text.lower()):
                try:
                    projects = []
                    if self._project_manager:
                        projects = await asyncio.get_event_loop().run_in_executor(
                            None, self._project_manager.list_all
                        )
                    if not projects:
                        await message.answer("No projects found. Create one in the Web UI first.")
                    else:
                        lines = ["📂 *Available projects:*\n"]
                        for i, p in enumerate(projects, 1):
                            name = p.get("name") or p.get("project_id", "?")
                            pid = p.get("project_id", "")
                            lines.append(f"{i}. *{name}* (`{pid}`)")
                        lines.append('\nSay _"open project <name>"_ to switch context.')
                        await message.answer("\n".join(lines), parse_mode="Markdown")
                except Exception as _exc:
                    logger.warning("[TELEGRAM] list projects failed: %s", _exc)
                    await message.answer("Could not load project list.")
                return

            # --- open project <name> ---
            _open_m = re.search(
                r"\b(?:open|switch\s+to|use|start|enter)\s+project\s+(.+)",
                text.lower()
            )
            if _open_m:
                target = _open_m.group(1).strip().rstrip(".")
                try:
                    matched_id = None
                    matched_name = None
                    if self._project_manager:
                        all_projects = await asyncio.get_event_loop().run_in_executor(
                            None, self._project_manager.list_all
                        )
                        for p in all_projects:
                            pname = (p.get("name") or p.get("project_id", "")).lower()
                            pid = p.get("project_id", "")
                            if target in pname or pname in target or target == pid.lower():
                                matched_id = pid
                                matched_name = p.get("name") or pid
                                break
                    if matched_id:
                        await self._set_active_project(chat_id, matched_id)
                        await message.answer(
                            f"✅ Switched to project *{matched_name}*.\n\n"
                            f"Project context is now active for this chat. "
                            f'Say _"switch to telegram chat"_ to return to normal mode.',
                            parse_mode="Markdown"
                        )
                    else:
                        await message.answer(
                            f'Project "{target}" not found. Say _"list projects"_ to see available ones.',
                            parse_mode="Markdown"
                        )
                except Exception as _exc:
                    logger.warning("[TELEGRAM] open project failed: %s", _exc)
                    await message.answer("Could not switch project.")
                return

            # --- close / exit project ---
            if re.search(
                r"\b(?:switch\s+to\s+telegram|exit\s+project|close\s+project|"
                r"back\s+to\s+telegram|leave\s+project|no\s+project)\b",
                text.lower()
            ):
                current = await self._get_active_project(chat_id)
                await self._set_active_project(chat_id, None)
                if current:
                    await message.answer("✅ Switched back to Telegram chat mode.")
                else:
                    await message.answer("Already in Telegram chat mode.")
                return

            try:
                chunks = self._retriever.retrieve(text)
                memory_context = self._retriever.format_for_prompt(chunks)
            except Exception:
                memory_context = ""

            # Load active project for this chat
            active_project_id = await self._get_active_project(chat_id)
            project_display_name = ""
            if active_project_id and self._project_manager:
                proj = await asyncio.get_event_loop().run_in_executor(
                    None, self._project_manager.get, active_project_id
                )
                if proj:
                    project_display_name = proj.get("name") or active_project_id
                else:
                    # Project was deleted — clear stale state
                    await self._set_active_project(chat_id, None)
                    active_project_id = None

            response_parts = []
            async for chunk in self._orchestrator.handle(
                message=text,
                session_id=f"tg-{chat_id}",
                channel="telegram",
                memory_context=memory_context,
                project_id=active_project_id or None,
            ):
                response_parts.append(chunk)

            # Strip internal runtime markers ([GEN], [EXE], [PLAN], etc.)
            response_text = "".join(response_parts)
            response_text = re.sub(r"^\[GEN\] ", "", response_text)
            response_text = re.sub(r"\[EXE\] \d+ tool\(s\) executed\n?", "", response_text)
            response_text = response_text.strip()

            # Prefix with project name when in project mode
            if active_project_id and project_display_name and response_text:
                response_text = f"[{project_display_name}]\n\n{response_text}"

            if response_text:
                # Telegram max message length is 4096 chars
                for i in range(0, len(response_text), 4000):
                    await message.answer(response_text[i:i+4000])

            # Push conversation to open web UI tabs
            username = message.from_user.username or message.from_user.first_name or chat_id
            push_payload = {
                "session_id": f"tg-{chat_id}",
                "sender": username,
                "chat_id": chat_id,
                "user_message": text,
                "response": response_text,
                "project_id": active_project_id or None,
            }

            # Persist to Redis inbox so reconnecting clients can fetch it
            # Also store primary chat_id so web UI can mirror back to Telegram
            if self._redis:
                try:
                    import json as _json
                    entry = _json.dumps(push_payload)
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: (
                            self._redis.r.lpush("remnant:telegram_inbox", entry),
                            self._redis.r.ltrim("remnant:telegram_inbox", 0, 99),
                            self._redis.r.set("remnant:telegram_primary_chat", chat_id),
                        ),
                    )
                except Exception as _exc:
                    logger.warning("[TELEGRAM] Redis store failed: %s", _exc)

            if self._broadcast_fn:
                try:
                    await self._broadcast_fn({"type": "tg_message", **push_payload})
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
