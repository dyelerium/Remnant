#!/usr/bin/env python3
"""CLI version of the planning wizard — create a new Remnant project interactively."""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
import yaml


@click.command()
@click.option("--name", prompt="Project name", help="Project name")
@click.option("--description", prompt="Project description", default="", help="Brief description")
@click.option(
    "--template",
    type=click.Choice(["default", "dev", "research"]),
    default="default",
    prompt="Template",
    help="Project template",
)
@click.option("--working-dir", default="", prompt="Working directory (blank for default)")
@click.option("--budget", default=2.0, prompt="Daily budget (USD)", type=float)
@click.option("--enable-mcp/--no-mcp", default=False, prompt="Enable Claude Code MCP integration?")
@click.option("--dry-run", is_flag=True, help="Print project definition without creating")
def main(name, description, template, working_dir, budget, enable_mcp, dry_run):
    """Bootstrap a new Remnant project."""
    from core.config_loader import get_config

    config = get_config().load()

    project_id = name.lower().replace(" ", "_")[:64]
    project = {
        "project_id": project_id,
        "name": name,
        "description": description,
        "template": template,
        "working_dir": working_dir or f"./workspace/{project_id}",
        "budget_usd_daily": budget,
        "enable_mcp": enable_mcp,
        "status": "active",
    }

    if dry_run:
        click.echo("\n=== Project Definition (dry run) ===")
        click.echo(yaml.dump(project, default_flow_style=False))
        return

    try:
        from memory.redis_client import RemnantRedisClient
        from core.project_manager import ProjectManager

        redis = RemnantRedisClient(config)
        pm = ProjectManager(redis, config)
        created = pm.create(project)

        click.echo(f"\n✓ Project created: {created['project_id']}")
        click.echo(f"  Name:        {created['name']}")
        click.echo(f"  Template:    {created['template']}")
        click.echo(f"  Budget/day:  ${created['budget_usd_daily']:.2f}")
        click.echo(f"  MCP:         {created['enable_mcp']}")

        if enable_mcp and working_dir:
            # Generate CLAUDE.md
            claude_md = pm.generate_claude_md(created)
            claude_md_path = Path(working_dir) / "CLAUDE.md"
            claude_md_path.parent.mkdir(parents=True, exist_ok=True)
            claude_md_path.write_text(claude_md)
            click.echo(f"  CLAUDE.md:   {claude_md_path}")

        click.echo(f"\nProject ready. Use --project-id {project_id} in API calls.")

    except Exception as exc:
        click.echo(f"\n✗ Failed to create project: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
