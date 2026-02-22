"""Agent graph — AgentNode + AgentEdge dataclasses, graph traversal helpers."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class NodeStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"


class EdgeType(str, Enum):
    DELEGATION = "delegation"   # Parent → child task
    CALLBACK = "callback"       # Child → parent result
    PEER = "peer"               # Sibling coordination


@dataclass
class AgentNode:
    """A single agent in the hierarchical graph."""
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "agent"
    agent_type: str = "default"    # From agents.yaml
    project_id: Optional[str] = None
    lane_id: Optional[str] = None
    status: NodeStatus = NodeStatus.IDLE
    depth: int = 0                 # Nesting level (0 = conductor)
    max_depth: int = 3
    metadata: dict = field(default_factory=dict)

    def can_delegate(self) -> bool:
        return self.depth < self.max_depth

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "agent_type": self.agent_type,
            "project_id": self.project_id,
            "lane_id": self.lane_id,
            "status": self.status.value,
            "depth": self.depth,
            "max_depth": self.max_depth,
        }


@dataclass
class AgentEdge:
    """A directed edge between two agents."""
    source_id: str
    target_id: str
    edge_type: EdgeType = EdgeType.DELEGATION
    task: Optional[str] = None
    result: Optional[Any] = None
    created_at: float = field(default_factory=lambda: __import__("time").time())


class AgentGraph:
    """In-memory hierarchical agent graph."""

    def __init__(self) -> None:
        self._nodes: dict[str, AgentNode] = {}
        self._edges: list[AgentEdge] = []

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def add_node(self, node: AgentNode) -> None:
        self._nodes[node.agent_id] = node

    def get_node(self, agent_id: str) -> Optional[AgentNode]:
        return self._nodes.get(agent_id)

    def remove_node(self, agent_id: str) -> None:
        self._nodes.pop(agent_id, None)
        self._edges = [
            e for e in self._edges
            if e.source_id != agent_id and e.target_id != agent_id
        ]

    def update_status(self, agent_id: str, status: NodeStatus) -> None:
        node = self._nodes.get(agent_id)
        if node:
            node.status = status

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def add_edge(self, edge: AgentEdge) -> None:
        self._edges.append(edge)

    def get_children(self, agent_id: str) -> list[AgentNode]:
        child_ids = {
            e.target_id for e in self._edges
            if e.source_id == agent_id and e.edge_type == EdgeType.DELEGATION
        }
        return [self._nodes[cid] for cid in child_ids if cid in self._nodes]

    def get_parent(self, agent_id: str) -> Optional[AgentNode]:
        for edge in self._edges:
            if edge.target_id == agent_id and edge.edge_type == EdgeType.DELEGATION:
                return self._nodes.get(edge.source_id)
        return None

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def descendants(self, agent_id: str) -> list[AgentNode]:
        """BFS traversal of all descendant nodes."""
        visited = set()
        queue = [agent_id]
        result = []

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            children = self.get_children(current)
            for child in children:
                result.append(child)
                queue.append(child.agent_id)

        return result

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "type": e.edge_type.value,
                    "task": e.task,
                }
                for e in self._edges
            ],
        }
