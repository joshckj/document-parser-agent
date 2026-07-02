"""
Documiner orchestrator deep agent (router).

A single `create_deep_agent` that classifies intent and delegates:
  - parse a document        -> call_ocr_api tool
  - extract/render content  -> extractor subagent (its own reasoning instance)
  - simple Q&A / greeting   -> answer directly

The orchestrator never extracts blocks itself; it embeds the extractor's
`render_blocks(<ref>)` keyword verbatim in its final answer.

Public surface:
    get_documiner_agent(checkpointer) -> compiled deep agent (singleton)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI

from core.config import get_settings
from orchestrator.agents.subagents import make_extractor_subagent
from orchestrator.tools.call_parser_api import call_ocr_api

logger = logging.getLogger(__name__)

# prompt_registry/documiner.md lives at the repo root.
# This file: app/orchestrator/agents/orchestrator_agent.py
#   parents[0]=agents [1]=orchestrator [2]=app [3]=backend [4]=documiner [5]=repo root
_PROMPT_PATH = Path(__file__).resolve().parents[5] / "prompt_registry" / "documiner.md"

_FALLBACK_PROMPT = """You are **Documiner**, a document intelligence assistant. \
You coordinate parsing and extraction; you do not extract blocks yourself.

- To parse a document from a file path, use the `call_ocr_api` tool.
- When the user wants specific content rendered (a table, images, headings, etc.), \
delegate to the `extractor` subagent with a JSON task {"user_request": "<their ask>"}. \
The extractor returns a `render_blocks(<ref>)` keyword — embed it VERBATIM in your reply \
where the content should appear.
- For greetings or simple questions, answer directly.
Keep replies concise."""


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        raw = _PROMPT_PATH.read_text(encoding="utf-8")
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return raw.strip()
    return _FALLBACK_PROMPT


def _build_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.agent_key,
        base_url=settings.agent_base_url,
        temperature=0.3,
    )


def _build_agent(checkpointer: Optional[Any] = None) -> Any:
    agent = create_deep_agent(
        model=_build_llm(),
        tools=[call_ocr_api],
        subagents=[make_extractor_subagent()],
        system_prompt=_load_system_prompt(),
        checkpointer=checkpointer,
    )
    logger.info("Documiner orchestrator deep agent compiled")
    return agent


# ---------------------------------------------------------------------------
# Singleton (one instance per checkpointer)
# ---------------------------------------------------------------------------

_agent_instance: Optional[Any] = None
_agent_checkpointer_id: Optional[int] = None


def get_documiner_agent(checkpointer: Optional[Any] = None) -> Any:
    global _agent_instance, _agent_checkpointer_id
    cp_id = id(checkpointer) if checkpointer is not None else None
    if _agent_instance is None or (checkpointer is not None and cp_id != _agent_checkpointer_id):
        _agent_instance = _build_agent(checkpointer=checkpointer)
        _agent_checkpointer_id = cp_id
    return _agent_instance
