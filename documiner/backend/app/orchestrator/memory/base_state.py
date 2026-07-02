"""
Shared base state for the Documiner orchestrator and its subgraphs.

Inherits from MessagesState to get the `messages` channel (Annotated with
add_messages). Slimmed for the document-parsing domain — no SQL/RBAC fields.

Related files:
    - app/orchestrator/memory/state.py: OrchestratorState (adds parser fields)
"""

from typing import Optional

from langgraph.graph import MessagesState


class BaseAgentState(MessagesState):
    """Base state shared by the orchestrator and all subgraphs.

    From MessagesState:
        messages: Annotated[list[BaseMessage], add_messages]
    """

    # Input
    user_prompt: str
    session_id: str

    # Output (shared by all nodes)
    answer: Optional[str]
    stage: Optional[str]
    error: Optional[str]
