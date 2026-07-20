import agents.critic as critic_module
from tests.conftest import FakeLLM


def test_critic_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(critic_module, "CRITIC_ENABLED", False)
    state = {"status": "success", "final_answer": "There are 6 customers."}
    result = critic_module.critic(state)
    assert "critic_feedback" not in result


def test_critic_noop_on_non_success_status(monkeypatch):
    monkeypatch.setattr(critic_module, "CRITIC_ENABLED", True)
    state = {"status": "failed", "final_answer": "blocked"}
    result = critic_module.critic(state)
    assert "critic_feedback" not in result


def test_critic_annotates_on_failing_verdict(monkeypatch):
    monkeypatch.setattr(critic_module, "CRITIC_ENABLED", True)
    verdict_json = '{"passes": false, "feedback": "Answer ignores the date filter in the question."}'
    monkeypatch.setattr(critic_module, "get_chat_model", lambda **kw: FakeLLM([verdict_json]))

    state = {
        "status": "success",
        "user_query_masked": "How many orders last month?",
        "execution_result": [{"n": 6}],
        "final_answer": "There are 6 orders.",
    }
    result = critic_module.critic(state)

    assert result["critic_feedback"] == "Answer ignores the date filter in the question."


def test_critic_passes_silently_on_passing_verdict(monkeypatch):
    monkeypatch.setattr(critic_module, "CRITIC_ENABLED", True)
    verdict_json = '{"passes": true, "feedback": ""}'
    monkeypatch.setattr(critic_module, "get_chat_model", lambda **kw: FakeLLM([verdict_json]))

    state = {
        "status": "success",
        "user_query_masked": "How many customers are there?",
        "execution_result": [{"n": 6}],
        "final_answer": "There are 6 customers.",
    }
    result = critic_module.critic(state)

    assert "critic_feedback" not in result
