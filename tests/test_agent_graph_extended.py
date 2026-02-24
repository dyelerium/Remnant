"""
Extended AgentGraph / AgentNode / AgentEdge tests.

Covers (3+ per area):
  - AgentNode dataclass fields and defaults
  - AgentNode.can_delegate() at/below/above max_depth
  - AgentNode.to_dict() serialisation
  - AgentEdge creation and edge_type defaults
  - AgentGraph: add/get/remove node
  - AgentGraph: add_edge, get_children (DELEGATION filter)
  - AgentGraph: get_parent
  - AgentGraph: update_status
  - AgentGraph: descendants (BFS, cycles not present, empty)
  - AgentGraph: to_dict()
  - NodeStatus and EdgeType enum values
"""
from __future__ import annotations

import pytest

from core.agent_graph import (
    AgentEdge,
    AgentGraph,
    AgentNode,
    EdgeType,
    NodeStatus,
)


# ===========================================================================
# AgentNode
# ===========================================================================

class TestAgentNode:
    def test_default_status_is_idle(self):
        node = AgentNode()
        assert node.status == NodeStatus.IDLE

    def test_default_agent_type(self):
        node = AgentNode()
        assert node.agent_type == "default"

    def test_auto_uuid_generation(self):
        n1 = AgentNode()
        n2 = AgentNode()
        assert n1.agent_id != n2.agent_id

    def test_custom_name(self):
        node = AgentNode(name="researcher")
        assert node.name == "researcher"

    def test_depth_defaults_to_zero(self):
        node = AgentNode()
        assert node.depth == 0

    def test_max_depth_defaults_to_three(self):
        node = AgentNode()
        assert node.max_depth == 3

    # ---- can_delegate ----

    def test_can_delegate_at_zero_depth(self):
        node = AgentNode(depth=0, max_depth=3)
        assert node.can_delegate() is True

    def test_can_delegate_at_max_minus_one(self):
        node = AgentNode(depth=2, max_depth=3)
        assert node.can_delegate() is True

    def test_cannot_delegate_at_max_depth(self):
        node = AgentNode(depth=3, max_depth=3)
        assert node.can_delegate() is False

    def test_cannot_delegate_above_max_depth(self):
        node = AgentNode(depth=5, max_depth=3)
        assert node.can_delegate() is False

    def test_can_delegate_with_max_depth_one(self):
        node = AgentNode(depth=0, max_depth=1)
        assert node.can_delegate() is True

    def test_cannot_delegate_with_max_depth_zero(self):
        node = AgentNode(depth=0, max_depth=0)
        assert node.can_delegate() is False

    # ---- to_dict ----

    def test_to_dict_contains_all_keys(self):
        node = AgentNode(name="test", agent_type="coder", depth=1, max_depth=2)
        d = node.to_dict()
        for key in ("agent_id", "name", "agent_type", "project_id", "lane_id", "status", "depth", "max_depth"):
            assert key in d

    def test_to_dict_status_is_string(self):
        node = AgentNode()
        d = node.to_dict()
        assert isinstance(d["status"], str)
        assert d["status"] == "idle"

    def test_to_dict_running_status(self):
        node = AgentNode()
        node.status = NodeStatus.RUNNING
        d = node.to_dict()
        assert d["status"] == "running"

    def test_to_dict_depth_values(self):
        node = AgentNode(depth=2, max_depth=4)
        d = node.to_dict()
        assert d["depth"] == 2
        assert d["max_depth"] == 4

    def test_to_dict_project_id_none_by_default(self):
        node = AgentNode()
        assert node.to_dict()["project_id"] is None


# ===========================================================================
# AgentEdge
# ===========================================================================

class TestAgentEdge:
    def test_default_edge_type_is_delegation(self):
        edge = AgentEdge(source_id="a", target_id="b")
        assert edge.edge_type == EdgeType.DELEGATION

    def test_custom_edge_type_callback(self):
        edge = AgentEdge(source_id="a", target_id="b", edge_type=EdgeType.CALLBACK)
        assert edge.edge_type == EdgeType.CALLBACK

    def test_peer_edge_type(self):
        edge = AgentEdge(source_id="a", target_id="b", edge_type=EdgeType.PEER)
        assert edge.edge_type == EdgeType.PEER

    def test_task_field(self):
        edge = AgentEdge(source_id="a", target_id="b", task="analyze data")
        assert edge.task == "analyze data"

    def test_result_field_none_by_default(self):
        edge = AgentEdge(source_id="a", target_id="b")
        assert edge.result is None

    def test_created_at_is_float(self):
        edge = AgentEdge(source_id="a", target_id="b")
        assert isinstance(edge.created_at, float)
        assert edge.created_at > 0


# ===========================================================================
# AgentGraph
# ===========================================================================

@pytest.fixture
def graph():
    return AgentGraph()


class TestAgentGraphNodes:
    def test_add_and_retrieve_node(self, graph):
        node = AgentNode(name="alpha")
        graph.add_node(node)
        assert graph.get_node(node.agent_id) is node

    def test_get_nonexistent_node_returns_none(self, graph):
        assert graph.get_node("nonexistent-id") is None

    def test_remove_node(self, graph):
        node = AgentNode(name="beta")
        graph.add_node(node)
        graph.remove_node(node.agent_id)
        assert graph.get_node(node.agent_id) is None

    def test_remove_node_also_removes_edges(self, graph):
        n1 = AgentNode(name="parent")
        n2 = AgentNode(name="child")
        graph.add_node(n1)
        graph.add_node(n2)
        graph.add_edge(AgentEdge(source_id=n1.agent_id, target_id=n2.agent_id))

        graph.remove_node(n1.agent_id)
        # Edge referencing n1 should be gone
        children = graph.get_children(n1.agent_id)
        assert len(children) == 0

    def test_remove_nonexistent_node_is_noop(self, graph):
        graph.remove_node("ghost-id")  # Should not raise

    def test_update_status(self, graph):
        node = AgentNode()
        graph.add_node(node)
        graph.update_status(node.agent_id, NodeStatus.RUNNING)
        assert graph.get_node(node.agent_id).status == NodeStatus.RUNNING

    def test_update_status_all_values(self, graph):
        node = AgentNode()
        graph.add_node(node)
        for status in NodeStatus:
            graph.update_status(node.agent_id, status)
            assert graph.get_node(node.agent_id).status == status

    def test_update_status_nonexistent_is_noop(self, graph):
        graph.update_status("ghost-id", NodeStatus.FAILED)  # Should not raise


class TestAgentGraphEdges:
    def test_get_children_returns_delegation_only(self, graph):
        parent = AgentNode(name="parent")
        child = AgentNode(name="child")
        peer = AgentNode(name="peer")
        for n in [parent, child, peer]:
            graph.add_node(n)

        graph.add_edge(AgentEdge(
            source_id=parent.agent_id,
            target_id=child.agent_id,
            edge_type=EdgeType.DELEGATION,
        ))
        graph.add_edge(AgentEdge(
            source_id=parent.agent_id,
            target_id=peer.agent_id,
            edge_type=EdgeType.PEER,
        ))

        children = graph.get_children(parent.agent_id)
        assert len(children) == 1  # Only DELEGATION
        assert children[0].agent_id == child.agent_id

    def test_get_children_empty(self, graph):
        node = AgentNode()
        graph.add_node(node)
        assert graph.get_children(node.agent_id) == []

    def test_get_parent_returns_correct_node(self, graph):
        parent = AgentNode(name="root")
        child = AgentNode(name="worker")
        graph.add_node(parent)
        graph.add_node(child)
        graph.add_edge(AgentEdge(
            source_id=parent.agent_id,
            target_id=child.agent_id,
        ))
        found = graph.get_parent(child.agent_id)
        assert found is not None
        assert found.agent_id == parent.agent_id

    def test_get_parent_for_root_returns_none(self, graph):
        node = AgentNode(name="lonely")
        graph.add_node(node)
        assert graph.get_parent(node.agent_id) is None

    def test_multiple_children(self, graph):
        root = AgentNode(name="root")
        c1 = AgentNode(name="c1")
        c2 = AgentNode(name="c2")
        c3 = AgentNode(name="c3")
        for n in [root, c1, c2, c3]:
            graph.add_node(n)
        for c in [c1, c2, c3]:
            graph.add_edge(AgentEdge(source_id=root.agent_id, target_id=c.agent_id))

        children = graph.get_children(root.agent_id)
        assert len(children) == 3


class TestAgentGraphDescendants:
    def test_descendants_of_leaf_is_empty(self, graph):
        leaf = AgentNode(name="leaf")
        graph.add_node(leaf)
        assert graph.descendants(leaf.agent_id) == []

    def test_descendants_returns_direct_children(self, graph):
        root = AgentNode()
        child = AgentNode()
        graph.add_node(root)
        graph.add_node(child)
        graph.add_edge(AgentEdge(source_id=root.agent_id, target_id=child.agent_id))
        desc = graph.descendants(root.agent_id)
        assert len(desc) == 1
        assert desc[0].agent_id == child.agent_id

    def test_descendants_deep_tree(self, graph):
        nodes = [AgentNode(name=f"n{i}") for i in range(5)]
        for n in nodes:
            graph.add_node(n)
        # Chain: 0 → 1 → 2 → 3 → 4
        for i in range(4):
            graph.add_edge(AgentEdge(
                source_id=nodes[i].agent_id,
                target_id=nodes[i + 1].agent_id,
            ))
        desc = graph.descendants(nodes[0].agent_id)
        assert len(desc) == 4

    def test_descendants_wide_tree(self, graph):
        root = AgentNode()
        children = [AgentNode() for _ in range(4)]
        for n in [root] + children:
            graph.add_node(n)
        for c in children:
            graph.add_edge(AgentEdge(source_id=root.agent_id, target_id=c.agent_id))
        desc = graph.descendants(root.agent_id)
        assert len(desc) == 4

    def test_descendants_nonexistent_node(self, graph):
        assert graph.descendants("nonexistent-id") == []

    def test_descendants_excludes_peers(self, graph):
        n1 = AgentNode()
        n2 = AgentNode()
        graph.add_node(n1)
        graph.add_node(n2)
        graph.add_edge(AgentEdge(
            source_id=n1.agent_id,
            target_id=n2.agent_id,
            edge_type=EdgeType.PEER,
        ))
        # Peer edges don't count as children in get_children
        desc = graph.descendants(n1.agent_id)
        assert len(desc) == 0


class TestAgentGraphToDict:
    def test_to_dict_structure(self, graph):
        node = AgentNode(name="solo")
        graph.add_node(node)
        d = graph.to_dict()
        assert "nodes" in d
        assert "edges" in d

    def test_to_dict_node_count(self, graph):
        for i in range(3):
            graph.add_node(AgentNode(name=f"n{i}"))
        d = graph.to_dict()
        assert len(d["nodes"]) == 3

    def test_to_dict_edge_count(self, graph):
        n1 = AgentNode()
        n2 = AgentNode()
        graph.add_node(n1)
        graph.add_node(n2)
        graph.add_edge(AgentEdge(source_id=n1.agent_id, target_id=n2.agent_id))
        d = graph.to_dict()
        assert len(d["edges"]) == 1

    def test_to_dict_edge_has_source_and_target(self, graph):
        n1 = AgentNode()
        n2 = AgentNode()
        graph.add_node(n1)
        graph.add_node(n2)
        graph.add_edge(AgentEdge(source_id=n1.agent_id, target_id=n2.agent_id))
        edge_dict = graph.to_dict()["edges"][0]
        assert edge_dict["source"] == n1.agent_id
        assert edge_dict["target"] == n2.agent_id

    def test_empty_graph_to_dict(self, graph):
        d = graph.to_dict()
        assert d["nodes"] == []
        assert d["edges"] == []


# ===========================================================================
# Enum values
# ===========================================================================

class TestEnums:
    def test_node_status_values(self):
        assert NodeStatus.IDLE.value == "idle"
        assert NodeStatus.RUNNING.value == "running"
        assert NodeStatus.WAITING.value == "waiting"
        assert NodeStatus.DONE.value == "done"
        assert NodeStatus.FAILED.value == "failed"

    def test_edge_type_values(self):
        assert EdgeType.DELEGATION.value == "delegation"
        assert EdgeType.CALLBACK.value == "callback"
        assert EdgeType.PEER.value == "peer"

    def test_node_status_str_enum(self):
        # NodeStatus is a str enum — should compare equal to string
        assert NodeStatus.DONE == "done"

    def test_edge_type_str_enum(self):
        assert EdgeType.DELEGATION == "delegation"
