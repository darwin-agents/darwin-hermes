from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    darwin_id: str
    session_id: str
    agent_jwt: str
    backend_url: str

    # Injected via Modal Secret
    redis_url: str = Field(default="redis://localhost:6379")
    browserbase_api_key: str = Field(default="")
    browserbase_project_id: str = Field(default="")
    langsmith_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")

    llm_model: str = Field(default="claude-sonnet-4-6")
    vcc_ref: str = Field(default="")
