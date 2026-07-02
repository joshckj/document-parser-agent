"""
Analyzer agent implementation using Deep Agent Lite (create_deep_agent).

The Analyzer agent performs data analysis using execute_sql and other tools
on a temporary table created after text-to-SQL graph execution.

Related files:
    - app/orchestrator/tools/analyzer_tools.py: Tools available to this agent
    - app/orchestrator/agents/subagents.py: Wrapper that runs this agent as a subagent
"""

import logging
from typing import Any, Dict, Optional, List
from dataclasses import dataclass

from app.utils.time_utils import get_singapore_time_iso

from langfuse import Langfuse
from deepagents import create_deep_agent
from langchain.agents.middleware import dynamic_prompt, ModelFallbackMiddleware, ToolCallLimitMiddleware
from app.utils.agent_profiles import register_agent_profiles
from langgraph.types import StreamWriter
from langchain_core.messages import BaseMessage

from app.core.config import Settings
from app.llms import create_llm, create_fallback_models
from app.utils.prompt_loader import get_prompt_and_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level checkpointer (set once at startup by orchestrator_graph.py)
# ---------------------------------------------------------------------------

_analyzer_checkpointer: Optional[Any] = None


def set_analyzer_checkpointer(checkpointer: Any) -> None:
    """Set the shared Postgres checkpointer for all analyzer agent instances."""
    global _analyzer_checkpointer
    _analyzer_checkpointer = checkpointer


def get_analyzer_checkpointer() -> Optional[Any]:
    return _analyzer_checkpointer


_ANALYZER_FALLBACK_PROMPT = """You are a helpful data analyst that helps business users understand their data through the backend Postgres database.

# Context

## Table Context (SQL Query + Sample Data)
{{table_context}}

# Available Tools

You have access to these tools to analyze the data:

- execute_sql: Run Postgres SQL queries for analysis
- get_table_stats: Get statistics for numeric columns
- check_data_quality: Check null values and unique counts
- get_time_coverage: Get the start and end date/timestamp of datetime columns

# Instructions

1. Use write_todos to plan your analysis steps before calling any tool.
2. Understand the request and gather data using appropriate tools.
3. Analyze patterns, trends, anomalies, or insights.
4. Respond concisely in clear, business-friendly language.
5. End with ONE specific follow-up question.

# Large Result Sets

When a query returns a large result set, the data is automatically stored as a file and you will receive a file reference with a short preview instead of the full rows. Use the built-in read_file and grep tools to examine specific slices of that file rather than trying to load everything at once. Focus your reads on the columns and rows most relevant to the analysis question.

# Response Guidelines

- Be concise and avoid technical jargon
- Use actual numbers and data points from your analysis
- Focus on what the data means for the business
- Never fabricate data; only use real data from tools
- Always end with one actionable question to continue the conversation
"""


@dataclass
class AnalyzerContext:
    """Context for analyzer agent."""
    user_request: str
    table_context: str  # top 5 rows; SQL is hidden currently not to confuse the agent
    user_profile: str
    user_preferences: str
    permissions: str
    session_id: str = ""  # used by dedup_tool_call for per-invocation cache scoping


def _message_type(msg: Any) -> str:
    t = getattr(msg, "type", None)
    if isinstance(t, str):
        return t
    if isinstance(msg, dict):
        return str(msg.get("role") or "")
    return ""


def _tool_calls(msg: Any) -> List[Dict[str, Any]]:
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        return list(tool_calls)
    if isinstance(msg, dict):
        tc = msg.get("tool_calls")
        if isinstance(tc, list):
            return tc
    return []


def _content(msg: Any) -> str:
    c = getattr(msg, "content", None)
    if c is not None:
        return str(c)
    if isinstance(msg, dict):
        return str(msg.get("content") or "")
    return ""


def _tool_name(msg: Any) -> str:
    n = getattr(msg, "name", None)
    if n is not None:
        return str(n)
    if isinstance(msg, dict):
        return str(msg.get("name") or "")
    return ""


# Frontend tool definitions (executed on client via CopilotKit)




def create_analyzer_agent(
    settings: Settings,
    langfuse: Langfuse,
    tools: List[Any],
    temperature: float = 0.3,
    max_tokens: int = 3000,
    checkpointer: Optional[Any] = None,
) -> Any:
    """Create Analyzer Deep Agent Lite using create_deep_agent.

    The Analyzer agent:
    - Analyzes user request to determine analysis approach
    - Uses backend tools to query Postgres database (temp table)
    - Generates concise, business-friendly insights
    - Provides actionable follow-up questions
    
    Args:
        settings: Application settings
        langfuse: Langfuse client for prompt management
        tools: List of backend tools (bound to connection)
        temperature: Model temperature
        max_tokens: Maximum tokens to generate
        
    Returns:
        LangChain agent instance
    """
    base_system_prompt, prompt_config = get_prompt_and_config(
        settings, "analyzer_agent", fallback_prompt=_ANALYZER_FALLBACK_PROMPT
    )
    temperature = prompt_config.get("temperature", temperature)
    max_tokens = prompt_config.get("max_tokens", max_tokens)
    model_override = prompt_config.get("model")
    enable_thinking = prompt_config.get("enable_thinking")
    
    # Initialize model using factory
    model = create_llm(
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
        model_override=model_override,
        enable_thinking=enable_thinking,
    )
    
    # Create dynamic system prompt
    @dynamic_prompt
    def analyzer_system_prompt(request) -> str:
        """Generate system prompt with context."""
        ctx = request.runtime.context
        
        prompt_text = base_system_prompt.replace("{{user_request}}", ctx.user_request or "")
        prompt_text = prompt_text.replace("{{table_context}}", ctx.table_context or "No table context available")
        
        memory_block = (
            "\n\n# User Memory\n"
            f"Profile: {ctx.user_profile or 'None'}\n"
            f"Preferences: {ctx.user_preferences or 'None'}\n"
            f"Permissions: {ctx.permissions or 'None'}\n"
        )
        
        return prompt_text + memory_block + f"\n# System Time\n{get_singapore_time_iso()}\n"
    
    # Create fallback models for resilience
    fallback_models = create_fallback_models(
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    
    middleware = [
        analyzer_system_prompt,
        ToolCallLimitMiddleware(run_limit=15, exit_behavior="continue"),
    ]
    if fallback_models:
        middleware.append(ModelFallbackMiddleware(*fallback_models))

    # Use the provided checkpointer, fall back to the module-level shared one.
    effective_checkpointer = checkpointer or _analyzer_checkpointer

    # Disable general-purpose subagent auto-add.
    register_agent_profiles()
    agent = create_deep_agent(
        model=model,
        tools=tools,
        middleware=middleware,
        context_schema=AnalyzerContext,
        checkpointer=effective_checkpointer,
    )
    
    return agent



async def run_analyzer_agent(
    agent: Any,
    user_request: str,
    table_context: str,
    langfuse: Langfuse,
    *,
    user_profile: str = "None",
    user_preferences: str = "None",
    permissions: str = "None",
    session_id: str = "",
    thread_id: str = "analyzer",
    langfuse_handler: Optional[Any] = None,
    writer: Optional[StreamWriter] = None,
) -> Dict[str, Any]:
    """Run Analyzer agent and return analysis results.
    
    Args:
        agent: LangChain agent instance
        user_request: User's analysis request (potentially rewritten by orchestrator)
        table_context: SQL query + top 5 rows of data
        langfuse: Langfuse client
        user_profile: User profile information
        user_preferences: User preferences
        permissions: User permissions
        langfuse_handler: Optional CallbackHandler for tracing
        
    Returns:
        Dict with insights and response
    """
    logger.info("Analyzer agent performing analysis")
    
    # Create context
    analyzer_context = AnalyzerContext(
        user_request=user_request,
        table_context=table_context or "No table context available",
        user_profile=user_profile,
        user_preferences=user_preferences,
        permissions=permissions,
        session_id=session_id,
    )
    
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 30,
    }
    if langfuse_handler:
        config["callbacks"] = [langfuse_handler]
    
    # Build messages with only current request
    from langchain_core.messages import HumanMessage
    messages = [HumanMessage(content=user_request)]
    
    # Run agent
    final_state: Dict[str, Any] = {}
    last_len = len(messages)
    pending_tool_count: Optional[int] = None
    collected_tool_results: List[str] = []
    collected_tool_names: List[str] = []
    all_tool_calls: List[Dict[str, Any]] = []

    try:
        if writer:
            async for values in agent.astream(
                {"messages": messages},
                config=config,
                context=analyzer_context,
                stream_mode="values"
            ):
                if not values:
                    continue
                final_state = values
                
                current_msgs = values.get("messages") or []
                if not isinstance(current_msgs, list):
                    continue

                if len(current_msgs) <= last_len:
                    continue

                new_msgs = current_msgs[last_len:]
                last_len = len(current_msgs)

                for msg in new_msgs:
                    mtype = _message_type(msg)
                    
                    if mtype == "ai":
                        tc = _tool_calls(msg)
                        pending_tool_count = len(tc) if tc else 0
                        collected_tool_results = []
                        collected_tool_names = []
                        
                        if tc and writer:
                            writer({
                                "event": "analyzer-working",
                                "payload": {"count": len(tc)},
                            })
                    
                    elif mtype == "tool":
                        collected_tool_results.append(_content(msg))
                        collected_tool_names.append(_tool_name(msg))
                        
                        if pending_tool_count is not None and pending_tool_count > 0:
                            if len(collected_tool_results) >= pending_tool_count:
                                if writer:
                                    writer({
                                        "event": "analyzer-tool-output",
                                        "payload": {
                                            "results": collected_tool_results,
                                            "tools": collected_tool_names,
                                        },
                                    })
                                all_tool_calls.append({
                                    "tools": list(collected_tool_names),
                                    "results": list(collected_tool_results),
                                })
                                pending_tool_count = None
        else:
            # Fallback to invoke if no writer provided (backward compatibility)
            final_state = await agent.ainvoke(
                {"messages": messages},
                config=config,
                context=analyzer_context,
            )

    except Exception as e:
        logger.error(f"Analyzer agent error: {e}", exc_info=True)
        # We might want to re-raise or return a partial result, but for now let's capture it
        # and ensure we return "Unable to perform analysis" if we failed.
    
    # Extract results
    messages = final_state.get("messages", [])
    final_message = messages[-1] if messages else None
    final_response = final_message.content if final_message else "Unable to perform analysis."
    
    logger.info("Analyzer agent completed")

    if writer:
        writer({
            "event": "analyzer-complete",
            "payload": {
                "insights": final_response,
                "tool_calls": all_tool_calls,
            },
        })

    return {
        "insights": final_response,
        "response": final_response,
    }

