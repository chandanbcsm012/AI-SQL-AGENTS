"""Critic Agent -- opt-in reflection step (AGENTIC_CRITIC_ENABLED=true).

Judges whether the formatted answer actually addresses the original
question, given the executed rows. This is a quality *signal*, not a
corrective loop: on a "no" verdict it annotates state.critic_feedback
(surfaced in the UI as a warning) rather than triggering another
regeneration attempt. A real auto-correcting loop needs careful interaction
with route_after_validation's MAX_REGENERATION_ATTEMPTS cap to avoid
infinite loops -- that's a natural next step, deliberately not done here
under this pass's time constraints; see docs/IMPLEMENTATION_PLAN.md.

Disabled by default so it changes nothing about the existing, tested
pipeline unless explicitly turned on.
"""
import os

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from langchain_core.output_parsers import PydanticOutputParser
from middleware.tracing import traced_node
from model_factory import ModelRole, get_chat_model
from resilience import resilient_node

CRITIC_ENABLED = os.getenv("AGENTIC_CRITIC_ENABLED", "false").lower() == "true"

PROMPT = """You are reviewing an AI-generated answer for quality.

Question: {question}
Rows returned: {rows}
Answer given: {answer}

Does the answer correctly and completely address the question, using only
the rows shown (no invented data)? {format_instructions}
"""


class CriticVerdict(BaseModel):
    passes: bool = Field(description="true if the answer adequately addresses the question")
    feedback: str = Field(default="", description="One sentence on what's missing/wrong, empty if it passes")


_parser = PydanticOutputParser(pydantic_object=CriticVerdict)


def critic(state: dict) -> dict:
    if not CRITIC_ENABLED or state.get("status") != "success":
        return state

    prompt = PROMPT.format(
        question=state.get("user_query_masked", ""),
        rows=state.get("execution_result"),
        answer=state.get("final_answer", ""),
        format_instructions=_parser.get_format_instructions(),
    )
    provider = state.get("_force_provider")
    llm = get_chat_model(role=ModelRole.GENERAL, provider=provider)
    response = llm.invoke([HumanMessage(content=prompt)])

    try:
        verdict = _parser.parse(response.content)
    except Exception:
        return state  # can't parse a verdict -> don't block on it

    if not verdict.passes:
        state["critic_feedback"] = verdict.feedback

    return state


invoke = resilient_node()(traced_node(critic))
