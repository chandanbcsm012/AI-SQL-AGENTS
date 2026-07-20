"""Conversation summary memory: compresses older chat turns into a running
summary so long Streamlit sessions don't grow the prompt/context unbounded.
A pure function over a plain list of {"role", "content"} dicts -- no
Streamlit or graph dependency, so it's independently testable.
"""
from langchain_core.messages import HumanMessage

from model_factory import ModelRole, get_chat_model

SUMMARY_PROMPT = """Summarize this conversation so far in 3-5 sentences, preserving
any specific facts, filters, or entities the user asked about (they may be
referenced again in follow-up questions).

Previous summary (if any): {previous_summary}

New turns:
{turns}

Summary:
"""


def summarize_if_needed(
    messages: list[dict],
    existing_summary: str = "",
    keep_last: int = 6,
    provider=None,
) -> tuple[str, list[dict]]:
    """If `messages` has more than `keep_last` entries, folds everything
    before the last `keep_last` into (an updated) `existing_summary` and
    returns (summary, trimmed_messages). Otherwise returns unchanged.
    """
    if len(messages) <= keep_last:
        return existing_summary, messages

    to_summarize = messages[:-keep_last]
    kept = messages[-keep_last:]

    turns_text = "\n".join(f"{m['role']}: {m['content']}" for m in to_summarize)
    prompt = SUMMARY_PROMPT.format(previous_summary=existing_summary or "(none)", turns=turns_text)

    llm = get_chat_model(role=ModelRole.GENERAL, provider=provider)
    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content.strip(), kept
