"""
config.py — Minimal settings for local dev / test runs.

In production (Modal), all values come from env vars injected by
modal_app.py and the Modal Secret. This file is only used outside Modal.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    darwin_id: str = Field(default="local-dev")
    session_id: str = Field(default="local-session")
    agent_jwt: str = Field(default="")
    backend_url: str = Field(default="http://localhost:8000")
    vcc_ref: str = Field(default="")
