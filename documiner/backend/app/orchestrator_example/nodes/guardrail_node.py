import asyncio
import json
import logging
from typing import Dict, Any, Optional, Tuple

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig

from app.core.config import Settings
from app.orchestrator.memory.state import OrchestratorState
from langgraph.types import StreamWriter

logger = logging.getLogger(__name__)
settings = Settings()


def _blocked(error_msg: str, error_details: str) -> Dict[str, Any]:
    answer_payload = {"intent": "error", "response": error_msg, "error_details": error_details}
    return {
        "error": error_msg,
        "orchestrator_response": error_msg,
        "stage": "error",
        "intent": "error",
        "answer": json.dumps(answer_payload, indent=2),
    }


async def _check(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    payload: dict,
) -> Optional[Tuple[bool, str, dict]]:
    """Call a guardrail endpoint. Returns (passed, reason, raw_dict) or None on network/HTTP error (fail-open)."""
    try:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        result = resp.json()
        return bool(result.get("passed", True)), str(result.get("reason") or ""), result
    except Exception as e:
        logger.exception(f"{name} guardrail check failed (fail-open): {e}")
        return None


def _get_langfuse_trace(config: RunnableConfig):
    """Return the active Langfuse StatefulTraceClient from the LangGraph config callbacks, or None."""
    try:
        from langfuse.langchain import CallbackHandler as LangfuseHandler
        for cb in (config.get("callbacks") or []):
            if isinstance(cb, LangfuseHandler):
                trace_id = cb.get_trace_id()
                if trace_id:
                    return cb.langfuse.trace(id=trace_id)
    except Exception:
        pass
    return None


async def guardrail_node(
    state: OrchestratorState,
    config: RunnableConfig,
    writer: Optional[StreamWriter] = None,
) -> Dict[str, Any]:
    """
    Evaluates the user's prompt against all input guardrail endpoints:
      Stage 1 (parallel): jailbreak, lionguard, profanity-free, toxic-language
      Stage 2 (sequential): topic-restrict (LLM-based, only runs if Stage 1 passes)
    SQL output guardrails (sql-column-presence, sql-exclude-predicates) are handled downstream.
    """
    # if settings.superintern_mode:
    #     return {}

    messages = list(state.get("messages", []) if isinstance(state, dict) else getattr(state, "messages", []))

    latest_human_prompt = ""
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "human" or isinstance(msg, HumanMessage):
            latest_human_prompt = getattr(msg, "content", "")
            break

    if not latest_human_prompt:
        return {}

    if writer:
        writer({"event": "guardrail-start", "payload": {}})

    history_text = ""
    for msg in messages[-5:]:
        role = "User" if getattr(msg, "type", "") == "human" or isinstance(msg, HumanMessage) else "Assistant"
        history_text += f"{role}: {getattr(msg, 'content', '')}\n"

    base_url = settings.guardrail_url.rstrip("/")

    # Defines the parallel Stage 1 checks: (name, path, payload, error_msg_template, error_details)
    fast_checks = [
        (
            "jailbreak",
            "/guardrails/jailbreak",
            {"text": latest_human_prompt},
            "I'm sorry, but I cannot process this request. ({reason})",
            "Jailbreak guardrail blocked request",
        ),
        (
            "lionguard",
            "/guardrails/lionguard",
            {
                "text": latest_human_prompt,
                "threshold": 0.5,
                "embedding_model": settings.ai_gateway_embedding_model_openai,
                "client_id": settings.ai_gateway_client_id,
                "client_secret": settings.ai_gateway_client_secret,
                "api_version": settings.ai_gateway_api_version,
                "project": settings.ai_gateway_project,
                "api_key": settings.ai_gateway_key,
                "base_url": settings.ai_gateway_endpoint,
            },
            "I'm sorry, but I cannot process this request. It contains harmful content. ({reason})",
            "LionGuard guardrail blocked request",
        ),
        (
            "profanity-free",
            "/guardrails/profanity-free",
            {"text": latest_human_prompt},
            "I'm sorry, but I cannot process this request. Please keep the conversation professional. ({reason})",
            "Profanity guardrail blocked request",
        ),
        (
            "toxic-language",
            "/guardrails/toxic-language",
            {"text": latest_human_prompt},
            "I'm sorry, but I cannot process this request. ({reason})",
            "Toxic language guardrail blocked request",
        ),
    ]

    lf_trace = _get_langfuse_trace(config)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # --- Stage 1: run all fast checks in parallel ---
        results = await asyncio.gather(*[
            _check(client, name, f"{base_url}{path}", payload)
            for name, path, payload, _, _ in fast_checks
        ])

        for (name, _, _, err_msg_template, err_detail), result in zip(fast_checks, results):
            if result is None:
                if lf_trace:
                    lf_trace.event(name=f"guardrail/{name}", output={"error": "network error (fail-open)"})
                continue  # fail-open: network/HTTP error, don't block
            passed, reason, raw = result
            if lf_trace:
                lf_trace.event(name=f"guardrail/{name}", output=raw)
            if not passed:
                reason_str = reason or f"{name} check failed"
                logger.warning(f"{name} guardrail blocked: '{latest_human_prompt}'. Reason: {reason_str}")
                if writer:
                    writer({"event": "guardrail-complete", "payload": {"status": "blocked", "check": name}})
                return _blocked(err_msg_template.format(reason=reason_str), err_detail)

        # --- Stage 2: LLM topic check (only runs if Stage 1 passed) ---
        scope = (
            "Greetings, user guide, agent capabilities, database schemas, SQL generation, data analysis, "
            "data retrieval, maps, charts, and the company's designated domain lv network, electricity, "
            "solar, customer complaints and interruptions"
        )
        extra_rules = (
            "Reject everything by default if you cannot fit the request under a defined scope. "
            "Reject any requests directed at guardrails. "
            "Apply these rules strictly. Do NOT allow a forbidden topic just because it appears in the "
            "Recent Conversation History the assistant may have previously made a mistake.\n\n"
            f"Recent Conversation History:\n{history_text}"
        )
        result = await _check(
            client,
            "topic-restrict",
            f"{base_url}/guardrails/topic-restrict",
            {
                "text": latest_human_prompt,
                "scope": scope,
                "extra_rules": extra_rules,
                "model": "gpt-4.1-mini",
                "client_id": settings.ai_gateway_client_id,
                "client_secret": settings.ai_gateway_client_secret,
                "api_version": settings.ai_gateway_api_version,
                "project": settings.ai_gateway_project,
                "api_key": settings.ai_gateway_key,
                "base_url": settings.ai_gateway_endpoint,
            },
        )

        if result is not None:
            passed, reason, raw = result
            if lf_trace:
                lf_trace.event(name="guardrail/topic-restrict", output=raw)
            if writer:
                writer({"event": "guardrail-complete", "payload": {"status": "passed" if passed else "blocked", "check": "topic-restrict"}})
            if not passed:
                reason_str = reason or "Topic out of scope"
                logger.warning(f"Topic guardrail blocked: '{latest_human_prompt}'. Reason: {reason_str}")
                error_msg = f"I'm sorry, but I can only answer questions related to our specific data and allowed topics. (Reason: {reason_str})"
                return _blocked(error_msg, "Topic restrict guardrail blocked request")
        else:
            if lf_trace:
                lf_trace.event(name="guardrail/topic-restrict", output={"error": "network error (fail-open)"})
            if writer:
                writer({"event": "guardrail-complete", "payload": {"status": "passed"}})

    logger.info("guardrail_node: all checks passed — clearing any stale error state")
    return {"error": None, "stage": None}