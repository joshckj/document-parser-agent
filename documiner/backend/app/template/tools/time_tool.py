"""
Time tool for Template workflow.

Provides current time in Singapore timezone.
"""

from langchain_core.tools import tool

from app.utils.time_utils import get_singapore_time_iso


@tool
def get_current_time() -> str:
    """Get the current time in Singapore timezone (UTC+8)."""
    return get_singapore_time_iso()
