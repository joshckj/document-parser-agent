"""
Subagent definitions for the orchestrator Full Deep Agent.

Each subagent is a CompiledSubAgent wrapping a thin LangGraph StateGraph.
The thin graph runs the existing agent implementation and returns a structured
JSON final message so the parent orchestrator can reliably parse results.

Subagents:
  text_to_sql  – Phase 1 deep agent; returns {"query", "row_count", "stage"}
  analyzer     – Phase 2 deep agent; returns analysis text
  ui_agent     – Renders charts, maps, and tables; returns magic keywords

The parent orchestrator passes task messages as JSON:
    {
        "user_request": "..."
    }

Each thin graph parses this format; all fields are optional so unstructured
task messages still work as a fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from deepagents import CompiledSubAgent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from app.utils.rbac import (
    get_session_last_temp_table,
    get_session_permissions,
    get_session_table_context,
    normalize_permissions_context,
)
from app.utils.session_context import get_session_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_task(task_text: str) -> Dict[str, Any]:
    """Try to parse a JSON task payload from the task message.

    Falls back gracefully: returns {"user_request": task_text} if not JSON.
    """
    stripped = task_text.strip()
    # Try JSON block first
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare JSON
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


def _resolve_table_context(session_id: str, temp_table_name: str, relayed_context: str) -> str:
    """Return the authoritative table_context for a session.

    Reads from the session registry populated by prepare_temp_table (same
    pattern as permissions). Falls back to whatever the orchestrator LLM
    relayed, then to a bare table-name string.
    """
    cached = get_session_table_context(session_id)
    if cached:
        return cached
    # Fallback: use the relayed context, ensuring the table name is present
    if relayed_context and temp_table_name in relayed_context:
        return relayed_context
    return f"Table name: {temp_table_name}\n{relayed_context or 'No sample data available'}"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _subagent_cache_namespace(
    session_id: str,
    subagent: str,
    table_context: str = "",
) -> Tuple[str, dict]:
    """Return (namespace, user_payload) for a subagent semantic cache call.

    Namespace format: "{role}:{user_id}:{subagent}:{table_hash}"
    Including the table_context hash means the same request with different
    underlying data gets a different namespace, preventing false cache hits.
    """
    session_ctx = get_session_context(session_id) or {}
    permissions = get_session_permissions(session_id) or {}
    user_id = session_ctx.get("user_id", "")
    role = permissions.get("role", "default")
    _hashable = "\n".join(
        line for line in table_context.splitlines()
        if not line.startswith("Table name:")
    ).strip()
    table_hash = (
        hashlib.md5(_hashable[:500].encode()).hexdigest()[:8]
        if _hashable and table_context != "No table context available"
        else "notbl"
    )
    namespace = f"{role}:{user_id}:{subagent}:{table_hash}"
    user_payload = {"sub": user_id, "role": role}
    return namespace, user_payload


# ---------------------------------------------------------------------------
# text_to_sql subagent
# ---------------------------------------------------------------------------

async def _text_to_sql_node(state: Dict[str, Any], config, writer) -> Dict[str, Any]:
    """Thin wrapper: runs text_to_sql deep agent, returns structured JSON summary."""
    from app.text_to_sql.agents.sql_agent import text_to_sql_agent_node

    task_text = _last_human_content(state.get("messages", []))
    payload = _parse_task(task_text)

    # Propagate the parent session_id so text_to_sql uses a stable thread_id.
    session_id = (config or {}).get("configurable", {}).get("thread_id", "") or ""

    # Read permissions from session registry (set by orchestrator_agent_node before
    # invocation). This avoids the LLM relaying permissions through task message text.
    permissions = normalize_permissions_context(get_session_permissions(session_id))

    # Pass empty messages — text_to_sql owns its history via its own checkpoint.
    extended_state = {
        "messages": [],
        "user_prompt": payload.get("user_request", task_text),
        "permissions": permissions,
        "session_id": session_id,
        "clear_history": False,
    }

    result = await text_to_sql_agent_node(extended_state, writer)

    rows = result.get("rows") or []
    summary: Dict[str, Any] = {
        "query": result.get("query"),
        "row_count": result.get("row_count", 0),
        "stage": result.get("stage", "done"),
    }
    # Include rows when the result set is small so the orchestrator can answer
    # schema/metadata questions (e.g. "which columns are monetary?") directly
    # without re-issuing the same query. Without rows, the orchestrator only
    # sees a row count and loops endlessly trying to surface the data.
    if rows and len(rows) <= 30:
        summary["rows"] = rows
    if result.get("key_findings"):
        summary["key_findings"] = result["key_findings"]
    if result.get("hex_resolution") is not None:
        summary["hex_resolution"] = result["hex_resolution"]

    return {"messages": [AIMessage(content=json.dumps(summary), name="text_to_sql")]}


def _make_text_to_sql_graph():
    builder = StateGraph(MessagesState)
    builder.add_node("run", _text_to_sql_node)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile()


def make_text_to_sql_subagent() -> CompiledSubAgent:
    return CompiledSubAgent(
        name="text_to_sql",
        description=(
            "Use this to answer questions that require querying structured data from the "
            "database. Translates natural language to SQL, validates, and executes it. "
            "Returns JSON with the executed SQL query, row_count, stage, and rows (when "
            "the result set is 30 rows or fewer). Use the rows directly to answer "
            "schema or metadata questions (e.g. column names, data types). "
            "For larger result sets or when analysis/visualization is needed, call "
            "prepare_temp_table with the returned SQL query before running analysis or "
            "visualization subagents. Pass a JSON task with user_request only."
        ),
        runnable=_make_text_to_sql_graph(),
    )


# ---------------------------------------------------------------------------
# analyzer subagent
# ---------------------------------------------------------------------------

async def _analyzer_node(state: Dict[str, Any], config, writer) -> Dict[str, Any]:
    """Thin wrapper: parses task message, creates DB-bound tools, runs analyzer."""
    task_text = _last_human_content(state.get("messages", []))
    payload = _parse_task(task_text)

    user_request: str = payload.get("user_request", task_text)
    session_id = (config or {}).get("configurable", {}).get("thread_id", "") or ""
    table_context: str = get_session_table_context(session_id) or "No table context available"
    temp_table_name: Optional[str] = get_session_last_temp_table(session_id)

    # Extract table name from table_context as fallback
    if not temp_table_name:
        m = re.search(r"Table name:\s*(speedy_temp\.\w+)", table_context)
        if m:
            temp_table_name = m.group(1)

    # --- Semantic cache check ---
    _cache_ns, _cache_payload = _subagent_cache_namespace(session_id, "analyzer", table_context)
    try:
        from app.core.config import get_settings as _get_settings
        # if not _get_settings().superintern_mode:
        from app.utils.cache import get_semantic_cache_embedding
        _cached = await get_semantic_cache_embedding(user_request, _cache_payload, namespace=_cache_ns)
        if _cached and _cached.get("value") and _cached.get("key"):
            _analysis = _cached["value"]
            if isinstance(_analysis, str):
                try:
                    _parsed = json.loads(_analysis)
                    if isinstance(_parsed, str):
                        _analysis = _parsed
                except Exception:
                    pass
            logger.info("analyzer cache hit for request: %.80s", user_request)
            writer({"event": "analyzer-complete", "payload": {"insights": _analysis, "tool_calls": []}})
            return {"messages": [AIMessage(content=_analysis, name="analyzer")]}
    except Exception as _ce:
        logger.warning("analyzer cache check failed (non-fatal): %s", _ce)

    try:
        from app.core.config import get_settings
        from app.observability.langfuse_client import init_langfuse
        from app.orchestrator.agents.analyzer_agent import (
            create_analyzer_agent,
            run_analyzer_agent,
        )
        from app.orchestrator.tools.analyzer_tools import get_analyzer_tools
        from app.utils.db_pool import get_async_readonly_connection_pool

        settings = get_settings()
        langfuse = init_langfuse(settings)
        pool = await get_async_readonly_connection_pool()

        user_request_with_table = (
            f"Analyze {temp_table_name} table. {user_request}"
            if temp_table_name
            else user_request
        )
        context_with_instruction = table_context + (
            f"\n\nIMPORTANT: The data is stored in '{temp_table_name}'. "
            "Use this exact table name in queries. Do not wrap the full name in "
            'double quotes — quote schema and table separately if needed.'
            if temp_table_name
            else ""
        )

        analyzer_thread_id = f"{session_id}_analyzer" if session_id else "analyzer"

        from app.orchestrator.agents.analyzer_agent import get_analyzer_checkpointer
        async with pool.connection() as conn:
            tools = get_analyzer_tools(
                conn,
                allowed_tables=[temp_table_name] if temp_table_name else None,
            )
            agent = create_analyzer_agent(
                settings=settings,
                langfuse=langfuse,
                tools=tools,
                checkpointer=get_analyzer_checkpointer(),
            )
            result = await run_analyzer_agent(
                agent=agent,
                user_request=user_request_with_table,
                table_context=context_with_instruction,
                langfuse=langfuse,
                session_id=temp_table_name or "",
                thread_id=analyzer_thread_id,
                writer=writer,
            )

        analysis = result.get("insights", "Unable to perform analysis.")

        # --- Semantic cache store ---
        if analysis and analysis != "Unable to perform analysis.":
            try:
                from app.utils.cache import store_semantic_cache_result
                await store_semantic_cache_result(user_request, json.dumps(analysis), _cache_payload, namespace=_cache_ns)
            except Exception as _se:
                logger.warning("analyzer cache store failed (non-fatal): %s", _se)

        return {"messages": [AIMessage(content=analysis, name="analyzer")]}

    except Exception as exc:
        logger.exception("analyzer subagent node failed")
        return {
            "messages": [
                AIMessage(
                    content=json.dumps({
                        "error": "analysis_failed",
                        "reason": str(exc)[:300],
                        "suggestion": "The analyzer encountered an error. Try simplifying the request or inform the user.",
                    }),
                    name="analyzer",
                )
            ]
        }


def _make_analyzer_graph():
    builder = StateGraph(MessagesState)
    builder.add_node("run", _analyzer_node)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile()


def make_analyzer_subagent() -> CompiledSubAgent:
    return CompiledSubAgent(
        name="analyzer",
        description=(
            "Use this to generate analytical insights, trends, and business commentary "
            "from a SQL result set already stored in a temp table. Returns written analysis "
            "text. Pass a JSON task with user_request only."
        ),
        runnable=_make_analyzer_graph(),
    )



# ---------------------------------------------------------------------------
# ui_agent subagent
# ---------------------------------------------------------------------------

async def _ui_agent_node(state: Dict[str, Any], config, writer) -> Dict[str, Any]:
    """Thin wrapper: injects session context and runs the ui_agent."""
    task_text = _last_human_content(state.get("messages", []))
    payload = _parse_task(task_text)

    user_request: str = payload.get("user_request", task_text)
    session_id = (config or {}).get("configurable", {}).get("thread_id", "") or ""

    session_context = get_session_context(session_id)
    existing_chart_config: Optional[Dict[str, Any]] = session_context.get("chart_config")
    existing_map_config: Optional[Dict[str, Any]] = session_context.get("map_config")

    # Fetch user preferences from the store
    user_id = session_context.get("user_id", "")
    user_preferences = "None"
    user_profile = "None"
    if user_id:
        try:
            from app.utils.store import get_store
            from app.utils.namespace import make_namespace
            store = await get_store()
            pref_items = await store.asearch(make_namespace("preferences", user_id), limit=10)
            if pref_items:
                user_preferences = "\n".join(
                    item.value.get("content", "") for item in pref_items
                    if item.value and item.value.get("content")
                ) or "None"
            profile_item = await store.aget(make_namespace("preferences", user_id), "profile")
            if profile_item and profile_item.value:
                user_profile = profile_item.value.get("content", "None") or "None"
        except Exception as exc:
            logger.warning("ui_agent: failed to fetch preferences for user %s: %s", user_id, exc)

    permissions = normalize_permissions_context(get_session_permissions(session_id))
    permissions_str = json.dumps(permissions, indent=2) if permissions else "None"

    try:
        from app.core.config import get_settings
        from app.observability.langfuse_client import init_langfuse
        from app.orchestrator.agents.ui_agent import UIAgentContext, get_ui_agent

        settings = get_settings()
        langfuse = init_langfuse(settings)

        ui_context = UIAgentContext(
            user_preferences=user_preferences,
            user_profile=user_profile,
            permissions=permissions_str,
            existing_chart_config=existing_chart_config,
            existing_map_config=existing_map_config,
        )

        ui_thread_id = f"{session_id}_ui_agent" if session_id else "ui_agent"
        agent = get_ui_agent(langfuse=langfuse)

        final_content = user_request
        async for chunk in agent.astream(
            {"messages": [HumanMessage(content=user_request)]},
            config={"configurable": {"thread_id": ui_thread_id}, "recursion_limit": 40},
            context=ui_context,
            stream_mode=["updates", "custom"],
            version="v2",
        ):
            chunk_type = chunk.get("type", "")
            chunk_data = chunk.get("data")

            # Forward custom events (charter-complete, mapper-complete) upstream
            if chunk_type == "custom" and isinstance(chunk_data, dict) and "event" in chunk_data:
                writer(chunk_data)
            elif chunk_type == "updates" and isinstance(chunk_data, dict):
                # Extract final AI message from state updates.
                # Use updates (not message stream) so the complete message is available,
                # avoiding issues with extended thinking where content is a list, not a string.
                for node_update in chunk_data.values():
                    if not isinstance(node_update, dict):
                        continue
                    for msg in reversed(node_update.get("messages", [])):
                        if getattr(msg, "type", "") != "ai":
                            continue
                        if getattr(msg, "tool_calls", None):
                            continue
                        content = msg.content
                        if isinstance(content, str) and content.strip():
                            final_content = content
                            break
                        elif isinstance(content, list):
                            # Filter out thinking/redacted_thinking blocks (extended thinking)
                            text = "".join(
                                block.get("text", "") if isinstance(block, dict) else ""
                                for block in content
                                if not (isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"))
                            ).strip()
                            if text:
                                final_content = text
                                break

        return {"messages": [AIMessage(content=final_content, name="ui_agent")]}

    except Exception as exc:
        logger.exception("ui_agent subagent node failed")
        return {
            "messages": [
                AIMessage(
                    content=json.dumps({
                        "error": "ui_rendering_failed",
                        "reason": str(exc)[:300],
                    }),
                    name="ui_agent",
                )
            ]
        }


def _make_ui_agent_graph():
    builder = StateGraph(MessagesState)
    builder.add_node("run", _ui_agent_node)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile()


def make_ui_agent_subagent() -> CompiledSubAgent:
    return CompiledSubAgent(
        name="ui_agent",
        description=(
            "Use this to render UI components — charts, maps, and/or inline tables. "
            "Pass a plain-English instruction describing what to visualize. "
            "Always include the temp_table_name explicitly — the agent previews it to discover columns. "
            "User preferences and existing configs are auto-injected. "
            "You may request multiple components in one call "
            "(e.g. 'bar chart of sales by zone using speedy_temp.table_abc and a map of incident hotspots using speedy_temp.table_xyz'). "
            "To update an existing UI component, pass in the component id "
            "(e.g. 'use red color instead of blue for chart_xy123456'). "
        ),
        runnable=_make_ui_agent_graph(),
    )