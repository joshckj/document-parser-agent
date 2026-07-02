"""
Public entrypoint for the Documiner chat agent.

`invoke_deep_agent` drives the orchestrator StateGraph (router deep agent +
finalize). It keeps its original signature so the /chat endpoint contract is
stable, plus a `session_id` used to resolve the cached OCR JSON for extraction.
"""

from __future__ import annotations

from dotenv import load_dotenv

from langchain_core.messages import AIMessage, HumanMessage

from orchestrator.graph.orchestrator_graph import get_graph

load_dotenv()


async def invoke_deep_agent(
    messages: list[dict[str, str]],
    session_id: str = "",
    agent_model: str | None = None,
    agent_key: str | None = None,
    agent_base_url: str | None = None,
) -> str:
    """Run the orchestrator graph over a chat history and return the reply.

    agent_model / agent_key / agent_base_url are accepted for backward
    compatibility; the model is configured centrally via core.config.settings.
    """
    lc_messages: list[HumanMessage | AIMessage] = []
    for m in messages:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))

    graph = get_graph()
    result = await graph.ainvoke(
        {"messages": lc_messages, "session_id": session_id, "user_prompt": _last_user(messages)}
    )
    return result.get("answer") or "Done."


def _last_user(messages: list[dict[str, str]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""
