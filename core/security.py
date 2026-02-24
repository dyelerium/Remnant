"""
Unified Security Manager — injection detection, prompt redaction,
tool policies, memory sanitisation.

All security checks in one place. Called by:
  - memory.memory_recorder.MemoryRecorder.record()
  - core.runtime (before tool execution)
  - core.llm_client (prompt redaction before LLM calls)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SecurityManager:
    """Unified security enforcement for the Remnant framework."""

    def __init__(self, redis_client, config: dict, audit_logger=None) -> None:
        self.redis = redis_client
        self.config = config
        self._audit = audit_logger

        sec = config.get("injection_detection", {})
        self._enabled: bool = sec.get("enabled", True)

        # Compile blocked patterns (literal strings, not regexes)
        self._blocked_patterns: list[re.Pattern] = [
            re.compile(re.escape(p), re.IGNORECASE)
            for p in sec.get("blocked_patterns", [])
        ]
        self._suspicious_keywords: list[str] = sec.get("suspicious_keywords", [])

        # Secret detection patterns
        self._secret_patterns: list[re.Pattern] = [
            re.compile(p) for p in sec.get("secret_patterns", [])
        ]

        # Redaction rules
        redact = config.get("redaction", {})
        self._redact_enabled: bool = redact.get("enabled", True)
        self._redact_rules: list[tuple[re.Pattern, str]] = []
        for rule in redact.get("patterns", []):
            flags = 0
            if "IGNORECASE" in rule.get("flags", ""):
                flags |= re.IGNORECASE
            self._redact_rules.append(
                (re.compile(rule["pattern"], flags), rule["replacement"])
            )

        # Tool policies
        tool_pol = config.get("tool_policies", {})
        self._default_policy: str = tool_pol.get("default_policy", "deny")
        self._global_allowed: list[str] = tool_pol.get("global_allowed", [])
        self._project_overrides: dict = tool_pol.get("project_overrides", {})
        self._require_confirmation: list[str] = tool_pol.get("require_confirmation", [])

        # Logging
        log_cfg = config.get("logging", {})
        self._log_blocked: bool = log_cfg.get("log_blocked_attempts", True)
        self._blocked_ttl: int = log_cfg.get("blocked_ttl_days", 30) * 86400
        self._blocked_prefix: str = log_cfg.get("redis_prefix", "security:blocked")

    # ------------------------------------------------------------------
    # Injection Detection
    # ------------------------------------------------------------------

    def is_safe(self, text: str) -> tuple[bool, str]:
        """
        Check if text is safe to store/process.

        Returns:
            (True, "") if safe.
            (False, reason) if unsafe.
        """
        if not self._enabled:
            return True, ""

        for pattern in self._blocked_patterns:
            if pattern.search(text):
                return False, f"blocked_pattern:{pattern.pattern[:40]}"

        suspicious_count = sum(
            1 for kw in self._suspicious_keywords if kw.lower() in text.lower()
        )
        if suspicious_count >= 3:
            return False, f"suspicious_keyword_concentration:{suspicious_count}"

        for pattern in self._secret_patterns:
            if pattern.search(text):
                return False, "secret_pattern_detected"

        return True, ""

    # ------------------------------------------------------------------
    # Prompt Redaction
    # ------------------------------------------------------------------

    def redact_prompt(self, text: str) -> str:
        """Mask API keys, tokens, passwords before LLM calls."""
        if not self._redact_enabled:
            return text
        for pattern, replacement in self._redact_rules:
            text = pattern.sub(replacement, text)
        return text

    # ------------------------------------------------------------------
    # Tool Policies
    # ------------------------------------------------------------------

    def check_tool_policy(
        self,
        tool_name: str,
        project_id: Optional[str] = None,
    ) -> bool:
        """
        Check whether a tool is allowed for the given project.

        Returns True if allowed, False if denied.
        """
        # Global allow list
        if tool_name in self._global_allowed:
            return True

        # Project-specific override
        if project_id and project_id in self._project_overrides:
            override = self._project_overrides[project_id]
            if tool_name in override.get("denied", []):
                return False
            if tool_name in override.get("allowed", []):
                return True

        # Default policy
        return self._default_policy == "allow"

    def requires_confirmation(self, tool_name: str) -> bool:
        """Return True if the tool requires explicit user confirmation."""
        return tool_name in self._require_confirmation

    # ------------------------------------------------------------------
    # Memory Sanitisation
    # ------------------------------------------------------------------

    def sanitise_memory(self, chunks: list[dict]) -> list[dict]:
        """Filter out chunks that fail the safety check before prompt injection."""
        safe_chunks = []
        for chunk in chunks:
            text = chunk.get("text_excerpt", "")
            ok, reason = self.is_safe(text)
            if ok:
                safe_chunks.append(chunk)
            else:
                logger.warning(
                    "[SECURITY] Filtered unsafe memory chunk id=%s reason=%s",
                    chunk.get("id", "?"),
                    reason,
                )
        return safe_chunks

    # ------------------------------------------------------------------
    # Blocked attempt logging
    # ------------------------------------------------------------------

    def log_blocked(self, text: str, reason: str, session_id: str = "", channel: str = "") -> None:
        """Log a blocked attempt to Redis (security log + audit log)."""
        if self._log_blocked and self.redis:
            key = f"{self._blocked_prefix}:{int(time.time())}:{hash(text[:100]) & 0xFFFFFFFF}"
            try:
                self.redis.r.hset(
                    key,
                    mapping={
                        "text": text[:500],
                        "reason": reason,
                        "timestamp": int(time.time()),
                    },
                )
                self.redis.r.expire(key, self._blocked_ttl)
            except Exception as exc:
                logger.error("[SECURITY] Failed to log blocked attempt: %s", exc)

        if self._audit:
            self._audit.log_security(
                reason=reason,
                message_preview=text,
                session_id=session_id,
                channel=channel,
            )

    def reload(self, config: dict) -> None:
        """Hot-reload all patterns and policies without app restart."""
        self.__init__(self.redis, config, self._audit)

    def get_blocked_log(self, limit: int = 50) -> list[dict]:
        """Retrieve recent blocked attempts from Redis."""
        pattern = f"{self._blocked_prefix}:*"
        keys = list(self.redis.r.scan_iter(pattern, count=200))
        keys = sorted(keys, reverse=True)[:limit]

        results = []
        for key in keys:
            raw = self.redis.r.hgetall(key)
            entry = {
                k.decode() if isinstance(k, bytes) else k: (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in raw.items()
            }
            results.append(entry)
        return results
