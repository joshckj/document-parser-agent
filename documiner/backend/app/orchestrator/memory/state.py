"""
State definitions for the Documiner Orchestrator LangGraph workflow.

Defines `OrchestratorState`, which extends `BaseAgentState` with orchestrator
outputs. The document-parser orchestrator is a thin router around a deep agent,
so the state is intentionally small.

Related files:
    - app/orchestrator/memory/base_state.py: Base class
    - app/orchestrator/graph/orchestrator_graph.py: Uses this state
"""

from typing import Any, Dict, Optional

from orchestrator.memory.base_state import BaseAgentState


def merge_agent_outputs(left: Optional[Any], right: Optional[Any]) -> Optional[Any]:
    """Reducer for merging parallel subagent outputs (last write wins).

    Not attached to any field yet — kept for pattern fidelity so that when a
    second subagent runs in parallel with the extractor, its output field can
    be Annotated[..., merge_agent_outputs] without reworking the state.
    """
    if right is not None:
        return right
    return left


class OrchestratorState(BaseAgentState):
    """State for the Documiner orchestrator graph.

    Inherits from BaseAgentState:
        messages, user_prompt, session_id, answer, stage, error

    Adds document-parser specifics.
    """

    # Full OCR JSON of the most recent parse (also cached in session_store).
    ocr_result: Optional[Dict[str, Any]]
