"""
Direct tools for the orchestrator Full Deep Agent.

These are single-step operations that the orchestrator calls itself (not delegated
to subagents):

  prepare_temp_table  – creates a persistent speedy_temp table from a SQL query
  search_knowledge    – RAG knowledge-base search
  update_memory       – saves a user preference to long-term store
  get_sample_questions – re-exported from existing module (unchanged)

get_stream_writer is used in prepare_temp_table to emit structured progress
payloads into the custom stream channel.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.tools import InjectedToolArg, tool
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer

from app.utils.data_utils import is_geometry_column
from app.utils.dedup import dedup_tool_call
from app.utils.rbac import (
    register_session_last_temp_table,
    register_session_temp_table,
    register_session_table_context,
    registry_get,
    registry_upsert,
)

# Tables with more columns than this threshold get a column-list summary
# instead of full sample rows to avoid bloating subagent context windows.
_WIDE_TABLE_COLUMN_THRESHOLD = 20

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool: prepare_temp_table
# ---------------------------------------------------------------------------

@tool
async def prepare_temp_table(
    predictions_key: Optional[str] = None,
    sql_query: Optional[str] = None,
    raw_data: Optional[List[Dict]] = None,
    geojson_key: Optional[str] = None,
    hex_resolution: Optional[int] = None,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
) -> str:
    """Create a temporary analysis table from a SQL query, cached prediction data, or raw dict data.

    Exactly one of sql_query, predictions_key, or raw_data must be supplied.
    Preferred priority of use is predictions_key > sql_query > raw_data, when multiple sources are available.

    Call this immediately after text_to_sql (or after call_rest_api for gasleakagent) and
    BEFORE invoking analyzer, charter, or mapper subagents.

    Args:
        predictions_key: Redis key returned after data retrieval ().
        sql_query: The validated SQL SELECT query returned by text_to_sql.
        raw_data: A list of dicts (uniform keys) to insert directly as a temp tabl (10 rows max).
        geojson_key: Optional Redis key for hex polygon GeoJSON returned alongside predictions_key.
        hex_resolution: H3 resolution used alongside geojson_key (7, 8, or 9). 

    Returns:
        JSON with: temp_table_name, table_context (column names + 5 sample rows), row_count.
    """
    # Extract thread_id for session-scoped registration
    thread_id = "default"
    if runtime and runtime.context:
        thread_id = getattr(runtime.context, "session_id", "default") or "default"

    writer = get_stream_writer()

    # Enforce mutual exclusion: exactly one source group must be provided.
    provided = sum([bool(sql_query), bool(predictions_key), raw_data is not None])
    if provided != 1:
        return json.dumps({
            "error": (
                "Exactly one of sql_query, predictions_key, or raw_data must be supplied; "
                f"got {provided}. (geojson_key is optional and only valid alongside predictions_key)"
            )
        })
    if geojson_key and not predictions_key:
        return json.dumps({"error": "geojson_key is only valid when predictions_key is also provided."})

    # ── predictions_key branch: build table from Redis-cached gasleakagent predictions ──
    if predictions_key:
        from app.text_to_sql.tools.get_schema import get_redis_client
        from app.utils.db_pool import get_async_connection_pool
        from app.utils.data_utils import strip_geom_columns

        rc = get_redis_client()
        raw = rc.get(predictions_key)
        if not raw:
            return json.dumps({
                "error": "Predictions not found or expired. Ask the user to re-run the gas leak analysis."
            })
        predictions: List[Dict] = json.loads(raw)
        rc.delete(predictions_key)

        table_name = f"gasleak_{str(uuid.uuid4())[:8]}"
        full_name = f"speedy_temp.{table_name}"

        writer({"event": "prepare-temp-table-start", "payload": {}})

        sample = predictions[0]
        col_defs = ", ".join(
            f'"{k}" NUMERIC' if isinstance(sample[k], (int, float)) else f'"{k}" TEXT'
            for k in sample
        )

        try:
            pool = await get_async_connection_pool()
            # Drop the previous temp table for this thread before creating the new one.
            old_entry = await registry_get(thread_id)
            if old_entry:
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(f"DROP TABLE IF EXISTS {old_entry['table_name']}")
                        await conn.commit()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("CREATE SCHEMA IF NOT EXISTS speedy_temp")
                    await cur.execute(f"DROP TABLE IF EXISTS {full_name}")
                    await cur.execute(f"CREATE TABLE {full_name} ({col_defs})")
                    cols = list(sample.keys())
                    col_list = ", ".join(f'"{c}"' for c in cols)
                    ph = ", ".join(["%s"] * len(cols))
                    for row in predictions:
                        await cur.execute(
                            f"INSERT INTO {full_name} ({col_list}) VALUES ({ph})",
                            [row.get(c) for c in cols],
                        )
                    await conn.commit()
        except Exception as exc:
            logger.exception("prepare_temp_table (predictions_key) failed")
            writer({"event": "prepare-temp-table-error", "payload": {"error": str(exc)}})
            return json.dumps({"error": str(exc), "temp_table_name": None})

        register_session_temp_table(thread_id, table_name)
        register_session_last_temp_table(thread_id, full_name)
        await registry_upsert(thread_id, full_name, None)  # sql_query=None: predictions branch

        display_cols = list(sample.keys())
        sample_rows = predictions[:5]
        table_context = (
            f"Table name: {full_name}\n"
            f"Columns: {', '.join(display_cols)}\n"
            f"Row count: {len(predictions)}\n"
            f"Sample (5 rows):\n{json.dumps(sample_rows, default=str, indent=2)}"
        )
        register_session_table_context(thread_id, table_context)

        # Fetch hex polygon GeoJSON if provided (gas leak hex map path)
        geojson_payload = None
        if geojson_key:
            raw_geo = rc.get(geojson_key)
            if raw_geo:
                geojson_payload = json.loads(raw_geo)
                rc.delete(geojson_key)
            else:
                logger.warning("prepare_temp_table: geojson_key %s not found in Redis", geojson_key)

        writer({
            "event": "prepare-temp-table-complete",
            "payload": {
                "table_name": full_name,
                "row_count": len(predictions),
                "rows": predictions,
                "geojson": geojson_payload,
            },
        })

        hint = (
            None if geojson_key else
            "Hex map will not be rendered: no geojson_key was provided. "
            "If this was unintentional, supply the geojson_key returned by the respective subagent."
        )
        return json.dumps({
            "temp_table_name": full_name,
            "table_context": table_context,
            "row_count": len(predictions),
            "rows": sample_rows,
            **({"hint": hint} if hint else {}),
        })

    # ── raw_data branch: caller supplies list of dicts directly ──────────────
    if raw_data is not None:
        if not raw_data:
            return json.dumps({"error": "raw_data list is empty."})
        if len(raw_data) > 10:
            return json.dumps({
                "error": (
                    f"raw_data exceeds the 10-row limit ({len(raw_data)} rows provided). "
                    "For larger datasets, use sql_query or predictions_key"
                    "returned by the respective subagent instead."
                )
            })

        from app.utils.db_pool import get_async_connection_pool

        sample = raw_data[0]
        table_name = f"raw_{str(uuid.uuid4())[:8]}"
        full_name = f"speedy_temp.{table_name}"

        col_defs = ", ".join(
            f'"{k}" NUMERIC' if isinstance(sample[k], (int, float)) else f'"{k}" TEXT'
            for k in sample
        )

        writer({"event": "prepare-temp-table-start", "payload": {}})

        try:
            pool = await get_async_connection_pool()
            old_entry = await registry_get(thread_id)
            if old_entry:
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(f"DROP TABLE IF EXISTS {old_entry['table_name']}")
                        await conn.commit()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("CREATE SCHEMA IF NOT EXISTS speedy_temp")
                    await cur.execute(f"DROP TABLE IF EXISTS {full_name}")
                    await cur.execute(f"CREATE TABLE {full_name} ({col_defs})")
                    cols = list(sample.keys())
                    col_list = ", ".join(f'"{c}"' for c in cols)
                    ph = ", ".join(["%s"] * len(cols))
                    for row in raw_data:
                        await cur.execute(
                            f"INSERT INTO {full_name} ({col_list}) VALUES ({ph})",
                            [row.get(c) for c in cols],
                        )
                    await conn.commit()
        except Exception as exc:
            logger.exception("prepare_temp_table (raw_data) failed")
            writer({"event": "prepare-temp-table-error", "payload": {"error": str(exc)}})
            return json.dumps({"error": str(exc), "temp_table_name": None})

        register_session_temp_table(thread_id, table_name)
        register_session_last_temp_table(thread_id, full_name)
        await registry_upsert(thread_id, full_name, None)

        display_cols = list(sample.keys())
        sample_rows = raw_data[:5]
        table_context = (
            f"Table name: {full_name}\n"
            f"Columns: {', '.join(display_cols)}\n"
            f"Row count: {len(raw_data)}\n"
            f"Sample (5 rows):\n{json.dumps(sample_rows, default=str, indent=2)}"
        )
        register_session_table_context(thread_id, table_context)

        writer({
            "event": "prepare-temp-table-complete",
            "payload": {
                "table_name": full_name,
                "row_count": len(raw_data),
                "rows": raw_data,
            },
        })

        return json.dumps({
            "temp_table_name": full_name,
            "table_context": table_context,
            "row_count": len(raw_data),
            "rows": sample_rows,
        })

    # ── sql_query branch: original behaviour ──────────────────────────────────
    writer({"event": "prepare-temp-table-start", "payload": {}})

    assert sql_query is not None
    table_name = f"table_{str(uuid.uuid4())[:8]}"
    clean_query = sql_query.strip().rstrip(";")

    try:
        from app.utils.db_pool import get_async_connection_pool
        from app.utils.data_utils import strip_geom_columns

        pool = await get_async_connection_pool()

        # Drop the previous temp table for this thread before creating the new one.
        old_entry = await registry_get(thread_id)
        if old_entry:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"DROP TABLE IF EXISTS {old_entry['table_name']}")
                    await conn.commit()

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("CREATE SCHEMA IF NOT EXISTS speedy_temp")
                await cur.execute(f"DROP TABLE IF EXISTS speedy_temp.{table_name}")
                await cur.execute(
                    f"CREATE TABLE speedy_temp.{table_name} AS ({clean_query})"
                )
                await conn.commit()

                # Fetch all rows — used for the SSE event so the frontend can ingest them.
                await cur.execute(f"SELECT * FROM speedy_temp.{table_name}")
                if cur.description:
                    columns = [d.name for d in cur.description]
                    all_raw_rows = await cur.fetchall()
                    all_rows = [strip_geom_columns(dict(zip(columns, r))) for r in all_raw_rows]
                    all_rows = json.loads(json.dumps(all_rows, default=str))
                else:
                    columns = []
                    all_rows = []

        row_count = len(all_rows)
        full_name = f"speedy_temp.{table_name}"
        sample_rows = all_rows[:5]

        # Non-geom column names (agents still see geom names via get_schema on
        # source tables; here we list only display columns to keep context clean).
        display_columns = [c for c in columns if not is_geometry_column(c)]

        if len(display_columns) > _WIDE_TABLE_COLUMN_THRESHOLD:
            # Wide table: list all columns + 2 sample rows to keep context lean.
            table_context = (
                f"Table name: {full_name}\n"
                f"Columns ({len(display_columns)}): {', '.join(display_columns)}\n"
                f"Row count: {row_count}\n"
                f"Sample (2 rows):\n"
                f"{json.dumps(sample_rows[:2], default=str, indent=2)}"
            )
        else:
            table_context = (
                f"Table name: {full_name}\n"
                f"First {len(sample_rows)} rows:\n"
                f"{json.dumps(sample_rows, default=str, indent=2)}"
            )

        if hex_resolution is not None:
            table_context += f"\nHex resolution: {hex_resolution}"

        # Register this temp table and context after formatting the context string.
        register_session_temp_table(thread_id, table_name)
        register_session_last_temp_table(thread_id, full_name)
        register_session_table_context(thread_id, table_context)
        await registry_upsert(thread_id, full_name, sql_query)  # persist for cross-turn survival

        # Single authoritative event: includes all rows + sql_query so the frontend
        # can ingest directly. Rows are NOT repeated in the tool return value.
        writer({
            "event": "prepare-temp-table-complete",
            "payload": {
                "table_name": full_name,
                "row_count": row_count,
                "rows": all_rows,
                "sql_query": sql_query,
            },
        })

        return json.dumps({
            "temp_table_name": full_name,
            "table_context": table_context,
            "row_count": row_count,
            "sql_query": sql_query,
        })

    except Exception as exc:
        logger.exception("prepare_temp_table failed")
        writer({"event": "prepare-temp-table-error", "payload": {"error": str(exc)}})
        return json.dumps({
            "error": str(exc),
            "temp_table_name": None,
            "table_context": None,
            "row_count": 0,
        })


# ---------------------------------------------------------------------------
# Tool: search_knowledge
# ---------------------------------------------------------------------------

@tool
@dedup_tool_call
async def search_knowledge(
    query: str,
    top_k: int = 5,
    indexes: Optional[List[str]] = None,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
) -> str:
    """Search the knowledge base for domain information, documentation, and business logic.

    Args:
        query: Natural-language question to search for.
        top_k: Number of results to return (default 5).
        indexes: Which logical index(es) to search ['domain', 'metadata']. If not provided, searches the domain index only.

    Returns:
        Formatted text with relevant knowledge chunks and their source references.
    """
    writer = get_stream_writer()

    try:
        from app.tools.azure_search import azure_ai_search

        result = await azure_ai_search.ainvoke({"query": query, "top_k": top_k, "indexes": indexes})  

        if isinstance(result, tuple):
            text, docs = result
            writer({
                "event": "rag-knowledge-retrieved",
                "payload": {"chunk_count": len(docs)},
            })
            return text or "No results found."

        return str(result) if result else "No results found."

    except Exception as exc:
        logger.exception("search_knowledge failed")
        return f"Knowledge search failed: {exc}"


# ---------------------------------------------------------------------------
# Tool: update_memory
# ---------------------------------------------------------------------------

@tool
@dedup_tool_call
async def update_memory(
    instruction: str,
    runtime: Annotated[ToolRuntime, InjectedToolArg],
) -> str:
    """Save or remove a user preference in long-term memory.

    Call this when the user explicitly asks to remember, save, or forget something
    about their preferences or profile.

    Args:
        instruction: What to remember or forget (e.g. "I prefer bar charts over pie charts").

    Returns:
        Confirmation message.
    """
    user_id = ""
    role = ""
    if runtime and runtime.context:
        user_id = getattr(runtime.context, "user_id", "") or ""
        role = getattr(runtime.context, "role", "") or ""

    try:
        from langchain_core.messages import HumanMessage
        from app.memory.graph.memory_graph import get_memory_graph

        memory_graph = await get_memory_graph()

        initial_state = {
            "messages": [HumanMessage(content=instruction)],
            "user_id": user_id,
            "user_prompt": instruction,
            "role": role,
        }

        config = {"configurable": {"thread_id": f"memory:{user_id}"}}
        result = await memory_graph.ainvoke(initial_state, config=config)  # type: ignore[arg-type]

        messages = result.get("messages", [])
        last_msg = messages[-1] if messages else None
        return last_msg.content if last_msg else "Memory updated successfully."

    except Exception as exc:
        logger.exception("update_memory failed")
        return f"Memory update failed: {exc}"

