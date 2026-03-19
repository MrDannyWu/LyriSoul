"""
auth.py
-------
Spotify OAuth 2.0 flow using Spotipy + Starlette SessionMiddleware.

Token lifecycle:
  1. Frontend redirects user to GET /auth/login
  2. User authorises on Spotify, which redirects back to GET /auth/callback
  3. We exchange the `code` for an access_token + refresh_token, stored in session
  4. All API handlers read the token from session and pass it to spotify_client
  5. GET /auth/refresh re-fetches a new access_token using the stored refresh_token
"""

import logging
from typing import Optional

import spotipy
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from spotipy.oauth2 import SpotifyOAuth

from config import settings
from models import AuthStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_oauth_manager(state: Optional[str] = None) -> SpotifyOAuth:
    """Create a fresh SpotifyOAuth instance (stateless helper)."""
    return SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=settings.spotify_scopes,
        state=state,
        open_browser=False,
        cache_handler=spotipy.cache_handler.MemoryCacheHandler(),
    )


def get_token_from_session(request: Request) -> Optional[dict]:
    """Return the stored token dict from session, or None."""
    return request.session.get("token_info")


def get_spotify_client(request: Request) -> spotipy.Spotify:
    """
    Build an authenticated Spotipy client from the session token.
    Raises 401 if not logged in.
    """
    token_info = get_token_from_session(request)
    if not token_info:
        raise HTTPException(status_code=401, detail="Not authenticated. Please login via /auth/login")

    # Auto-refresh if expired
    oauth = _get_oauth_manager()
    if oauth.is_token_expired(token_info):
        try:
            token_info = oauth.refresh_access_token(token_info["refresh_token"])
            request.session["token_info"] = token_info
            logger.info("Token refreshed for session")
        except Exception as e:
            logger.warning("Token refresh failed: %s", e)
            request.session.clear()
            raise HTTPException(
                status_code=401,
                detail="Session expired. Please login again via /auth/login",
            )

    return spotipy.Spotify(auth=token_info["access_token"])


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/login", summary="Redirect to Spotify login page")
def login(request: Request):
    """
    Generate a Spotify authorisation URL and redirect the user to it.
    After authorising, Spotify redirects back to /auth/callback.
    """
    oauth = _get_oauth_manager()
    auth_url = oauth.get_authorize_url()
    logger.info("Redirecting to Spotify auth: %s", auth_url)
    return RedirectResponse(url=auth_url)


@router.get("/callback", summary="OAuth2 callback — exchange code for tokens")
def callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    """
    Spotify redirects here after user authorises (or denies) the app.
    On success: store tokens in session, redirect to frontend.
    On denial:  raise 403.
    """
    if error:
        logger.warning("Spotify auth error: %s", error)
        raise HTTPException(status_code=403, detail=f"Spotify auth denied: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    oauth = _get_oauth_manager()
    try:
        token_info = oauth.get_access_token(code, as_dict=True, check_cache=False)
    except Exception as e:
        logger.error("Token exchange failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to exchange authorization code")

    # Save to session
    request.session["token_info"] = token_info

    # Fetch user profile and persist display_name for convenience
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
    request.session["user"] = {
        "id": user.get("id"),
        "display_name": user.get("display_name"),
        "avatar_url": (user.get("images") or [{}])[0].get("url"),
    }

    logger.info("User logged in: %s", user.get("display_name"))

    # Redirect to the frontend SPA
    return RedirectResponse(url="/")


@router.get("/refresh", summary="Manually refresh the access token")
def refresh_token(request: Request):
    """Force-refresh the stored access token using the refresh_token."""
    token_info = get_token_from_session(request)
    if not token_info:
        raise HTTPException(status_code=401, detail="Not authenticated")

    oauth = _get_oauth_manager()
    try:
        new_token = oauth.refresh_access_token(token_info["refresh_token"])
        request.session["token_info"] = new_token
        return {"message": "Token refreshed successfully", "expires_in": new_token.get("expires_in")}
    except Exception as e:
        logger.error("Manual refresh failed: %s", e)
        raise HTTPException(status_code=500, detail="Token refresh failed")


@router.get("/logout", summary="Clear session and logout")
def logout(request: Request):
    """Clear the server-side session."""
    request.session.clear()
    return {"message": "Logged out successfully"}


@router.get(
    "/status",
    response_model=AuthStatusResponse,
    summary="Check current authentication status",
)
def auth_status(request: Request):
    """Returns login state and basic user info (if logged in)."""
    token_info = get_token_from_session(request)
    user = request.session.get("user", {})
    return AuthStatusResponse(
        logged_in=token_info is not None,
        display_name=user.get("display_name"),
        user_id=user.get("id"),
        avatar_url=user.get("avatar_url"),
    )
