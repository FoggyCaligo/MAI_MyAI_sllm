from mai.scratchpad import ScratchpadPutTool, ScratchpadRegistry, ScratchpadUpdateTool, TurnEvidenceRegistry


def _source_kinds(tool) -> set[str]:
    schema = tool.schema()
    source_schema = schema["properties"]["arguments"]["properties"]["sources"]["items"]
    return {
        variant["properties"]["kind"]["const"]
        for variant in source_schema["oneOf"]
    }


def test_scratchpad_tools_expose_only_structured_source_kinds() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)

    assert _source_kinds(ScratchpadPutTool(scratchpads=scratchpads, evidence=evidence)) == {
        "web",
        "model",
        "user",
        "internal_file",
    }
    assert _source_kinds(ScratchpadUpdateTool(scratchpads=scratchpads, evidence=evidence)) == {
        "web",
        "model",
        "user",
        "internal_file",
    }


def test_scratchpad_schema_no_longer_exposes_source_ids_array() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    arguments = ScratchpadPutTool(scratchpads=scratchpads, evidence=evidence).schema()["properties"]["arguments"]

    assert "sources" in arguments["properties"]
    assert "source_ids" not in arguments["properties"]
    assert arguments["required"] == ["content", "sources"]


def test_evidence_id_is_optional_inside_each_source() -> None:
    evidence = TurnEvidenceRegistry()
    scratchpads = ScratchpadRegistry(evidence=evidence)
    source_schema = ScratchpadPutTool(scratchpads=scratchpads, evidence=evidence).schema()["properties"]["arguments"]["properties"]["sources"]["items"]

    for variant in source_schema["oneOf"]:
        assert variant["required"] == ["kind"]
        assert "evidence_id" in variant["properties"]
