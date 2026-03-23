"""
models.py
---------
Shared Pydantic models used across API response serialization.
"""

from pydantic import BaseModel, Field
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Lyrics
# ─────────────────────────────────────────────────────────────────────────────

class LyricLine(BaseModel):
    """A single timestamped lyric line parsed from LRC format."""
    time_ms: int = Field(..., description="Timestamp in milliseconds")
    text: str = Field(..., description="Lyric text at this timestamp")


class LyricsResponse(BaseModel):
    """Lyric data for a track, supporting both synced (LRC) and plain text."""
    synced: List[LyricLine] = Field(
        default_factory=list,
        description="Timestamped lyrics, sorted by time_ms",
    )
    plain: Optional[str] = Field(
        default=None,
        description="Full plain-text lyrics (fallback when synced unavailable)",
    )
    has_synced: bool = Field(
        default=False,
        description="Whether synced (timestamped) lyrics are available",
    )
    source: str = Field(default="lrclib", description="Lyrics data provider")


# ─────────────────────────────────────────────────────────────────────────────
# Spotify Track / Artist / Album
# ─────────────────────────────────────────────────────────────────────────────

class ArtistModel(BaseModel):
    id: str
    name: str
    spotify_url: Optional[str] = None


class AlbumModel(BaseModel):
    id: str
    name: str
    cover_url: Optional[str] = Field(
        default=None,
        description="URL of album cover image (640px preferred)",
    )
    release_date: Optional[str] = None


class TrackModel(BaseModel):
    id: str
    name: str
    artists: List[ArtistModel]
    album: AlbumModel
    duration_ms: int
    progress_ms: int = Field(
        default=0,
        description="Current playback position in milliseconds",
    )
    is_playing: bool = False
    shuffle_state: bool = False
    repeat_state: str = "off"
    spotify_url: Optional[str] = None
    preview_url: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Audio Features
# ─────────────────────────────────────────────────────────────────────────────

class AudioFeaturesModel(BaseModel):
    """
    Spotify audio analysis values, each in range [0.0, 1.0] unless noted.
    """
    track_id: str
    energy: float = Field(..., ge=0.0, le=1.0)
    valence: float = Field(..., ge=0.0, le=1.0, description="Musical positivity")
    danceability: float = Field(..., ge=0.0, le=1.0)
    acousticness: float = Field(..., ge=0.0, le=1.0)
    instrumentalness: float = Field(..., ge=0.0, le=1.0)
    tempo: float = Field(..., description="Estimated tempo in BPM")
    loudness: float = Field(..., description="Average loudness in dB (typically -60 to 0)")
    mode: int = Field(..., description="0 = minor, 1 = major")
    key: int = Field(..., description="Pitch class notation: 0=C, 1=C♯/D♭, ..., 11=B")
    time_signature: int = Field(..., description="Estimated time signature (e.g. 4)")

    # Derived helper — useful for frontend theme/mood logic
    @property
    def mood_label(self) -> str:
        """Simple mood label derived from energy + valence."""
        if self.valence >= 0.6 and self.energy >= 0.6:
            return "happy"
        elif self.valence < 0.4 and self.energy >= 0.6:
            return "angry"
        elif self.valence >= 0.6 and self.energy < 0.4:
            return "peaceful"
        else:
            return "melancholic"


# ─────────────────────────────────────────────────────────────────────────────
# Composite Responses
# ─────────────────────────────────────────────────────────────────────────────

class CurrentTrackResponse(BaseModel):
    """Combined response for GET /api/current-track."""
    track: TrackModel
    lyrics: LyricsResponse
    audio_features: Optional[AudioFeaturesModel] = None


class RecentTrackItem(BaseModel):
    track: TrackModel
    played_at: str = Field(..., description="ISO 8601 timestamp of when it was played")


class RecentTracksResponse(BaseModel):
    items: List[RecentTrackItem]
    total: int


# ─────────────────────────────────────────────────────────────────────────────
# Auth & Setup
# ─────────────────────────────────────────────────────────────────────────────

class SetupStatusResponse(BaseModel):
    is_configured: bool
    redirect_uri: str

class SetupRequestModel(BaseModel):
    client_id: str
    redirect_uri: str

class AuthStatusResponse(BaseModel):
    logged_in: bool
    display_name: Optional[str] = None
    user_id: Optional[str] = None
    avatar_url: Optional[str] = None


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None
