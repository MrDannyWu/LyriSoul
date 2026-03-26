"""
spotify_client.py
-----------------
Thin wrapper around the Spotipy API calls used by this application.
Each function accepts a pre-built `spotipy.Spotify` client so that
auth concerns (token refresh etc.) are handled entirely in auth.py.
"""

import logging
from typing import Optional

import spotipy
from fastapi import HTTPException

from models import (
    AlbumModel,
    ArtistModel,
    AudioFeaturesModel,
    CurrentTrackResponse,
    RecentTrackItem,
    RecentTracksResponse,
    TrackModel,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_artist(artist_data: dict) -> ArtistModel:
    return ArtistModel(
        id=artist_data["id"],
        name=artist_data["name"],
        spotify_url=artist_data.get("external_urls", {}).get("spotify"),
    )


def _parse_album(album_data: dict) -> AlbumModel:
    # Pick the largest available image
    images = album_data.get("images", [])
    cover_url = images[0]["url"] if images else None
    return AlbumModel(
        id=album_data["id"],
        name=album_data["name"],
        cover_url=cover_url,
        release_date=album_data.get("release_date"),
    )


def _parse_track(
    item: dict, 
    progress_ms: int = 0, 
    is_playing: bool = False,
    shuffle_state: bool = False,
    repeat_state: str = "off"
) -> TrackModel:
    return TrackModel(
        id=item["id"],
        name=item["name"],
        artists=[_parse_artist(a) for a in item.get("artists", [])],
        album=_parse_album(item["album"]),
        duration_ms=item["duration_ms"],
        progress_ms=progress_ms,
        is_playing=is_playing,
        shuffle_state=shuffle_state,
        repeat_state=repeat_state,
        spotify_url=item.get("external_urls", {}).get("spotify"),
        preview_url=item.get("preview_url"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API functions
# ─────────────────────────────────────────────────────────────────────────────


# Module-level cache: keep the last successfully-parsed playback data so we can
# return it when Spotify's API momentarily goes silent (e.g. right after pause/resume).
_last_playback_data: Optional[dict] = None
_last_playback_ts: float = 0.0
_PLAYBACK_CACHE_TTL: float = 86400.0  # 24 hours (prevents Spotify API idle timeouts from violently kicking user out of Lyrics UI after pausing for 30s)

def get_current_track(sp: spotipy.Spotify) -> Optional[TrackModel]:
    """
    Fetch the user's currently playing track and active playback state.
    Strategy:
      1. Try current_playback() – gives shuffle/repeat state.
      2. If empty, fall back to current_user_playing_track().
      3. If still empty but we have a recent cached response (≤30 s old),
         return that with is_playing=False so the UI keeps showing the track.
    Returns None only when there is genuinely nothing to show.
    """
    global _last_playback_data, _last_playback_ts

    import time
    data = None

    # ── 1. Primary: current_playback() ──────────────────────────────────────
    try:
        data = sp.current_playback()
        logger.info("current_playback(): is_playing=%s, type=%s, has_item=%s",
                    data.get("is_playing") if data else None,
                    data.get("currently_playing_type") if data else None,
                    bool(data and data.get("item")))
    except spotipy.SpotifyException as e:
        logger.warning("current_playback() failed: %s", e)

    # ── 2. Fallback: current_user_playing_track() ───────────────────────────
    if not data or not data.get("item"):
        logger.info("current_playback returned empty — trying current_user_playing_track")
        try:
            fallback = sp.current_user_playing_track()
            if fallback and fallback.get("item"):
                data = {
                    "item":                   fallback["item"],
                    "progress_ms":            fallback.get("progress_ms", 0),
                    "is_playing":             fallback.get("is_playing", False),
                    "currently_playing_type": fallback.get("currently_playing_type", "track"),
                    "shuffle_state":          False,
                    "repeat_state":           "off",
                }
                logger.info("Fallback track: %s", fallback["item"].get("name"))
        except spotipy.SpotifyException as e:
            logger.error("current_user_playing_track() failed: %s", e)
            raise HTTPException(status_code=502, detail="Spotify API error fetching current track")

    # ── 3. Still empty? Use recent cache to avoid false "nothing playing" ───
    if not data or not data.get("item"):
        age = time.time() - _last_playback_ts
        if _last_playback_data and age <= _PLAYBACK_CACHE_TTL:
            logger.info("Both APIs empty — serving cached playback data (age=%.1fs)", age)
            data = {**_last_playback_data, "is_playing": False}
        else:
            logger.info("No track playing (both APIs empty, cache expired/missing)")
            return None

    # ── Skip actual podcast episodes (they have no album art / lyrics) ──────
    playing_type = data.get("currently_playing_type", "track")
    if playing_type == "episode":
        logger.info("Skipping podcast episode")
        return None

    # ── Cache on success ─────────────────────────────────────────────────────
    _last_playback_data = data
    _last_playback_ts   = time.time()

    item = data["item"]
    return _parse_track(
        item,
        progress_ms=data.get("progress_ms", 0),
        is_playing=data.get("is_playing", False),
        shuffle_state=data.get("shuffle_state", False),
        repeat_state=data.get("repeat_state", "off"),
    )



def get_audio_features(sp: spotipy.Spotify, track_id: str) -> Optional[AudioFeaturesModel]:
    """
    Fetch audio analysis features for a single track by Spotify track ID.
    Returns None if Spotify has no features for the track (local files,
    podcasts, or API errors) — audio features are optional and should not
    break the main current-track endpoint.
    """
    if not track_id:
        return None
        
    # Spotify deprecated audio_features on Nov 27 2024, causing expected 403s.
    # Spotipy logs the HTTP Error internally before raising it. We suppress the 
    # internal spotipy logger temporarily to avoid console spam.
    sp_logger = logging.getLogger("spotipy.client")
    orig_level = sp_logger.level
    try:
        sp_logger.setLevel(logging.CRITICAL)
        features_list = sp.audio_features([track_id])
    except Exception as e:
        logger.debug("audio_features unavailable for %s: %s", track_id, e)
        return None
    finally:
        sp_logger.setLevel(orig_level)

    if not features_list or features_list[0] is None:
        return None

    f = features_list[0]
    try:
        return AudioFeaturesModel(
            track_id=track_id,
            energy=f["energy"],
            valence=f["valence"],
            danceability=f["danceability"],
            acousticness=f["acousticness"],
            instrumentalness=f["instrumentalness"],
            tempo=f["tempo"],
            loudness=f["loudness"],
            mode=f["mode"],
            key=f["key"],
            time_signature=f["time_signature"],
        )
    except (KeyError, TypeError) as e:
        logger.warning("Malformed audio features for %s: %s", track_id, e)
        return None


def get_recent_tracks(sp: spotipy.Spotify, limit: int = 20) -> RecentTracksResponse:
    """
    Fetch the user's recently played tracks (up to 50).
    """
    limit = max(1, min(limit, 50))  # Spotify caps at 50
    try:
        data = sp.current_user_recently_played(limit=limit)
    except spotipy.SpotifyException as e:
        logger.error("Spotify API error (recently_played): %s", e)
        raise HTTPException(status_code=502, detail="Spotify API error fetching recent tracks")

    items = []
    for entry in data.get("items", []):
        track = _parse_track(entry["track"])
        items.append(
            RecentTrackItem(
                track=track,
                played_at=entry.get("played_at", ""),
            )
        )

    return RecentTracksResponse(items=items, total=len(items))


def get_track_by_id(sp: spotipy.Spotify, track_id: str) -> TrackModel:
    """Fetch a specific track's details by its Spotify track ID."""
    try:
        item = sp.track(track_id)
    except spotipy.SpotifyException as e:
        logger.error("Spotify API error (track %s): %s", track_id, e)
        raise HTTPException(status_code=502, detail=f"Spotify API error fetching track {track_id}")

    if not item:
        raise HTTPException(status_code=404, detail=f"Track not found: {track_id}")

    return _parse_track(item)


def control_playback(sp: spotipy.Spotify, action: str, **kwargs):
    """
    Control playback state on the user's active device.
    Supported actions: 'play', 'pause', 'next', 'previous', 'shuffle', 'repeat'.
    """
    try:
        if action == "play":
            sp.start_playback(**kwargs)
        elif action == "pause":
            sp.pause_playback(**kwargs)
        elif action == "next":
            sp.next_track(**kwargs)
        elif action == "previous":
            sp.previous_track(**kwargs)
        elif action == "shuffle":
            state = kwargs.get("state", True)
            sp.shuffle(state)
        elif action == "repeat":
            state = kwargs.get("state", "context")
            sp.repeat(state)
        elif action == "seek":
            if "position_ms" not in kwargs:
                raise ValueError("Missing position_ms for seek action")
            sp.seek_track(int(kwargs["position_ms"]))
        else:
            raise ValueError(f"Unknown playback action: {action}")
    except spotipy.SpotifyException as e:
        logger.error("Spotify API error during playback control (%s): %s", action, e)
        # 403 usually means no active device or premium required.
        status = e.http_status if e.http_status else 502
        raise HTTPException(status_code=status, detail=f"Failed to execute playback action: {action}. Make sure you have an active Spotify device.")
    except Exception as e:
        logger.error("Unexpected error during playback control: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error executing playback control")

