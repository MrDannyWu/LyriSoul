"""
config.py
---------
Centralised application settings loaded from environment variables / .env file.
Uses pydantic-settings for validation.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List

import sys
import os

def get_env_path():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    data_dir = os.path.join(base_dir, "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, ".env")

class Settings(BaseSettings):
    """Application configuration — all values can be overridden via .env."""

    # Security / Auth — must be stable across restarts so session cookies survive
    secret_key: str = "lyrisoul-stable-session-key-do-not-change"

    # Spotify BYOK Config (Can be empty on first boot)
    spotify_client_id: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:666/callback"

    # Scopes needed by the app
    spotify_scopes: str = (
        "user-read-currently-playing "
        "user-read-playback-state "
        "user-read-recently-played "
        "user-modify-playback-state"
    )


    # ── CORS ───────────────────────────────────────────────────────────────
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Accept either a list or a comma-separated string from .env."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ── Cache / Performance ────────────────────────────────────────────────
    # Seconds to cache lyrics to avoid hammering LRCLIB
    lyrics_cache_ttl: int = 300

    model_config = SettingsConfigDict(
        env_file=get_env_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# Global singleton — import this everywhere
settings = Settings()
