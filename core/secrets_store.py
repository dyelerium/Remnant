"""Secrets store — AES-GCM via cryptography lib, stored in Redis."""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_SECRET_KEY_PREFIX = "remnant:secret:"


class SecretsStore:
    """
    Encrypt/decrypt secrets using AES-GCM (via cryptography.hazmat).
    Ciphertext stored in Redis under remnant:secret:{name}.

    Master key is 32 bytes, base64url-encoded, from REMNANT_MASTER_KEY env var.
    """

    def __init__(self, redis_client, master_key: Optional[str] = None) -> None:
        self.redis = redis_client.r
        raw_key = master_key or os.environ.get("REMNANT_MASTER_KEY", "")
        if not raw_key:
            logger.warning(
                "[SECRETS] No REMNANT_MASTER_KEY set — secrets will NOT be encrypted"
            )
            self._key: Optional[bytes] = None
        else:
            try:
                self._key = base64.urlsafe_b64decode(raw_key + "==")
                if len(self._key) < 16:
                    raise ValueError("Master key too short (need ≥16 bytes)")
                # Normalise to 32 bytes
                self._key = self._key[:32].ljust(32, b"\x00")
            except Exception as exc:
                logger.error("[SECRETS] Invalid master key: %s", exc)
                self._key = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_secret(self, name: str, value: str) -> None:
        """Encrypt and store a secret."""
        if not self._key:
            # Store plaintext with a warning (dev mode only)
            logger.warning("[SECRETS] Storing secret %r unencrypted (no master key)", name)
            self.redis.set(f"{_SECRET_KEY_PREFIX}{name}", value)
            return

        ciphertext = self._encrypt(value)
        self.redis.set(f"{_SECRET_KEY_PREFIX}{name}", ciphertext)
        logger.info("[SECRETS] Stored secret: %s", name)

    def get_secret(self, name: str) -> Optional[str]:
        """Retrieve and decrypt a secret."""
        raw = self.redis.get(f"{_SECRET_KEY_PREFIX}{name}")
        if raw is None:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode()

        if not self._key:
            return raw  # Plaintext fallback

        try:
            return self._decrypt(raw)
        except Exception as exc:
            logger.error("[SECRETS] Failed to decrypt secret %r: %s", name, exc)
            return None

    def delete_secret(self, name: str) -> bool:
        """Delete a secret. Returns True if it existed."""
        deleted = self.redis.delete(f"{_SECRET_KEY_PREFIX}{name}")
        return bool(deleted)

    def list_secrets(self) -> list[str]:
        """List all stored secret names."""
        keys = list(self.redis.scan_iter(f"{_SECRET_KEY_PREFIX}*"))
        prefix_len = len(_SECRET_KEY_PREFIX)
        return [
            (k.decode() if isinstance(k, bytes) else k)[prefix_len:]
            for k in keys
        ]

    # ------------------------------------------------------------------
    # Crypto helpers
    # ------------------------------------------------------------------

    def _encrypt(self, plaintext: str) -> str:
        """AES-256-GCM encrypt. Returns base64url(nonce || ciphertext || tag)."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)  # 96-bit nonce
        aesgcm = AESGCM(self._key)
        ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext.encode(), None)
        # Prepend nonce
        combined = nonce + ciphertext_and_tag
        return base64.urlsafe_b64encode(combined).decode()

    def _decrypt(self, encoded: str) -> str:
        """AES-256-GCM decrypt."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        combined = base64.urlsafe_b64decode(encoded + "==")
        nonce = combined[:12]
        ciphertext_and_tag = combined[12:]
        aesgcm = AESGCM(self._key)
        plaintext = aesgcm.decrypt(nonce, ciphertext_and_tag, None)
        return plaintext.decode()
