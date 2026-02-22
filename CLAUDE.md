# Remnant Framework — Claude Code Instructions

## Project Overview
Remnant is a Docker-first, Redis-backed, hierarchical multi-agent framework.
Key components: HNSW vector memory, LLM provider management with budgets,
unified security, Curator agent, planning wizard, and channels (WebSocket, Telegram, WhatsApp).

## Architecture

```
api/main.py          ← FastAPI entrypoint (lifespan wires all components)
core/runtime.py      ← RECALL → PLAN → LLM → TOOLS → RECORD → CURATE loop
core/orchestrator.py ← Conductor: routes messages → lanes → agents
core/security.py     ← UNIFIED: injection + redaction + tool policy + memory sanitisation
memory/redis_client.py ← Pooled Redis + HNSW index management
memory/embedding_provider.py ← sentence-transformers (default, 384 dims)
api/mcp_endpoints.py ← MCP SSE server (Claude Code compatible)
```

## Development Setup

```bash
# Quick start (needs Docker)
cp .env.example .env  # fill in API keys
docker compose up -d

# Or local dev
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

## Key Conventions

- **Imports**: use absolute imports from project root (`from memory.redis_client import ...`)
- **Config**: always loaded via `core.config_loader.get_config().load()`
- **Security**: ALL memory recordings go through `core.security.SecurityManager.is_safe()`
- **LLM calls**: always use `core.llm_client.LLMClient` (budget-enforced)
- **Async**: use `asyncio.get_event_loop().run_in_executor()` for blocking calls in async context
- **Logging**: use `structlog.get_logger(__name__)` (not stdlib logging directly)

## Testing

```bash
pytest tests/ -v
pytest tests/e2e/ -v  # requires Redis running
```

## Important Files
- `config/remnant.yaml` — embedding: sentence-transformers/all-MiniLM-L6-v2 (384 dims)
- `config/security.yaml` — injection patterns, redaction rules, tool policies
- `config/llm_providers.yaml` — provider registry and use-case defaults
- `config/budget.yaml` — token/cost caps

## MCP Integration

Add to Claude Code `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "remnant": {
      "url": "http://localhost:8000/mcp",
      "transport": "http"
    }
  }
}
```

Available MCP tools: `memory_retrieve`, `memory_record`, `agent_run`, `skill_execute`

## Do Not

- Never bypass `SecurityManager.is_safe()` before recording memory
- Never call LLM providers directly — always go through `LLMClient` (budget enforcement)
- Never hardcode API keys — use `.env` / `SecretsStore`
- Never modify `memory/__init__.py` exports without updating this file
