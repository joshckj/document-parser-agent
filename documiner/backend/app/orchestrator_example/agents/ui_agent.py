"""
UI Agent — renders charts, maps, and tables for the Speedy analytics platform.

Consolidates charter and mapper capabilities under a single create_agent.
The orchestrator delegates ALL visualization requests to this agent with a
plain-English instruction; the ui_agent handles:

  1. Previewing the temp table to discover available columns.
  2. Loading the appropriate charter / mapper skill to decide chart / map type.
  3. Assembling the validated config.
  4. Calling render_chart / render_map / render_table tools, which:
       - Validate the config against the Pydantic schema.
       - Emit charter-complete / mapper-complete SSE events so the frontend
         can create the widget immediately.
       - Register the result in render_registry for _extract_results.
  5. Retrying automatically if a tool returns a validation error.
  6. Returning to the orchestrator with the magic keywords to embed in the
     final response.

Orchestrator task format (only user_request is required):

    {"user_request": "bar chart of incidents by zone using speedy_temp.table_abc"}

Public surface
--------------
get_ui_agent(checkpointer, settings, langfuse) -> compiled agent (singleton)
reset_ui_agent()                               -> force rebuild on next call
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelFallbackMiddleware, ToolCallLimitMiddleware, dynamic_prompt
from langchain_core.tools import tool
from langfuse import Langfuse
from langgraph.config import get_config, get_stream_writer

from app.core.config import Settings, get_settings
from app.llms import create_fallback_models, create_llm
from app.observability.langfuse_client import init_langfuse
from app.utils.prompt_loader import get_prompt_and_config
from app.utils.render_registry import register_render_chart, register_render_map, register_render_table
from app.utils.skill_loader import BaseSkillMiddleware, load_skill_content, load_skills_from_langfuse
from app.utils.time_utils import get_singapore_time_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skills directory resolution
# ---------------------------------------------------------------------------

_CHARTER_SKILLS_DIR = Path(__file__).parents[3] / "skills_registry" / "charter"
_MAPPER_SKILLS_DIR  = Path(__file__).parents[3] / "skills_registry" / "mapper"


# ---------------------------------------------------------------------------
# Context schema
# ---------------------------------------------------------------------------

@dataclass
class UIAgentContext:
    """Runtime context injected per invocation."""
    user_preferences: str
    user_profile: str
    permissions: str
    existing_chart_config: Optional[Dict[str, Any]]
    existing_map_config: Optional[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Render tools (unchanged)
# ---------------------------------------------------------------------------

@tool
async def render_chart(config_json: str) -> str:
    """Validate a ChartConfig and stream it to the frontend as a chart widget.

    Pass the complete chart configuration as a JSON string.

    Required fields:
      id       – pass null for a new chart (id is auto-generated); pass the existing
                 chart id (e.g. "chart_abc12345") when modifying an existing chart
      type     – one of: bar, groupedBar, line, scatter, bubble, pie,
                         doughnut, nightingale, radar, boxplot, treemap, heatmap
      table    – fully-qualified temp table, e.g. "speedy_temp.table_xyz"
      x        – exact column name string (NOT an object)
      y        – exact column name string (NOT an object)
      layout   – {"title": "...", "xaxis": {"title": "..."}, "yaxis": {"title": "..."}}

    Optional fields:
      series, size, value, colors, stacked, sizeRange, sort, layout.fill, layout.smooth

    Returns:
      On success: success message with the magic keyword to use in the final response.
      On failure: a validation error message — fix the issues and call again.
    """
    from app.orchestrator.agents.charter_agent import ChartConfig

    config_dict: Dict[str, Any]
    try:
        config_dict = json.loads(config_json)
    except json.JSONDecodeError as exc:
        return f"ERROR: config_json is not valid JSON — {exc}. Fix the JSON and call render_chart again."

    if not config_dict.get("id"):
        config_dict["id"] = f"chart_{uuid.uuid4().hex[:8]}"

    try:
        validated = ChartConfig.model_validate(config_dict)
    except Exception as exc:
        return (
            f"ERROR: ChartConfig validation failed — {exc}\n"
            "Fix the listed fields and call render_chart again with corrected config_json."
        )

    chart_config = validated.model_dump()

    cfg = get_config()
    session_id = cfg.get("configurable", {}).get("thread_id", "default")
    register_render_chart(session_id, chart_config)

    writer = get_stream_writer()
    writer({"event": "charter-complete", "payload": {"chart_config": chart_config}})

    chart_id = chart_config["id"]
    title = (chart_config.get("layout") or {}).get("title", "Chart")
    return (
        f'Chart "{title}" rendered successfully (id: {chart_id}).\n'
        f"Magic keyword for final response: render_chart({chart_id})"
    )


@tool
async def render_map(config_json: str) -> str:
    """Validate a MapConfig and stream it to the frontend as a map widget.

    Pass the complete map configuration as a JSON string.

    Required fields:
      id             – pass null for a new map (id is auto-generated); pass the existing
                       map id (e.g. "map_abc12345") when modifying an existing map
      type           – one of: points, heatmap, choropleth
      table          – fully-qualified temp table, e.g. "speedy_temp.table_xyz"
      title          – map title
      label          – column to label points / regions

    Type-specific required fields:
      points/heatmap  – latitude_column, longitude_column
      heatmap         – weight_column
      choropleth      – boundary_type (zone | planning_area | hex), weight_column

    Optional fields: category_column, colors

    Returns:
      On success: success message with the magic keyword to use in the final response.
      On failure: a validation error message — fix the issues and call again.
    """
    from app.orchestrator.agents.mapper_agent import MapConfig

    config_dict: Dict[str, Any]
    try:
        config_dict = json.loads(config_json)
    except json.JSONDecodeError as exc:
        return f"ERROR: config_json is not valid JSON — {exc}. Fix the JSON and call render_map again."

    if not config_dict.get("id"):
        config_dict["id"] = f"map_{uuid.uuid4().hex[:8]}"

    try:
        validated = MapConfig.model_validate(config_dict)
    except Exception as exc:
        return (
            f"ERROR: MapConfig validation failed — {exc}\n"
            "Fix the listed fields and call render_map again with corrected config_json."
        )

    map_config = validated.model_dump()

    cfg = get_config()
    session_id = cfg.get("configurable", {}).get("thread_id", "default")
    register_render_map(session_id, map_config)

    writer = get_stream_writer()
    writer({"event": "mapper-complete", "payload": {"map_config": map_config}})

    map_id = map_config["id"]
    title = map_config.get("title", "Map")
    return (
        f'Map "{title}" rendered successfully (id: {map_id}).\n'
        f"Magic keyword for final response: render_map({map_id})"
    )


@tool
async def render_table(table_name: str) -> str:
    """Register a temp table for inline rendering in the orchestrator's final response.

    table_name must be a fully-qualified speedy_temp table, e.g.
    "speedy_temp.table_abc12345".

    No SSE event is emitted — the table is rendered inline by the frontend
    when it sees the magic keyword in the orchestrator's final text.

    Returns:
      On success: the magic keyword to include in the final response.
      On failure: an error message.
    """
    if not table_name or not table_name.strip().startswith("speedy_temp."):
        return (
            "ERROR: table_name must start with 'speedy_temp.' — "
            f"got: {table_name!r}. Use the exact temp table name from the session."
        )

    cfg = get_config()
    session_id = cfg.get("configurable", {}).get("thread_id", "default")
    register_render_table(session_id, table_name.strip())

    return (
        f'Table "{table_name}" registered for inline rendering.\n'
        f"Magic keyword for final response: render_table({table_name.strip()})"
    )


# ---------------------------------------------------------------------------
# Preview tool
# ---------------------------------------------------------------------------

@tool
async def preview_temp_table(table_name: str) -> str:
    """Preview the first 5 rows of a temp table, excluding geometry columns.

    Call this immediately after receiving a temp table name to discover
    available columns before building any chart or map configuration.

    Args:
        table_name: Fully-qualified temp table, e.g. "speedy_temp.table_abc12345"

    Returns:
      Table name, non-geometry column names, and first 5 rows as JSON.
    """
    if not table_name or not table_name.strip().startswith("speedy_temp."):
        return f"ERROR: table_name must start with 'speedy_temp.' — got: {table_name!r}"

    table_name = table_name.strip()
    try:
        from app.utils.data_utils import is_geometry_column, strip_geom_columns
        from app.utils.db_pool import get_async_connection_pool

        pool = await get_async_connection_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SELECT * FROM {table_name} LIMIT 5")
                if cur.description:
                    columns = [d.name for d in cur.description]
                    raw_rows = await cur.fetchall()
                    display_columns = [c for c in columns if not is_geometry_column(c)]
                    rows = [strip_geom_columns(dict(zip(columns, r))) for r in raw_rows]
                else:
                    display_columns, rows = [], []

        return (
            f"Table name: {table_name}\n"
            f"Columns: {', '.join(display_columns)}\n"
            f"First {len(rows)} rows:\n"
            f"{json.dumps(rows, default=str, indent=2)}"
        )
    except Exception as exc:
        return f"ERROR fetching table preview: {exc}"


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_FALLBACK_SYSTEM_PROMPT = """You are the UI Agent — a specialist that renders data visualizations \
(charts, maps, tables) for the Speedy analytics platform.

## Your role

You receive a plain-English instruction from the orchestrator describing what UI components to create.
You call the render_chart, render_map, and/or render_table tools with valid configurations.
Each tool validates the config and streams the widget to the frontend immediately.
After all components are rendered, you return a short message to the orchestrator listing the magic keywords \
it must embed in its final response.

## Workflow

1. **Get table context** — the orchestrator instruction includes a temp table name \
(e.g. `speedy_temp.table_abc12345`). Call `preview_temp_table(table_name)` immediately to discover \
the available columns. If no table name was given, respond: \
"Please provide the temp table name so I can render the visualization."
2. **Load skill** — for charts: load `decide-chart-type` skill, then the group skill.
                    For maps: load `decide-map-type` skill, then the type skill.
3. **Assemble config** — use exact column names from the preview output.
4. **Call render tool** — pass the full config as a JSON string.
5. **Retry on error** — if the tool returns an ERROR, read the message carefully, fix the field(s),
                         and call the tool again. Maximum 3 attempts per component.
6. **Report** — once all components are rendered, return the list of magic keywords.

## Existing configs (for edits)

Chart: {{existing_chart_config}}
Map:   {{existing_map_config}}

## ChartConfig schema (for render_chart)

```json
{
  "id": null,
  "type": "bar | groupedBar | line | scatter | bubble | pie | doughnut | nightingale | radar | boxplot | treemap | heatmap",
  "table": "speedy_temp.<table_name>",
  "x": "<exact column name>",
  "y": "<exact column name>",
  "series": "<column or null>",
  "size": "<column or null — bubble only>",
  "value": "<column or null — heatmap only>",
  "colors": {"<category>": "#hex"} | null,
  "stacked": false,
  "sizeRange": [8, 42],
  "sort": "default | alphabetical_asc | alphabetical_desc | numerical_asc | numerical_desc | date_asc | date_desc | time_asc | time_desc | month | day_of_week",
  "layout": {
    "title": "<chart title>",
    "xaxis": {"title": "<x-axis label>"},
    "yaxis": {"title": "<y-axis label>"},
    "fill": false,
    "smooth": false
  }
}
```

## MapConfig schema (for render_map)

```json
{
  "id": null,
  "type": "points | heatmap | choropleth",
  "table": "speedy_temp.<table_name>",
  "title": "<map title>",
  "label": "<column for labels/tooltips>",
  "category_column": "<column for point coloring — points only>",
  "weight_column": "<numeric column — heatmap / choropleth>",
  "latitude_column": "<lat column — points / heatmap>",
  "longitude_column": "<lng column — points / heatmap>",
  "boundary_type": "zone | planning_area | hex",
  "colors": {"<category>": "#hex"} | {"low": "#hex", "medium": "#hex", "high": "#hex"} | null
}
```

## Multiple components

You may render several components in a single call. Call each render tool in sequence.
The orchestrator can reference multiple tables — use the table name given explicitly in the instruction.

## Final response format

After rendering, respond EXACTLY in this format (substitute real values):

```
UI components ready:
- Chart "<title>" rendered → render_chart(<chart_id>)
- Map "<title>" rendered → render_map(<map_id>)
- Table rendered → render_table(<table_name>)

Embed these magic keywords in your final response where you want the components to appear.
Example: "Here is the data render_table(speedy_temp.table_abc) and the chart render_chart(chart_xy12)."
```

Only include lines for components that were actually rendered.

## Critical rules

- Call `preview_temp_table` first — use EXACT column names from its output, never invent columns.
- For new charts/maps set `"id": null` — the tool auto-generates the id and returns it in the magic keyword.
- For modifications pass the existing id from the existing config (e.g. `"id": "chart_abc12345"`).
- Pass config as a single-line JSON string (no pretty-print newlines inside strings).
- Never generate analysis text or SQL — only render tool calls.
"""


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

def _build_ui_agent(settings: Settings, langfuse: Any, checkpointer: Optional[Any] = None) -> Any:
    base_prompt, prompt_config = get_prompt_and_config(
        settings, "ui_agent", fallback_prompt=_FALLBACK_SYSTEM_PROMPT
    )

    temperature = prompt_config.get("temperature", 0.2)
    max_tokens = prompt_config.get("max_tokens", 3000)
    model_override = prompt_config.get("model")
    enable_thinking = prompt_config.get("enable_thinking")

    model = create_llm(
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
        model_override=model_override,
        enable_thinking=enable_thinking,
    )

    logger.info("TEST: Created LLM for UI Agent with model=%s, temperature=%.2f, max_tokens=%d, enable_thinking=%s",
                model_override or "default", temperature, max_tokens, enable_thinking)

    # --- Skills ---
    charter_skills = load_skills_from_langfuse(langfuse, "charter", _CHARTER_SKILLS_DIR)
    mapper_skills  = load_skills_from_langfuse(langfuse, "mapper",  _MAPPER_SKILLS_DIR)

    @tool
    def load_ui_skill(skill_name: str) -> str:
        """Load full configuration instructions for a charter or mapper skill.

        Call this AFTER previewing the table to get detailed field rules,
        constraints, and examples for the chosen chart or map type.

        For chart decisions: start with 'decide-chart-type', then load the specific
        group skill (e.g. 'bar-line-scatter', 'proportion-charts').
        For map decisions:   start with 'decide-map-type',  then load the type skill
        (e.g. 'points-map', 'heatmap-map', 'choropleth-map').

        To load a reference file within a skill folder, use the path format:
          "<skill_name>/references/<file_name>"
        Example: load_ui_skill("bar-line-scatter/references/sorting")

        Args:
            skill_name: Skill name or reference path.
        """
        charter_names = {s["name"] for s in charter_skills}
        if "/" in skill_name:
            prefix = skill_name.split("/")[0]
            if prefix in charter_names:
                return load_skill_content(
                    skill_name, charter_skills, _CHARTER_SKILLS_DIR,
                    langfuse=langfuse, agent_name="charter",
                )
            return load_skill_content(
                skill_name, mapper_skills, _MAPPER_SKILLS_DIR,
                langfuse=langfuse, agent_name="mapper",
            )
        if skill_name in charter_names:
            return load_skill_content(
                skill_name, charter_skills, _CHARTER_SKILLS_DIR,
                langfuse=langfuse, agent_name="charter",
            )
        return load_skill_content(
            skill_name, mapper_skills, _MAPPER_SKILLS_DIR,
            langfuse=langfuse, agent_name="mapper",
        )

    class UISkillMiddleware(BaseSkillMiddleware):
        tools = [load_ui_skill]

        def __init__(self_) -> None:
            self_._init_addendum(
                charter_skills + mapper_skills,
                "After previewing the table, use `load_ui_skill` to load instructions "
                "for the relevant chart or map type. Start with 'decide-chart-type' for "
                "charts or 'decide-map-type' for maps.",
            )
        # Uses BaseSkillMiddleware default: injects skills menu only, no forced tool_choice.
        # preview_temp_table should run first before skill loading.

    class UIHistoryFilterMiddleware(AgentMiddleware):
        """Strip intermediate tool-call turns from prior conversation history.

        The checkpointer stores the full thread, but the LLM only needs clean
        instruction → final-output pairs as context. Current-turn messages
        (from the last HumanMessage onward) are always passed through intact.
        """

        @staticmethod
        def _filter(messages: list) -> list:
            if len(messages) <= 1:
                return messages
            # Locate the start of the current turn (last HumanMessage)
            last_human = -1
            for i in range(len(messages) - 1, -1, -1):
                if getattr(messages[i], "type", "") == "human":
                    last_human = i
                    break
            if last_human <= 0:
                return messages
            history = messages[:last_human]
            current_turn = messages[last_human:]
            # From history keep only: HumanMessages + final AIMessages (no tool_calls)
            filtered_history = [
                msg for msg in history
                if getattr(msg, "type", "") == "human"
                or (getattr(msg, "type", "") == "ai" and not getattr(msg, "tool_calls", None))
            ]
            return filtered_history + current_turn

        def wrap_model_call(self, request, handler):
            msgs = request.state.get("messages", [])
            filtered = self._filter(msgs)
            if len(filtered) != len(msgs):
                return handler(request.override(state={**request.state, "messages": filtered}))
            return handler(request)

        async def awrap_model_call(self, request, handler):
            msgs = request.state.get("messages", [])
            filtered = self._filter(msgs)
            if len(filtered) != len(msgs):
                return await handler(request.override(state={**request.state, "messages": filtered}))
            return await handler(request)

    @dynamic_prompt
    def _system_prompt(request) -> str:
        ctx = request.runtime.context

        existing_chart = getattr(ctx, "existing_chart_config", None)
        existing_map = getattr(ctx, "existing_map_config", None)

        prompt = base_prompt
        prompt = prompt.replace(
            "{{existing_chart_config}}",
            json.dumps(existing_chart, indent=2) if existing_chart else "None (create new)",
        )
        prompt = prompt.replace(
            "{{existing_map_config}}",
            json.dumps(existing_map, indent=2) if existing_map else "None (create new)",
        )

        prefs = getattr(ctx, "user_preferences", "None") or "None"
        profile = getattr(ctx, "user_profile", "None") or "None"
        permissions = getattr(ctx, "permissions", "None") or "None"

        return (
            prompt
            + f"\n\n# User Context\nProfile: {profile}\nPreferences: {prefs}\nPermissions: {permissions}\n"
            + f"\n# System Time\n{get_singapore_time_iso()}\n"
        )

    middleware: list = [
        UIHistoryFilterMiddleware(),
        _system_prompt,
        UISkillMiddleware(),
        ToolCallLimitMiddleware(run_limit=20, exit_behavior="continue"),
    ]

    fallback_models = create_fallback_models(settings=settings, temperature=temperature, max_tokens=max_tokens)
    if fallback_models:
        middleware.append(ModelFallbackMiddleware(*fallback_models))

    agent = create_agent(
        model=model,
        tools=[load_ui_skill, preview_temp_table, render_chart, render_map, render_table],
        middleware=middleware,
        context_schema=UIAgentContext,
        checkpointer=checkpointer,
    )

    logger.info("UI Agent compiled (model=%s)", model_override or "default")
    return agent


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_ui_agent_instance: Optional[Any] = None
_ui_agent_checkpointer_id: Optional[int] = None


def get_ui_agent(
    checkpointer: Optional[Any] = None,
    settings: Optional[Settings] = None,
    langfuse: Optional[Any] = None,
) -> Any:
    global _ui_agent_instance, _ui_agent_checkpointer_id

    cp_id = id(checkpointer) if checkpointer is not None else None

    if _ui_agent_instance is None or (checkpointer is not None and cp_id != _ui_agent_checkpointer_id):
        if settings is None:
            settings = get_settings()
        if langfuse is None:
            langfuse = init_langfuse(settings)
        _ui_agent_instance = _build_ui_agent(settings, langfuse, checkpointer=checkpointer)
        _ui_agent_checkpointer_id = cp_id

    return _ui_agent_instance


def reset_ui_agent() -> None:
    global _ui_agent_instance, _ui_agent_checkpointer_id
    _ui_agent_instance = None
    _ui_agent_checkpointer_id = None
