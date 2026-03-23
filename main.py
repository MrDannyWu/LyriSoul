"""
main.py
-------
FastAPI application entry point.

Startup:
    uvicorn main:app --reload --port 666

API overview:
    /auth/*       — Spotify OAuth2 flow
    /api/*        — Protected music data endpoints
    /health       — Health check
    /docs         — Swagger UI (auto-generated)
"""

import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import auth
import lyrics_client
import spotify_client
from config import settings
from models import (
    AudioFeaturesModel,
    AuthStatusResponse,
    CurrentTrackResponse,
    LyricsResponse,
    RecentTracksResponse,
    SetupRequestModel,
    SetupStatusResponse,
    TrackModel,
)
from dotenv import set_key

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# App initialisation
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Spotify Lyrics API",
    description=(
        "A FastAPI backend that wraps Spotify's Web API and LRCLIB to deliver "
        "real-time current-track info with synchronised lyrics and audio features."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="lyrics_session",
    max_age=3600 * 8,   # 8-hour session lifetime
    https_only=False,    # Set to True in production behind HTTPS
    same_site="lax",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,  # Required for session cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth router ───────────────────────────────────────────────────────────────

app.include_router(auth.router)

# ── Static frontend ───────────────────────────────────────────────
import os
_frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
app.mount("/static", StaticFiles(directory=_frontend_dir), name="static")

@app.get("/", include_in_schema=False)
def serve_frontend():
    """Serve the frontend SPA from the root URL."""
    response = FileResponse(os.path.join(_frontend_dir, "index.html"))
    # Disable cache to ensure users get the newest Zero-Config HTML/JS
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ─────────────────────────────────────────────────────────────────────────────
# Dependency: require authenticated Spotify client
# ─────────────────────────────────────────────────────────────────────────────

def require_spotify(request: Request):
    """FastAPI dependency — returns an authenticated spotipy.Spotify client."""
    return auth.get_spotify_client(request)


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"], summary="Health check")
def health():
    return {"status": "ok", "version": app.version}

# ─────────────────────────────────────────────────────────────────────────────
# /api/config — Dynamic Setup (BYOK)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/config/status", response_model=SetupStatusResponse, tags=["system"])
def config_status():
    """Check if the user has provided their own Spotify Client ID (BYOK)."""
    is_ready = bool(settings.spotify_client_id and len(settings.spotify_client_id.strip()) >= 32)
    return SetupStatusResponse(is_configured=is_ready, redirect_uri=settings.spotify_redirect_uri)


@app.post("/api/config/setup", response_model=SetupStatusResponse, tags=["system"])
def config_setup(data: SetupRequestModel):
    """Save the user's custom Spotify Client ID to .env and hot-reload."""
    from config import get_env_path
    env_path = get_env_path()
    
    # Write to .env file (No client secret needed for PKCE!)
    set_key(env_path, "SPOTIFY_CLIENT_ID", data.client_id.strip())
    set_key(env_path, "SPOTIFY_REDIRECT_URI", data.redirect_uri.strip())
    
    # Update running config memory
    settings.spotify_client_id = data.client_id.strip()
    settings.spotify_redirect_uri = data.redirect_uri.strip()
    
    return SetupStatusResponse(is_configured=True, redirect_uri=settings.spotify_redirect_uri)




# ─────────────────────────────────────────────────────────────────────────────
# /callback — Root-level OAuth redirect URI
# Must match EXACTLY what is registered in Spotify Developer Dashboard
# Delegates to the same logic as /auth/callback
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/callback", tags=["auth"], include_in_schema=False)
def spotify_root_callback(
    request: Request,
    code: str = None,
    error: str = None,
):
    """
    Spotify OAuth2 redirect target registered in the Developer Dashboard.
    Delegates to the auth.callback handler.
    """
    return auth.callback(request, code=code, error=error)


# ─────────────────────────────────────────────────────────────────────────────
# /api — Music data endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/api/current-track",
    response_model=CurrentTrackResponse,
    tags=["music"],
    summary="Get currently playing track with lyrics and audio features",
)
async def current_track(sp=Depends(require_spotify)):
    """
    Primary endpoint for the lyrics app.

    Returns:
    - **track**: Current Spotify track (name, artist, album cover, progress, duration)
    - **lyrics**: Synced (LRC) + plain text lyrics from LRCLIB
    - **audio_features**: energy, valence, tempo, danceability, etc.

    If nothing is playing, returns 204 No Content.
    """
    track = spotify_client.get_current_track(sp)
    if not track:
        return Response(status_code=204)  # 204 must have no body — never raise HTTPException with 204

    # Fetch lyrics and audio features concurrently
    import asyncio

    artist_name = track.artists[0].name if track.artists else ""

    lyrics_task = asyncio.create_task(
        lyrics_client.get_lyrics(artist_name, track.name, getattr(track, 'duration_ms', 0))
    )

    # audio_features is a sync Spotipy call — run in threadpool
    loop = asyncio.get_running_loop()
    audio_task = loop.run_in_executor(
        None, spotify_client.get_audio_features, sp, track.id
    )

    lyrics = await lyrics_task
    try:
        audio_features = await audio_task
    except Exception as e:
        logger.warning("audio_features task failed (non-critical): %s", e)
        audio_features = None

    return CurrentTrackResponse(
        track=track,
        lyrics=lyrics,
        audio_features=audio_features,
    )


@app.get(
    "/api/lyrics",
    response_model=LyricsResponse,
    tags=["music"],
    summary="Get lyrics by artist and track name (no login required)",
)
async def get_lyrics(
    artist: str = Query(..., description="Artist name", examples=["Adele"]),
    track: str = Query(..., description="Track/song name", examples=["Hello"]),
    duration_ms: int = Query(0, description="Track duration in ms for fingerprinting matching"),
):
    """
    Fetch lyrics from LRCLIB without requiring Spotify authentication.
    Useful for searching lyrics directly from the frontend.

    Returns synced (LRC timestamped) lyrics when available,
    with plain-text lyrics as fallback.
    """
    return await lyrics_client.get_lyrics(artist, track, duration_ms)


@app.get(
    "/api/recent-tracks",
    response_model=RecentTracksResponse,
    tags=["music"],
    summary="Get recently played tracks (requires login)",
)
def recent_tracks(
    limit: int = Query(default=20, ge=1, le=50, description="Number of tracks to return"),
    sp=Depends(require_spotify),
):
    """
    Returns the user's listening history (up to 50 tracks).
    Useful for a "recently played" sidebar.
    """
    return spotify_client.get_recent_tracks(sp, limit=limit)


@app.get(
    "/api/audio-features/{track_id}",
    response_model=AudioFeaturesModel,
    tags=["music"],
    summary="Get audio features for a specific track",
)
def audio_features(track_id: str, sp=Depends(require_spotify)):
    """
    Returns Spotify audio analysis data for any track by its Spotify ID.
    Useful for driving dynamic UI themes (e.g. colour based on valence/energy).
    """
    features = spotify_client.get_audio_features(sp, track_id)
    if not features:
        raise HTTPException(status_code=404, detail=f"No audio features found for track: {track_id}")
    return features


@app.get(
    "/api/track/{track_id}",
    tags=["music"],
    summary="Get details for a specific track by Spotify ID",
)
def get_track(track_id: str, sp=Depends(require_spotify)):
    """Retrieve full track metadata for a given Spotify track ID."""
    return spotify_client.get_track_by_id(sp, track_id)


from pydantic import BaseModel
from typing import Optional

class PlaybackControlParams(BaseModel):
    state: Optional[str | bool] = None
    position_ms: Optional[int] = None

@app.post(
    "/api/player/{action}",
    tags=["music"],
    summary="Control playback (play, pause, next, prev, shuffle, repeat)"
)
def control_player(
    action: str, 
    params: Optional[PlaybackControlParams] = None, 
    sp=Depends(require_spotify)
):
    """
    Control Spotify playback.
    Valid actions: 'play', 'pause', 'next', 'previous', 'shuffle', 'repeat'.
    For 'shuffle' state should be boolean.
    For 'repeat' state should be one of 'track', 'context', 'off'.
    Must have active Spotify Premium device.
    """
    valid_actions = {"play", "pause", "next", "previous", "shuffle", "repeat", "seek"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid action. Allowed: {valid_actions}")

    kwargs = {}
    if params is not None:
        if params.state is not None:
            kwargs["state"] = params.state
        if params.position_ms is not None:
            kwargs["position_ms"] = params.position_ms

    spotify_client.control_playback(sp, action, **kwargs)
    return {"status": "success", "action": action}


# ─────────────────────────────────────────────────────────────────────────────
# Exception handlers
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dev entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=666, reload=True)
