# Remnant Skill Template

Skills are YAML files that map a named action to a backing tool.
Place skill files in `skills/builtin/` or `skills/imported/`.

## Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique skill identifier (snake_case) |
| `description` | string | Human-readable description for LLM/UI |
| `tool` | string | Backing tool name (from tool registry) |

## Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `tags` | list[str] | Categorisation tags |
| `arg_map` | dict | Rename args before passing to tool |
| `safety_level` | string | `safe`, `confirm`, `restricted` |
| `requires` | list[str] | Required env vars or configs |
| `examples` | list[dict] | Example invocations |

## Example Skill

```yaml
name: web_search
description: Search the web using DuckDuckGo and return results.
tool: http_client
tags: [search, research]
safety_level: safe

# Rename user-facing args to tool args
arg_map:
  query: url  # Mapped before calling http_client

requires: []

examples:
  - description: Search for Python documentation
    args:
      query: "Python asyncio documentation"
```

## Input / Output Schema

```yaml
# Document the expected args and return format
input_schema:
  type: object
  properties:
    query:
      type: string
      description: Search query string
  required: [query]

output_schema:
  type: object
  properties:
    results:
      type: array
      items:
        type: string
```

## Safety Guidance

- `safe`: No confirmation required, no destructive operations
- `confirm`: Requires explicit user approval before execution
- `restricted`: Admin/project override required in `config/security.yaml`
