import os
from functools import lru_cache

from pydantic import AliasChoices, ConfigDict, Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    agent_model: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_MODEL", "MODEL", "agent_model", "model"),
    )
    agent_key: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_KEY", "API_KEY", "agent_key", "api_key"),
    )
    agent_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_BASE_URL", "BASE_URL", "agent_base_url", "base_url"),
    )

    azure_openai_api_version: str = Field(default="2024-10-21")

    def get_db_url(self) -> str:
        """Backwards-compatible accessor used by legacy call sites."""
        return getattr(self, "db_url", "")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""

    return Settings()


settings = get_settings()