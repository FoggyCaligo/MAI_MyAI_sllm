from __future__ import annotations

from collections import Counter
import re

from ... import config
from .anchors import (
    ASSISTANT_ANCHOR_ID,
    SEARCH_ANCHOR_ID,
    concept_node_id,
    fact_node_id,
    search_query_node_id,
    search_result_node_id,
    user_anchor_id,
    utterance_node_id,
)
from .models import GraphEdge, GraphNode
from .repository import GraphRepository
from .text_graph import TokenSpan, normalize_token, tokenize_spans


_TEXT_GRAPH_VERSION = 2


class GraphMemoryService:
    def __init__(self, repo: GraphRepository) -> None:
        self._repo = repo
        self._ensure_shared_anchors()

    def _ensure_shared_anchors(self) -> None:
        for node_id, labels in (
            (ASSISTANT_ANCHOR_ID, ["AI", "assistant"]),
            (SEARCH_ANCHOR_ID, ["external", "search"]),
        ):
            if self._repo.get_node(node_id) is None:
                self._repo.upsert_node(GraphNode(
                    node_id=node_id,
                    labels=labels,
                    node_type="anchor",
                    provenance="system_policy",
                    trust_score=1.0,
                    stability_score=1.0,
                ))

    def ensure_user_anchor(self, user_id: str) -> str:
        anchor_id = user_anchor_id(user_id)
        if self._repo.get_node(anchor_id) is None:
            self._repo.upsert_node(
                GraphNode(
                    node_id=anchor_id,
                    labels=[user_id],
                    node_type="anchor",
                    payload={"user_id": user_id},
                    provenance="system_policy",
                    trust_score=1.0,
                    stability_score=1.0,
                )
            )
        return anchor_id

    def record_user_utterance(self, *, user_id: str, text: str, session_id: str | None) -> str:
        anchor_id = self.ensure_user_anchor(user_id)
        utterance_id = utterance_node_id(user_id, text, session_id)
        existing_utterance = self._repo.get_node(utterance_id)
        is_new_utterance = existing_utterance is None
        if is_new_utterance:
            self._repo.upsert_node(
                GraphNode(
                    node_id=utterance_id,
                    labels=[text],
                    node_type="utterance",
                    payload={
                        "user_id": user_id,
                        "session_id": session_id,
                        "text_graph_version": _TEXT_GRAPH_VERSION,
                    },
                    provenance="user_utterance",
                    trust_score=1.0,
                    stability_score=0.8,
                )
            )
            self._repo.add_edge(
                GraphEdge(
                    source_id=anchor_id,
                    target_id=utterance_id,
                    relation="spoke",
                    payload={"session_id": session_id},
                    provenance="user_utterance",
                    trust_score=1.0,
                )
            )
        needs_graph_refresh = (
            is_new_utterance
            or int((existing_utterance.payload if existing_utterance else {}).get("text_graph_version") or 0)
            < _TEXT_GRAPH_VERSION
        )
        if needs_graph_refresh:
            self._graphize_text(
                owner_anchor_id=anchor_id,
                carrier_node_id=utterance_id,
                text=text,
                edge_prefix="user",
                payload={
                    "user_id": user_id,
                    "session_id": session_id,
                    "text_graph_version": _TEXT_GRAPH_VERSION,
                },
            )
            if existing_utterance is not None:
                existing_utterance.payload["text_graph_version"] = _TEXT_GRAPH_VERSION
                self._repo.upsert_node(existing_utterance)
        self.record_user_facts(
            user_id=user_id,
            text=text,
            session_id=session_id,
            utterance_id=utterance_id,
        )
        return utterance_id

    def record_user_facts(
        self,
        *,
        user_id: str,
        text: str,
        session_id: str | None,
        utterance_id: str,
    ) -> list[str]:
        anchor_id = self.ensure_user_anchor(user_id)
        fact_ids: list[str] = []
        for fact_text in self._extract_fact_candidates(text):
            node_id = fact_node_id(user_id, fact_text, namespace="fact")
            if self._repo.get_node(node_id) is None:
                self._repo.upsert_node(
                    GraphNode(
                        node_id=node_id,
                        labels=[fact_text],
                        node_type="fact",
                        payload={
                            "user_id": user_id,
                            "session_id": session_id,
                            "source": "user_utterance",
                        },
                        provenance="user_assertion",
                        trust_score=0.75,
                        stability_score=0.5,
                    )
                )
            self._repo.add_edge(
                GraphEdge(
                    source_id=anchor_id,
                    target_id=node_id,
                    relation="asserted_fact",
                    payload={"session_id": session_id},
                    provenance="user_assertion",
                    trust_score=0.8,
                )
            )
            self._repo.add_edge(
                GraphEdge(
                    source_id=utterance_id,
                    target_id=node_id,
                    relation="derived_fact",
                    payload={"session_id": session_id},
                    provenance="derived_from_utterance",
                    trust_score=0.7,
                )
            )
            fact_ids.append(node_id)
        return fact_ids

    def record_search_results(self, *, query: str, results: list[dict]) -> list[str]:
        query_text = query.strip()
        if not query_text or not results:
            return []

        query_node_id = search_query_node_id(query_text)
        if self._repo.get_node(query_node_id) is None:
            self._repo.upsert_node(
                GraphNode(
                    node_id=query_node_id,
                    labels=[query_text],
                    node_type="search_query",
                    payload={"query": query_text},
                    provenance="search",
                    trust_score=0.7,
                )
            )
        self._repo.add_edge(
            GraphEdge(
                source_id=SEARCH_ANCHOR_ID,
                target_id=query_node_id,
                relation="issued_search",
                payload={},
                provenance="search",
                trust_score=0.8,
            )
        )
        for span in tokenize_spans(query_text):
            query_concept_id = self._ensure_normalized_concept(span.normalized)
            self._repo.add_edge(
                GraphEdge(
                    source_id=query_node_id,
                    target_id=query_concept_id,
                    relation="search_query_term",
                    payload={"query": query_text, "token_index": span.token_index},
                    provenance="search",
                    trust_score=0.7,
                )
            )

        recorded: list[str] = []
        for item in results:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            source = str(item.get("source") or "").strip() or "unknown"
            if not title and not url:
                continue

            result_node_id = search_result_node_id(query_text, title, url)
            if self._repo.get_node(result_node_id) is None:
                self._repo.upsert_node(
                    GraphNode(
                        node_id=result_node_id,
                        labels=[title or url],
                        node_type="search_result",
                        payload={
                            "query": query_text,
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": source,
                        },
                        provenance="search",
                        trust_score=0.65,
                    )
                )
            self._repo.add_edge(
                GraphEdge(
                    source_id=query_node_id,
                    target_id=result_node_id,
                    relation="returned_result",
                    payload={"source": source},
                    provenance="search",
                    trust_score=0.7,
                )
            )
            recorded.append(result_node_id)
            self._graphize_text(
                owner_anchor_id=SEARCH_ANCHOR_ID,
                carrier_node_id=result_node_id,
                text="\n".join(part for part in [title, snippet] if part),
                edge_prefix="search",
                payload={
                    "query": query_text,
                    "title": title,
                    "url": url,
                    "source": source,
                },
            )

            for fact_text in self._extract_fact_candidates(snippet):
                fact_id = fact_node_id(url or title or query_text, fact_text, namespace="search_fact")
                if self._repo.get_node(fact_id) is None:
                    self._repo.upsert_node(
                        GraphNode(
                            node_id=fact_id,
                            labels=[fact_text],
                            node_type="search_fact",
                            payload={
                                "query": query_text,
                                "title": title,
                                "url": url,
                                "source": source,
                            },
                            provenance="search",
                            trust_score=0.6,
                        )
                    )
                self._repo.add_edge(
                    GraphEdge(
                        source_id=result_node_id,
                        target_id=fact_id,
                        relation="supports_fact",
                        payload={"source": source},
                        provenance="search",
                        trust_score=0.65,
                    )
                )
                recorded.append(fact_id)
        return recorded

    def record_file_text_activation(
        self,
        *,
        user_id: str,
        path: str,
        content: str,
        session_id: str | None,
    ) -> dict:
        path_text = path.strip()
        if not path_text or not _is_nodeable_text_path(path_text):
            return {"node_ids": [], "nodes": []}
        ranked_nodes = _rank_text_node_candidates(_truncate_file_activation_text(content))
        if not ranked_nodes:
            return {"node_ids": [], "nodes": []}

        _ = self.ensure_user_anchor(user_id)
        summary_text = " ".join(str(item["label"]) for item in ranked_nodes)
        context_id = fact_node_id(user_id, f"{path_text}|{summary_text}", namespace="file_context")
        if self._repo.get_node(context_id) is None:
            self._repo.upsert_node(
                GraphNode(
                    node_id=context_id,
                    labels=[f"{path_text}: {summary_text}", path_text],
                    node_type="file_context",
                    payload={
                        "user_id": user_id,
                        "session_id": session_id,
                        "path": path_text,
                        "source": "file_read",
                        "node_count": len(ranked_nodes),
                        "suppress_from_summary": True,
                    },
                    provenance="file_read",
                    trust_score=0.55,
                    stability_score=0.35,
                )
            )
        concept_ids = self._graphize_text(
            owner_anchor_id=context_id,
            carrier_node_id=context_id,
            text=summary_text,
            edge_prefix="file",
            payload={"user_id": user_id, "session_id": session_id, "path": path_text},
        )
        return {
            "context_node_id": context_id,
            "node_ids": [context_id, *concept_ids],
            "nodes": ranked_nodes,
        }

    def user_memory_summary(
        self,
        user_id: str,
        *,
        query: str = "",
        limit: int = 5,
        min_signal: float = 0.0,
        exclude_node_ids: set[str] | None = None,
        activation_node_ids: set[str] | None = None,
        activation_node_weights: dict[str, float] | None = None,
    ) -> list[dict]:
        anchor_id = self.ensure_user_anchor(user_id)
        terms = {term.lower() for term in re.findall(r"\w+", query) if len(term) >= 2}
        excluded = exclude_node_ids or set()
        activation_weights = self._activation_related_node_weights(
            activation_node_weights
            if activation_node_weights is not None
            else {node_id: 1.0 for node_id in (activation_node_ids or set())}
        )
        ranked: list[tuple[float, dict]] = []
        for node in self._repo.neighbors(anchor_id):
            if node.node_id in excluded:
                continue
            if self._is_derived_from_excluded_node(node.node_id, excluded):
                continue
            if not node.labels or not node.is_active:
                continue
            if node.payload.get("suppress_from_summary"):
                continue
            if node.node_type not in {"fact", "utterance"}:
                continue
            label = node.labels[0]
            label_terms = {term.lower() for term in re.findall(r"\w+", label)}
            relevance = len(terms.intersection(label_terms)) / max(1, len(terms)) if terms else 0.0
            type_bonus = 1.0 if node.node_type == "fact" else 0.25
            activation = activation_weights.get(node.node_id, 0.0)
            if terms and max(relevance, activation) < max(0.0, min_signal):
                continue
            activation_bonus = 1.5 * activation
            score = relevance * 4.0 + activation_bonus + type_bonus + node.trust_score + node.stability_score
            ranked.append((score, {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "label": self._format_user_memory_for_model(user_id=user_id, node=node, label=label),
                "raw_label": label,
                "subgraph": self._small_subgraph_summary(
                    user_id=user_id,
                    node=node,
                    relation_limit=4,
                    excluded_node_ids=excluded,
                ),
                "score": round(score, 4),
                "score_components": {
                    "relevance": round(relevance, 4),
                    "relevance_weighted": round(relevance * 4.0, 4),
                    "activation": round(activation, 4),
                    "activation_bonus": round(activation_bonus, 4),
                    "type_bonus": round(type_bonus, 4),
                    "trust_score": round(node.trust_score, 4),
                    "stability_score": round(node.stability_score, 4),
                },
            }))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("label") or "")))
        items: list[dict] = []
        seen_labels: set[str] = set()
        for _, item in ranked:
            label_key = re.sub(r"\s+", " ", str(item.get("raw_label") or "").strip().lower())
            if not label_key or label_key in seen_labels:
                continue
            seen_labels.add(label_key)
            items.append(item)
            if limit > 0 and len(items) >= limit:
                break
        return items

    def _small_subgraph_summary(
        self,
        *,
        user_id: str,
        node: GraphNode,
        relation_limit: int,
        excluded_node_ids: set[str] | None = None,
    ) -> dict:
        excluded = excluded_node_ids or set()
        ranked_relations: list[tuple[tuple, dict]] = []
        for edge in self._repo.edges_for_node(node.node_id):
            if not edge.is_active:
                continue
            other_id = edge.target_id if edge.source_id == node.node_id else edge.source_id
            if other_id in excluded:
                continue
            other = self._repo.get_node(other_id)
            if other is None or not other.is_active or other.node_type == "anchor":
                continue
            other_owner = str(other.payload.get("user_id") or "")
            if other_owner and other_owner != user_id:
                continue
            relation = {
                "relation": edge.relation,
                "direction": "outgoing" if edge.source_id == node.node_id else "incoming",
                "node_id": other.node_id,
                "label": other.labels[0] if other.labels else other.node_id,
                "node_type": other.node_type,
                "provenance": edge.provenance,
                "support_count": edge.support_count,
                "trust_score": round(edge.trust_score, 4),
            }
            ranked_relations.append((self._subgraph_relation_rank(edge=edge, other=other), relation))
        ranked_relations.sort(key=lambda item: item[0])

        source = {
            key: node.payload.get(key)
            for key in ("source", "title", "url", "path", "session_id", "status")
            if node.payload.get(key) not in (None, "")
        }
        summary = {
            "focus": {
                "node_id": node.node_id,
                "label": node.labels[0] if node.labels else node.node_id,
                "node_type": node.node_type,
                "provenance": node.provenance,
                "trust_score": round(node.trust_score, 4),
                "stability_score": round(node.stability_score, 4),
            },
            "relations": [relation for _, relation in ranked_relations[:max(0, relation_limit)]],
        }
        if source:
            summary["source"] = source
        return summary

    def _subgraph_relation_rank(self, *, edge: GraphEdge, other: GraphNode) -> tuple:
        if edge.relation in {"replaces", "supports_fact", "derived_fact", "asserted_fact", "returned_result"}:
            priority = 0
        elif edge.relation == "spoke" or edge.relation.endswith("_mentions_concept"):
            priority = 1
        elif edge.relation.endswith("_references_concept"):
            priority = 2
        elif edge.relation.endswith("_adjacent_concept"):
            priority = 3
        else:
            priority = 2
        return (
            priority,
            -edge.support_count,
            -edge.trust_score,
            -edge.edge_weight,
            other.node_type,
            other.labels[0] if other.labels else other.node_id,
        )

    def _activation_related_node_weights(self, activation_node_weights: dict[str, float]) -> dict[str, float]:
        related = {
            node_id: max(0.0, weight)
            for node_id, weight in activation_node_weights.items()
            if weight > 0.0
        }
        for node_id, weight in activation_node_weights.items():
            if weight <= 0.0:
                continue
            for edge in self._repo.edges_for_node(node_id):
                if not edge.is_active:
                    continue
                related[edge.source_id] = max(related.get(edge.source_id, 0.0), weight)
                related[edge.target_id] = max(related.get(edge.target_id, 0.0), weight)
        return related

    def _activation_related_node_ids(self, activation_node_ids: set[str]) -> set[str]:
        return set(self._activation_related_node_weights({node_id: 1.0 for node_id in activation_node_ids}))

    def local_activation_node_ids_for_utterance(
        self,
        *,
        user_id: str,
        utterance_id: str,
        previous_activation_node_ids: set[str] | None = None,
    ) -> set[str]:
        _ = self.ensure_user_anchor(user_id)
        utterance = self._repo.get_node(utterance_id)
        if utterance is None or utterance.node_type != "utterance":
            return set()
        if str(utterance.payload.get("user_id") or "") != user_id:
            return set()

        current_local = {utterance_id}
        for edge in self._repo.edges_for_node(utterance_id):
            if not edge.is_active:
                continue
            other_id = edge.target_id if edge.source_id == utterance_id else edge.source_id
            other = self._repo.get_node(other_id)
            if other is not None and other.is_active:
                current_local.add(other.node_id)

        previous = previous_activation_node_ids or set()
        previous_non_overlapping = {node_id for node_id in previous if node_id not in current_local}
        return current_local | previous_non_overlapping

    def local_activation_node_weights_for_utterance(
        self,
        *,
        user_id: str,
        utterance_id: str,
        previous_activation_node_ids: set[str] | None = None,
        previous_activation_node_weights: dict[str, float] | None = None,
        previous_weight: float = 0.5,
    ) -> dict[str, float]:
        current_local = self.local_activation_node_ids_for_utterance(
            user_id=user_id,
            utterance_id=utterance_id,
            previous_activation_node_ids=None,
        )
        weights = {node_id: 1.0 for node_id in current_local}
        previous_weights = (
            previous_activation_node_weights
            if previous_activation_node_weights is not None
            else {node_id: previous_weight for node_id in (previous_activation_node_ids or set())}
        )
        for node_id, weight in previous_weights.items():
            if node_id not in current_local:
                weights[node_id] = max(weights.get(node_id, 0.0), max(0.0, weight))
        return weights

    def _is_derived_from_excluded_node(self, node_id: str, excluded_node_ids: set[str]) -> bool:
        if not excluded_node_ids:
            return False
        for edge in self._repo.edges_for_node(node_id):
            if edge.relation != "derived_fact":
                continue
            if edge.source_id in excluded_node_ids or edge.target_id in excluded_node_ids:
                return True
        return False

    def _format_user_memory_for_model(self, *, user_id: str, node: GraphNode, label: str) -> str:
        if node.node_type == "fact":
            return (
                f'사용자({user_id})에 대한 기억: "{label}" '
                "이 문장의 1인칭 표현은 assistant가 아니라 사용자에게 귀속됩니다."
            )
        return (
            f'사용자({user_id})가 이전에 말한 발화: "{label}" '
            "이 발화의 speaker는 사용자이며 assistant의 자기소개가 아닙니다."
        )

    def record_fact_correction(
        self,
        *,
        user_id: str,
        previous_fact_id: str,
        replacement_text: str,
        session_id: str | None = None,
    ) -> str:
        """Replace a user fact while preserving both provenance and history.

        MK5 intentionally does not guess corrections from cue strings. The planner
        calls this explicit operation after it has identified the previous fact.
        """
        anchor_id = self.ensure_user_anchor(user_id)
        previous = self._repo.get_node(previous_fact_id)
        if previous is None or previous.node_type != "fact":
            raise ValueError("previous_fact_id must identify an existing fact")
        if str(previous.payload.get("user_id") or "") != user_id:
            raise ValueError("cannot correct another user's fact")

        replacement = replacement_text.strip()
        if not replacement:
            raise ValueError("replacement_text must not be empty")
        replacement_id = fact_node_id(user_id, replacement, namespace="fact")
        self._repo.upsert_node(GraphNode(
            node_id=replacement_id,
            labels=[replacement],
            node_type="fact",
            payload={
                "user_id": user_id,
                "session_id": session_id,
                "source": "user_correction",
                "replaces": previous_fact_id,
            },
            provenance="user_correction",
            trust_score=0.95,
            stability_score=0.75,
        ))
        self._repo.add_edge(GraphEdge(
            source_id=anchor_id,
            target_id=replacement_id,
            relation="asserted_fact",
            payload={"session_id": session_id, "correction": True},
            provenance="user_correction",
            trust_score=0.95,
        ))
        self._repo.add_edge(GraphEdge(
            source_id=replacement_id,
            target_id=previous_fact_id,
            relation="replaces",
            payload={"session_id": session_id},
            provenance="user_correction",
            trust_score=1.0,
            conflict_count=1,
        ))
        previous.payload["superseded_by"] = replacement_id
        previous.payload["status"] = "superseded"
        previous.is_active = False
        previous.trust_score = min(previous.trust_score, 0.2)
        self._repo.upsert_node(previous)
        # Keep the original utterance for audit/provenance, but do not feed a
        # sentence known to contain the superseded fact back into the LLM summary.
        for edge in self._repo.edges_for_node(previous_fact_id):
            if edge.relation != "derived_fact" or edge.target_id != previous_fact_id:
                continue
            utterance = self._repo.get_node(edge.source_id)
            if utterance is None or utterance.node_type != "utterance":
                continue
            utterance.payload["suppress_from_summary"] = True
            utterance.payload["superseded_fact_id"] = previous_fact_id
            self._repo.upsert_node(utterance)
        return replacement_id

    def graph_search(
        self,
        *,
        user_id: str,
        query: str = "",
        node_id: str = "",
        limit: int = 8,
        exclude_node_ids: set[str] | None = None,
    ) -> list[dict]:
        _ = self.ensure_user_anchor(user_id)
        query = query.strip()
        node_id = node_id.strip()
        if not query and not node_id:
            return []
        excluded = exclude_node_ids or set()
        results: list[dict] = []
        anchor_id = self.ensure_user_anchor(user_id)
        user_reachable = {node.node_id for node in self._repo.neighbors(anchor_id)} | {anchor_id}
        if node_id:
            selected = self._repo.get_node(node_id)
            candidates = [selected] if selected is not None else []
        else:
            candidates = self._repo.search_nodes(query, limit=max(limit * 4, limit))
        for node in candidates:
            if node is None or not node.is_active:
                continue
            if len(results) >= limit:
                break
            if node.node_id in excluded:
                continue
            if self._is_derived_from_excluded_node(node.node_id, excluded):
                continue
            owner = str(node.payload.get("user_id") or "")
            is_external = node.provenance == "search" or node.node_type.startswith("search_")
            if owner and owner != user_id:
                continue
            if not is_external and node.node_id not in user_reachable and node.node_type != "concept":
                continue
            results.append(self._small_subgraph_summary(
                user_id=user_id,
                node=node,
                relation_limit=6,
                excluded_node_ids=excluded,
            ))
        return results

    def delete_user_memory(self, user_id: str) -> dict[str, int]:
        cleaned = user_id.strip()
        if not cleaned:
            raise ValueError("user_id must not be empty")
        return self._repo.delete_user_graph(
            user_id=cleaned,
            anchor_id=user_anchor_id(cleaned),
        )

    def _extract_fact_candidates(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text.strip())
        if not normalized:
            return []

        parts = re.split(r"[\n\r]+|(?<=[.!????])\s+", normalized)
        facts: list[str] = []
        seen: set[str] = set()
        for part in parts:
            candidate = part.strip(" \t\"'[]()")
            if len(candidate) < 6:
                continue
            if candidate.endswith("?"):
                continue
            lowered = candidate.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            facts.append(candidate)
        return facts

    def _graphize_text(
        self,
        *,
        owner_anchor_id: str,
        carrier_node_id: str,
        text: str,
        edge_prefix: str,
        payload: dict,
    ) -> list[str]:
        spans = tokenize_spans(text)
        if not spans:
            return []

        concept_ids: list[str] = []
        sentence_concepts: dict[int, list[str]] = {}
        for span in spans:
            node_id = self._ensure_concept_node(span)
            self._repo.add_edge(
                GraphEdge(
                    source_id=carrier_node_id,
                    target_id=node_id,
                    relation=f"{edge_prefix}_mentions_concept",
                    payload={
                        **payload,
                        "token": span.token,
                        "normalized": span.normalized,
                        "sentence_index": span.sentence_index,
                        "token_index": span.token_index,
                    },
                    provenance=_graphize_provenance(edge_prefix),
                    trust_score=0.75 if edge_prefix == "user" else 0.6,
                )
            )
            self._repo.add_edge(
                GraphEdge(
                    source_id=owner_anchor_id,
                    target_id=node_id,
                    relation=f"{edge_prefix}_references_concept",
                    payload={**payload, "normalized": span.normalized},
                    provenance=_graphize_provenance(edge_prefix),
                    trust_score=0.7 if edge_prefix == "user" else 0.6,
                )
            )
            concept_ids.append(node_id)
            sentence_concepts.setdefault(span.sentence_index, []).append(node_id)

        for concept_list in sentence_concepts.values():
            for left, right in zip(concept_list, concept_list[1:]):
                if left == right:
                    continue
                self._repo.add_edge(
                    GraphEdge(
                        source_id=left,
                        target_id=right,
                        relation=f"{edge_prefix}_adjacent_concept",
                        payload=payload,
                        provenance=_graphize_provenance(edge_prefix),
                        trust_score=0.65,
                    )
                )
        return concept_ids

    def _ensure_concept_node(self, span: TokenSpan) -> str:
        node_id = concept_node_id(span.normalized)
        if self._repo.get_node(node_id) is None:
            self._repo.upsert_node(
                GraphNode(
                    node_id=node_id,
                    labels=[span.token, span.normalized],
                    node_type="concept",
                    payload={"normalized": span.normalized},
                    provenance="ingest",
                    trust_score=0.5,
                    stability_score=0.4,
                )
            )
        return node_id

    def _ensure_normalized_concept(self, normalized: str) -> str:
        node_id = concept_node_id(normalized)
        if self._repo.get_node(node_id) is None:
            self._repo.upsert_node(
                GraphNode(
                    node_id=node_id,
                    labels=[normalized],
                    node_type="concept",
                    payload={"normalized": normalized},
                )
            )
        return node_id


def _is_nodeable_text_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".txt", ".md", ".markdown"))


def _graphize_provenance(edge_prefix: str) -> str:
    if edge_prefix == "user":
        return "user_utterance"
    if edge_prefix == "file":
        return "file_read"
    return "search"


def _rank_text_node_candidates(content: str) -> list[dict]:
    spans = _file_text_tokenize_spans(content)
    if not spans:
        return []

    total = max(1, len(spans))
    counts = Counter(span.normalized for span in spans)
    first_positions: dict[str, int] = {}
    labels: dict[str, str] = {}
    for index, span in enumerate(spans):
        first_positions.setdefault(span.normalized, index)
        labels.setdefault(span.normalized, span.token)

    ranked: list[tuple[float, str]] = []
    for normalized, frequency in counts.items():
        first_index = first_positions.get(normalized, total)
        frequency_score = frequency / total
        first_seen_score = 1.0 - min(first_index, total) / total
        score = frequency_score * 0.7 + first_seen_score * 0.3
        ranked.append((score, normalized))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    keep_ratio = min(1.0, max(0.0, config.FILE_TEXT_NODE_KEEP_RATIO))
    keep_count = int(len(ranked) * keep_ratio)
    if keep_ratio > 0 and keep_count == 0:
        keep_count = 1
    keep_count = min(keep_count, max(0, config.FILE_TEXT_NODE_MAX_ITEMS))

    nodes: list[dict] = []
    for score, normalized in ranked[:keep_count]:
        frequency = counts[normalized]
        first_index = first_positions.get(normalized, total)
        nodes.append({
            "label": labels.get(normalized, normalized),
            "normalized": normalized,
            "score": round(score, 4),
            "score_components": {
                "frequency": frequency,
                "frequency_score": round(frequency / total, 4),
                "first_seen_score": round(1.0 - min(first_index, total) / total, 4),
            },
        })
    return nodes


def _file_text_tokenize_spans(content: str) -> list[TokenSpan]:
    fallback_spans = _regex_token_spans(content)
    if len(content) > 2000:
        return fallback_spans
    spans = tokenize_spans(content)
    if (
        len({span.normalized for span in spans}) >= 2
        and _average_token_length(spans) >= _average_token_length(fallback_spans)
    ):
        return spans
    return fallback_spans or spans


def _regex_token_spans(content: str) -> list[TokenSpan]:
    fallback_spans: list[TokenSpan] = []
    token_index = 0
    for sentence_index, line in enumerate(content.splitlines()):
        for token in re.findall(r"\w+", line, re.UNICODE):
            normalized = normalize_token(token)
            if not normalized:
                continue
            fallback_spans.append(
                TokenSpan(
                    token=token,
                    normalized=normalized,
                    sentence_index=sentence_index,
                    token_index=token_index,
                )
            )
            token_index += 1
    return fallback_spans


def _average_token_length(spans: list[TokenSpan]) -> float:
    if not spans:
        return 0.0
    return sum(len(span.normalized) for span in spans) / len(spans)


def _truncate_file_activation_text(content: str) -> str:
    max_chars = max(0, config.FILE_TEXT_ACTIVATION_MAX_CHARS)
    if not max_chars or len(content) <= max_chars:
        return content
    return content[:max_chars]
