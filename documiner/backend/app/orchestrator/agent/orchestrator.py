import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Ensure the tools package (orchestrator/tools/) is importable
_AGENT_DIR = Path(__file__).resolve().parent
_ORCHESTRATOR_DIR = _AGENT_DIR.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

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


def _build_executor(model: str, api_key: str, base_url: str) -> AgentExecutor:
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, _TOOLS, prompt)
    return AgentExecutor(agent=agent, tools=_TOOLS, verbose=True)


async def invoke_deep_agent(
    messages: list[dict[str, str]],
    agent_model: str,
    agent_key: str,
    agent_base_url: str,
) -> str:
    """Invoke the orchestrator with a chat history and return the reply."""
    executor = _build_executor(agent_model, agent_key, agent_base_url)

    chat_history: list[Any] = []
    for m in messages[:-1]:
        if m["role"] == "user":
            chat_history.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            chat_history.append(AIMessage(content=m["content"]))

    last_input = messages[-1]["content"] if messages else ""

    result = await executor.ainvoke({
        "input": last_input,
        "chat_history": chat_history,
    })
    return result.get("output", "")
