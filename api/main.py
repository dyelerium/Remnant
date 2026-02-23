"""FastAPI app factory with lifespan — Redis connect, index ensure, scheduler start."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Global singletons (set up in lifespan)
_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown."""
    from core.config_loader import get_config
    from core.logging_config import configure_logging
    from core.security import SecurityManager
    from core.secrets_store import SecretsStore
    from core.provider_registry import ProviderRegistry
    from core.budget_manager import BudgetManager
    from core.llm_client import LLMClient
    from core.planner import Planner
    from core.agent_graph import AgentGraph
    from core.lane_manager import LaneManager
    from core.orchestrator import Orchestrator
    from core.runtime import AgentRuntime
    from core.scheduling import Scheduler
    from core.curator_agent import CuratorAgent
    from core.project_manager import ProjectManager
    from memory.redis_client import RemnantRedisClient
    from memory.embedding_provider import get_embedding_provider
    from memory.memory_retriever import MemoryRetriever
    from memory.memory_recorder import MemoryRecorder
    from memory.memory_compactor import MemoryCompactor
    from memory.curator_bridge import CuratorBridge
    from memory.project_index import ProjectIndex
    from memory.global_index import GlobalIndex
    from skills.skill_registry import SkillRegistry
    from tools.shell_tool import ShellTool
    from tools.http_tool import HTTPTool
    from tools.filesystem_tool import FilesystemTool
    from tools.n8n_tool import N8nTool
    from tools.code_exec_tool import CodeExecTool

    # -- Config + logging --
    cfg_loader = get_config()
    config = cfg_loader.load()

    configure_logging(
        level=config.get("log_level", "INFO"),
        json_logs=config.get("env", "development") == "production",
    )
    logger.info("Remnant starting up…")

    # -- Redis --
    redis_client = RemnantRedisClient(config)
    redis_client.ensure_index()
    logger.info("Redis connected and index ready")

    # -- Security & Secrets --
    security_cfg = config.get("injection_detection", {})
    security_cfg.update(config.get("redaction", {}))
    security_cfg.update({"tool_policies": config.get("tool_policies", {})})
    security_cfg.update({"logging": config.get("logging", {})})
    # Pass the full config
    security = SecurityManager(redis_client, config)
    secrets = SecretsStore(redis_client)

    # -- LLM stack --
    providers_cfg = {
        "providers": config.get("providers", {}),
        "defaults": config.get("defaults", {}),
    }
    registry = ProviderRegistry(providers_cfg)
    budget = BudgetManager(redis_client, config.get("global", config))
    embedding = get_embedding_provider(config)
    llm = LLMClient(config, registry, budget)

    # -- Memory stack --
    retriever = MemoryRetriever(redis_client, embedding, config)
    recorder = MemoryRecorder(redis_client, embedding, security, config)
    curator_bridge = CuratorBridge(redis_client)
    compactor = MemoryCompactor(redis_client, embedding, recorder, llm, config)
    curator = CuratorAgent(llm, curator_bridge, config)
    proj_index = ProjectIndex(redis_client)
    global_index = GlobalIndex(redis_client, config)

    # -- Tools --
    tools_cfg = config.get("tools", {})
    tool_registry = {
        "shell": ShellTool(timeout=tools_cfg.get("code_exec", {}).get("timeout_seconds", 30)),
        "http_client": HTTPTool(
            allowed_domains=tools_cfg.get("http_client", {}).get("allowed_domains") or None,
            timeout=tools_cfg.get("http_client", {}).get("timeout_seconds", 30),
        ),
        "filesystem": FilesystemTool(
            allowed_paths=tools_cfg.get("filesystem", {}).get("allowed_paths")
        ),
        "n8n": N8nTool(),
        "code_exec": CodeExecTool(
            timeout=tools_cfg.get("code_exec", {}).get("timeout_seconds", 30),
            allowed_languages=tools_cfg.get("code_exec", {}).get("allowed_languages"),
        ),
    }

    # -- Agent runtime --
    async def lane_handler(message, lane_id):
        pass  # Messages processed via orchestrator.handle()

    agent_graph = AgentGraph()
    lane_manager = LaneManager(lane_handler)
    planner = Planner(llm, config)
    runtime = AgentRuntime(retriever, recorder, llm, security, curator, config, tool_registry)
    orchestrator = Orchestrator(planner, lane_manager, agent_graph, runtime, config)

    # -- Skills --
    skill_registry = SkillRegistry("skills")
    skill_registry.load()

    # -- Project manager --
    project_manager = ProjectManager(redis_client, config)

    # -- Scheduler --
    scheduler = Scheduler(compactor, curator, redis_client, config)
    scheduler.start()

    # Start Curator background worker
    await curator.start()

    # Expose singletons via app.state
    app.state.config = config
    app.state.redis = redis_client
    app.state.security = security
    app.state.secrets = secrets
    app.state.llm = llm
    app.state.registry = registry
    app.state.budget = budget
    app.state.retriever = retriever
    app.state.recorder = recorder
    app.state.compactor = compactor
    app.state.curator = curator
    app.state.curator_bridge = curator_bridge
    app.state.proj_index = proj_index
    app.state.global_index = global_index
    app.state.tool_registry = tool_registry
    app.state.skill_registry = skill_registry
    app.state.project_manager = project_manager
    app.state.orchestrator = orchestrator
    app.state.agent_graph = agent_graph
    app.state.lane_manager = lane_manager
    app.state.scheduler = scheduler

    logger.info("Remnant ready")
    yield

    # -- Shutdown --
    logger.info("Remnant shutting down…")
    await curator.stop()
    scheduler.stop()


def create_app() -> FastAPI:
    """Application factory."""
    from api.routes.chat import router as chat_router
    from api.routes.memory import router as memory_router
    from api.routes.llm import router as llm_router
    from api.routes.projects import router as projects_router
    from api.routes.health import router as health_router
    from api.routes.admin import router as admin_router
    from api.routes.settings import router as settings_router
    from api.routes.whatsapp import router as whatsapp_router
    from api.mcp_endpoints import router as mcp_router

    app = FastAPI(
        title="Remnant Framework",
        description="Redis-backed hierarchical multi-agent framework",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten in production via config
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(health_router)
    app.include_router(chat_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(llm_router, prefix="/api")
    app.include_router(projects_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(whatsapp_router, prefix="/api")
    app.include_router(mcp_router)

    # Serve Web UI — public/ (no build step) takes priority, then dist/ if built
    webui_public = Path("webui/public")
    webui_dist = Path("webui/dist")
    if webui_public.exists():
        app.mount("/", StaticFiles(directory=str(webui_public), html=True), name="webui")
    elif webui_dist.exists():
        app.mount("/", StaticFiles(directory=str(webui_dist), html=True), name="webui")

    return app


app = create_app()
