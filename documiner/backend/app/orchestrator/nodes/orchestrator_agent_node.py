"""
Graph node that runs the Documiner orchestrator deep agent.

Invokes the compiled deep agent with the conversation, using the session_id as
the thread_id so the extractor subagent can resolve the cached OCR JSON. Writes
the agent's final text into state["answer"].
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from orchestrator.agents.orchestrator_agent import get_documiner_agent

logger = logging.getLogger(__name__)


def _final_text(messages: list) -> str:
    """Extract the last assistant text (handles str or content-block lists)."""
    for msg in reversed(messages):
        if getattr(msg, "type", "") != "ai":
            continue
        if getattr(msg, "tool_calls", None):
            continue
        content = msg.content
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "".join(
                block.get("text", "") if isinstance(block, dict) else ""
                for block in content
                if not (isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"))
            ).strip()
            if text:
                return text
    return ""


async def orchestrator_agent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    session_id = state.get("session_id") or ""
    messages = state.get("messages", [])

    agent = get_documiner_agent()
    try:
        result = await agent.ainvoke(
            {"messages": messages},
            config={"configurable": {"thread_id": session_id}, "recursion_limit": 40},
        )
    except Exception as exc:
        logger.exception("orchestrator agent node failed")
        return {"answer": "Sorry — something went wrong handling that request.", "stage": "error", "error": str(exc)[:300]}

    answer = _final_text(result.get("messages", [])) or "Done."
    return {"answer": answer, "stage": "done"}
