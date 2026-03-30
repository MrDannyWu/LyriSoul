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
import json
import os
from typing import Optional

import spotipy
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from spotipy.oauth2 import SpotifyPKCE

from config import settings, get_env_path
from models import AuthStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# ─────────────────────────────────────────────────────────────────────────────
# Persistent token storage (survives restarts)
# ─────────────────────────────────────────────────────────────────────────────

def _token_path() -> str:
    data_dir = os.path.dirname(get_env_path())
    return os.path.join(data_dir, "spotify_token.json")

def _load_token() -> Optional[dict]:
    path = _token_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _save_token(token_info: dict):
    path = _token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(token_info, f)

def _delete_token():
    path = _token_path()
    if os.path.exists(path):
        os.remove(path)


def _pkce_path() -> str:
    data_dir = os.path.dirname(get_env_path())
    return os.path.join(data_dir, "pkce_verifier.tmp")

def _save_pkce_verifier(verifier: str):
    with open(_pkce_path(), "w", encoding="utf-8") as f:
        f.write(verifier)

def _load_pkce_verifier() -> Optional[str]:
    path = _pkce_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return None

def _delete_pkce_verifier():
    path = _pkce_path()
    if os.path.exists(path):
        os.remove(path)


def _get_oauth_manager(state: Optional[str] = None) -> SpotifyPKCE:
    """Create a fresh SpotifyPKCE instance (stateless helper)."""
    return SpotifyPKCE(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_redirect_uri,
        scope=settings.spotify_scopes,
        state=state,
        open_browser=False,
        cache_handler=spotipy.cache_handler.MemoryCacheHandler(),
    )


def get_token_from_session(request: Request) -> Optional[dict]:
    """Return token: first from session (fast path), then from disk."""
    token = request.session.get("token_info")
    if not token:
        token = _load_token()
        if token:
            request.session["token_info"] = token  # warm the session cache
    return token


def get_spotify_client(request: Request) -> spotipy.Spotify:
    token_info = get_token_from_session(request)
    if not token_info:
        raise HTTPException(status_code=401, detail="Not authenticated. Please login via /auth/login")

    oauth = _get_oauth_manager()
    if oauth.is_token_expired(token_info):
        try:
            token_info = oauth.refresh_access_token(token_info["refresh_token"])
            _save_token(token_info)                       # persist refreshed token
            request.session["token_info"] = token_info
            logger.info("Token refreshed and saved to disk")
        except spotipy.oauth2.SpotifyOauthError as e:
            logger.warning("Token refresh irrevocably failed (invalid grant): %s", e)
            _delete_token()
            request.session.clear()
            raise HTTPException(
                status_code=401,
                detail="Session expired and refresh revoked. Please login again via /auth/login",
            )
        except Exception as e:
            logger.error("Transient network error refreshing token: %s", e)
            raise HTTPException(
                status_code=502,
                detail="Spotify API is temporarily unreachable. Skipping refresh safely.",
            )

    return spotipy.Spotify(auth=token_info["access_token"])


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/login", summary="Redirect to Spotify login page")
def login(request: Request, desktop: Optional[int] = 0):
    """
    Generate a Spotify authorisation URL and redirect the user to it.
    After authorising, Spotify redirects back to /auth/callback.
    """
    if not settings.spotify_client_id or len(settings.spotify_client_id.strip()) < 32:
        logger.warning("Attempted to login without a valid Client ID. Redirecting to frontend config.")
        return RedirectResponse(url="/")
        
    request.session["desktop_mode"] = desktop

    oauth = _get_oauth_manager()
    auth_url = oauth.get_authorize_url()

    # Store code_verifier in BOTH session AND disk file.
    # The session cookie may be dropped when WebView navigates to an external
    # domain (accounts.spotify.com), so the disk file is the reliable fallback.
    request.session["pkce_verifier"] = oauth.code_verifier
    _save_pkce_verifier(oauth.code_verifier)

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
    
    # Restore the PKCE code_verifier — try session first, then disk fallback
    verifier = request.session.pop("pkce_verifier", None)
    if not verifier:
        verifier = _load_pkce_verifier()
        if verifier:
            logger.info("Restored PKCE verifier from disk fallback")
    _delete_pkce_verifier()  # always clean up

    if not verifier:
        logger.error("Missing PKCE code_verifier in session")
        raise HTTPException(status_code=400, detail="Session expired or invalid PKCE request. Please try logging in again.")
    oauth.code_verifier = verifier
    
    # Critical: Spotipy's get_access_token() checks BOTH code_verifier AND code_challenge.
    # If either is None, it silently NUKES code_verifier by generating new random ones!
    # We must fake a code_challenge here to bypass this malicious overwrite.
    oauth.code_challenge = "RESTORED_BYPASS_NOOP"

    try:
        # SpotifyPKCE gets the token and drops the details. We must fetch the 
        # full dict (incl. refresh_token) out of the ephemeral MemoryCacheHandler.
        oauth.get_access_token(code, check_cache=False)
        token_info = oauth.cache_handler.get_cached_token()
        if not token_info:
            raise Exception("No cached token found after exchange")
    except Exception as e:
        logger.error("Token exchange failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to exchange authorization code: {str(e)}")

    # Save to session AND disk (persistent across restarts)
    request.session["token_info"] = token_info
    _save_token(token_info)

    # Fetch user profile and persist display_name for convenience
    sp = spotipy.Spotify(auth=token_info["access_token"])
    user = sp.current_user()
    request.session["user"] = {
        "id": user.get("id"),
        "display_name": user.get("display_name"),
        "avatar_url": (user.get("images") or [{}])[0].get("url"),
    }

    logger.info("User logged in: %s", user.get("display_name"))

    is_desktop = request.session.pop("desktop_mode", 0)
    if is_desktop:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "LyriSoul")
            if hwnd:
                if ctypes.windll.user32.IsIconic(hwnd):
                    ctypes.windll.user32.ShowWindow(hwnd, 9) # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.error("Failed to foreground desktop window: %s", e)

        from fastapi.responses import HTMLResponse
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head><title>LyriSoul Auth</title></head>
        <body style="background:#080c14;color:#1DB954;font-family:sans-serif;text-align:center;padding-top:100px;">
            <h2>Authentication Successful! 🚀</h2>
            <p style="color:#fff">You may now close this browser window safely.</p>
            <script>window.close();</script>
        </body>
        </html>
        """)

    # Redirect to the frontend SPA for web users
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


@router.post("/logout", summary="Clear session and logout")
def logout(request: Request):
    """Clear the server-side session and delete the persisted token."""
    _delete_token()
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
    if not token_info:
        return AuthStatusResponse(logged_in=False)

    user = request.session.get("user")
    
    # If we have a token but NO user info in session (common in fresh WebView),
    # fetch it once from Spotify and warm the session.
    if not user:
        try:
            sp = spotipy.Spotify(auth=token_info["access_token"])
            me = sp.current_user()
            user = {
                "id": me.get("id"),
                "display_name": me.get("display_name"),
                "avatar_url": (me.get("images") or [{}])[0].get("url"),
            }
            request.session["user"] = user
            logger.info("Warmed session for user: %s", user.get("display_name"))
        except Exception as e:
            logger.warning("Session warming failed: %s", e)
            return AuthStatusResponse(logged_in=True)

    return AuthStatusResponse(
        logged_in=True,
        display_name=user.get("display_name"),
        user_id=user.get("id"),
        avatar_url=user.get("avatar_url"),
    )
