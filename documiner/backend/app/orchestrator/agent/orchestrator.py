import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Ensure the tools package (orchestrator/tools/) is importable
_AGENT_DIR = Path(__file__).resolve().parent
_ORCHESTRATOR_DIR = _AGENT_DIR.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from tools.call_parser_api import call_ocr_api

# Load system prompt from prompt_registry/documiner.md
_PROMPT_PATH = (
    Path(__file__).resolve().parents[5] / "prompt_registry" / "documiner.md"
)


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        raw = _PROMPT_PATH.read_text()
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return raw.strip()
    return "You are a helpful document parsing assistant."


_SYSTEM_PROMPT = _load_system_prompt()
_TOOLS = [call_ocr_api]


def _build_agent(model: str, api_key: str, base_url: str):
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
    )
    return create_react_agent(llm, _TOOLS, prompt=_SYSTEM_PROMPT)


async def invoke_deep_agent(
    messages: list[dict[str, str]],
    agent_model: str,
    agent_key: str,
    agent_base_url: str,
) -> str:
    """Invoke the orchestrator with a chat history and return the reply."""
    agent = _build_agent(agent_model, agent_key, agent_base_url)

    lc_messages: list[HumanMessage | AIMessage] = []
    for m in messages:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))

    result = await agent.ainvoke({"messages": lc_messages})
    last = result["messages"][-1]
    return last.content if hasattr(last, "content") else str(last)
