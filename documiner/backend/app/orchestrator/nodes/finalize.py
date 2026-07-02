"""
Finalize node: expand render_blocks(<ref>) magic keywords.

The extractor subagent returns `render_blocks(<ref>)` and the orchestrator
embeds it verbatim. Here we swap each keyword for the stored HTML fragment,
wrapped in a `<<TABLE>> ... <<END>>` marker the frontend detects and injects.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from orchestrator import session_store

_RENDER_RE = re.compile(r"render_blocks\(\s*([A-Za-z0-9_]+)\s*\)")

TABLE_OPEN = "<<TABLE>>"
TABLE_CLOSE = "<<END>>"


def finalize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    answer = state.get("answer") or ""
    session_id = state.get("session_id") or ""

    def _replace(match: re.Match) -> str:
        ref = match.group(1)
        html = session_store.pop_rendered_block(session_id, ref)
        if not html:
            return ""  # ref expired / missing — drop the keyword
        return f"{TABLE_OPEN}{html}{TABLE_CLOSE}"

    expanded = _RENDER_RE.sub(_replace, answer)
    return {"answer": expanded}
