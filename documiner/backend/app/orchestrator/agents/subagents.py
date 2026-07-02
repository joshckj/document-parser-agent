"""
SubAgent definitions for the Documiner orchestrator.

The Extractor is a CompiledSubAgent wrapping a thin one-node StateGraph. The
node runs the inner Extractor reasoning agent (its own LLM instance) and
packages the HTML it produced into a magic keyword (`render_blocks(<ref>)`)
that the orchestrator embeds verbatim and the graph's finalize step expands.

Mirrors orchestrator_example/agents/subagents.py, minus the SQL/RBAC machinery.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List

from deepagents import CompiledSubAgent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from orchestrator import session_store
from orchestrator.agents.extractor_agent import run_extractor_agent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (task parsing — copied from the example, graceful fallback)
# ---------------------------------------------------------------------------

def _parse_task(task_text: str) -> Dict[str, Any]:
    stripped = task_text.strip()
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"user_request": stripped}


def _last_human_content(messages: List[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content or ""
    return ""


# ---------------------------------------------------------------------------
# Extractor subagent
# ---------------------------------------------------------------------------

async def _extractor_node(state: Dict[str, Any], config, writer) -> Dict[str, Any]:
    """Thin wrapper: runs the inner Extractor agent, packages HTML as a keyword."""
    task_text = _last_human_content(state.get("messages", []))
    payload = _parse_task(task_text)
    user_request: str = payload.get("user_request", task_text)

    session_id = (config or {}).get("configurable", {}).get("thread_id", "") or ""

    try:
        confirmation = await run_extractor_agent(user_request, session_id)
    except Exception as exc:
        logger.exception("extractor subagent failed")
        return {
            "messages": [
                AIMessage(
                    content=json.dumps({"error": "extraction_failed", "reason": str(exc)[:300]}),
                    name="extractor",
                )
            ]
        }

    html = session_store.pop_extracted(session_id)
    if not html:
        # Nothing extracted — relay the agent's own not-found sentence.
        return {"messages": [AIMessage(content=confirmation, name="extractor")]}

    ref = f"blk_{uuid.uuid4().hex[:10]}"
    session_store.set_rendered_block(session_id, ref, html)
    # Return the magic keyword for the orchestrator to embed verbatim.
    return {"messages": [AIMessage(content=f"render_blocks({ref})", name="extractor")]}


def _make_extractor_graph():
    builder = StateGraph(MessagesState)
    builder.add_node("run", _extractor_node)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile()


def make_extractor_subagent() -> CompiledSubAgent:
    return CompiledSubAgent(
        name="extractor",
        description=(
            "Use this to extract specific content from the document the user has "
            "parsed — tables, images, headings, or any block type — and render it. "
            "The extractor inspects the document, decides which blocks match the "
            "request, and returns a `render_blocks(<ref>)` keyword. Embed that "
            "keyword VERBATIM in your final answer where the content should appear. "
            'Pass a JSON task with user_request only, e.g. {"user_request": "give me the table"}.'
        ),
        runnable=_make_extractor_graph(),
    )
