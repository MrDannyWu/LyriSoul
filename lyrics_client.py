"""
lyrics_client.py
----------------
Lyrics fetching from LRCLIB (https://lrclib.net) — free, no API key needed.
Also contains the LRC-format parser used to produce timestamped LyricLine objects.

LRCLIB returns:
  - syncedLyrics  : "[mm:ss.xx] line text" format
  - plainLyrics   : plain text fallback

Fallback order: syncedLyrics → plainLyrics → {"not found"}
"""

import base64
import logging
import re
import time
from typing import Dict, Optional, Tuple

import httpx

from models import LyricLine, LyricsResponse

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
LRC_LINE_RE = re.compile(r"\[(\d+):(\d+\.\d+)\](.*)")

# ─────────────────────────────────────────────────────────────────────────────
# Simple in-memory cache (artist+title → (timestamp, LyricsResponse))
# ─────────────────────────────────────────────────────────────────────────────

_cache: Dict[str, Tuple[float, LyricsResponse]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_key(artist: str, track: str) -> str:
    return f"{artist.lower().strip()}|{track.lower().strip()}"


def _get_cached(key: str) -> Optional[LyricsResponse]:
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _set_cache(key: str, value: LyricsResponse) -> None:
    _cache[key] = (time.time(), value)


# ─────────────────────────────────────────────────────────────────────────────
# LRC Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_lrc(lrc_text: str) -> list[LyricLine]:
    """
    Parse an LRC-format string into a list of LyricLine objects sorted by time.

    LRC format example:
        [00:17.35] Hello, it's me
        [00:22.90] I was wondering if after all these years...
    """
    lines: list[LyricLine] = []
    for raw_line in lrc_text.splitlines():
        match = LRC_LINE_RE.match(raw_line.strip())
        if not match:
            continue
        minutes = int(match.group(1))
        seconds = float(match.group(2))
        text = match.group(3).strip()
        time_ms = int((minutes * 60 + seconds) * 1000)
        if text:  # skip empty/instrumental lines
            lines.append(LyricLine(time_ms=time_ms, text=text))

    return sorted(lines, key=lambda l: l.time_ms)


# ─────────────────────────────────────────────────────────────────────────────
# LRCLIB HTTP calls
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_lyrics_raw(artist: str, track: str) -> Optional[dict]:
    """
    Hit LRCLIB GET /api/get endpoint.
    Returns the raw JSON dict, or None if not found / on HTTP errors.
    """
    params = {
        "artist_name": artist,
        "track_name": track,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{LRCLIB_BASE}/get", params=params)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.info("LRCLIB: lyrics not found for '%s' – '%s'", artist, track)
                return None
            else:
                logger.warning(
                    "LRCLIB returned %s for '%s' – '%s'",
                    response.status_code,
                    artist,
                    track,
                )
                return None
    except httpx.RequestError as e:
        logger.error("LRCLIB request error: %s", e)
        return None


async def search_lyrics_raw(query: str) -> Optional[dict]:
    """
    Fallback search using LRCLIB GET /api/search when exact match fails.
    Returns the first result or None.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{LRCLIB_BASE}/search", params={"q": query})
            if response.status_code == 200:
                results = response.json()
                return results[0] if results else None
            return None
    except httpx.RequestError as e:
        logger.error("LRCLIB search error: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Fallback Fetchers (Netease & KuGou)
# ─────────────────────────────────────────────────────────────────────────────

async def search_lyrics_netease(artist: str, track: str) -> Optional[str]:
    """Fallback 1: Search Netease Cloud Music (163) for LRC lyrics."""
    query = f"{artist} {track}"
    search_url = "https://music.163.com/api/search/get/web"
    params = {"s": query, "type": 1, "limit": 5}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://music.163.com/"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(search_url, params=params, headers=headers)
            data = res.json()
            
            songs = data.get("result", {}).get("songs", [])
            if not songs:
                return None
                
            for song in songs:
                song_id = song["id"]
                lyric_url = "https://music.163.com/api/song/lyric"
                l_params = {"id": song_id, "lv": 1, "tv": -1, "kv": 1}
                l_res = await client.get(lyric_url, params=l_params, headers=headers)
                l_data = l_res.json()
                
                lrc = l_data.get("lrc", {}).get("lyric", "")
                if lrc:
                    return lrc
    except Exception as e:
        logger.warning("Netease fallback error for '%s': %s", query, e)
        
    return None

async def search_lyrics_kugou(artist: str, track: str) -> Optional[str]:
    """
    Search KuGou for raw LRC text. Works well for tracks not on LRCLIB.
    """
    query = f"{artist} {track}"
    search_url = "http://songsearch.kugou.com/song_search_v2"
    params = {
        "keyword": query,
        "page": 1,
        "pagesize": 1,
        "platform": "WebFilter"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Search for the song
            res = await client.get(search_url, params=params, headers=headers)
            data = res.json()
            songs = data.get("data", {}).get("lists", [])
            if not songs:
                return None
            
            song = songs[0]
            filehash = song.get("FileHash")
            artist_name = song.get("SingerName")
            song_name = song.get("SongName")
            duration = song.get("Duration", 0) * 1000
            album_id = song.get("ID")
            
            # 2. Search for the lyric accesskey
            l_search_url = "http://krcs.kugou.com/search"
            l_params = {
                "ver": 1,
                "man": "yes",
                "client": "mobi",
                "keyword": f"{artist_name} - {song_name}",
                "duration": duration,
                "hash": filehash,
                "album_audio_id": album_id
            }
            l_res = await client.get(l_search_url, params=l_params, headers=headers)
            l_data = l_res.json()
            candidates = l_data.get("candidates", [])
            if not candidates:
                return None
                
            accesskey = candidates[0].get("accesskey")
            lrc_id = candidates[0].get("id")
            
            # 3. Download and decode base64 LRC
            dl_url = "http://lyrics.kugou.com/download"
            dl_params = {
                "ver": 1,
                "client": "pc",
                "id": lrc_id,
                "accesskey": accesskey,
                "fmt": "lrc",
                "charset": "utf8"
            }
            dl_res = await client.get(dl_url, params=dl_params, headers=headers)
            dl_data = dl_res.json()
            
            lrc_b64 = dl_data.get("content", "")
            if lrc_b64:
                return base64.b64decode(lrc_b64).decode('utf-8')
                
    except Exception as e:
        logger.warning("KuGou fallback error for '%s': %s", query, e)
        
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

async def get_lyrics(artist: str, track: str) -> LyricsResponse:
    """
    Fetch and parse lyrics for a given artist + track name.

    Strategy:
      1. Check in-memory cache
      2. Exact search: LRCLIB /api/get
      3. Fuzzy fallback: LRCLIB /api/search
      4. Return empty LyricsResponse if nothing found
    """
    key = _cache_key(artist, track)

    # 1. Cache hit
    cached = _get_cached(key)
    if cached:
        logger.debug("Lyrics cache hit: %s | %s", artist, track)
        return cached

    # 2. Exact fetch
    raw = await fetch_lyrics_raw(artist, track)

    # 3. Fuzzy fallback
    if not raw:
        logger.info("Falling back to LRCLIB search for '%s' – '%s'", artist, track)
        raw = await search_lyrics_raw(f"{artist} {track}")

    # 4. Fallbacks (Netease -> KuGou)
    source = "lrclib"
    synced_raw = ""
    plain_raw = None
    
    if raw:
        synced_raw = raw.get("syncedLyrics") or ""
        plain_raw = raw.get("plainLyrics") or None
    else:
        logger.info("Falling back to Netease Cloud Music for '%s' – '%s'", artist, track)
        netease_lrc = await search_lyrics_netease(artist, track)
        if netease_lrc:
            synced_raw = netease_lrc
            source = "netease"
        else:
            logger.info("Falling back to KuGou for '%s' – '%s'", artist, track)
            kugou_lrc = await search_lyrics_kugou(artist, track)
            if kugou_lrc:
                synced_raw = kugou_lrc
                source = "kugou"

    # Build response
    synced_lines = parse_lrc(synced_raw) if synced_raw.strip() else []

    result = LyricsResponse(
        synced=synced_lines,
        plain=plain_raw,
        has_synced=len(synced_lines) > 0,
        source=source,
    )

    _set_cache(key, result)
    return result
