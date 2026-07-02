"""
Auto Dream — passive background memory extraction from session history.

Triggered at most once every 12 hours per user per session. Fires as a background task
so the main graph is never blocked.

Flow:
  1. load_user_memory_node checks last auto dream timestamp from the store
  2. If 12h have passed: stamp immediately (prevent double-fire), fire background task
  3. Background task reads deep agent checkpoint history, runs LLM extraction,
     saves new preferences to the store under category "auto_extracted"
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from app.utils.namespace import make_namespace
from app.utils.time_utils import get_singapore_time, get_singapore_time_iso

logger = logging.getLogger(__name__)

# Auto dream fires when the interval elapses OR enough new messages accumulate since last auto_dream.
_DREAM_INTERVAL_SECONDS = 12 * 60 * 60    # every 12 hours
_DREAM_MIN_NEW_MESSAGES = 50              # every 50 new messages

# Store key used to track the user's last active thread across sessions.
_LAST_THREAD_KEY = "last_thread"

_EXTRACTION_PROMPT = """\
You are analyzing a conversation history to extract user preferences worth \
remembering for future sessions.

Focus ONLY on explicit or strongly implied preferences about:
- Data visualization (chart types, colors, formats, aggregations)
- Analysis style (level of detail, tone, what metrics they care about)
- Domain defaults (time ranges, regions, filters they consistently apply)
- Workflow habits (how they phrase requests, what they follow up with)

Extract 0-5 concrete preference statements worth saving long-term.
Each should be a single clear sentence starting with "User".
If nothing meaningful is found, return an empty list.

Respond ONLY with a JSON array of strings, for example:
["User prefers bar charts over pie charts", "User always filters to Singapore region"]
or [] if nothing to save.

Today's date is {today}. When writing preference statements, replace any relative \
time references ("yesterday", "last week", "recently", etc.) with the exact date.

Conversation:
{transcript}
"""


async def should_run_auto_dream(user_id: str, thread_id: str, current_message_count: int = 0) -> bool:
    """Return True if the interval has elapsed OR enough new messages have accumulated."""
    from app.utils.store import get_store
    store = await get_store()
    try:
        item = await store.aget(make_namespace("auto_dream", user_id), thread_id)
        if item is None:
            return True
        value = item.value or {}
        last_run_str = value.get("timestamp", "")
        if not last_run_str:
            return True
        last_run = datetime.fromisoformat(last_run_str)
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_run).total_seconds()
        if elapsed >= _DREAM_INTERVAL_SECONDS:
            return True
        # Option 2: enough new messages since last processed
        processed_count = value.get("processed_message_count", 0)
        new_since_last = current_message_count - processed_count
        return new_since_last >= _DREAM_MIN_NEW_MESSAGES
    except Exception as exc:
        logger.warning("auto_dream: failed to read last-run timestamp: %s", exc)
        return False  # fail safe — don't trigger if we can't check


async def _stamp_dream(user_id: str, thread_id: str, message_count: int = 0) -> None:
    """Write the current timestamp and processed message count to prevent double-fire and duplicate extraction."""
    from app.utils.store import get_store
    store = await get_store()
    try:
        await store.aput(
            make_namespace("auto_dream", user_id),
            thread_id,
            {"timestamp": get_singapore_time_iso(), "processed_message_count": message_count},
        )
    except Exception as exc:
        logger.warning("auto_dream: failed to stamp timestamp: %s", exc)


async def _run_dream(user_id: str, thread_id: str) -> None:
    """Background task: read history → LLM extraction → save preferences."""
    try:
        from langchain_core.messages import AIMessage, HumanMessage
        from app.orchestrator.agents.orchestrator_agent import get_orchestrator_agent
        from app.utils.store import get_store
        from app.core.config import get_settings
        from app.llms import create_llm

        # Read deep agent's checkpoint history for this session
        agent = get_orchestrator_agent()
        agent_config = {"configurable": {"thread_id": thread_id}}
        state = await agent.aget_state(agent_config)
        messages = (state.values or {}).get("messages", [])

        # logger.info("auto_dream: found %d messages in history for thread %s", len(messages), thread_id)
        if not messages:
            # logger.info("auto_dream: no history found for thread %s, skipping", thread_id)
            return

        # Only process messages since the last dream run to avoid duplicate extraction.
        store = await get_store()
        stamp_item = await store.aget(make_namespace("auto_dream", user_id), thread_id)
        processed_count = (stamp_item.value or {}).get("processed_message_count", 0) if stamp_item else 0
        new_messages = messages[processed_count:]
        total_count = len(messages)

        # logger.info(
        #     "auto_dream: processing %d new messages (skipping first %d already processed)",
        #     len(new_messages), processed_count,
        # )
        if not new_messages:
            # logger.info("auto_dream: no new messages since last run for thread %s, skipping", thread_id)
            # Re-stamp with current time so the interval resets.
            await _stamp_dream(user_id, thread_id, message_count=total_count)
            return

        # Patterns that indicate structured-output noise, not real conversational turns
        _NOISE_PREFIXES = ("Chart Config:", "Map Config:", "Chart config:", "Map config:")

        def _extract_text(msg_content) -> str:
            if isinstance(msg_content, str):
                return msg_content
            return " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in msg_content
            )

        def _clean_assistant(text: str) -> str:
            # Strip boilerplate orchestrator wrapper prefix
            for prefix in ("Orchestrator Response:\n\n", "Orchestrator Response:\n", "Orchestrator Response: "):
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break
            return text.strip()

        # Build a compact transcript from new messages only (last 40 of the new slice)
        transcript_lines = []
        for msg in new_messages[-40:]:
            if isinstance(msg, HumanMessage):
                text = _extract_text(msg.content).strip()
                if text:
                    transcript_lines.append(f"User: {text[:400]}")
            elif isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                text = _clean_assistant(_extract_text(msg.content))
                # Skip pure structured-output lines (Chart Config, Map Config, etc.)
                if not text or any(text.startswith(p) for p in _NOISE_PREFIXES):
                    continue
                transcript_lines.append(f"Assistant: {text[:400]}")

        # logger.info(
        #     "auto_dream: built transcript with %d lines from %d candidate messages",
        #     len(transcript_lines), min(len(messages), 40),
        # )
        if not transcript_lines:
            # logger.info("auto_dream: transcript is empty (no human/AI messages), skipping")
            return

        transcript = "\n".join(transcript_lines)

        # logger.info("auto_dream: transcript preview: %r", transcript[:600])

        # LLM extraction
        settings = get_settings()
        llm = create_llm(settings=settings, temperature=0.1, max_tokens=10000, enable_thinking=True)
        # logger.info("auto_dream: invoking LLM with transcript of %d chars", len(transcript))
        today = get_singapore_time().strftime("%A, %d-%m-%Y")  # e.g. "Tuesday, 10-06-2026"
        response = await llm.ainvoke([HumanMessage(content=_EXTRACTION_PROMPT.format(transcript=transcript, today=today))])
        # logger.info("auto_dream: LLM response (type=%s) %s", type(response.content).__name__, str(response.content))

        # response.content may be a list of content blocks (e.g. Anthropic/Claude)
        raw = response.content
        if isinstance(raw, list):
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw
            ).strip()
        else:
            content = str(raw).strip()

        # logger.info("auto_dream: parsed content length=%d, preview=%r", len(content), content[:120])

        # Strip markdown fences if the model wraps its output
        if "```" in content:
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:]

        try:
            extracted: list = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("auto_dream: could not parse LLM output: %s", content[:200])
            return

        if not extracted:
            # logger.info("auto_dream: nothing to save for user %s", user_id)
            return

        store = await get_store()
        saved = 0
        for pref in extracted:
            if isinstance(pref, str) and pref.strip():
                try:
                    await store.aput(
                        make_namespace("preferences", user_id),
                        str(uuid.uuid4()),
                        {
                            "category": "auto_extracted",
                            "content": pref.strip(),
                            "updated_at": get_singapore_time_iso(),
                        },
                    )
                    saved += 1
                except Exception as exc:
                    logger.error("auto_dream: failed to save preference (embedding service down?): %s", exc)

        # logger.info("auto_dream: saved %d preferences for user %s", saved, user_id)

        # Stamp with the total message count so the next run only sees newer messages.
        await _stamp_dream(user_id, thread_id, message_count=total_count)

    except Exception:
        logger.exception("auto_dream: background task failed for user %s", user_id)


async def _drop_thread_temp_tables(old_thread_id: str) -> None:
    """Drop the speedy_temp table for a thread the user has left.

    Called as a fire-and-forget background task when a session change is detected.
    Reads the table name from speedy_temp._registry, drops it from PG, and removes
    the registry row. At most one stale table per user until a cron sweeper is added.
    """
    from app.utils.rbac import registry_get, registry_delete
    from app.utils.db_pool import get_async_connection_pool

    entry = await registry_get(old_thread_id)
    if not entry:
        return

    full_name = entry.get("table_name")
    if full_name:
        try:
            pool = await get_async_connection_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"DROP TABLE IF EXISTS {full_name}")
                    await conn.commit()
            logger.info("_drop_thread_temp_tables: dropped %s for old thread %s", full_name, old_thread_id)
        except Exception as exc:
            logger.warning("_drop_thread_temp_tables: failed to drop %s: %s", full_name, exc)

    await registry_delete(old_thread_id)


async def _get_last_thread(user_id: str) -> str | None:
    """Return the last active thread_id for this user, or None."""
    from app.utils.store import get_store
    store = await get_store()
    try:
        item = await store.aget(make_namespace("auto_dream", user_id), _LAST_THREAD_KEY)
        return (item.value or {}).get("thread_id") if item else None
    except Exception:
        return None


async def _set_last_thread(user_id: str, thread_id: str) -> None:
    """Persist the current thread_id as the last active thread for this user."""
    from app.utils.store import get_store
    store = await get_store()
    try:
        await store.aput(
            make_namespace("auto_dream", user_id),
            _LAST_THREAD_KEY,
            {"thread_id": thread_id},
        )
    except Exception as exc:
        logger.warning("auto_dream: failed to store last thread_id: %s", exc)


def maybe_fire_auto_dream(user_id: str, thread_id: str) -> None:
    """Schedule auto dream check + fire as a background coroutine.

    Called from load_user_memory_node. Uses create_task so it never blocks
    the main graph — if the event loop has no running loop this is a no-op.

    Triggers:
      - Option 1 (new session): if the stored last_thread_id differs from thread_id,
        fire dream for the PREVIOUS thread unconditionally, then update last_thread_id.
      - Option 2 (message count): fire for the current thread if >= _DREAM_MIN_NEW_MESSAGES
        new messages have accumulated since the last run.
      - Interval: fire for the current thread if _DREAM_INTERVAL_SECONDS have elapsed.
    """
    async def _check_and_fire() -> None:
        from app.orchestrator.agents.orchestrator_agent import get_orchestrator_agent

        # --- Option 1: new-session trigger ---
        last_thread = await _get_last_thread(user_id)
        if last_thread and last_thread != thread_id:
            # logger.info(
            #     "auto_dream: new session detected for user %s — firing dream for previous thread %s",
            #     user_id, last_thread,
            # )
            asyncio.create_task(
                _run_dream(user_id, last_thread),
                name=f"auto_dream_prev:{user_id}",
            )
            # Drop the old thread's temp table now that the user has moved to a new chat.
            asyncio.create_task(
                _drop_thread_temp_tables(last_thread),
                name=f"drop_temp_tables:{last_thread}",
            )
        await _set_last_thread(user_id, thread_id)

        # --- Options 2 + interval: current-thread trigger ---
        # Peek at the current message count without loading the full state.
        try:
            agent = get_orchestrator_agent()
            agent_config = {"configurable": {"thread_id": thread_id}}
            state = await agent.aget_state(agent_config)
            current_message_count = len((state.values or {}).get("messages", []))
        except Exception:
            current_message_count = 0

        if not await should_run_auto_dream(user_id, thread_id, current_message_count):
            return

        # Stamp timestamp now (preserve count) to block concurrent double-fire.
        from app.utils.store import get_store
        store = await get_store()
        try:
            existing = await store.aget(make_namespace("auto_dream", user_id), thread_id)
            existing_count = (existing.value or {}).get("processed_message_count", 0) if existing else 0
        except Exception:
            existing_count = 0
        await _stamp_dream(user_id, thread_id, message_count=existing_count)
        asyncio.create_task(
            _run_dream(user_id, thread_id),
            name=f"auto_dream:{user_id}",
        )
        # logger.info("auto_dream: fired for user %s (thread %s)", user_id, thread_id)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_check_and_fire(), name=f"auto_dream_check:{user_id}")
    except RuntimeError:
        pass  # no running loop — skip silently (e.g. tests, startup)
