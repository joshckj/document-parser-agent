"""
Extractor agent — its own reasoning LLM instance.

The orchestrator only routes; this agent does the actual thinking about which
parts of the parsed document the user wants. It inspects the document's block
labels, decides which one(s) match the request, and pulls them out as HTML via
its session-bound tools (get_extractor_tools).

The authoritative HTML is written to session_store by the get_blocks tool; this
agent's job is the decision + tool orchestration. run_extractor_agent returns
the agent's final text (a short confirmation), used only for logging/fallback.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from core.config import get_settings
from orchestrator.tools.extractor_tools import get_extractor_tools

logger = logging.getLogger(__name__)


EXTRACTOR_SYSTEM_PROMPT = """You are the **Extractor** — a specialist that pulls specific \
content out of a document that has already been parsed by OCR.

The document is broken into blocks, each tagged with a `block_label` such as \
`table`, `image`, `text`, or `paragraph_title`.

## How to work
1. Call `list_block_labels` first to see which block types this document actually contains.
2. Decide which label(s) best match what the user asked for. Examples:
   - "give me the table" / "extract the tables" -> `table`
   - "show the images / figures" -> `image`
   - "pull the headings" -> `paragraph_title`
   If the request is vague, choose the single most likely label.
3. Call `get_blocks(<label>)` for the label you chose. It returns an HTML fragment.
4. Reply with ONE short sentence confirming what you extracted (e.g. "Extracted 1 table.").
   Do NOT paste the HTML into your reply — it is captured automatically.

## If nothing matches
If `list_block_labels` shows the requested type is absent, or `get_blocks` finds none, \
reply in one sentence that the document has no such content. Do not invent content.
"""


def _build_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.agent_key,
        base_url=settings.agent_base_url,
        temperature=0.0,
    )


async def run_extractor_agent(user_request: str, session_id: str) -> str:
    """Run the Extractor reasoning agent for one request.

    Side effect: the get_blocks tool writes the extracted HTML fragment to
    session_store (pop_extracted(session_id) retrieves it).

    Returns the agent's final text (short confirmation / not-found message).
    """
    tools = get_extractor_tools(session_id)
    agent = create_react_agent(_build_llm(), tools, prompt=EXTRACTOR_SYSTEM_PROMPT)

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=user_request)]},
        config={"recursion_limit": 20},
    )
    last = result["messages"][-1]
    return last.content if hasattr(last, "content") else str(last)
