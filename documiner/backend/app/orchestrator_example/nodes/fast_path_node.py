"""
Fast-path node for deterministic, non-agentic responses.

Pattern
-------
Each fast-path scenario is a handler that returns a FastPathResult:
  - answer: str          — stored in state, picked up by the done SSE event
  - events: list[dict]   — ordered {event, data} pairs streamed as SSE

The node emits every event via StreamWriter so the frontend receives the same
SSE shape it already handles for agentic responses. No temp tables are created
in Postgres — rows are inlined in the SSE payload and the frontend ingests them
directly into PGlite (same mechanism as the gasleakagent predictions path).

To add a new fast-path scenario:
  1. Write a handler: async def _handle_<name>(state) -> FastPathResult
  2. Add its detection patterns to _FAST_PATH_REGISTRY
  3. Register the handler in _HANDLERS

Why we don't trust RBAC alone (data_access scenario)
-----------------------------------------------------
RBAC may grant access to lv_network.substation, but lv_network might not appear
in POSTGRES_SCHEMAS or might simply not exist in Postgres. Two filters applied:
  1. synchronize_permissions() — strips grants whose schema is absent from the
     configured POSTGRES_SCHEMAS list.
  2. Postgres query scoped to effective_schemas — only real BASE TABLE objects
     that exist right now are returned.
  3. For table-level enforcement, results are further filtered to the exact set
     of RBAC-granted tables.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, NamedTuple

from langgraph.types import StreamWriter

from app.core.config import get_settings
from app.orchestrator.memory.state import OrchestratorState
from app.utils.db_pool import get_async_connection_pool
from app.utils.rbac import (
    get_effective_schemas,
    normalize_permissions_context,
    synchronize_permissions,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class FastPathResult(NamedTuple):
    answer: str                   # stored in state → returned in the done SSE event
    events: List[Dict[str, Any]]  # ordered list of {event, data} dicts to SSE-stream


# ---------------------------------------------------------------------------
# Registry: intent name → detection phrases
# Keep patterns unambiguous — false positives (routing a complex query here
# and silently skipping the agent) are worse than false negatives.
# ---------------------------------------------------------------------------

_FAST_PATH_REGISTRY: Dict[str, List[str]] = {
    "data_access": [
        "what data do i have access to",
        "what data can i access",
        "what tables do i have access to",
        "what tables can i access",
        "what schemas do i have access to",
        "what schemas can i access",
        "what datasets do i have access to",
        "what datasets can i access",
        "what data is available to me",
        "what tables are available to me",
        "show me my data access",
        "show my data access",
        "my accessible tables",
        "my accessible schemas",
    ],
    # Add new intents here, e.g.:
    # "my_role": ["what is my role", "what role do i have", "what permissions do i have"],
}


def detect_fast_path_intent(user_prompt: str) -> str | None:
    """Return the intent name if the prompt maps to a fast-path scenario, else None."""
    lowered = (user_prompt or "").lower().strip()
    for intent, patterns in _FAST_PATH_REGISTRY.items():
        if any(pattern in lowered for pattern in patterns):
            return intent
    return None


def is_fast_path_query(user_prompt: str) -> bool:
    """Convenience wrapper used by the graph routing edge."""
    return detect_fast_path_intent(user_prompt) is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Stable PGlite table name for the data-access result.
# No UUID suffix — PGlite is per-browser-tab, so overwriting on repeat
# queries is the correct behaviour.
_DATA_ACCESS_TABLE = "speedy_temp.data_access"


async def _fetch_table_metadata(schemas: List[str]) -> List[Dict[str, Any]]:
    """
    Return [{schema, table_name, table_comment, row_count}] for all BASE TABLEs
    in the given schemas.

    row_count: pg_class.reltuples — planner estimate updated by VACUUM/autovacuum,
    no table scan required. n_live_tup from pg_stat_user_tables is avoided because
    it is only populated after ANALYZE runs, producing 0 for all un-analyzed tables.
    reltuples is -1 for tables that have never been vacuumed; we surface that as 0.

    table_comment: obj_description(c.oid, 'pg_class') — avoids the ::regclass cast
    that can fail if a table name collides with a type name.
    """
    if not schemas:
        return []

    pool = await get_async_connection_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    n.nspname                                        AS schema,
                    c.relname                                        AS table_name,
                    obj_description(c.oid, 'pg_class')              AS table_comment,
                    GREATEST(c.reltuples::bigint, 0)                AS row_count
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = ANY(%s)
                  AND c.relkind = 'r'
                ORDER BY n.nspname, c.relname
                """,
                (schemas,),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Scenario handlers
# ---------------------------------------------------------------------------

async def _handle_data_access(state: OrchestratorState) -> FastPathResult:
    """
    Answer "what data do I have access to?" by streaming rows directly to the
    frontend via prepare-temp-table-complete — no real Postgres temp table created.

    The frontend ingests the rows into PGlite and renders a table component when
    it sees render_table(...) in the token text, exactly as it does for the
    gasleakagent predictions path.

    Columns streamed: schema, table_name, table_comment, row_count

    SSE sequence:
      fast-path-start          — signals fast-path handling to the frontend
      prepare-temp-table-start — standard loading indicator
      prepare-temp-table-complete — rows inlined; frontend ingests into PGlite
      token                    — intro text with render_table(...) directive
      fast-path-complete       — completion signal
    """
    settings = get_settings()
    configured_schemas: List[str] = settings.get_postgres_schemas()

    raw_permissions = state.get("permissions") or {}
    role = state.get("role") or ""

    permissions = normalize_permissions_context(raw_permissions, role=role)

    # Layer 1: strip grants whose schema isn't in POSTGRES_SCHEMAS
    synced = synchronize_permissions(permissions, configured_schemas)
    effective_schemas = get_effective_schemas(synced, configured_schemas)

    # Layer 2: fetch only tables that actually exist in Postgres right now,
    # with comment and estimated row count
    try:
        rows = await _fetch_table_metadata(effective_schemas)
    except Exception as exc:
        logger.warning("_handle_data_access: metadata query failed: %s", exc)
        rows = []

    # Layer 3 (table-level enforcement): keep only RBAC-granted tables
    allowed_tables_rbac = {
        str(t).lower() for t in synced.get("allowed_tables", []) if str(t).strip()
    }
    if allowed_tables_rbac:
        rows = [
            r for r in rows
            if f"{r['schema']}.{r['table_name']}".lower() in allowed_tables_rbac
        ]

    row_count = len(rows)

    if rows:
        intro = (
            f"Here is the data you currently have access to "
            f"({row_count} table{'s' if row_count != 1 else ''}):\n\n"
            f"render_table({_DATA_ACCESS_TABLE})"
        )
    else:
        intro = (
            "Based on your current permissions, no datasets are currently "
            "available to you in the system. Please contact your administrator "
            "if you believe this is incorrect."
        )

    events: List[Dict[str, Any]] = [
        {
            "event": "fast-path-start",
            "data": {"intent": "data_access"},
        },
        {
            "event": "prepare-temp-table-start",
            "data": {},
        },
        {
            "event": "prepare-temp-table-complete",
            "data": {
                "table_name": _DATA_ACCESS_TABLE,
                "row_count": row_count,
                "rows": rows,
            },
        },
        {
            "event": "token",
            "data": {"content": intro, "source": "fast-path"},
        },
        {
            "event": "fast-path-complete",
            "data": {"intent": "data_access"},
        },
    ]

    return FastPathResult(answer=intro, events=events)


# ---------------------------------------------------------------------------
# Dispatch table: intent name → handler
# ---------------------------------------------------------------------------

_HANDLERS = {
    "data_access": _handle_data_access,
}


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

async def fast_path_node(
    state: OrchestratorState,
    writer: StreamWriter,
) -> Dict[str, Any]:
    """
    Dispatch to the appropriate fast-path handler and stream its events.

    The handler returns an ordered list of {event, data} dicts; this node
    emits each one via StreamWriter so the SSE layer forwards them exactly
    as it does for agentic events. stage='answer' tells finalize_success to
    pass through without caching (result is user-specific and time-sensitive).
    """
    user_prompt = state.get("user_prompt", "")
    intent = detect_fast_path_intent(user_prompt)

    handler = _HANDLERS.get(intent or "")
    if handler is None:
        logger.error("fast_path_node: no handler for intent=%r", intent)
        answer = "I'm sorry, I couldn't process that request."
        writer({"event": "token", "payload": {"content": answer, "source": "fast-path"}})
        return {"orchestrator_response": answer, "answer": answer, "stage": "answer"}

    result: FastPathResult = await handler(state)

    for evt in result.events:
        writer({"event": evt["event"], "payload": evt["data"]})

    return {
        "orchestrator_response": result.answer,
        "answer": result.answer,
        "stage": "answer",
    }
