from tests.conftest import FakeLLM

import memory


def test_no_summary_needed_below_threshold(monkeypatch):
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    summary, kept = memory.summarize_if_needed(messages, keep_last=6)
    assert summary == ""
    assert kept == messages


def test_summarizes_once_over_threshold(monkeypatch):
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(10)]
    monkeypatch.setattr(memory, "get_chat_model", lambda **kw: FakeLLM(["Summary of early turns."]))

    summary, kept = memory.summarize_if_needed(messages, keep_last=6)

    assert summary == "Summary of early turns."
    assert kept == messages[-6:]
    assert len(kept) == 6
