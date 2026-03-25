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
import os
import sys
from logging.handlers import TimedRotatingFileHandler

# ─────────────────────────────────────────────────────────────────────────────
# Logging — daily rotating files in data/logs/
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging():
    # Resolve logs directory next to the data/ folder
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    logs_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Daily rotating file handler — one file per day: logs/2026-03-25.log
    from datetime import date
    log_file = os.path.join(logs_dir, f"{date.today().isoformat()}.log")
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1,
        backupCount=30, encoding="utf-8", utc=False
    )
    # Rename rolled files to YYYY-MM-DD.log instead of .log.2026-03-24
    file_handler.suffix = "%Y-%m-%d"
    file_handler.namer = lambda name: os.path.join(
        os.path.dirname(name),
        f"{name.split('.')[-1]}.log"
    ) if "." in os.path.basename(name) else name
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

_setup_logging()
logger = logging.getLogger(__name__)

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
    max_age=3600 * 24 * 30,  # 30-day session lifetime
    https_only=False,
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
# /api/cover — Album art proxy with permanent local disk cache
# ─────────────────────────────────────────────────────────────────────────────

def _cover_cache_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "data", "cache")
    os.makedirs(d, exist_ok=True)
    return d


@app.get("/api/cover/{track_id}", tags=["music"], summary="Album art with local cache")
async def get_cover(track_id: str, url: str = Query(..., description="Spotify CDN image URL")):
    """
    Proxy endpoint for album cover images.
    - Checks data/cache/{track_id}.jpg on disk first.
    - If missing, downloads from Spotify CDN and saves permanently.
    - Returns the image bytes with proper Content-Type.
    """
    cache_path = os.path.join(_cover_cache_dir(), f"{track_id}.jpg")

    # Serve from disk cache
    if os.path.exists(cache_path):
        return FileResponse(
            cache_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    # Download from Spotify CDN
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            image_bytes = resp.content
            content_type = resp.headers.get("content-type", "image/jpeg")
    except Exception as e:
        logger.warning("Cover download failed for track %s: %s", track_id, e)
        raise HTTPException(status_code=502, detail="Failed to fetch album cover")

    # Save to disk (best-effort)
    try:
        with open(cache_path, "wb") as f:
            f.write(image_bytes)
        logger.info("Album cover cached: %s", cache_path)
    except Exception as e:
        logger.warning("Cover cache write failed: %s", e)

    return Response(
        content=image_bytes,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
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

    # ── Album cover: cache to disk server-side, rewrite URL ─────────────────
    # Download happens in background so it never delays the response.
    # The cover_url in the response is rewritten to the local proxy endpoint,
    # so the browser always loads from disk (works even with stale JS cache).
    if track.album and track.album.cover_url:
        cdn_url = track.album.cover_url
        cache_path = os.path.join(_cover_cache_dir(), f"{track.id}.jpg")

        if not os.path.exists(cache_path):
            async def _download_cover(tid: str, url: str, dest: str):
                import httpx as _httpx
                try:
                    async with _httpx.AsyncClient(timeout=10.0) as c:
                        r = await c.get(url, follow_redirects=True)
                        r.raise_for_status()
                        with open(dest, "wb") as f:
                            f.write(r.content)
                    logger.info("Album cover cached: %s", dest)
                except Exception as exc:
                    logger.warning("Cover download failed for %s: %s", tid, exc)

            asyncio.create_task(_download_cover(track.id, cdn_url, cache_path))

        # Always point client to local proxy (serves from disk if cached,
        # falls back to CDN download if not yet ready)
        track.album.cover_url = f"/api/cover/{track.id}?url={cdn_url}"

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


@app.get("/api/user/profile", tags=["user"], summary="Get current user's Spotify profile")
async def get_user_profile(sp=Depends(require_spotify)):
    """
    Returns display name, Spotify profile URL, follower count and avatar URL.
    The avatar image is cached to data/cache/avatar_{user_id}.jpg on first fetch.
    """
    import asyncio, httpx as _httpx
    try:
        me = sp.me()
    except Exception as e:
        logger.error("Spotify me() failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to fetch user profile")

    user_id    = me.get("id", "")
    name       = me.get("display_name") or user_id
    profile_url = me.get("external_urls", {}).get("spotify", "")
    followers  = me.get("followers", {}).get("total", 0)
    images     = me.get("images", [])
    avatar_cdn = images[0]["url"] if images else None

    # Cache avatar to disk
    local_avatar_url = None
    if avatar_cdn and user_id:
        cache_filename = f"avatar_{user_id}.jpg"
        cache_path = os.path.join(_cover_cache_dir(), cache_filename)
        if not os.path.exists(cache_path):
            try:
                async with _httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(avatar_cdn, follow_redirects=True)
                    r.raise_for_status()
                    with open(cache_path, "wb") as f:
                        f.write(r.content)
                logger.info("User avatar cached: %s", cache_path)
            except Exception as exc:
                logger.warning("Avatar download failed: %s", exc)
        if os.path.exists(cache_path):
            local_avatar_url = f"/api/cover/avatar_{user_id}?url={avatar_cdn}"

    return {
        "id": user_id,
        "name": name,
        "profile_url": profile_url,
        "followers": followers,
        "avatar_url": local_avatar_url or avatar_cdn,
        "avatar_cdn": avatar_cdn,
    }



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
