#!/usr/bin/env python3
"""Sync YAML config changes → Redis (hot-reload without restart)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import click


@click.command()
@click.option("--config-dir", default="config", help="Config directory")
@click.option("--watch", is_flag=True, help="Watch for changes and re-sync automatically")
@click.option("--redis-url", default=None, help="Redis URL (overrides config)")
def main(config_dir, watch, redis_url):
    """Sync Remnant YAML config to Redis."""
    from core.config_loader import ConfigLoader
    from memory.redis_client import RemnantRedisClient

    loader = ConfigLoader(config_dir)
    config = loader.load()

    if redis_url:
        config["redis"]["host"] = redis_url

    def do_sync():
        try:
            redis = RemnantRedisClient(config)
            if not redis.ping():
                click.echo("✗ Redis not reachable", err=True)
                return

            # Store flattened config under remnant:config key
            config_json = json.dumps(config, default=str)
            redis.r.set("remnant:config:current", config_json)
            redis.r.publish("remnant:config:reload", "1")

            click.echo("✓ Config synced to Redis")
        except Exception as exc:
            click.echo(f"✗ Sync failed: {exc}", err=True)

    do_sync()

    if watch:
        click.echo("Watching for config changes… (Ctrl-C to stop)")
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class ConfigHandler(FileSystemEventHandler):
                def on_modified(self, event):
                    if event.src_path.endswith(".yaml"):
                        click.echo(f"Changed: {event.src_path}")
                        loader.reload()
                        do_sync()

            observer = Observer()
            observer.schedule(ConfigHandler(), config_dir, recursive=False)
            observer.start()
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                observer.stop()
            observer.join()
        except ImportError:
            click.echo("watchdog not installed — run: pip install watchdog")


if __name__ == "__main__":
    main()
