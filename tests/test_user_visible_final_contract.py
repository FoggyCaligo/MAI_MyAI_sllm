from mai.app.runtime import AGENT_SYSTEM_PROMPT


def test_agent_prompt_requires_tool_results_to_be_reported_in_final_answer():
    prompt = AGENT_SYSTEM_PROMPT.lower()
    assert "cannot see" in prompt
    assert "terminal" in prompt
    assert "tool" in prompt
    assert "final answer" in prompt
