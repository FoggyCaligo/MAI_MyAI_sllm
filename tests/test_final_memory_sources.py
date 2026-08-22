from mai.final_memory import FinalMemoryExecutor
from mai.graph import GraphRepository, GraphSourceStore
from mai.memory_revise import ReviseMemoryTool
from mai.memory_write import WriteMemoryTool
from mai.scratchpad import ScratchpadRegistry, TurnEvidenceRegistry


def test_direct_user_memory_persists_user_and_assistant_sources(tmp_path) -> None:
    db = tmp_path / "graph.sqlite3"
    repository = GraphRepository(db)
    sources = GraphSourceStore(db)
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    try:
        repository.ensure_user_anchor(user_id="u", turn_id="seed", source_text="seed")
        executor = FinalMemoryExecutor(
            writer=WriteMemoryTool(repository, source_store=sources),
            reviser=ReviseMemoryTool(repository, source_store=sources),
            scratchpads=scratchpads,
            evidence=evidence,
            source_store=sources,
        )
        result = executor.execute(
            user_id="u",
            turn_id="t1",
            user_text="내가 커피를 좋아해",
            fixed_answer="기억할게",
            recall_result=None,
            mutations=[
                {
                    "kind": "write_memory",
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "좋아함",
                        "object": {"new_node": {"name": "커피"}},
                    },
                }
            ],
        )
        edge_id = int(result["mutations"][0]["edge"]["edge_id"])
        summary = sources.provenance_summary(user_id="u", edge_id=edge_id)
        assert summary["source_kind"] == "user_message"
        assert {item["source_kind"] for item in summary["sources"]} == {
            "user_message",
            "assistant_message",
        }
        assert all("내가 커피를 좋아해" not in str(item) for item in summary["sources"])
    finally:
        sources.close()
        repository.close()


def test_scratchpad_backed_memory_persists_underlying_file_evidence(tmp_path) -> None:
    db = tmp_path / "graph.sqlite3"
    repository = GraphRepository(db)
    sources = GraphSourceStore(db)
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    try:
        repository.ensure_user_anchor(user_id="u", turn_id="seed", source_text="seed")
        evidence.register_attachment(
            turn_id="t1",
            item={
                "evidence_id": "attachment:1",
                "path": "C:/example.txt",
                "kind": "text",
                "status": "loaded",
                "content": "setting=20",
                "truncated": False,
            },
        )
        scratchpad = scratchpads.put(
            turn_id="t1",
            content="설정값은 20",
            sources=[{"kind": "internal_file", "evidence_id": "attachment:1"}],
        )
        executor = FinalMemoryExecutor(
            writer=WriteMemoryTool(repository, source_store=sources),
            reviser=ReviseMemoryTool(repository, source_store=sources),
            scratchpads=scratchpads,
            evidence=evidence,
            source_store=sources,
        )
        result = executor.execute(
            user_id="u",
            turn_id="t1",
            user_text="첨부를 확인해줘",
            fixed_answer="설정값은 20이야",
            recall_result=None,
            mutations=[
                {
                    "kind": "write_memory",
                    "scratchpad_ids": [scratchpad.scratchpad_id],
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "설정값",
                        "object": {"new_node": {"name": "20"}},
                    },
                }
            ],
        )
        edge_id = int(result["mutations"][0]["edge"]["edge_id"])
        summary = sources.provenance_summary(user_id="u", edge_id=edge_id)
        kinds = {item["source_kind"] for item in summary["sources"]}
        assert "file_evidence" in kinds
        assert "scratchpad" in kinds
        assert "assistant_message" in kinds
        assert "user_message" not in kinds
        file_source = next(item for item in summary["sources"] if item["source_kind"] == "file_evidence")
        raw = sources.read_source(user_id="u", source_id=int(file_source["source_id"]))
        assert raw["content"] == "setting=20"
        scratchpad_source = next(item for item in summary["sources"] if item["source_kind"] == "scratchpad")
        scratchpad_raw = sources.read_source(user_id="u", source_id=int(scratchpad_source["source_id"]))
        assert scratchpad_raw["metadata"] == {
            "sources": [{"kind": "internal_file", "evidence_id": "attachment:1"}]
        }
    finally:
        sources.close()
        repository.close()


def test_model_only_scratchpad_does_not_masquerade_as_external_evidence(tmp_path) -> None:
    db = tmp_path / "graph.sqlite3"
    repository = GraphRepository(db)
    sources = GraphSourceStore(db)
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    try:
        repository.ensure_user_anchor(user_id="u", turn_id="seed", source_text="seed")
        scratchpad = scratchpads.put(
            turn_id="t1",
            content="후속으로 config.py를 확인해야 함",
            sources=[{"kind": "model"}],
        )
        executor = FinalMemoryExecutor(
            writer=WriteMemoryTool(repository, source_store=sources),
            reviser=ReviseMemoryTool(repository, source_store=sources),
            scratchpads=scratchpads,
            evidence=evidence,
            source_store=sources,
        )
        result = executor.execute(
            user_id="u",
            turn_id="t1",
            user_text="계속 확인해줘",
            fixed_answer="확인할게",
            recall_result=None,
            mutations=[
                {
                    "kind": "write_memory",
                    "scratchpad_ids": [scratchpad.scratchpad_id],
                    "arguments": {
                        "subject": {"kind": "user"},
                        "relation": "작업대상",
                        "object": {"new_node": {"name": "config.py"}},
                    },
                }
            ],
        )
        edge_id = int(result["mutations"][0]["edge"]["edge_id"])
        summary = sources.provenance_summary(user_id="u", edge_id=edge_id)
        kinds = {item["source_kind"] for item in summary["sources"]}
        assert kinds == {"assistant_message", "scratchpad"}
        assert "web_evidence" not in kinds
        assert "file_evidence" not in kinds
    finally:
        sources.close()
        repository.close()
