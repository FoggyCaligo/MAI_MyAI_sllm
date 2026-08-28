"""Temporary pure-agent experiment: no preflight and no automatic recall."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agent.runtime import AgentRuntime
from ..llm.models import ModelConfig
from ..llm.ollama import OllamaAdapter
from ..memory.graph.repository import MemoryGraphRepository
from ..memory.index import SqliteFtsConceptIndex
from ..memory.recall.service import RecallService
from ..memory.runtime import MemoryRuntime
from ..memory.segmenter import SentenceBreakerSegmenter
from ..memory.tools import register_memory_tools
from ..memory.working import WorkingGraph
from ..tools.local import register_local_pc_tools
from ..tools.registry import ToolRegistry


PURE_AGENT_SYSTEM = """
You are running inside the MAI agent runtime.

Your current capabilities are defined by the native tools supplied with this request, not by assumptions learned during model training about what a language model can or cannot do.

When information required to answer is not present in the current request, use an appropriate available native tool to obtain it. In particular, use the memory tools for stored user history or preferences and local file/code tools for facts that require inspecting the computer.

Do not claim that you cannot access memory, files, code, the terminal, or other capabilities when a corresponding native tool is currently available. Do not invent tool results. If a tool fails, treat that failure as real.

Use tools only when they are needed to answer or perform the request. Stable general knowledge that is already sufficient does not require a tool call.
""".strip()


@dataclass(slots=True)
class PureAgentExperiment:
    user_id: str
    agent: AgentRuntime
    memory: MemoryRuntime
    graph: MemoryGraphRepository
    concept_index: SqliteFtsConceptIndex
    segmenter: SentenceBreakerSegmenter

    async def run_once(self, prompt: str) -> dict[str, object]:
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        evidence = self.memory.record_raw_user_evidence(self.user_id, prompt)
        result = await self.agent.run_user_message(
            prompt,
            prior_messages=({"role": "system", "content": PURE_AGENT_SYSTEM},),
        )
        successful_tool_results = tuple(
            execution.content for execution in result.tool_executions if execution.ok
        )
        await self.memory.finish_turn(
            user_id=self.user_id,
            user_text=prompt,
            final_answer=result.content,
            user_evidence=evidence,
            successful_tool_results=successful_tool_results,
        )
        return {
            "answer": result.content,
            "model_rounds": result.model_rounds,
            "tools": [
                {
                    "name": execution.name,
                    "arguments": execution.arguments,
                    "ok": execution.ok,
                    "error_type": execution.error_type,
                }
                for execution in result.tool_executions
            ],
        }

    def close(self) -> None:
        self.segmenter.close()
        self.concept_index.close()
        self.graph.close()


def build_experiment(
    *,
    user_id: str,
    model: str,
    ollama_host: str,
    memory_db_path: str | Path,
    sentence_breaker_db_path: str | Path,
    cwd: str | Path | None = None,
) -> PureAgentExperiment:
    if not user_id.strip():
        raise ValueError("user_id must be non-empty")

    graph = MemoryGraphRepository(memory_db_path)
    segmenter = SentenceBreakerSegmenter(db_path=str(sentence_breaker_db_path))
    concept_index = SqliteFtsConceptIndex(memory_db_path)
    recall = RecallService(graph, concept_index, segmenter)
    memory = MemoryRuntime(
        graph,
        concept_index,
        segmenter,
        recall,
        now=lambda: datetime.now(timezone.utc),
    )
    memory.ensure_user(user_id)

    registry = ToolRegistry()
    register_local_pc_tools(registry, cwd=cwd)
    working = WorkingGraph()
    register_memory_tools(
        registry,
        memory,
        working,
        user_id=user_id,
        include_recall_entry=True,
    )

    adapter = OllamaAdapter(ModelConfig(model=model, host=ollama_host, think=True))
    agent = AgentRuntime(adapter, registry)
    return PureAgentExperiment(user_id, agent, memory, graph, concept_index, segmenter)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MAI's temporary pure-agent C experiment with no preflight and no auto-recall."
    )
    parser.add_argument("prompt", help="One user request. Each process invocation starts with no dialogue context.")
    parser.add_argument("--user-id", default=os.environ.get("MAI_USER_ID", "local-user"))
    parser.add_argument("--model", default=os.environ.get("MAIN_MODEL", "ornith-1.5:9b"))
    parser.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--memory-db", default=os.environ.get("MEMORY_DB_PATH", "./data/memory.sqlite3"))
    parser.add_argument(
        "--sentence-breaker-db",
        default=os.environ.get("SENTENCE_BREAKER_DB_PATH", "./data/sentence_breaker.sqlite3"),
    )
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    experiment = build_experiment(
        user_id=args.user_id,
        model=args.model,
        ollama_host=args.ollama_host,
        memory_db_path=args.memory_db,
        sentence_breaker_db_path=args.sentence_breaker_db,
        cwd=args.cwd,
    )
    try:
        result = await experiment.run_once(args.prompt)
    finally:
        experiment.close()

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(result["answer"])
    print("\n--- C experiment diagnostics ---")
    print(f"model_rounds: {result['model_rounds']}")
    tools = result["tools"]
    if not tools:
        print("tools: none")
        return
    for tool in tools:
        print(
            f"tool: {tool['name']} ok={tool['ok']} arguments={json.dumps(tool['arguments'], ensure_ascii=False)}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
