from mai.agent.runtime import USER_VISIBLE_RESULT_CONTRACT


def test_agent_prompt_requires_tool_results_to_be_reported_in_final_answer():
    prompt = USER_VISIBLE_RESULT_CONTRACT.lower()
    assert "cannot see" in prompt
    assert "terminal" in prompt
    assert "tool" in prompt
    assert "final answer" in prompt
