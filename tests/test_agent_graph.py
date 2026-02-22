"""Tests: graph creation, delegation, teardown."""
from __future__ import annotations

import pytest

from core.agent_graph import (
    AgentEdge,
    AgentGraph,
    AgentNode,
    EdgeType,
    NodeStatus,
)


@pytest.fixture
def graph():
    return AgentGraph()


@pytest.fixture
def conductor():
    return AgentNode(name="conductor", agent_type="conductor", depth=0)


@pytest.fixture
def worker():
    return AgentNode(name="worker", agent_type="coder", depth=1)


class TestAgentGraph:
    def test_add_and_get_node(self, graph, conductor):
        graph.add_node(conductor)
        assert graph.get_node(conductor.agent_id) is conductor

    def test_remove_node(self, graph, conductor):
        graph.add_node(conductor)
        graph.remove_node(conductor.agent_id)
        assert graph.get_node(conductor.agent_id) is None

    def test_add_edge_and_get_children(self, graph, conductor, worker):
        graph.add_node(conductor)
        graph.add_node(worker)
        graph.add_edge(AgentEdge(
            source_id=conductor.agent_id,
            target_id=worker.agent_id,
            edge_type=EdgeType.DELEGATION,
        ))
        children = graph.get_children(conductor.agent_id)
        assert len(children) == 1
        assert children[0].agent_id == worker.agent_id

    def test_get_parent(self, graph, conductor, worker):
        graph.add_node(conductor)
        graph.add_node(worker)
        graph.add_edge(AgentEdge(
            source_id=conductor.agent_id,
            target_id=worker.agent_id,
        ))
        parent = graph.get_parent(worker.agent_id)
        assert parent is not None
        assert parent.agent_id == conductor.agent_id

    def test_update_status(self, graph, conductor):
        graph.add_node(conductor)
        graph.update_status(conductor.agent_id, NodeStatus.RUNNING)
        assert graph.get_node(conductor.agent_id).status == NodeStatus.RUNNING

    def test_descendants(self, graph):
        root = AgentNode(name="root", depth=0)
        child1 = AgentNode(name="child1", depth=1)
        child2 = AgentNode(name="child2", depth=1)
        grandchild = AgentNode(name="grandchild", depth=2)

        for n in [root, child1, child2, grandchild]:
            graph.add_node(n)

        graph.add_edge(AgentEdge(source_id=root.agent_id, target_id=child1.agent_id))
        graph.add_edge(AgentEdge(source_id=root.agent_id, target_id=child2.agent_id))
        graph.add_edge(AgentEdge(source_id=child1.agent_id, target_id=grandchild.agent_id))

        desc = graph.descendants(root.agent_id)
        assert len(desc) == 3

    def test_can_delegate(self):
        node = AgentNode(depth=2, max_depth=3)
        assert node.can_delegate() is True

        node_at_max = AgentNode(depth=3, max_depth=3)
        assert node_at_max.can_delegate() is False

    def test_to_dict(self, graph, conductor, worker):
        graph.add_node(conductor)
        graph.add_node(worker)
        graph.add_edge(AgentEdge(
            source_id=conductor.agent_id,
            target_id=worker.agent_id,
        ))
        d = graph.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
