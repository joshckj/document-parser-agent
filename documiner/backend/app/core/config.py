from functools import lru_cache

from pydantic import AliasChoices, ConfigDict, Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Central application configuration.

    All environment variables are defined and validated here.
    Do NOT use os.getenv() elsewhere in the codebase.
    """

    # -------------------------------------------------------------------------
    # Core app settings
    # -------------------------------------------------------------------------
    app_name: str = "gas_leak_service"
    environment: str = "development"


    # -------------------------------------------------------------------------
    # Optional observability (Langfuse) — leave empty to skip
    # -------------------------------------------------------------------------
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # -------------------------------------------------------------------------
    # Paths / assets (defaults allow app to run out-of-the-box)
    # -------------------------------------------------------------------------
    prompt_directory: str = "prompt_registry"

    # -------------------------------------------------------------------------
    # Agent / LLM settings  (reads MODEL, API_KEY, BASE_URL from .env)
    # -------------------------------------------------------------------------
    agent_model: str = Field(
        default="hosted_vllm/Qwen/Qwen3.6-35B-A3B-FP8",
        validation_alias=AliasChoices("MODEL", "agent_model"),
    )
    agent_key: str = Field(
        default="",
        validation_alias=AliasChoices("API_KEY", "agent_key"),
    )
    agent_base_url: str = Field(
        default="https://litellm.qa.in.spdigital.sg",
        validation_alias=AliasChoices("BASE_URL", "agent_base_url"),
    )

    # -------------------------------------------------------------------------
    # Pydantic v2 configuration
    # -------------------------------------------------------------------------
    model_config = ConfigDict(
        env_file=[".env", "../.env"],  # works whether run from gasleakagent/ or project root
        env_file_encoding="utf-8",
        extra="ignore",  # prevents crashes from unused env vars
        protected_namespaces=("settings_",)
    )

    def get_db_url(self) -> str:
        """Backwards-compatible accessor used by legacy call sites."""
        return self.db_url


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.

    Ensures environment variables are read and validated only once.
    """
    return Settings()


# Module-level singleton used by import consumers (e.g. frontend.py)
settings: Settings = get_settings()