"""
In-memory per-session store for the Documiner orchestrator.

Mirrors the out-of-band "session registry" pattern from orchestrator_example
(e.g. get_session_table_context), but self-contained with no RBAC/DB deps.

Three things are kept, all keyed by the chat `session_id`:

  last_ocr        – the full OCR JSON of the most recent parse for the session.
                    Populated by /upload and by the call_ocr_api tool.
  extracted       – the HTML fragment the Extractor's get_blocks tool last
                    produced for the session (scratch handed back to the node).
  rendered_block  – ref -> HTML, produced by the Extractor node and consumed by
                    the graph's finalize step when it expands render_blocks(ref).

This is process-local and non-persistent, which is fine for a single-worker
dev/demo backend. If you scale to multiple workers, back these with Redis.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# session_id -> full OCR JSON payload
_last_ocr: Dict[str, Any] = {}

# session_id -> latest HTML fragment from the extractor's get_blocks tool
_extracted: Dict[str, str] = {}

# session_id -> {ref: html}
_rendered_blocks: Dict[str, Dict[str, str]] = {}


# ---------------------------------------------------------------------------
# Last OCR result
# ---------------------------------------------------------------------------

def set_last_ocr(session_id: Optional[str], payload: Any) -> None:
    if not session_id:
        return
    _last_ocr[session_id] = payload


def get_last_ocr(session_id: Optional[str]) -> Optional[Any]:
    if not session_id:
        return None
    return _last_ocr.get(session_id)


# ---------------------------------------------------------------------------
# Extractor scratch (HTML fragment produced by get_blocks)
# ---------------------------------------------------------------------------

def set_extracted(session_id: Optional[str], html: str) -> None:
    if not session_id:
        return
    _extracted[session_id] = html


def pop_extracted(session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    return _extracted.pop(session_id, None)


# ---------------------------------------------------------------------------
# Rendered blocks (ref -> html) for render_blocks(ref) marker expansion
# ---------------------------------------------------------------------------

def set_rendered_block(session_id: Optional[str], ref: str, html: str) -> None:
    if not session_id:
        return
    _rendered_blocks.setdefault(session_id, {})[ref] = html


def pop_rendered_block(session_id: Optional[str], ref: str) -> Optional[str]:
    if not session_id:
        return None
    return _rendered_blocks.get(session_id, {}).pop(ref, None)
