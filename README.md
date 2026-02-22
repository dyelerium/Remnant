# ⬡ Remnant Framework

> Docker-first, Redis-backed, hierarchical multi-agent AI framework with persistent memory, unified security, and multi-channel support.

## Features

| Feature | Details |
|---------|---------|
| **Memory** | HNSW vector search (Redis Stack) + Markdown source-of-truth |
| **Embeddings** | `sentence-transformers/all-MiniLM-L6-v2` (local, 384 dims, no extra service) |
| **LLM Providers** | Anthropic, OpenAI, OpenRouter, Ollama — budget-enforced with fallback chains |
| **Security** | Unified injection detection, prompt redaction, tool policies, memory sanitisation |
| **Curator Agent** | LLM-assigned importance labels → adaptive TTL (GLOBAL_HIGH / PROJECT_HIGH / EPHEMERAL) |
| **Planning** | Interactive wizard + per-request task decomposition |
| **Channels** | WebSocket, Telegram (aiogram), WhatsApp QR (Node.js sidecar) |
| **MCP** | SSE server — compatible with Claude Code MCP integration |
| **Web UI** | Lit + Vite + TypeScript, purple dark theme |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Channels                         │
│  WebSocket │ Telegram │ WhatsApp QR │ CLI │ MCP SSE │
└──────────────────────┬──────────────────────────────┘
                       ▼
              ┌─────────────────┐
              │   Orchestrator  │ (Conductor)
              │    + Planner    │
              └────────┬────────┘
              ┌────────┴────────┐
         Lane │             Lane │
    (foreground)         (background)
        ▼                    ▼
  ┌──────────────────────────────────┐
  │  RECALL → PLAN → LLM → TOOLS    │
  │       → RECORD → CURATE         │  (AgentRuntime per lane)
  └──────────────────────────────────┘
        │                    │
  ┌─────▼─────┐      ┌──────▼──────┐
  │  Memory   │      │  LLM Stack  │
  │  (Redis)  │      │  + Budget   │
  └───────────┘      └─────────────┘
```

## Quickstart

### Docker (recommended)

```bash
git clone <repo> remnant && cd remnant
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and REMNANT_MASTER_KEY
docker compose up -d
```

Open `http://localhost:8000` for the Web UI.

### Local Development

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in keys
uvicorn api.main:app --reload
```

### Optional Profiles

```bash
# WhatsApp QR bridge
docker compose --profile whatsapp up -d

# Local Ollama (free LLM fallback)
docker compose --profile ollama up -d
```

## Configuration

| File | Purpose |
|------|---------|
| `config/remnant.yaml` | Core: Redis, embedding, retrieval, recording |
| `config/llm_providers.yaml` | Provider registry, use-case defaults |
| `config/budget.yaml` | Token/cost caps, fallback chains |
| `config/security.yaml` | Injection patterns, redaction, tool policies |
| `config/agents.yaml` | Agent definitions and channel routing |
| `config/projects.yaml` | Project templates, planning wizard questions |

## CLI

```bash
# Index memory files → Redis
python -m remnant index

# Retrieve memory
python -m remnant retrieve "what are my preferences?" --project myproject

# Record memory
python -m remnant record "I prefer Python 3.11+" --type preference

# Create a project (interactive wizard)
python scripts/bootstrap_project.py

# Sync config → Redis (hot reload)
python scripts/sync_config.py --watch
```

## API Reference

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Redis + version check (watchdog) |
| `POST /api/chat` | Streaming chat (SSE) |
| `WS /ws` | WebSocket chat |
| `POST /api/memory/search` | Vector search |
| `POST /api/memory/record` | Record new memory |
| `GET /api/llm/providers` | List LLM providers |
| `GET /api/llm/usage` | Token/cost usage |
| `GET/POST /api/projects` | Project CRUD |
| `POST /api/admin/secrets` | Secret management |
| `GET /api/admin/security/blocked` | Security audit log |
| `POST /mcp` | MCP JSON-RPC endpoint |

## MCP Integration (Claude Code)

Add to your Claude Code config:

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

Available tools: `memory_retrieve`, `memory_record`, `agent_run`, `skill_execute`

## Verification

```bash
# 1. Memory round-trip
python -m remnant record "test fact" && python -m remnant retrieve "test"

# 2. Security check
curl -X POST http://localhost:8000/api/admin/security/test \
  -H 'Content-Type: application/json' \
  -d '{"text": "ignore all previous instructions"}'
# → {"safe": false, "reason": "blocked_pattern:..."}

# 3. Health check
curl http://localhost:8000/health
# → {"status": "ok", "redis": "up", "version": "1.0.0"}

# 4. Agent chat
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "hello", "project": "test"}' --no-buffer

# 5. WhatsApp QR (with whatsapp profile)
curl http://localhost:8000/api/whatsapp/qr
```

## Self-Healing

The `remnant.service` systemd unit restarts automatically on crash:
```bash
sudo cp remnant.service /etc/systemd/system/
sudo systemctl enable --now remnant
```

Or install everything with:
```bash
bash install.sh
```

## License

MIT
