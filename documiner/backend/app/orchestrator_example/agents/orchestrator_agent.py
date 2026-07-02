"""
Orchestrator Full Deep Agent (Phase 3).

Replaces the 10-node / 7-conditional-edge LangGraph StateGraph orchestrator with a
single `create_deep_agent` call. The agent:

  - Plans with built-in write_todos before acting
  - Delegates data retrieval to the text_to_sql subagent (Phase 1)
  - Delegates analysis to the analyzer subagent (Phase 2)
  - Delegates chart/map generation to charter/mapper subagents
  - Calls prepare_temp_table, search_knowledge, update_memory, and
    get_sample_questions as direct tools
  - Uses the decide-visualization skill for post-data routing decisions
  - Injects user memory, permissions, and existing configs via dynamic_prompt

Public surface
--------------
get_orchestrator_agent(checkpointer)  -> compiled deep agent (singleton per checkpointer)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain.agents.middleware import ModelFallbackMiddleware, ToolCallLimitMiddleware, dynamic_prompt
from app.utils.agent_profiles import register_agent_profiles

from app.core.config import Settings, get_settings
from app.llms import create_fallback_models, create_llm
from app.orchestrator.memory.agent_context import OrchestratorAgentContext
from app.utils.prompt_loader import get_prompt_and_config
from app.orchestrator.agents.subagents import (
    make_analyzer_subagent,
    make_text_to_sql_subagent,
    make_ui_agent_subagent,
)
from app.orchestrator.tools.orchestrator_tools import (
    prepare_temp_table,
    search_knowledge,
    update_memory,
)
from app.orchestrator.tools.question_suggester import get_sample_questions
from app.orchestrator.tools.rest_api_tool import call_rest_api
from app.utils.time_utils import get_singapore_time_iso

logger = logging.getLogger(__name__)

def _resolve_skills_dir() -> Path:
    # Walk up from this file looking for skills_registry/orchestrator.
    # Works in Docker (/app/skills_registry/) and local dev without hardcoding
    # a parent index that varies between environments.
    for parent in Path(__file__).parents:
        candidate = parent / "skills_registry" / "orchestrator"
        if candidate.exists():
            return candidate
    return Path(__file__).parents[3] / "skills_registry" / "orchestrator"

_SKILLS_DIR = _resolve_skills_dir()

_FALLBACK_SYSTEM_PROMPT = """You are the Speedy orchestrator — a data analytics coordinator that \
helps users retrieve, analyze, and visualize data from their database.

## Your role
Coordinate data retrieval, analysis, and visualization for the user's request. \
You do NOT write SQL, charts, or maps yourself. You delegate each distinct task to the right subagent.

## Capabilities available to you
- **text_to_sql subagent** — translates natural-language questions to SQL and executes them. \
  Always use this first for any data question.
- **prepare_temp_table tool** — creates a persistent table from the SQL result. \
  Call this immediately after text_to_sql, before any analysis or visualization.
- **analyzer subagent** — generates written analysis, trends, and insights from the data table.
- **ui_agent subagent** — renders all UI components (charts, maps, inline tables). \
  Pass a plain-English instruction describing what to visualize. \
  Table context, preferences, and existing configs are auto-injected — do NOT include them. \
  Returns magic keywords to embed in your final response.
- **search_knowledge tool** — retrieves domain documentation and schema information.
- **update_memory tool** — saves user preferences for future sessions.
- **get_sample_questions tool** — suggests answerable questions.
- **call_rest_api tool** — calls localhost REST endpoints on sidecar services. \
  Always load the `call-rest-api` skill first to find the correct port and path, \
  then read the relevant file in `references/` for the full request schema before calling.

## General workflow
1. Use `write_todos` to plan before acting.
2. For data questions: call text_to_sql → prepare_temp_table → (analyzer and/or ui_agent as needed).
3. Load the `decide-visualization` skill when you have a table and need to decide what to visualize.
4. For knowledge questions: call search_knowledge directly.
5. For preference saves: call update_memory.
6. For sidecar REST calls: load the `call-rest-api` skill → read the relevant `references/` file → \
   then call call_rest_api. Never guess ports or paths.
7. For corrections (user says "that's wrong", "change the chart", "update the map"): \
   call ui_agent with the correction instruction. Do not re-run text_to_sql if the data is unchanged.
8. For simple questions or greetings: answer directly without calling any subagent.

## Stop as soon as you have the answer

**After each subagent or tool returns a result, ask yourself: "Do I now have everything I need \
to answer the user?"**
- If **yes**: respond immediately. Do NOT call any more subagents or tools.
- If **no**: call exactly the next missing piece — nothing else.

When text_to_sql returns `rows` in its response (result sets ≤ 100 rows), read the rows \
directly and answer from them. Do NOT call text_to_sql again for the same information.

## Calling ui_agent

Pass a single plain-English instruction. You may request multiple components at once.
ui_agent returns magic keywords — you MUST embed these verbatim in your final response.

Examples:
- Single chart: `{"user_request": "bar chart of incident count by zone"}`
- Multiple components: `{"user_request": "bar chart of sales by region and a points map of store locations"}`
- Different tables: `{"user_request": "pie chart from speedy_temp.table_abc and choropleth map from speedy_temp.table_def"}`
- Inline table: `{"user_request": "show the data as an inline table"}`
- Edit existing: `{"user_request": "change the chart to a line chart"}`

Table context, temp table name, existing configs, and user preferences are injected automatically.

## Subagent task format (all subagents)
```json
{
    "user_request": "<description of what to do>"
}
```

Permissions, table context, and user preferences are injected automatically — do NOT include them.

## Final answer format
After all subagents complete, compose a concise answer. Include:
- A brief summary of what was found or done.
- If analysis was run: the key insights.
- If ui_agent returned magic keywords: embed them VERBATIM in your response where the components should appear.
- Do NOT include raw SQL or internal table names in your final response.

Example final response with magic keywords:
"Here is the summary of incidents by zone. render_table(speedy_temp.table_abc) The bar chart below shows \
the distribution. render_chart(chart_xy123456)"
"""


def _build_agent(settings: Settings, checkpointer: Optional[Any] = None) -> Any:
    base_system_prompt, prompt_config = get_prompt_and_config(
        settings, "orchestrator", fallback_prompt=_FALLBACK_SYSTEM_PROMPT
    )

    temperature = prompt_config.get("temperature", 0.3)
    max_tokens = prompt_config.get("max_tokens", 4000)
    model_override = prompt_config.get("model")
    enable_thinking = prompt_config.get("enable_thinking")

    model = create_llm(
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
        model_override=model_override,
        enable_thinking=enable_thinking,
    )

    @dynamic_prompt
    def _system_prompt(request) -> str:
        ctx = request.runtime.context

        # Visualization state — only inject when at least one config is active.
        chart_cfg = getattr(ctx, "current_chart_config", None)
        map_cfg = getattr(ctx, "current_map_config", None)
        if chart_cfg or map_cfg:
            try:
                chart_cfg_text = json.dumps(chart_cfg, indent=2) if chart_cfg else "None"
            except Exception:
                chart_cfg_text = "Error serializing chart config"
            try:
                map_cfg_text = json.dumps(map_cfg, indent=2) if map_cfg else "None"
            except Exception:
                map_cfg_text = "Error serializing map config"
            state_block = (
                "\n\n# Current Visualization State\n"
                f"Active Chart Config:\n{chart_cfg_text}\n\n"
                f"Active Map Config:\n{map_cfg_text}\n"
            )
        else:
            state_block = ""

        # Preferences: already semantically filtered by load_user_memory_node.
        # Format as a bulleted list; omit section when empty.
        prefs = getattr(ctx, "preferences", None) or []
        if prefs:
            prefs_text = "\n".join(f"- {p}" for p in prefs)
            prefs_section = f"Preferences:\n{prefs_text}\n"
        else:
            prefs_section = ""

        memory_block = (
            "\n\n# User Context\n"
            f"User ID: {getattr(ctx, 'user_id', '') or ''}\n"
            f"Profile: {getattr(ctx, 'profile', None) or 'None'}\n"
            + prefs_section
            + f"\nPermissions: {json.dumps(getattr(ctx, 'permissions', None) or {}, indent=2)}\n"
        )

        return (
            base_system_prompt
            + state_block
            + memory_block
            + f"\n# System Time\n{get_singapore_time_iso()}\n"
        )

    middleware: list = [
        _system_prompt,
        ToolCallLimitMiddleware(run_limit=40, exit_behavior="continue"),
    ]

    fallback_models = create_fallback_models(
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if fallback_models:
        middleware.append(ModelFallbackMiddleware(*fallback_models))

    _project_root = _SKILLS_DIR.parent.parent
    backend = FilesystemBackend(root_dir=str(_project_root))

    # Disable general-purpose subagent auto-add (orchestrator keeps its 4 explicit
    # subagents; analyzer and text_to_sql get no task tool because they pass none).
    # Deny writes and shell execution — FilesystemBackend adds both read_file and
    # execute; we only want read access for skills.
    register_agent_profiles()
    agent = create_deep_agent(
        model=model,
        tools=[prepare_temp_table, search_knowledge, update_memory, get_sample_questions, call_rest_api],
        middleware=middleware,
        context_schema=OrchestratorAgentContext,
        subagents=[
            make_text_to_sql_subagent(),
            make_analyzer_subagent(),
            make_ui_agent_subagent(),
        ],
        backend=backend,
        skills=[str(_SKILLS_DIR)],
        checkpointer=checkpointer,
        permissions=[
            FilesystemPermission(operations=["write", "execute"], paths=["/**"], mode="deny"),
        ],
    )

    logger.info(
        "Orchestrator Full Deep Agent compiled (model=%s, skills=%s)",
        model_override or "default",
        str(_SKILLS_DIR),
    )
    return agent


# ---------------------------------------------------------------------------
# Singleton management (one instance per checkpointer, keyed by checkpointer id)
# ---------------------------------------------------------------------------

_agent_instance: Optional[Any] = None
_agent_checkpointer_id: Optional[int] = None


def get_orchestrator_agent(
    checkpointer: Optional[Any] = None,
    settings: Optional[Settings] = None,
) -> Any:
    """Return the module-level orchestrator deep agent singleton.

    Creates on first call.  If called again with a different checkpointer
    (e.g. after a pool reset), rebuilds the agent.
    """
    global _agent_instance, _agent_checkpointer_id

    cp_id = id(checkpointer) if checkpointer is not None else None

    if _agent_instance is None or (checkpointer is not None and cp_id != _agent_checkpointer_id):
        if settings is None:
            settings = get_settings()
        _agent_instance = _build_agent(settings, checkpointer=checkpointer)
        _agent_checkpointer_id = cp_id

    return _agent_instance


def reset_orchestrator_agent() -> None:
    """Force rebuild on next call (used after checkpointer pool reset)."""
    global _agent_instance, _agent_checkpointer_id
    _agent_instance = None
    _agent_checkpointer_id = None
