"""Auto-compute version from git describe or a baked-in VERSION file."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _compute() -> str:
    # 1. Docker bakes a VERSION file at image build time — use it when present.
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        v = version_file.read_text().strip()
        if v:
            return v

    # 2. Fall back to git describe (local dev, outside Docker).
    try:
        desc = subprocess.check_output(
            ["git", "describe", "--tags", "--long"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # Format: v0.0-{N}-g{hash}  →  v0.{N}
        parts = desc.rsplit("-", 2)
        if len(parts) == 3:
            commits = int(parts[1])
            return f"v0.{commits}"
    except Exception:
        pass

    return "v0.0"


VERSION: str = _compute()
