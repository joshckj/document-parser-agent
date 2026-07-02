"""
Finalization node for orchestrator error handling.

Constructs a standard error response payload and logs error details.
"""

import time
import json
import logging
from typing import Dict, Any
from langgraph.types import StreamWriter
from langchain_core.messages import AIMessage

from app.orchestrator.memory.state import OrchestratorState

logger = logging.getLogger(__name__)


async def finalize_error_node(
    state: OrchestratorState,
    writer: StreamWriter,
) -> Dict[str, Any]:
    """Finalize error state for the orchestrator."""
    
    elapsed_time = time.time() - state.get("elapsed_time", time.time())
    
    # Collect generic error information usually found in state or generic defaults
    error_message = state.get("error") or state.get("orchestrator_response") or "An unexpected error occurred during processing."
    
    error_details = {
        "message": error_message,
        "intent": state.get("intent"),
        "validation_errors": state.get("validation_errors"),
        "execution_errors": state.get("execution_errors"),
    }
    
    # Log the error
    logger.error(f"Orchestrator finalized with error: {error_details}")

    # Notify UI
    writer({"event": "error", "payload": error_details})
    
    # Clean prefix for friendly messages
    prefix = "" if error_message.startswith("I'm sorry") else "I encountered an error: "
    # Create a user-facing error message
    answer_payload = {
        "intent": "error",
        "response": f"{prefix}{error_message}",
        "error_details": error_details
    }
    answer = json.dumps(answer_payload, indent=2)
    ai_response = AIMessage(content=answer)

    return {
        "messages": [ai_response],
        "elapsed_time": elapsed_time,
        "stage": "error",
        "answer": answer,
    }
