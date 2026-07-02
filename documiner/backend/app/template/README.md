# Template Agent — Developer Guide

This directory is the canonical starting point for building a new LangGraph agent in this backend. Copy the whole `template/` folder, rename everything to your agent's name, and follow this guide.

---

## Directory Layout

```
template/
├── agents/
│   └── template_agent.py     # Agent factory (system prompt, tools, middleware)
├── graph/
│   └── template_graph.py     # LangGraph StateGraph wiring + checkpointer
├── memory/
│   └── state.py              # TemplateState (MessagesState + memory fields)
├── nodes/
│   ├── load_memory.py        # Pre-agent node: loads user preferences/profile
│   └── run_agent.py          # Agent execution node (streams, handles context)
└── tools/
    └── time_tool.py          # Example @tool definition
```

**Companion directories outside `template/`:**

| Path                                | Purpose                                       |
| ----------------------------------- | --------------------------------------------- |
| `skills_registry/template/`         | Skill markdown files (lazy-loaded at runtime) |
| `prompt_registry/template_agent.md` | Local system prompt fallback (optional)       |
| `app/api/v1/endpoints/template.py`  | FastAPI router wired to this graph            |

---

## Step 1 — Copy and Rename

```bash
cp -r app/template app/my_agent
```

Then do a project-wide rename (`template` → `my_agent`, `Template` → `MyAgent`). The key files to touch are:

1. `agents/template_agent.py` → `agents/my_agent.py`
2. `graph/template_graph.py` → `graph/my_agent_graph.py`
3. `memory/state.py` (rename `TemplateState` → `MyAgentState`, `TemplateContext` → `MyAgentContext`)
4. `nodes/run_agent.py` (update the import of the agent factory)

---

## Step 2 — Add or Change the System Prompt

The system prompt is loaded by priority order (first non-empty wins):

1. **Langfuse** — prompt named `template_agent` (or whatever you pass to `get_prompt_and_config`)
2. **Local markdown** — `prompt_registry/template_agent.md` (relative to the configured `PROMPT_DIRECTORY`)
3. **Hardcoded fallback** — `_TEMPLATE_FALLBACK_PROMPT` string in `agents/template_agent.py`

### Using the hardcoded fallback (quickest for dev)

Edit `_TEMPLATE_FALLBACK_PROMPT` in [agents/template_agent.py](agents/template_agent.py):

```python
_TEMPLATE_FALLBACK_PROMPT = (
    "You are a customer support bot for Acme Corp.\n\n"
    "Answer only questions related to Acme products.\n\n"
    "# Current User Memories\n\n"
    "{{user_memories}}\n\n"
    "# System Time\n\n"
    "{{time}}"
)
```

Keep the `{{user_memories}}` and `{{time}}` placeholders — `create_template_agent` replaces them at runtime.

### Using a local markdown file

Create `prompt_registry/my_agent.md` (the directory is set by `PROMPT_DIRECTORY` in `.env`):

```markdown
---
name: my_agent
config:
  temperature: 0.3
  max_tokens: 4000
  model: claude-sonnet-4-5 # optional override
---

You are a customer support bot for Acme Corp.
...
```

Then change the `prompt_name` argument in `agents/my_agent.py`:

```python
system_prompt_template, prompt_config = get_prompt_and_config(
    settings, "my_agent", fallback_prompt=_MY_AGENT_FALLBACK_PROMPT
)
```

### Model config options (in the markdown front matter `config:` block)

| Key               | Type   | Default     | Notes                                   |
| ----------------- | ------ | ----------- | --------------------------------------- |
| `temperature`     | float  | `0.7`       | Sampling temperature                    |
| `max_tokens`      | int    | `2000`      | Max output tokens                       |
| `model`           | string | env default | Override model (e.g. `claude-opus-4-7`) |
| `enable_thinking` | bool   | `null`      | Enable extended thinking                |

---

## Step 3 — Add Tools

Tools are plain `@tool`-decorated functions. Add them to `tools/`.

```python
# tools/my_tool.py
from langchain_core.tools import tool

@tool
def lookup_order(order_id: str) -> str:
    """Look up the status of a customer order by order ID."""
    # ... your logic ...
    return f"Order {order_id} is shipped."
```

Then register the tool in `agents/my_agent.py`:

```python
from app.my_agent.tools.my_tool import lookup_order

tools = [
    get_current_time,
    lookup_order,          # <-- add here
]
```

The `load_template_skill` tool (skill lazy-loading) and memory tools (`upsert_user_preference`, `remove_user_preference`) are added automatically — you only list your domain-specific tools here.

---

## Step 4 — Add Skills (Lazy-Loaded Instructions)

Skills are markdown files that the agent loads on-demand. This keeps the system prompt lean — only a skills _menu_ is injected at start; full instructions are fetched when the agent calls `load_template_skill("skill_name")`.

### Create a skill

```
skills_registry/
└── my_agent/
    └── order_handling/
        ├── SKILL.md
        └── references/
            └── refund_policy.md    # optional sub-reference
```

`SKILL.md` format:

```markdown
---
name: order_handling
description: Rules for looking up, updating, and refunding customer orders.
---

# Order Handling

## Looking Up Orders

Call `lookup_order(order_id)` when the user provides an order number...

## Refunds

See the refund policy in `order_handling/references/refund_policy`.
```

### Wire the skill directory in the agent

In `agents/my_agent.py`, point `_SKILLS_DIR` at your new folder:

```python
_SKILLS_DIR = Path(__file__).parents[3] / "skills_registry" / "my_agent"
```

The skills are loaded at module init via `load_skills_from_disk(_SKILLS_DIR)` and injected into the system prompt as a menu. The agent calls `load_template_skill("order_handling")` when it needs full details.

---

## Step 5 — Update the State

[memory/state.py](memory/state.py) defines what gets passed between graph nodes. Add fields your agent needs:

```python
class MyAgentState(MessagesState):
    user_id: Optional[str]
    role: Optional[str]
    user_prompt: Optional[str]
    preferences: Optional[List[Dict[str, Any]]]
    profile: Optional[Dict[str, Any]]
    permissions: Optional[Dict[str, Any]]
    # --- add your agent-specific fields ---
    account_tier: Optional[str]
    recent_orders: Optional[List[Dict[str, Any]]]
```

Update `load_memory.py` to populate any new fields before the agent runs.

---

## Step 6 — Wire into the API

### 6a. Create the endpoint file

Copy `app/api/v1/endpoints/template.py` → `app/api/v1/endpoints/my_agent.py`.

Change the import at the top:

```python
from app.my_agent.graph import get_my_agent_graph
from app.my_agent.memory import MyAgentContext
```

And update the `get_template_graph()` call inside the endpoint to `get_my_agent_graph()`.

The endpoint ships two routes out of the box:

| Route                      | Description                                            |
| -------------------------- | ------------------------------------------------------ |
| `POST /my-agent/chat`      | SSE streaming — emits `update`, `done`, `error` events |
| `POST /my-agent/chat/sync` | Blocking JSON response                                 |
| `GET  /my-agent/health`    | Liveness check                                         |

### 6b. Register the router

In [app/api/v1/api.py](../api/v1/api.py), add:

```python
from app.api.v1.endpoints import my_agent   # <-- import

api_router.include_router(
    my_agent.router,
    prefix="/my-agent",
    tags=["my-agent-langgraph"],
)
```

### 6c. Register with LangGraph Studio (optional)

In `langgraph.json`:

```json
{
  "graphs": {
    "my_agent": "./app/my_agent/graph/my_agent_graph.py:graph"
  }
}
```

---

## Step 7 — Test with curl (Docker)

The backend runs inside the `backend_app` Docker container, exposed on **host port 5001**. All commands below assume the stack is already up; run from the project root (`Final_SPeedy_Codes/`) if it isn't:

```bash
# Start the full stack (detached)
docker compose up -d backend_app postgres redis

# Tail backend logs while testing
docker compose logs -f backend_app
```

> The base URL for all API calls is `http://localhost:5001`.

---

### Health check

```bash
curl http://localhost:5001/api/v1/my-agent/health
```

Expected:

```json
{
  "status": "ok",
  "service": "my-agent-graph",
  "test_user": "test_user@example.com",
  "test_role": "admin",
  "timestamp": 1745808000.0
}
```

---

### Streaming chat (SSE)

The `-N` flag disables curl buffering so SSE events print as they arrive.

```bash
curl -N -X POST http://localhost:5001/api/v1/my-agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my order status for ORD-123?", "session_id": "test-session-1"}'
```

Expected output (events arrive incrementally):

```
event: update
data: {"type": "ai", "content": "Let me look that up...", "message_count": 2}

event: update
data: {"type": "tool", "content": "", "message_count": 3}

event: done
data: {"response": "Order ORD-123 is shipped and arrives Thursday.", "elapsed_time": 1.42, "user_id": "test_user@example.com", "session_id": "test-session-1"}
```

---

### Non-streaming (sync) chat

```bash
curl -X POST http://localhost:5001/api/v1/my-agent/chat/sync \
  -H "Content-Type: application/json" \
  -d '{"message": "What time is it?", "session_id": "test-session-1"}'
```

Expected:

```json
{
  "response": "The current time in Singapore is 2026-04-28T14:30:00+08:00.",
  "elapsed_time": 0.87,
  "trace_id": "abc123"
}
```

---

### Conversation continuity (session_id keeps thread alive)

```bash
# Turn 1 — store a preference
curl -s -X POST http://localhost:5001/api/v1/my-agent/chat/sync \
  -H "Content-Type: application/json" \
  -d '{"message": "Remember that I prefer concise answers.", "session_id": "alice-session"}' \
  | jq .response

# Turn 2 — same session_id, agent uses stored context
curl -s -X POST http://localhost:5001/api/v1/my-agent/chat/sync \
  -H "Content-Type: application/json" \
  -d '{"message": "What can you do?", "session_id": "alice-session"}' \
  | jq .response
```

> **Note:** Conversation persistence (checkpointer) only activates when `FAST_ENV=prod` or `localprod`. In the default dev environment the graph compiles without a checkpointer, so history is in-process only and does not survive a container restart.

---

### Rebuilding after code changes

The `backend_app` image mounts `prompt_registry/` as a live volume but the Python source is baked into the image at build time. After editing agent code, rebuild and restart:

```bash
docker compose up -d --build backend_app
```

To inspect the running container directly:

```bash
docker compose exec backend_app bash
```

---

## Quick-Reference Checklist

```
[ ] Copy template/ → my_agent/
[ ] Rename all TemplateXxx → MyAgentXxx symbols
[ ] Edit system prompt (fallback string or prompt_registry markdown)
[ ] Add tools to tools/ and register in agents/my_agent.py
[ ] Create skills_registry/my_agent/<skill_name>/SKILL.md if needed
[ ] Update _SKILLS_DIR path in agents/my_agent.py
[ ] Add any state fields to memory/state.py
[ ] Populate new state fields in nodes/load_memory.py
[ ] Create app/api/v1/endpoints/my_agent.py
[ ] Register router in app/api/v1/api.py
[ ] (Optional) Add graph entry to langgraph.json
[ ] Test: curl health, sync, streaming
```