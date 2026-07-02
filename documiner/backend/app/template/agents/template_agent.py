"""
Template agent using native LangChain create_agent.

This creates a simple chatbot agent with:
- System prompt from Langfuse (with local markdown fallback)
- Config overrides (temperature, max_tokens, model) from prompt
- Memory management tools (upsert/remove preference/profile)
- Progressive skill disclosure via load_template_skill
- Raw LangGraph streaming

Skills live at: skills_registry/template/<skill_name>/SKILL.md

Related files:
- app/template/tools/time_tools.py: Tools used by this agent
- app/template/nodes/run_agent.py: Node that runs this agent
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, cast

from langchain.agents import create_agent
from langchain.agents.middleware import ModelFallbackMiddleware, ModelRequest, dynamic_prompt
from langchain.tools import tool
from langgraph.store.base import BaseStore

from app.core.config import Settings, get_settings
from app.llms import create_llm, create_fallback_models
from app.observability.langfuse_client import init_langfuse
from app.template.memory import TemplateState
from app.template.tools import get_current_time
from app.utils.time_utils import get_singapore_time_iso
from app.utils.prompt_loader import get_prompt_and_config
from app.utils.skill_loader import load_skills_from_langfuse, load_skill_content, BaseSkillMiddleware
from app.tools.memory_tools import upsert_user_preference, remove_user_preference

logger = logging.getLogger(__name__)

# Skills live at <project_root>/skills_registry/template/ which is copied to
# /app/skills_registry/template/ in the Docker container (WORKDIR=/app).
_SKILLS_DIR = Path(__file__).parents[3] / "skills_registry" / "template"


# Fallback prompt for template agent
_TEMPLATE_FALLBACK_PROMPT = (
    "You are a helpful AI assistant. Be concise and helpful in your responses.\n\n"
    "You have access to memory management tools to store and retrieve user preferences and profile.\n"
    "Use these tools when the user asks you to remember something or update their preferences.\n\n"
    "# Current User Memories\n\n"
    "{{user_memories}}\n\n"
    "# System Time\n\n"
    "{{time}}"
)


def create_template_agent(
    settings: Optional[Settings] = None,
    store: Optional[BaseStore] = None,
) -> Any:
    """Create the template chatbot agent.

    Args:
        settings: Application settings (loads from env if None)
        store: LangGraph store for memory tools (required for memory operations)

    Returns:
        Compiled LangGraph agent with memory management capabilities
    """
    if settings is None:
        settings = get_settings()

    # --- Skills (loaded here so load_template_skill closes over langfuse) ---
    langfuse = init_langfuse(settings)
    template_skills = load_skills_from_langfuse(langfuse, "template", _SKILLS_DIR)

    @tool
    def load_template_skill(skill_name: str) -> str:
        """Load full instructions for a template agent skill or a reference file within a skill.

        Call this to get detailed guidelines for handling a specific category of user
        request. To load a reference file within a skill folder, use the path format:
          "<skill_name>/references/<file_name>"
        Example: load_template_skill("memory-guidelines/references/key_naming")

        Args:
            skill_name: Skill name (e.g. "memory-guidelines") or a reference path
                        (e.g. "memory-guidelines/references/key_naming").
        """
        return load_skill_content(skill_name, template_skills, _SKILLS_DIR,
                                  langfuse=langfuse, agent_name="template")

    class TemplateSkillMiddleware(BaseSkillMiddleware):
        tools = [load_template_skill]

        def __init__(self_) -> None:
            self_._init_addendum(
                template_skills,
                "Use `load_template_skill` to load full instructions for the relevant skill "
                "before handling requests that fall into one of these categories.",
            )

    # Load prompt and config from Langfuse/markdown using centralized loader
    system_prompt_template, prompt_config = get_prompt_and_config(
        settings, "template_agent", fallback_prompt=_TEMPLATE_FALLBACK_PROMPT
    )
    
    # Also try "template" as a fallback prompt name
    if not system_prompt_template.strip():
        system_prompt_template, prompt_config = get_prompt_and_config(
            settings, "template", fallback_prompt=_TEMPLATE_FALLBACK_PROMPT
        )
    
    # Extract config values with defaults
    temperature = cast(float, prompt_config.get("temperature", 0.7))
    max_tokens = cast(int, prompt_config.get("max_tokens", 2000))
    model_override = prompt_config.get("model")  # None = use default from .env
    enable_thinking = prompt_config.get("enable_thinking")

    model = create_llm(
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
        model_override=model_override,
        enable_thinking=enable_thinking,
    )
    
    # Build tools list (load_template_skill is added via TemplateSkillMiddleware)
    tools = [
        get_current_time,
    ]

    # Add memory tools if store is provided
    if store is not None:
        tools.extend([
            upsert_user_preference,
            remove_user_preference,
        ])
        logger.info("Memory management tools added to template agent")
    else:
        logger.info("No store provided - memory tools not available")

    @dynamic_prompt
    async def template_system_prompt(request: ModelRequest) -> str:
        """Inject loaded memory context into system prompt."""
        user_id = (request.state.get("user_id") or "").strip()
        role = (request.state.get("role") or "").strip()
        preferences = request.state.get("preferences") or []
        profile = request.state.get("profile")
        permissions = request.state.get("permissions") or {}

        memories_blob = {
            "user": {
                "id": user_id,
                "role": role,
            },
            "preferences": preferences,
            "profile": profile,
            "permissions": permissions,
        }

        # Replace placeholders in the prompt template
        sys_text = system_prompt_template.replace("{{user_memories}}", str(memories_blob))

        # Append system time
        sys_text = sys_text + f"\n# System Time\n{get_singapore_time_iso()}\n"
        return sys_text

    fallback_models = create_fallback_models(
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    middleware: list[Any] = [template_system_prompt, TemplateSkillMiddleware()]
    if fallback_models:
        middleware.append(ModelFallbackMiddleware(*fallback_models))

    agent = create_agent(
        model=model,
        tools=tools,
        state_schema=TemplateState,
        middleware=middleware,
        store=store,  # Pass store for tool execution (though we use contextvars)
    )

    return agent
