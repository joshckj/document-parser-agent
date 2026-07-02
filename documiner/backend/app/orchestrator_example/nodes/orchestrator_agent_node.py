"""
LangGraph node that invokes the orchestrator Full Deep Agent.

This module acts as the bridge between the thin StateGraph wrapper
(check_cache → load_user_memory → guardrail → deep_agent → finalize)
and the orchestrator deep agent itself.

Responsibilities:
  1. Build OrchestratorAgentContext from the current OrchestratorState.
  2. Invoke the deep agent with astream, forwarding ALL custom events from
     every namespace (main agent + subagents) to the outer StreamWriter.
  3. After the agent completes, extract structured results from the message
     history and return them as OrchestratorState delta fields.

Result extraction strategy
--------------------------
The deep agent records everything in messages.  We scan in reverse for:

  - The last AIMessage (not a tool call) → orchestrator_response
  - ToolMessages whose matching AIMessage tool_call has name "task" and
    args["name"] == "charter"   → chart_config (parse JSON)
  - ToolMessages whose matching AIMessage tool_call has name "task" and
    args["name"] == "mapper"    → map_config (parse JSON)
  - ToolMessages whose matching AIMessage tool_call has name "task" and
    args["name"] == "analyzer"  → analysis (plain text)
  - ToolMessages whose matching AIMessage tool_call has name "task" and
    args["name"] == "text_to_sql" → last text_to_sql result
  - ToolMessages with tool name "prepare_temp_table" → temp_table_name,
    table_context, row_count, query
  - ToolMessages with tool name "search_knowledge" → rag_response
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Overwrite, StreamWriter

from app.orchestrator.memory.agent_context import OrchestratorAgentContext
from app.orchestrator.memory.state import OrchestratorState
from app.utils.rbac import (
    normalize_permissions_context,
    clear_session_temp_tables,
    register_session_permissions,
    clear_session_permissions,
    registry_get,
    registry_delete,
    pg_table_exists,
    register_session_temp_table,
    register_session_last_temp_table,
    register_session_table_context,
)
from app.utils.render_registry import clear_render_results, get_render_results
from app.utils.session_context import clear_session_context, register_session_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result extraction helpers
# ---------------------------------------------------------------------------

def _build_tool_call_index(messages: List[Any]) -> Dict[str, Dict[str, Any]]:
    """Build {tool_call_id: tool_call_dict} index from all AIMessage tool_calls."""
    index: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", []) or []:
            tc_id = tc.get("id") or ""
            if tc_id:
                index[tc_id] = tc
    return index


def _extract_results(messages: List[Any], session_id: str = "") -> Dict[str, Any]:
    """Scan agent messages and extract structured outputs.

    chart_config / map_config are read from the render_registry (populated by
    render_chart / render_map tools inside ui_agent) rather than from message
    content, since ui_agent streams them as SSE events rather than returning
    them as JSON.
    """
    tc_index = _build_tool_call_index(messages)

    chart_config: Optional[Dict[str, Any]] = None
    map_config: Optional[Dict[str, Any]] = None
    analysis: Optional[str] = None
    temp_table_name: Optional[str] = None
    table_context: Optional[str] = None
    row_count: Optional[int] = None
    query: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None
    rag_response: Optional[str] = None
    orchestrator_response: Optional[str] = None

    for msg in messages:
        # Final orchestrator AI message (not a tool call)
        if isinstance(msg, AIMessage):
            content = msg.content
            if content and not getattr(msg, "tool_calls", None):
                orchestrator_response = str(content)
            continue

        if not isinstance(msg, ToolMessage):
            continue

        matched_tc = tc_index.get(msg.tool_call_id or "")
        tc_name = (matched_tc or {}).get("name", "")
        tc_args = (matched_tc or {}).get("args", {})

        content_str = msg.content or ""

        # --- Subagent task results ---
        if tc_name == "task":
            subagent_name = tc_args.get("subagent_type", "") or tc_args.get("name", "")

            if subagent_name == "analyzer":
                analysis = content_str

            # ui_agent: chart_config / map_config are read from render_registry below.
            # text_to_sql result is consumed by prepare_temp_table implicitly.

        # --- Direct tool results ---
        elif tc_name == "prepare_temp_table":
            try:
                parsed = json.loads(content_str)
                if parsed.get("temp_table_name"):
                    temp_table_name = parsed["temp_table_name"]
                    table_context = parsed.get("table_context")
                    row_count = parsed.get("row_count")
                    rows = parsed.get("rows")
                    query = parsed.get("sql_query")
            except (json.JSONDecodeError, TypeError):
                pass

        elif tc_name == "search_knowledge":
            rag_response = content_str

    # Pull chart_config / map_config from the render registry (populated by
    # render_chart / render_map tools inside ui_agent during the run).
    if session_id:
        render = get_render_results(session_id)
        if render.get("chart_config"):
            chart_config = render["chart_config"]
        if render.get("map_config"):
            map_config = render["map_config"]

    return {
        "chart_config": chart_config,
        "map_config": map_config,
        "analysis": analysis,
        "temp_table_name": temp_table_name,
        "table_context": table_context,
        "row_count": row_count,
        "query": query,
        "rows": rows,
        "rag_response": rag_response,
        "orchestrator_response": orchestrator_response,
    }


def _prune_tool_traces(messages: List[Any]) -> List[Any]:
    """Keep only the final tool result per tool/subagent in the history."""
    tc_index = _build_tool_call_index(messages)
    keep_keys: set[str] = set()
    tool_keep_ids: set[str] = set()

    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue

        matched_tc = tc_index.get(msg.tool_call_id or "")
        tc_name = (matched_tc or {}).get("name", "")
        tc_args = (matched_tc or {}).get("args", {})

        if tc_name == "task":
            subagent_name = tc_args.get("subagent_type", "") or tc_args.get("name", "")
            key = f"task:{subagent_name}" if subagent_name else "task:unknown"
        else:
            key = f"tool:{tc_name}" if tc_name else "tool:unknown"

        if key in keep_keys:
            continue

        keep_keys.add(key)
        if msg.tool_call_id:
            tool_keep_ids.add(msg.tool_call_id)

    cleaned: List[Any] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            if msg.tool_call_id and msg.tool_call_id in tool_keep_ids:
                cleaned.append(msg)
            continue
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            continue
        cleaned.append(msg)

    return cleaned


async def _restore_temp_table(thread_id: str, state: OrchestratorState) -> None:
    """Ensure the temp table and RBAC registry are in sync at the start of each turn.

    Case A — table still exists in PG: re-register in session RBAC registry.
    Case B — table gone, registry has sql_query: recreate from SQL then re-register.
    Case C — table gone, no sql_query (predictions branch): clear registry row.
    """
    full_name = state.get("temp_table_name")
    if not full_name:
        return

    table_name = full_name.split(".", 1)[-1] if "." in full_name else full_name

    if await pg_table_exists(full_name):
        # Case A: table is alive — re-register so RBAC allows it this turn
        register_session_temp_table(thread_id, table_name)
        register_session_last_temp_table(thread_id, full_name)
        logger.debug("_restore_temp_table: re-registered existing %s", full_name)
        return

    # Table is gone — check registry for sql_query
    entry = await registry_get(thread_id)
    if not entry or not entry.get("sql_query"):
        if entry:
            await registry_delete(thread_id)
        logger.info("_restore_temp_table: %s gone, no sql_query to recreate", full_name)
        return

    # Case B: recreate from stored sql_query
    logger.info("_restore_temp_table: recreating %s from registry", full_name)
    try:
        import json as _json
        from app.utils.db_pool import get_async_connection_pool
        from app.utils.data_utils import is_geometry_column

        clean_query = entry["sql_query"].strip().rstrip(";")
        pool = await get_async_connection_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("CREATE SCHEMA IF NOT EXISTS speedy_temp")
                await cur.execute(f"DROP TABLE IF EXISTS {full_name}")
                await cur.execute(f"CREATE TABLE {full_name} AS ({clean_query})")
                await conn.commit()
                await cur.execute(f"SELECT * FROM {full_name} LIMIT 5")
                cols = [d.name for d in cur.description] if cur.description else []
                raw_rows = await cur.fetchall()
                rows = [dict(zip(cols, r)) for r in raw_rows]

        display_cols = [c for c in cols if not is_geometry_column(c)]
        register_session_temp_table(thread_id, table_name)
        register_session_last_temp_table(thread_id, full_name)
        table_context = (
            f"Table name: {full_name}\n"
            f"Columns: {', '.join(display_cols)}\n"
            f"First {len(rows)} rows:\n"
            f"{_json.dumps(rows, default=str, indent=2)}"
        )
        register_session_table_context(thread_id, table_context)
        logger.info("_restore_temp_table: successfully recreated %s", full_name)
    except Exception as exc:
        logger.warning("_restore_temp_table: failed to recreate %s: %s", full_name, exc)
        await registry_delete(thread_id)


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------

async def orchestrator_deep_agent_node(
    state: OrchestratorState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> Dict[str, Any]:
    """LangGraph node that invokes the orchestrator Full Deep Agent.

    Streams all custom events from the deep agent (including subagent namespaces)
    back through the outer StreamWriter so the frontend receives them.
    After completion, extracts structured results and writes them into
    OrchestratorState for the downstream finalize_success node.
    """
    from app.orchestrator.agents.orchestrator_agent import get_orchestrator_agent

    writer({"event": "orchestrator-deep-agent-start", "payload": {}})

    # Build per-invocation context from state
    permissions_raw = state.get("permissions")
    permissions_ctx = normalize_permissions_context(permissions_raw)

    ctx = OrchestratorAgentContext(
        user_id=state.get("user_id", ""),
        role=state.get("role", ""),
        permissions=permissions_ctx,
        memories=state.get("memories"),
        preferences=state.get("preferences"),
        profile=state.get("profile"),
        prompt_embedding=state.get("prompt_embedding"),
        current_chart_config=state.get("chart_config"),
        current_map_config=state.get("map_config"),
        session_id=config.get("configurable", {}).get("thread_id"),
    )

    # Pass only the current user prompt to the deep agent.
    # The deep agent owns its full conversation history via its own Postgres checkpoint
    # (same thread_id); sending existing_messages would duplicate every previous turn.
    user_prompt = state.get("rewritten_request") or state.get("user_prompt", "")
    input_messages = [HumanMessage(content=user_prompt)]

    thread_id = config.get("configurable", {}).get("thread_id", "default")
    callbacks = config.get("callbacks")

    agent_config: Dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 80,
    }
    if callbacks:
        agent_config["callbacks"] = callbacks

    # Restore temp table RBAC registration before session context is set so that
    # subagent wrappers see a populated registry if the table was recreated.
    await _restore_temp_table(thread_id, state)

    # Register session context so subagent wrapper nodes can read state without
    # relying on the LLM to relay it in task payloads.
    register_session_permissions(thread_id, permissions_ctx)
    register_session_context(
        thread_id,
        user_id=state.get("user_id", ""),
        chart_config=state.get("chart_config"),
        map_config=state.get("map_config"),
    )

    agent = get_orchestrator_agent()

    final_messages: List[Any] = []
    # Maps tool_call_id → subagent name (e.g. "analyzer") for namespace resolution.
    # Populated by accumulating streamed "task" tool call args as they arrive.
    _task_call_args: Dict[str, str] = {}
    _subagent_names: Dict[str, str] = {}
    _subagent_started: set = set()   # call_ids for which subagent-start was already emitted
    _emitted_tool_calls: set = set()  # call_ids already emitted to avoid duplicate events

    def _resolve_agent(ns: tuple) -> str:
        """Return the agent name for a given chunk namespace."""
        for part in ns:
            if part.startswith("tools:"):
                return _subagent_names.get(part[6:], "subagent")
        return "orchestrator"

    try:
        async for chunk in agent.astream(
            {"messages": input_messages},
            config=agent_config,
            context=ctx,
            stream_mode=["updates", "messages", "custom"],
            subgraphs=True,
            version="v2",
        ):
            # v2 format: every chunk is {"type": ..., "ns": (...), "data": ...}
            chunk_type: str = chunk.get("type", "")
            chunk_ns: tuple = chunk.get("ns", ())
            chunk_data = chunk.get("data")

            is_subagent = any(s.startswith("tools:") for s in chunk_ns)
            source = _resolve_agent(chunk_ns)

            if chunk_type == "messages":
                # chunk_data is (message_chunk, metadata)
                token, _metadata = chunk_data
                token_type = getattr(token, "type", "")
                tool_call_chunks = getattr(token, "tool_call_chunks", None) or []
                tool_calls = getattr(token, "tool_calls", None) or []

                if token_type == "ai":
                    if tool_call_chunks:
                        # Tool invocation starting — emit the tool name once.
                        # Also accumulate "task" tool args to learn the subagent name early.
                        for tc in tool_call_chunks:
                            call_id = tc.get("id") or ""
                            tc_name = tc.get("name") or ""
                            if tc_name == "task" and call_id:
                                _task_call_args.setdefault(call_id, "")
                            if call_id in _task_call_args:
                                _task_call_args[call_id] += tc.get("args") or ""
                                try:
                                    parsed = json.loads(_task_call_args[call_id])
                                    agent_name = parsed.get("subagent_type", "") or parsed.get("name", "")
                                    if agent_name and call_id not in _subagent_started:
                                        _subagent_names[call_id] = agent_name
                                        _subagent_started.add(call_id)
                                        writer({"event": "subagent-start", "payload": {"agent": agent_name}})
                                except json.JSONDecodeError:
                                    pass
                            if tc_name:
                                # Emit once per unique call_id (first chunk names it).
                                if call_id:
                                    if call_id not in _emitted_tool_calls:
                                        _emitted_tool_calls.add(call_id)
                                        writer({
                                            "event": "tool-call",
                                            "payload": {"tool": tc_name, "source": source},
                                        })
                                else:
                                    writer({
                                        "event": "tool-call",
                                        "payload": {"tool": tc_name, "source": source},
                                    })
                    elif tool_calls:
                        # Complete tool calls — extract subagent name from "task" args.
                        for tc in tool_calls:
                            call_id = tc.get("id") or ""
                            tc_name = tc.get("name") or ""
                            if tc_name == "task" and call_id:
                                _args = tc.get("args") or {}
                                agent_name = _args.get("subagent_type", "") or _args.get("name", "")
                                if agent_name:
                                    _subagent_names[call_id] = agent_name
                                    if call_id not in _subagent_started:
                                        _subagent_started.add(call_id)
                                        writer({"event": "subagent-start", "payload": {"agent": agent_name}})
                            if tc_name:
                                # Skip if already emitted from the chunks stream.
                                if call_id and call_id in _emitted_tool_calls:
                                    continue
                                if call_id:
                                    _emitted_tool_calls.add(call_id)
                                writer({
                                    "event": "tool-call",
                                    "payload": {"tool": tc_name, "source": source},
                                })
                    else:
                        content_blocks = getattr(token, "content_blocks", None) or []
                        if content_blocks:
                            for block in content_blocks:
                                block_type = block.get("type", "")
                                if block_type == "reasoning":
                                    reasoning_text = block.get("reasoning", "")
                                    if reasoning_text:
                                        writer({"event": "reasoning-token", "payload": {"content": reasoning_text, "source": source}})
                                elif block_type == "text":
                                    text = block.get("text", "")
                                    if text:
                                        writer({"event": "token", "payload": {"content": text, "source": source}})
                        else:
                            # Fallback: vLLM puts reasoning in additional_kwargs["reasoning_content"]
                            additional_kwargs = getattr(token, "additional_kwargs", None) or {}
                            reasoning_content = additional_kwargs.get("reasoning_content", "")
                            if reasoning_content:
                                writer({"event": "reasoning-token", "payload": {"content": reasoning_content, "source": source}})
                            content = token.content
                            if content:
                                if not isinstance(content, str):
                                    content = str(content)
                                writer({"event": "token", "payload": {"content": content, "source": source}})
                elif token_type == "tool":
                    # Tool result returned
                    tc_id = getattr(token, "tool_call_id", "") or ""
                    tool_name = getattr(token, "name", "")
                    payload: Dict[str, Any] = {"tool": tool_name, "source": source}
                    if tool_name == "task":
                        agent_name = _subagent_names.get(tc_id, "")
                        if agent_name:
                            payload["agent"] = agent_name
                    writer({"event": "tool-result", "payload": payload})

            elif chunk_type == "updates":
                # Emit a lightweight step event for main-agent node completions only
                # (subagent steps are implicit from tool-call/tool-result pairs)
                if not is_subagent:
                    for node_name in (chunk_data or {}):
                        if node_name in ("model", "tools"):
                            writer({
                                "event": "agent-step",
                                "payload": {"step": node_name},
                            })

            elif chunk_type == "custom" and isinstance(chunk_data, dict):
                # Structured progress payloads from get_stream_writer() in tools
                if "event" in chunk_data:
                    writer(chunk_data)
                else:
                    writer({"event": "tool-progress", "payload": chunk_data})

        # Retrieve final state to get messages after streaming completes
        final_state = await agent.aget_state(agent_config)
        final_messages = (final_state.values or {}).get("messages", [])

    except Exception as exc:
        logger.exception("Orchestrator deep agent invocation failed")
        writer({"event": "orchestrator-deep-agent-error", "payload": {"error": str(exc)}})
        clear_session_temp_tables(thread_id)
        clear_session_permissions(thread_id)
        clear_session_context(thread_id)
        clear_render_results(thread_id)
        return {
            "stage": "error",
            "error": str(exc),
            "orchestrator_response": f"I encountered an error: {exc}",
        }

    # Clean up session-scoped resources
    clear_session_temp_tables(thread_id)
    clear_session_permissions(thread_id)
    clear_session_context(thread_id)

    # Extract structured results from message history + render registry
    results = _extract_results(final_messages, session_id=thread_id)
    clear_render_results(thread_id)

    # Keep only final tool results per tool/subagent in the persisted history.
    try:
        cleaned_messages = _prune_tool_traces(final_messages)
        if hasattr(agent, "aupdate_state"):
            await agent.aupdate_state(
                agent_config,
                {"messages": Overwrite(cleaned_messages)},
                as_node="model",
            )
        elif hasattr(agent, "update_state"):
            agent.update_state(
                agent_config,
                {"messages": Overwrite(cleaned_messages)},
                as_node="model",
            )
    except Exception:
        logger.warning("Failed to prune tool traces from orchestrator history", exc_info=True)

    writer({
        "event": "orchestrator-deep-agent-complete",
        "payload": {
            "row_count": results.get("row_count"),
            "has_chart": results.get("chart_config") is not None,
            "has_map": results.get("map_config") is not None,
            "has_analysis": results.get("analysis") is not None,
        },
    })

    return {
        "messages": [],
        "orchestrator_response": results["orchestrator_response"],
        "query": results["query"],
        "rows": results["rows"],
        "row_count": results["row_count"],
        "temp_table_name": results["temp_table_name"],
        "table_context": results["table_context"],
        "chart_config": results["chart_config"],
        "map_config": results["map_config"],
        "analysis": results["analysis"],
        "rag_response": results["rag_response"],
        "stage": "done",
    }
