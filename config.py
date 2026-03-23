"""
config.py
---------
Centralised application settings loaded from environment variables / .env file.
Uses pydantic-settings for validation.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List


class Settings(BaseSettings):
    """Application configuration — all values can be overridden via .env."""

    # Security / Auth
    secret_key: str = "supersecret_lyrica_key_change_in_production"
    
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

    # ── Security ───────────────────────────────────────────────────────────
    secret_key: str = "change-me-to-a-random-secret-string"

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
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# Global singleton — import this everywhere
settings = Settings()
