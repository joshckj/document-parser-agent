"""
Tool: call_rest_api

Gives the orchestrator agent the ability to call localhost REST endpoints on
sidecar containers within the same pod. The host is always `localhost` — the
agent only supplies a port number and path, so it cannot reach external hosts.

Load the call-rest-api skill before invoking this tool to discover available
ports, endpoint paths, and expected request/response formats.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any, Dict, Optional

import httpx
from langchain_core.tools import InjectedToolArg, tool
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 180.0


@tool
async def call_rest_api(
    method: str,
    port: int,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
) -> str:
    """Call a localhost REST endpoint on a backend sidecar container.

    Load the call-rest-api skill before using this tool to discover available
    ports, endpoints, and expected request/response formats.

    Args:
        method: HTTP method — GET, POST, PUT, PATCH, or DELETE.
        port: Port number of the target sidecar.
        path: Endpoint path starting with /.
        params: Optional query parameters as a flat dict.
        json_body: Optional JSON body for POST/PUT/PATCH requests.

    Returns:
        JSON response body as a string, or {"error": ..., "status_code": ...}.
    """
    # Guard: require the call-rest-api skill to have been loaded in this turn.
    # The skill's SKILL.md contains "Sidecar Port Catalog" as a unique marker;
    # its presence in any prior ToolMessage proves the agent read it.
    # messages = runtime.state.get("messages", []) if runtime and runtime.state else []
    # skill_loaded = any(
    #     "Sidecar Port Catalog" in (getattr(msg, "content", "") or "")
    #     for msg in messages
    # )
    # if not skill_loaded:
    #     return json.dumps({
    #         "error": "skill_required",
    #         "message": (
    #             "You must load the `call-rest-api` skill before calling this tool. "
    #             "Read the skill to discover available ports, endpoints, and request schemas, "
    #             "then retry this tool call."
    #         ),
    #     })

    method = method.upper()
    url = f"http://localhost:{port}{path}"

    # Enrich json_body with session_id and user_id from graph context so the
    # agent only needs to supply the request payload — not identity fields.
    if json_body is not None and runtime is not None:
        ctx = getattr(runtime, "context", None)
        if ctx is not None:
            if "session_id" not in json_body and getattr(ctx, "session_id", None):
                json_body = {**json_body, "session_id": ctx.session_id}
            if "user_id" not in json_body and getattr(ctx, "user_id", None):
                json_body = {**json_body, "user_id": ctx.user_id}

    writer = get_stream_writer()
    writer({"event": "rest-api-start", "payload": {"method": method, "url": url}})

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.request(
                method=method,
                url=url,
                params=params or {},
                json=json_body,
            )

        try:
            body = response.json()
        except Exception:
            body = response.text

        writer({
            "event": "rest-api-complete",
            "payload": {"status_code": response.status_code, "url": url},
        })

        if response.is_error:
            return json.dumps({
                "error": f"HTTP {response.status_code}",
                "status_code": response.status_code,
                "body": body,
            })

        # Strip large prediction blobs — cache in Redis and return a compact reference
        if isinstance(body, dict) and body.get("predictions"):
            predictions = body.pop("predictions")
            key = f"gasleak:predictions:{uuid.uuid4().hex}"
            try:
                from app.text_to_sql.tools.get_schema import get_redis_client
                rc = get_redis_client()
                rc.setex(key, 300, json.dumps(predictions))
                body["predictions_key"] = key

                # Also cache GeoJSON if present
                geojson = body.pop("prediction_geojson", None)
                if geojson:
                    geojson_key = f"gasleak:geojson:{uuid.uuid4().hex}"
                    rc.setex(geojson_key, 300, json.dumps(geojson))
                    body["geojson_key"] = geojson_key
            except Exception as redis_err:
                logger.warning("Failed to cache gasleakagent predictions in Redis: %s", redis_err)
            body["row_count"] = len(predictions)
            body["sample_rows"] = predictions[:5]

        return json.dumps(body) if not isinstance(body, str) else body

    except httpx.TimeoutException:
        logger.warning("call_rest_api timed out: %s %s", method, url)
        writer({"event": "rest-api-error", "payload": {"error": "timeout", "url": url}})
        return json.dumps({"error": "Request timed out", "url": url})

    except Exception as exc:
        logger.exception("call_rest_api failed: %s %s", method, url)
        writer({"event": "rest-api-error", "payload": {"error": str(exc), "url": url}})
        return json.dumps({"error": str(exc), "url": url})
