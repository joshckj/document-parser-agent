"""
Finalization node for successful orchestrator execution.

Handles successful completions, constructs the final response payload,
saves conversation history, and drops the temporary table.

Caching is handled at the individual subagent level (text_to_sql, charter,
mapper, analyzer) rather than here.
"""

import json
import time
import logging
from typing import Dict, Any, List
from langgraph.types import StreamWriter
from langchain_core.messages import AIMessage, HumanMessage

from app.orchestrator.memory.state import OrchestratorState
from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def finalize_success_node(
    state: OrchestratorState,
    writer: StreamWriter,
) -> Dict[str, Any]:
    """Finalize successful result and cache it.
    
    Also handles subgraph errors that were finalized internally.
    Subgraphs (text_to_sql, memory, rag) handle their own errors and set stage='error'.
    This node detects that and passes through the error response without caching.
    
    Saves selective messages to conversation history:
    1. User prompt (HumanMessage) - if not already in messages
    2. Orchestrator response (AIMessage) - if available
    3. SQL query (AIMessage) - if available
    4. Chart config (AIMessage) - if available
    5. Map config (AIMessage) - if available
    6. Errors (AIMessage) - if stage is error
    """
    
    # Check if a subgraph finalized with an error
    if state.get("stage") == "error":
        logger.info("Detected subgraph error, passing through without caching")

        # Subgraph already created the error response in 'answer' field
        # Just pass it through without caching
        elapsed_time = time.time() - state.get("elapsed_time", time.time())

        # If answer exists, use it; otherwise create a generic error
        answer = state.get("answer")
        if not answer:
            fallback_msg = state.get("error") or state.get("orchestrator_response") or "An error occurred in processing your request."
            error_payload = {
                "intent": "error",
                "response": fallback_msg,
                "error_details": state.get("error", "Unknown error")
            }
            answer = json.dumps(error_payload, indent=2)

        # Save error to conversation history
        error_message = AIMessage(
            content=f"Error: {answer}",
            name="orchestrator_error"
        )

        return {
            "messages": [error_message],
            "elapsed_time": elapsed_time,
            "stage": "error",
            "answer": answer,
        }

    # Check for text-based answer (agent responded without SQL).
    if state.get("stage") == "answer":
        logger.info("Detected text-based answer, passing through without caching")
        elapsed_time = time.time() - state.get("elapsed_time", time.time())
        answer = state.get("answer", "No response generated.")

        answer_message = AIMessage(
            content=answer,
            name="text_to_sql"
        )

        return {
            "messages": [answer_message],
            "elapsed_time": elapsed_time,
            "stage": "answer",
            "answer": answer,
        }
    
    # Calculate elapsed time
    elapsed_time = time.time() - state.get("elapsed_time", time.time())
    
    # Collect messages to save to history
    messages_to_save: List[Any] = []

    # 0. Add user prompt as HumanMessage if not already in history
    existing_messages = state.get("messages", [])
    user_prompt = state.get("user_prompt", "")
    has_current_prompt = False
    for msg in reversed(existing_messages):
        if hasattr(msg, "type") and msg.type == "human":
            if msg.content == user_prompt:
                has_current_prompt = True
            break
    if not has_current_prompt and user_prompt:
        messages_to_save.append(HumanMessage(content=user_prompt))

    # 1. Orchestrator response
    if state.get("orchestrator_response") is not None:
        messages_to_save.append(
            AIMessage(
                content=f"Orchestrator Response: {state.get('orchestrator_response')}",
                name="orchestrator"
            )
        )

    # 2. SQL query
    if state.get("query") is not None:
        messages_to_save.append(
            AIMessage(
                content=f"SQL Query: {state.get('query')}",
                name="text_to_sql"
            )
        )

    # 3. Chart config
    if state.get("chart_config") is not None:
        messages_to_save.append(
            AIMessage(
                content=f"Chart Config: {json.dumps(state.get('chart_config'), indent=2)}",
                name="charter"
            )
        )

    # 4. Map config
    if state.get("map_config") is not None:
        messages_to_save.append(
            AIMessage(
                content=f"Map Config: {json.dumps(state.get('map_config'), indent=2)}",
                name="mapper"
            )
        )

    # Temp table survives turns; cleaned up by next prepare_temp_table call or session change.

    return {
        "messages": messages_to_save,
        "elapsed_time": elapsed_time,
        "stage": "done",
    }