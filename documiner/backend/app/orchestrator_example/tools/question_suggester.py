"""Question suggestion tool for orchestrator.

Returns answerable question suggestions from the SQL template index.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import tool, InjectedToolArg

from app.text_to_sql.tools.sql_templater import get_sql_templates
from app.utils.dedup import dedup_tool_call

logger = logging.getLogger(__name__)


def _normalize_count(count: int) -> int:
    """Clamp requested count to supported range."""
    if count < 1:
        return 1
    if count > 10:
        return 10
    return count


def _keyword_match_score(question: str, keywords: str) -> int:
    """Simple lexical match score used to boost keyword-relevant questions."""
    parts = [p.strip().lower() for p in keywords.split() if p.strip()]
    if not parts:
        return 0
    q = question.lower()
    return sum(1 for p in parts if p in q)


@tool
@dedup_tool_call
async def get_sample_questions(
    count: int = 3,
    keywords: Optional[str] = None,
    sources: Optional[List[str]] = ['ses_spm'],
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """Suggest answerable questions using SQL templates (questions only, no SQL).

    Args:
        count: Number of questions to return (default 3, max 10).
        keywords: Optional topic keywords to bias retrieval (for example: "outage trend").
        sources: Optional list of source names to restrict results to (for example: ["lv_network-all"]).

    Returns:
        A newline-separated list of suggested questions.
    """
    try:
        normalized_count = _normalize_count(count)
        query_text = (keywords or "").strip() or "common data analysis questions"

        prompt_embedding = (
            (config or {}).get("configurable", {}).get("prompt_embedding")
        )

        # Over-fetch then dedupe/rank so final output quality is stable.
        fetch_k = min(max(normalized_count * 4, 12), 40)
        templates: List[Dict[str, Any]] = get_sql_templates(
            message=query_text,
            embedding=prompt_embedding,
            top_k=fetch_k,
            sources=sources,
        )

        seen = set()
        candidates: List[Dict[str, Any]] = []
        for t in templates:
            q = (t.get("question") or "").strip()
            if not q:
                continue
            key = q.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "question": q,
                    "score": float(t.get("score") or 0.0),
                    "keyword_score": _keyword_match_score(q, keywords or ""),
                }
            )

        if keywords:
            candidates.sort(
                key=lambda x: (x["keyword_score"], x["score"]),
                reverse=True,
            )
        else:
            candidates.sort(key=lambda x: x["score"], reverse=True)

        selected = [c["question"] for c in candidates[:normalized_count]]
        if not selected:
            return "No suggested questions found."

        return "\n".join(f"{idx}. {q}" for idx, q in enumerate(selected, start=1))
    except Exception as e:
        logger.exception("Failed to suggest SQL template questions")
        return f"Failed to suggest questions: {e}"
