"""
test_api.py
-----------
Smoke tests for the Spotify Lyrics API.

Run with:
    pytest test_api.py -v

These tests use the real LRCLIB API for lyrics (no mocking needed — it's free
and public). Spotify-protected endpoints are checked for correct 401 responses
only, since OAuth requires a browser flow.
"""

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    """Sync test client — simple and fast for smoke tests."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# Health & Auth Status
# ─────────────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body


class TestAuthStatus:
    def test_not_logged_in(self, client):
        resp = client.get("/auth/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["logged_in"] is False
        assert body["display_name"] is None

    def test_login_redirect(self, client):
        """GET /auth/login should redirect to Spotify accounts page."""
        resp = client.get("/auth/login", follow_redirects=False)
        assert resp.status_code in (302, 307)
        location = resp.headers.get("location", "")
        assert "accounts.spotify.com" in location

    def test_logout(self, client):
        resp = client.get("/auth/logout")
        assert resp.status_code == 200
        assert "Logged out" in resp.json()["message"]


# ─────────────────────────────────────────────────────────────────────────────
# Lyrics (public endpoint — no auth needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestLyricsEndpoint:
    def test_lyrics_known_track(self, client):
        """Should return lyrics data for a well-known track."""
        resp = client.get("/api/lyrics", params={"artist": "Adele", "track": "Hello"})
        assert resp.status_code == 200
        body = resp.json()
        assert "synced" in body
        assert "plain" in body
        assert "has_synced" in body
        assert body["source"] == "lrclib"

    def test_lyrics_unknown_track(self, client):
        """Unknown track should still return 200 with empty lyrics (graceful)."""
        resp = client.get(
            "/api/lyrics",
            params={"artist": "ZZZUnknownArtist999", "track": "ZZZUnknownTrack999"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_synced"] is False

    def test_lyrics_missing_params(self, client):
        """Missing required query params should return 422 Unprocessable Entity."""
        resp = client.get("/api/lyrics")
        assert resp.status_code == 422

    def test_lyrics_missing_track(self, client):
        resp = client.get("/api/lyrics", params={"artist": "Adele"})
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Protected endpoints — expect 401 without auth
# ─────────────────────────────────────────────────────────────────────────────

class TestProtectedEndpoints:
    def test_current_track_unauthenticated(self, client):
        resp = client.get("/api/current-track")
        assert resp.status_code == 401

    def test_recent_tracks_unauthenticated(self, client):
        resp = client.get("/api/recent-tracks")
        assert resp.status_code == 401

    def test_audio_features_unauthenticated(self, client):
        resp = client.get("/api/audio-features/4iV5W9uYEdYUVa79Axb7Rh")
        assert resp.status_code == 401

    def test_get_track_unauthenticated(self, client):
        resp = client.get("/api/track/4iV5W9uYEdYUVa79Axb7Rh")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# LRC Parser unit tests (pure logic, no HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestLrcParser:
    def test_parse_basic_lrc(self):
        from lyrics_client import parse_lrc

        lrc = """
[00:17.35] Hello, it's me
[00:22.90] I was wondering if after all these years
[01:05.00] Hello from the other side
"""
        lines = parse_lrc(lrc)
        assert len(lines) == 3
        assert lines[0].time_ms == 17350
        assert lines[0].text == "Hello, it's me"
        assert lines[1].time_ms == 22900
        assert lines[2].time_ms == 65000

    def test_parse_empty_lrc(self):
        from lyrics_client import parse_lrc

        lines = parse_lrc("")
        assert lines == []

    def test_parse_skips_empty_lines(self):
        from lyrics_client import parse_lrc

        lrc = "[00:10.00] \n[00:20.00] Some text\n"
        lines = parse_lrc(lrc)
        assert len(lines) == 1
        assert lines[0].text == "Some text"

    def test_parse_sorted_output(self):
        from lyrics_client import parse_lrc

        # Deliberately out of order
        lrc = "[01:00.00] Second\n[00:30.00] First\n"
        lines = parse_lrc(lrc)
        assert lines[0].text == "First"
        assert lines[1].text == "Second"
