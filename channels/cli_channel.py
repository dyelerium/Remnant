"""CLI channel — local interactive interface for debugging."""
from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


class CLIChannel:
    """Interactive CLI loop for debugging Remnant locally."""

    name = "cli"

    def __init__(self, orchestrator, retriever) -> None:
        self._orchestrator = orchestrator
        self._retriever = retriever

    async def run_interactive(self, project_id: str = None) -> None:
        """Start interactive CLI loop."""
        print("Remnant CLI — type 'exit' to quit, 'project <id>' to switch project")
        print(f"Project: {project_id or 'global'}\n")

        session_id = "cli-session"

        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("You: ")
                )
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not line.strip():
                continue
            if line.strip().lower() == "exit":
                print("Goodbye!")
                break
            if line.strip().lower().startswith("project "):
                project_id = line.strip().split(" ", 1)[1].strip()
                print(f"Switched to project: {project_id}")
                continue

            try:
                chunks = self._retriever.retrieve(line, project_id=project_id)
                memory_context = self._retriever.format_for_prompt(chunks)
            except Exception:
                memory_context = ""

            print("Remnant: ", end="", flush=True)
            async for chunk in self._orchestrator.handle(
                message=line,
                project_id=project_id,
                session_id=session_id,
                channel="cli",
                memory_context=memory_context,
            ):
                print(chunk, end="", flush=True)
            print()

    async def send_message(self, recipient: str, message: str, **kwargs) -> None:
        print(f"[CLI → {recipient}] {message}")
