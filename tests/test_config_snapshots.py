"""
Tests: ConfigTool._snapshot_configs() and Scheduler._run_config_snapshot().

Covers:
  - Creates a .tar.gz in snapshots/ directory
  - Archive contains all *.yaml files from config_dir
  - Prunes to at most 20 snapshots
  - Filename follows config-{timestamp}.tar.gz pattern
  - Works when config_dir is empty (no yaml files)
  - Handles pre-existing snapshots correctly
  - Scheduler job wraps the same logic and logs correctly
"""
from __future__ import annotations

import asyncio
import tarfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _make_config_tool(tmp_path: Path):
    """Return a ConfigTool with a real tmp config_dir."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # Write some yaml files
    (config_dir / "remnant.yaml").write_text("project_name: test\n")
    (config_dir / "security.yaml").write_text("enabled: true\n")
    (config_dir / "llm_providers.yaml").write_text("providers: {}\n")

    from tools.config_tool import ConfigTool
    registry = MagicMock()
    registry.list_models.return_value = []
    return ConfigTool(registry, config_dir), config_dir, tmp_path


# ===========================================================================
# ConfigTool._snapshot_configs
# ===========================================================================

class TestSnapshotConfigs:
    def test_creates_tarball_in_snapshots_dir(self, tmp_path):
        tool, config_dir, root = _make_config_tool(tmp_path)
        tool._snapshot_configs()

        snap_dir = root / "snapshots"
        tarballs = list(snap_dir.glob("config-*.tar.gz"))
        assert len(tarballs) == 1

    def test_tarball_contains_yaml_files(self, tmp_path):
        tool, config_dir, root = _make_config_tool(tmp_path)
        tool._snapshot_configs()

        snap_dir = root / "snapshots"
        tarball = next(snap_dir.glob("config-*.tar.gz"))
        with tarfile.open(tarball, "r:gz") as tf:
            names = tf.getnames()
        assert "remnant.yaml" in names
        assert "security.yaml" in names
        assert "llm_providers.yaml" in names

    def test_filename_matches_pattern(self, tmp_path):
        tool, config_dir, root = _make_config_tool(tmp_path)
        before = int(time.time())
        tool._snapshot_configs()
        after = int(time.time())

        snap_dir = root / "snapshots"
        tarball = next(snap_dir.glob("config-*.tar.gz"))
        stem = tarball.stem  # "config-1234567890.tar" without .gz … wait, stem on config-1234567890.tar.gz is config-1234567890.tar
        # Use name: "config-1234567890.tar.gz"
        name = tarball.name
        assert name.startswith("config-")
        assert name.endswith(".tar.gz")
        # Extract timestamp
        ts = int(name[len("config-"):-len(".tar.gz")])
        assert before <= ts <= after + 1

    def test_prunes_to_20_snapshots(self, tmp_path):
        tool, config_dir, root = _make_config_tool(tmp_path)
        snap_dir = root / "snapshots"
        snap_dir.mkdir(exist_ok=True)

        # Pre-create 22 old dummy snapshots
        for i in range(22):
            (snap_dir / f"config-{1000 + i}.tar.gz").write_bytes(b"dummy")

        tool._snapshot_configs()

        remaining = list(snap_dir.glob("config-*.tar.gz"))
        assert len(remaining) <= 20

    def test_prunes_oldest_first(self, tmp_path):
        tool, config_dir, root = _make_config_tool(tmp_path)
        snap_dir = root / "snapshots"
        snap_dir.mkdir(exist_ok=True)

        # Use realistic Unix timestamps (old ones start at 1_700_000_000)
        # so they sort correctly alongside the new snapshot (~1.77B)
        base_ts = 1_700_000_000
        for i in range(20):
            (snap_dir / f"config-{base_ts + i}.tar.gz").write_bytes(b"old")

        tool._snapshot_configs()

        remaining = list(snap_dir.glob("config-*.tar.gz"))
        assert len(remaining) == 20
        # The oldest snapshot (base_ts + 0) should be gone
        assert not (snap_dir / f"config-{base_ts}.tar.gz").exists()

    def test_creates_snapshots_dir_if_absent(self, tmp_path):
        tool, config_dir, root = _make_config_tool(tmp_path)
        snap_dir = root / "snapshots"
        assert not snap_dir.exists()

        tool._snapshot_configs()
        assert snap_dir.exists()

    def test_empty_config_dir_creates_empty_tarball(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # No yaml files

        from tools.config_tool import ConfigTool
        registry = MagicMock()
        registry.list_models.return_value = []
        tool = ConfigTool(registry, config_dir)
        tool._snapshot_configs()

        snap_dir = tmp_path / "snapshots"
        tarballs = list(snap_dir.glob("config-*.tar.gz"))
        assert len(tarballs) == 1
        # Tarball should be readable but empty
        with tarfile.open(tarballs[0], "r:gz") as tf:
            assert tf.getnames() == []

    def test_second_snapshot_keeps_both_up_to_20(self, tmp_path):
        tool, config_dir, root = _make_config_tool(tmp_path)
        snap_dir = root / "snapshots"

        # Sequential timestamps: tarfile may also call time.time() internally,
        # so provide 100 unique values — the filenames will use the first value
        # of each _snapshot_configs call and will differ from each other.
        with patch("time.time", side_effect=range(1_770_000_000, 1_770_000_100)):
            tool._snapshot_configs()
            tool._snapshot_configs()

        tarballs = list(snap_dir.glob("config-*.tar.gz"))
        assert len(tarballs) == 2

    def test_non_yaml_files_not_included(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "remnant.yaml").write_text("x: 1\n")
        (config_dir / "notes.txt").write_text("should not be included")

        from tools.config_tool import ConfigTool
        registry = MagicMock()
        registry.list_models.return_value = []
        tool = ConfigTool(registry, config_dir)
        tool._snapshot_configs()

        snap_dir = tmp_path / "snapshots"
        tarball = next(snap_dir.glob("config-*.tar.gz"))
        with tarfile.open(tarball, "r:gz") as tf:
            names = tf.getnames()
        assert "remnant.yaml" in names
        assert "notes.txt" not in names


# ===========================================================================
# Scheduler._run_config_snapshot  (async job)
# ===========================================================================

class TestSchedulerConfigSnapshot:
    def _make_scheduler(self, tmp_path: Path):
        """Create a Scheduler pointed at tmp_path/config."""
        from core.scheduling import Scheduler

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "agents.yaml").write_text("agents: {}\n")

        sched = Scheduler(
            memory_compactor=MagicMock(),
            curator_agent=MagicMock(),
            redis_client=MagicMock(),
            config={},
        )
        # Override to use our tmp dir
        sched._config_dir = config_dir
        return sched, config_dir, tmp_path

    def test_async_job_creates_snapshot(self, tmp_path):
        sched, config_dir, root = self._make_scheduler(tmp_path)

        asyncio.get_event_loop().run_until_complete(sched._run_config_snapshot())

        snap_dir = root / "snapshots"
        tarballs = list(snap_dir.glob("config-*.tar.gz"))
        assert len(tarballs) == 1

    def test_async_job_tarball_readable(self, tmp_path):
        sched, config_dir, root = self._make_scheduler(tmp_path)
        asyncio.get_event_loop().run_until_complete(sched._run_config_snapshot())

        snap_dir = root / "snapshots"
        tarball = next(snap_dir.glob("config-*.tar.gz"))
        with tarfile.open(tarball, "r:gz") as tf:
            names = tf.getnames()
        assert "agents.yaml" in names

    def test_async_job_handles_exception_gracefully(self, tmp_path):
        sched, config_dir, root = self._make_scheduler(tmp_path)
        # Make config_dir an unreadable path to force an exception
        with patch("tarfile.open", side_effect=OSError("disk full")):
            # Should not raise — just log the error
            asyncio.get_event_loop().run_until_complete(sched._run_config_snapshot())
        # No tarball created
        snap_dir = root / "snapshots"
        assert not snap_dir.exists() or len(list(snap_dir.glob("*.tar.gz"))) == 0

    def test_async_job_prunes_to_20(self, tmp_path):
        sched, config_dir, root = self._make_scheduler(tmp_path)
        snap_dir = root / "snapshots"
        snap_dir.mkdir(exist_ok=True)

        # Pre-create 25 dummy snapshots
        for i in range(25):
            (snap_dir / f"config-{3000 + i}.tar.gz").write_bytes(b"dummy")

        asyncio.get_event_loop().run_until_complete(sched._run_config_snapshot())

        remaining = list(snap_dir.glob("config-*.tar.gz"))
        assert len(remaining) <= 20
