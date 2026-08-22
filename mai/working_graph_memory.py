from __future__ import annotations

import json
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Iterable

from .memory_extension import (
    AgentGraphMemoryExtension,
    MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN,
    MAX_NEW_NODES_PER_TURN,
    MemoryTurnState,
    PERSONAL_RELEVANCE,
)
from .model import ModelContractError


@dataclass(slots=True)
class WorkingGraphTurnState(MemoryTurnState):
    pending_nodes: dict[int, dict[str, Any]] = field(default_factory=dict)
    node_updates: dict[int, dict[str, Any]] = field(default_factory=dict)
    node_merges: list[dict[str, Any]] = field(default_factory=list)
    merged_node_targets: dict[int, int] = field(default_factory=dict)
    pending_edges: dict[int, dict[str, Any]] = field(default_factory=dict)
    edge_updates: dict[int, dict[str, Any]] = field(default_factory=dict)
    next_temp_node_id: int = -1
    next_temp_edge_id: int = -1


class WorkingGraphMemoryExtension(AgentGraphMemoryExtension):
    """Turn-local graph overlay committed only after the final answer is frozen.

    Actual Graph is read-only during Agent rounds. Recall gradually opens
    selected Actual nodes and their active one-hop neighborhoods into the
    Working Graph. Generate/fix actions mutate only this overlay. A successful
    final answer causes one atomic repository commit; a failed turn discards
    the overlay.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._states: dict[str, WorkingGraphTurnState] = {}
        self._states_lock = Lock()

    def begin_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        attachment_evidence: Iterable[dict[str, Any]] = (),
    ) -> WorkingGraphTurnState:
        base = super().begin_turn(
            user_id=user_id,
            turn_id=turn_id,
            user_text=user_text,
            attachment_evidence=attachment_evidence,
        )
        state = WorkingGraphTurnState(
            user_id=base.user_id,
            turn_id=base.turn_id,
            user_text=base.user_text,
            query_recall_performed=base.query_recall_performed,
            node_generation_unlocked=base.node_generation_unlocked,
            candidate_node_ids=set(base.candidate_node_ids),
            known_node_ids=set(base.known_node_ids),
            viewed_nodes=dict(base.viewed_nodes),
            viewed_edges=dict(base.viewed_edges),
            available_source_ids=set(base.available_source_ids),
            new_node_count=base.new_node_count,
            edge_mutations_by_node=dict(base.edge_mutations_by_node),
            events=list(base.events),
            tool_source_counter=base.tool_source_counter,
        )
        with self._states_lock:
            if state.turn_id in self._states:
                raise RuntimeError(f"working graph state already exists for turn {state.turn_id}")
            self._states[state.turn_id] = state
        return state

    def round_context(self, state: WorkingGraphTurnState) -> str:
        payload = json.loads(super().round_context(state))
        protocol = payload.setdefault("memory_protocol", {})
        protocol.update(
            {
                "actual_graph_is_read_only_during_agent_loop": True,
                "working_graph_accumulates_only_recalled_one_hop_regions": True,
                "working_mutations_are_pending_until_final_answer": True,
                "pending_state_is_not_past_memory_evidence": True,
                "answer_is_frozen_before_atomic_graph_commit": True,
                "graph_timestamps_describe_graph_record_changes_not_real_world_event_time": True,
            }
        )
        payload["working_graph"] = self._viewed_graph_payload(state)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _generate_node(self, *, arguments: dict[str, Any], state: WorkingGraphTurnState) -> dict[str, Any]:
        if not state.node_generation_unlocked:
            raise ModelContractError("memory/generate/node requires a fresh query recall first")
        if state.new_node_count >= MAX_NEW_NODES_PER_TURN:
            raise ModelContractError("new node budget exhausted for this turn")
        kind = str(arguments["kind"])
        name = str(arguments["name"]).strip()
        if not name:
            raise ModelContractError("generated node name must be non-empty")
        source_ids = self._require_sources(state, arguments["source_ids"])
        member_ids = [int(value) for value in arguments.get("member_node_ids", [])]
        if kind == "composite":
            if len(set(member_ids)) < 2 or any(node_id not in state.viewed_nodes for node_id in member_ids):
                raise ModelContractError("composite members must be opened in the current Working Graph")
        elif kind != "concept":
            raise ModelContractError("node kind must be concept or composite")
        elif member_ids:
            raise ModelContractError("concept node may not declare composite members")

        node_id = state.next_temp_node_id
        state.next_temp_node_id -= 1
        payload = {
            "node_id": node_id,
            "name": name,
            "kind": kind,
            "source_ids": list(source_ids),
            "member_node_ids": list(dict.fromkeys(member_ids)),
            "pending": True,
            "graph_created_at": None,
            "graph_updated_at": None,
        }
        state.pending_nodes[node_id] = payload
        state.viewed_nodes[node_id] = payload
        state.known_node_ids.add(node_id)
        state.new_node_count += 1
        state.node_generation_unlocked = False
        return {"status": "staged", "node": payload, "working_graph": self._viewed_graph_payload(state)}

    def _generate_edge(self, *, arguments: dict[str, Any], state: WorkingGraphTurnState) -> dict[str, Any]:
        start = self._resolved_working_node(state, int(arguments["start_node_id"]))
        end = self._resolved_working_node(state, int(arguments["end_node_id"]))
        if start not in state.viewed_nodes or end not in state.viewed_nodes:
            raise ModelContractError("edge endpoints must be opened in the current Working Graph")
        if start == end:
            raise ModelContractError("self-loop semantic edges are not allowed")
        source_ids = self._require_sources(state, arguments["source_ids"])

        existing_working = self._working_edge_for_pair(state, start=start, end=end)
        if existing_working is not None:
            return self._edge_rejection(state, existing_working, source_ids)
        if start > 0 and end > 0:
            existing_actual = self.repository.edge_for_pair(
                user_id=state.user_id,
                start_node_id=start,
                end_node_id=end,
            )
            if existing_actual is not None:
                edge_id = int(existing_actual["edge_id"])
                payload = self._actual_edge_payload(state.user_id, edge_id)
                state.viewed_edges[edge_id] = payload
                state.available_source_ids.update(int(value) for value in payload["source_ids"])
                return self._edge_rejection(state, payload, source_ids)

        self._consume_edge_budget(state, start, end)
        edge_id = state.next_temp_edge_id
        state.next_temp_edge_id -= 1
        payload = {
            "edge_id": edge_id,
            "start_node_id": start,
            "end_node_id": end,
            "relation": str(arguments["relation"]),
            "weight": float(arguments["weight"]),
            "personal_relevance": PERSONAL_RELEVANCE[str(arguments["personal_relevance"])],
            "source_ids": list(source_ids),
            "pending": True,
            "version_id": None,
            "committed_turn_id": None,
            "committed_at": None,
            "graph_created_at": None,
            "graph_updated_at": None,
        }
        if not 0.0 < float(payload["weight"]) <= 1.0:
            raise ModelContractError("new edge weight must be > 0 and <= 1")
        state.pending_edges[edge_id] = payload
        state.viewed_edges[edge_id] = payload
        return {"status": "staged", "edge": payload, "working_graph": self._viewed_graph_payload(state)}

    def _fix_node(self, *, arguments: dict[str, Any], state: WorkingGraphTurnState) -> dict[str, Any]:
        operation = str(arguments["operation"])
        source_ids = self._require_sources(state, arguments["source_ids"])
        if operation == "rename":
            node_id = self._resolved_working_node(state, int(arguments["node_id"]))
            self._require_working_node(state, node_id)
            name = str(arguments["name"]).strip()
            if not name:
                raise ModelContractError("node name must be non-empty")
            payload = dict(state.viewed_nodes[node_id])
            payload["name"] = name
            payload["source_ids"] = self._union_ids(payload.get("source_ids", []), source_ids)
            payload["pending"] = True
            payload["graph_updated_at"] = None
            state.viewed_nodes[node_id] = payload
            if node_id < 0:
                state.pending_nodes[node_id] = payload
            else:
                update = state.node_updates.setdefault(node_id, {})
                update["name"] = name
                update["source_ids"] = self._union_ids(update.get("source_ids", []), source_ids)
            target_id = node_id

        elif operation == "set_members":
            node_id = self._resolved_working_node(state, int(arguments["node_id"]))
            self._require_working_node(state, node_id)
            members = [self._resolved_working_node(state, int(value)) for value in arguments["member_node_ids"]]
            members = list(dict.fromkeys(members))
            if len(members) < 2 or any(member not in state.viewed_nodes for member in members):
                raise ModelContractError("composite members must be opened in the current Working Graph")
            payload = dict(state.viewed_nodes[node_id])
            if str(payload["kind"]) != "composite":
                raise ModelContractError("only composite nodes may have structural members")
            if node_id in members:
                raise ModelContractError("composite node cannot contain itself")
            payload["member_node_ids"] = members
            payload["source_ids"] = self._union_ids(payload.get("source_ids", []), source_ids)
            payload["pending"] = True
            payload["graph_updated_at"] = None
            state.viewed_nodes[node_id] = payload
            if node_id < 0:
                state.pending_nodes[node_id] = payload
            else:
                update = state.node_updates.setdefault(node_id, {})
                update["member_node_ids"] = members
                update["source_ids"] = self._union_ids(update.get("source_ids", []), source_ids)
            target_id = node_id

        elif operation == "merge":
            source_id = self._resolved_working_node(state, int(arguments["source_node_id"]))
            target_id = self._resolved_working_node(state, int(arguments["target_node_id"]))
            self._require_working_node(state, source_id)
            self._require_working_node(state, target_id)
            if source_id == target_id:
                raise ModelContractError("merge source and target must differ")
            if source_id > 0 and target_id < 0:
                raise ModelContractError("an existing Actual node cannot merge into a pending node")
            self._stage_merge(state=state, source_id=source_id, target_id=target_id, source_ids=source_ids)
        else:
            raise ModelContractError(f"unsupported memory/fix/node operation: {operation}")

        return {
            "status": "staged",
            "node": state.viewed_nodes[target_id],
            "working_graph": self._viewed_graph_payload(state),
        }

    def _fix_edge(self, *, arguments: dict[str, Any], state: WorkingGraphTurnState) -> dict[str, Any]:
        edge_id = int(arguments["edge_id"])
        if edge_id not in state.viewed_edges:
            raise ModelContractError("memory/fix/edge requires an edge in the current Working Graph")
        current = dict(state.viewed_edges[edge_id])
        start = self._resolved_working_node(state, int(current["start_node_id"]))
        end = self._resolved_working_node(state, int(current["end_node_id"]))
        self._consume_edge_budget(state, start, end)
        source_ids = self._require_sources(state, arguments["source_ids"])
        operation = str(arguments["operation"])
        if operation == "disconnect":
            weight = 0.0
            relation = str(current["relation"])
            relevance = float(current["personal_relevance"])
        elif operation == "update":
            relation = str(arguments["relation"])
            weight = max(0.0, min(1.0, float(current["weight"]) + float(arguments["weight_delta"])))
            relevance = max(
                float(current["personal_relevance"]),
                PERSONAL_RELEVANCE[str(arguments["personal_relevance"])],
            )
        else:
            raise ModelContractError(f"unsupported memory/fix/edge operation: {operation}")
        current.update(
            {
                "start_node_id": start,
                "end_node_id": end,
                "relation": relation,
                "weight": weight,
                "personal_relevance": relevance,
                "source_ids": self._union_ids(
                    current.get("source_ids", []) if current.get("pending") else [],
                    source_ids,
                ),
                "pending": True,
                "committed_turn_id": None,
                "committed_at": None,
                "graph_updated_at": None,
            }
        )
        state.viewed_edges[edge_id] = current
        if edge_id < 0:
            state.pending_edges[edge_id] = current
        else:
            state.edge_updates[edge_id] = current
        return {"status": "staged", "edge": current, "working_graph": self._viewed_graph_payload(state)}

    def _recall(self, *, arguments: dict[str, Any], state: WorkingGraphTurnState) -> dict[str, Any]:
        if set(arguments) == {"edge_id"}:
            edge_id = int(arguments["edge_id"])
            if edge_id not in state.viewed_edges:
                raise ModelContractError("memory/recall edge_id requires an edge in the current Working Graph")
            working = dict(state.viewed_edges[edge_id])
            if edge_id < 0:
                actual_current = None
                past_versions: list[dict[str, Any]] = []
            else:
                actual_current = self._actual_edge_payload(state.user_id, edge_id)
                current_version_id = int(actual_current["version_id"])
                past_versions = []
                for version in self.repository.edge_versions(user_id=state.user_id, edge_id=edge_id):
                    version_id = int(version["version_id"])
                    if version_id == current_version_id:
                        continue
                    past_versions.append(
                        {
                            "version_id": version_id,
                            "edge_id": edge_id,
                            "relation": str(version["relation"]),
                            "weight": float(version["weight"]),
                            "personal_relevance": float(version["personal_relevance"]),
                            "committed_turn_id": str(version["turn_id"]),
                            "committed_at": str(version["created_at"]),
                            "source_ids": self.source_store.source_ids_for_edge_version(
                                user_id=state.user_id,
                                edge_version_id=version_id,
                            ),
                        }
                    )
            return {
                "status": "edge_history",
                "edge_id": edge_id,
                "actual_current": actual_current,
                "working_current": working,
                "past_versions": past_versions,
                "working_state_is_past_evidence": False,
                "working_graph": self._viewed_graph_payload(state),
            }
        return super()._recall(arguments=arguments, state=state)

    def _open_one_hop(self, *, state: WorkingGraphTurnState, node_id: int) -> None:
        node_id = self._resolved_working_node(state, int(node_id))
        if node_id < 0:
            return
        neighborhood = self.repository.one_hop_neighborhood(user_id=state.user_id, focus_node_id=node_id)
        for node in neighborhood["nodes"]:
            actual_id = int(node["node_id"])
            working_id = self._resolved_working_node(state, actual_id)
            if actual_id in state.merged_node_targets:
                continue
            state.known_node_ids.add(working_id)
            if working_id not in state.viewed_nodes:
                payload = self._actual_node_payload(state.user_id, actual_id)
                state.viewed_nodes[working_id] = payload
                state.available_source_ids.update(int(value) for value in payload["source_ids"])
        for edge in neighborhood["edges"]:
            edge_id = int(edge["edge_id"])
            if edge_id in state.viewed_edges:
                continue
            payload = self._actual_edge_payload(state.user_id, edge_id)
            payload["start_node_id"] = self._resolved_working_node(state, int(payload["start_node_id"]))
            payload["end_node_id"] = self._resolved_working_node(state, int(payload["end_node_id"]))
            if payload["start_node_id"] == payload["end_node_id"]:
                continue
            state.viewed_edges[edge_id] = payload
            state.available_source_ids.update(int(value) for value in payload["source_ids"])

    def _node_payload(self, user_id: str, node_id: int) -> dict[str, Any]:
        return self._actual_node_payload(user_id, node_id)

    def _edge_payload(self, user_id: str, edge_id: int) -> dict[str, Any]:
        return self._actual_edge_payload(user_id, edge_id)

    def _actual_node_payload(self, user_id: str, node_id: int) -> dict[str, Any]:
        node = self.repository.get_node(user_id=user_id, node_id=node_id)
        return {
            "node_id": int(node["node_id"]),
            "name": str(node["name"]),
            "kind": str(node["kind"]),
            "source_ids": self.source_store.source_ids_for_node(user_id=user_id, node_id=node_id),
            "member_node_ids": self.repository.composite_members(user_id=user_id, composite_node_id=node_id)
            if str(node["kind"]) == "composite"
            else [],
            "pending": False,
            "graph_created_at": str(node["created_at"]),
            "graph_updated_at": str(node["updated_at"]),
        }

    def _actual_edge_payload(self, user_id: str, edge_id: int) -> dict[str, Any]:
        edge = self.repository.get_edge(user_id=user_id, edge_id=edge_id)
        version_id = int(edge["current_version_id"])
        return {
            "edge_id": int(edge["edge_id"]),
            "version_id": version_id,
            "start_node_id": int(edge["start_node_id"]),
            "end_node_id": int(edge["end_node_id"]),
            "relation": str(edge["relation"]),
            "weight": float(edge["weight"]),
            "personal_relevance": float(edge["personal_relevance"]),
            "source_ids": self.source_store.source_ids_for_edge_version(
                user_id=user_id,
                edge_version_id=version_id,
            ),
            "pending": False,
            "committed_turn_id": str(edge["version_turn_id"]),
            "committed_at": str(edge["version_created_at"]),
            "graph_created_at": str(edge["created_at"]),
            "graph_updated_at": str(edge["updated_at"]),
        }

    def _viewed_graph_payload(self, state: WorkingGraphTurnState) -> dict[str, Any]:
        return {
            "nodes": [state.viewed_nodes[node_id] for node_id in sorted(state.viewed_nodes)],
            "edges": [state.viewed_edges[edge_id] for edge_id in sorted(state.viewed_edges)],
        }

    def _recall_schema(self, state: WorkingGraphTurnState) -> dict[str, Any]:
        base = super()._recall_schema(state)
        arguments = base["properties"]["arguments"]
        variants = list(arguments.get("oneOf", []))
        viewed_edges = sorted(state.viewed_edges)
        if viewed_edges:
            variants.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["edge_id"],
                    "properties": {"edge_id": {"type": "integer", "enum": viewed_edges}},
                }
            )
        arguments["oneOf"] = variants
        return base

    def commit_turn(self, *, turn_id: str) -> dict[str, Any]:
        with self._states_lock:
            state = self._states.get(str(turn_id))
        if state is None:
            raise RuntimeError(f"working graph state is missing for turn {turn_id}")

        node_embeddings: dict[int, list[float]] = {}
        for node_id, payload in state.pending_nodes.items():
            node_embeddings[node_id] = self.embedding.embed([str(payload["name"])])[0]
        for node_id, update in state.node_updates.items():
            if "name" in update:
                node_embeddings[node_id] = self.embedding.embed([str(update["name"])])[0]

        result = self.repository.commit_working_graph(
            user_id=state.user_id,
            turn_id=state.turn_id,
            embedding_model=self.embedding_model_name,
            node_embeddings=node_embeddings,
            pending_nodes=state.pending_nodes,
            node_updates=state.node_updates,
            node_merges=state.node_merges,
            pending_edges=state.pending_edges,
            edge_updates=state.edge_updates,
        )
        with self._states_lock:
            self._states.pop(state.turn_id, None)
        return result

    def abort_turn(self, *, turn_id: str) -> None:
        with self._states_lock:
            self._states.pop(str(turn_id), None)

    def _stage_merge(
        self,
        *,
        state: WorkingGraphTurnState,
        source_id: int,
        target_id: int,
        source_ids: list[int],
    ) -> None:
        source = dict(state.viewed_nodes[source_id])
        target = dict(state.viewed_nodes[target_id])
        inherited_sources = [int(value) for value in source.get("source_ids", [])]
        combined_sources = self._union_ids(target.get("source_ids", []), inherited_sources, source_ids)

        new_edges: dict[int, dict[str, Any]] = {}
        pair_owner: dict[tuple[int, int], int] = {}
        for edge_id, raw in state.viewed_edges.items():
            edge = dict(raw)
            start = target_id if int(edge["start_node_id"]) == source_id else int(edge["start_node_id"])
            end = target_id if int(edge["end_node_id"]) == source_id else int(edge["end_node_id"])
            if start == end:
                raise ModelContractError("node merge would create a self-loop in the Working Graph")
            pair = (start, end)
            prior = pair_owner.get(pair)
            if prior is not None and prior != edge_id:
                raise ModelContractError("node merge would collide with another directed edge in the Working Graph")
            pair_owner[pair] = edge_id
            edge["start_node_id"] = start
            edge["end_node_id"] = end
            if start != int(raw["start_node_id"]) or end != int(raw["end_node_id"]):
                edge["pending"] = True
            new_edges[edge_id] = edge

        new_nodes: dict[int, dict[str, Any]] = {}
        for node_id, raw in state.viewed_nodes.items():
            if node_id == source_id:
                continue
            node = dict(raw)
            members = [target_id if int(value) == source_id else int(value) for value in node.get("member_node_ids", [])]
            members = list(dict.fromkeys(members))
            if node.get("kind") == "composite" and node.get("member_node_ids") and len(members) < 2:
                raise ModelContractError("node merge would leave a Working composite with fewer than two members")
            node["member_node_ids"] = members
            new_nodes[node_id] = node

        target = dict(new_nodes[target_id])
        target["source_ids"] = combined_sources
        target["pending"] = True
        target["graph_updated_at"] = None
        if source.get("kind") == "composite":
            if target.get("kind") != "composite":
                raise ModelContractError("merging a composite node into a concept would lose structural members")
            target_members = self._union_ids(target.get("member_node_ids", []), source.get("member_node_ids", []))
            target_members = [target_id if value == source_id else value for value in target_members]
            target_members = list(dict.fromkeys(target_members))
            if target_id in target_members or len(target_members) < 2:
                raise ModelContractError("node merge would create invalid composite membership")
            target["member_node_ids"] = target_members
        new_nodes[target_id] = target

        state.viewed_nodes = new_nodes
        state.viewed_edges = new_edges
        state.known_node_ids.discard(source_id)
        state.candidate_node_ids.discard(source_id)
        state.merged_node_targets[source_id] = target_id

        for edge_id, edge in new_edges.items():
            if edge_id < 0:
                state.pending_edges[edge_id] = edge
            elif edge.get("pending"):
                state.edge_updates[edge_id] = edge

        if source_id < 0:
            state.pending_nodes.pop(source_id, None)
            self._rewrite_pending_node_refs(state, source_id=source_id, target_id=target_id)
        else:
            state.node_merges.append(
                {"source_node_id": source_id, "target_node_id": target_id, "source_ids": combined_sources}
            )
        if target_id < 0:
            state.pending_nodes[target_id] = target
        else:
            update = state.node_updates.setdefault(target_id, {})
            update["source_ids"] = self._union_ids(update.get("source_ids", []), combined_sources)
            if target.get("kind") == "composite" and target.get("member_node_ids") != self._actual_node_payload(
                state.user_id, target_id
            ).get("member_node_ids"):
                update["member_node_ids"] = list(target["member_node_ids"])

    def _rewrite_pending_node_refs(self, state: WorkingGraphTurnState, *, source_id: int, target_id: int) -> None:
        for node_id, payload in list(state.pending_nodes.items()):
            members = [target_id if int(value) == source_id else int(value) for value in payload.get("member_node_ids", [])]
            payload = dict(payload)
            payload["member_node_ids"] = list(dict.fromkeys(members))
            state.pending_nodes[node_id] = payload
        for node_id, update in list(state.node_updates.items()):
            if "member_node_ids" in update:
                update["member_node_ids"] = list(
                    dict.fromkeys(target_id if int(value) == source_id else int(value) for value in update["member_node_ids"])
                )

    @staticmethod
    def _edge_rejection(state: WorkingGraphTurnState, payload: dict[str, Any], source_ids: list[int]) -> dict[str, Any]:
        return {
            "status": "rejected",
            "reason": "directed_edge_already_exists",
            "existing_edge_id": int(payload["edge_id"]),
            "existing_edge": payload,
            "requested_source_ids": source_ids,
            "working_graph": {
                "nodes": [state.viewed_nodes[node_id] for node_id in sorted(state.viewed_nodes)],
                "edges": [state.viewed_edges[edge_id] for edge_id in sorted(state.viewed_edges)],
            },
        }

    @staticmethod
    def _working_edge_for_pair(state: WorkingGraphTurnState, *, start: int, end: int) -> dict[str, Any] | None:
        for edge in state.viewed_edges.values():
            if int(edge["start_node_id"]) == int(start) and int(edge["end_node_id"]) == int(end):
                return edge
        return None

    @staticmethod
    def _union_ids(*groups: Iterable[int]) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        for group in groups:
            for raw in group:
                value = int(raw)
                if value not in seen:
                    seen.add(value)
                    result.append(value)
        return result

    @staticmethod
    def _require_working_node(state: WorkingGraphTurnState, node_id: int) -> None:
        if int(node_id) not in state.viewed_nodes:
            raise ModelContractError("node_id must be opened in the current Working Graph")

    @staticmethod
    def _resolved_working_node(state: WorkingGraphTurnState, node_id: int) -> int:
        current = int(node_id)
        seen: set[int] = set()
        while current in state.merged_node_targets:
            if current in seen:
                raise RuntimeError("working node merge map contains a cycle")
            seen.add(current)
            current = int(state.merged_node_targets[current])
        return current
