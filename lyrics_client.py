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
import difflib
from typing import Dict, Optional, Tuple

import httpx

from models import LyricLine, LyricsResponse

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
LRC_TIMETAG_RE = re.compile(r"\[(\d+):(\d+\.\d+)\]")

# ─────────────────────────────────────────────────────────────────────────────
# Persistent file cache — data/lyrics/<artist>—<track>.json, never expires
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
import sys

def _lyrics_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "data", "lyrics")
    os.makedirs(d, exist_ok=True)
    return d

def _cache_key(artist: str, track: str) -> str:
    """Human-readable, filesystem-safe key: 'adele—hello'."""
    def safe(s: str) -> str:
        s = s.lower().strip()
        return re.sub(r'[\\/:*?"<>|]', '_', s)
    return f"{safe(artist)}—{safe(track)}"

def _cache_path(key: str) -> str:
    return os.path.join(_lyrics_dir(), f"{key}.json")

# In-memory hot-cache (avoids disk reads on repeated calls within session)
_hot_cache: Dict[str, "LyricsResponse"] = {}

def _get_cached(key: str) -> Optional["LyricsResponse"]:
    # 1. Hot (memory) cache
    if key in _hot_cache:
        return _hot_cache[key]
    # 2. Disk cache
    path = _cache_path(key)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = LyricsResponse(**data)
            _hot_cache[key] = result
            return result
        except Exception as e:
            logger.warning("Lyrics disk cache read error (%s): %s", path, e)
    return None

def _set_cache(key: str, value: "LyricsResponse") -> None:
    _hot_cache[key] = value
    path = _cache_path(key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value.model_dump(), f, ensure_ascii=False, indent=2)
        logger.info("Lyrics cached to disk: %s", path)
    except Exception as e:
        logger.warning("Lyrics disk cache write error (%s): %s", path, e)


# ─────────────────────────────────────────────────────────────────────────────
# LRC Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_lrc(lrc_text: str) -> list[LyricLine]:
    """
    Parse an LRC-format string into a list of LyricLine objects sorted by time.

    LRC format example:
        [00:17.35] Hello, it's me
        [00:22.90] I was wondering if after all these years...
        [01:20.00][02:40.00] Multi-timestamp concatenation
    """
    lines: list[LyricLine] = []
    for raw_line in lrc_text.splitlines():
        raw_line = raw_line.strip()
        tags = LRC_TIMETAG_RE.findall(raw_line)
        if not tags:
            continue
            
        # Extract the pure text by removing all timestamp tags
        text = LRC_TIMETAG_RE.sub("", raw_line).strip()
        
        if text:  # skip empty/instrumental lines
            for minutes, seconds in tags:
                time_ms = int((int(minutes) * 60 + float(seconds)) * 1000)
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

def _is_valid_match(query_track: str, result_track: str, target_duration: int = 0, result_duration: int = 0) -> bool:
    """
    Prevent wildly incorrect lyrics matching.
    Handles bilingual titles like 'A Love Letter 情书' which may appear as
    '情书' or 'A Love Letter' in different music databases.
    """
    def clean(s: str) -> str:
        s = re.sub(r'\(.*?\)|\[.*?\]|（.*?）|【.*?】|-.*', '', s)
        return re.sub(r'[^\w]', '', s).lower()

    def cjk_only(s: str) -> str:
        """Extract only CJK (Chinese/Japanese/Korean) characters."""
        return re.sub(r'[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', '', s)

    def ascii_only(s: str) -> str:
        """Extract only ASCII word characters."""
        return re.sub(r'[^a-z0-9]', '', s.lower())

    c_query = clean(query_track)
    c_res   = clean(result_track)

    # If either cleans to empty, allow through
    if not c_query or not c_res:
        return True

    # --- Strategy 1: Full text ratio ---
    ratio = difflib.SequenceMatcher(None, c_query, c_res).ratio()
    threshold = 0.35
    if len(c_query) <= 2:  threshold = 0.8
    elif len(c_query) <= 4: threshold = 0.5
    if ratio >= threshold:
        return True

    # --- Strategy 2: CJK sub-match ---
    # "A Love Letter 情书" and "情书" share the same CJK part
    cjk_q = cjk_only(query_track)
    cjk_r = cjk_only(result_track)
    if cjk_q and cjk_r:
        cjk_ratio = difflib.SequenceMatcher(None, cjk_q, cjk_r).ratio()
        if cjk_ratio >= 0.7:
            return True
        # One is a substring of the other (e.g. "情书" in "情书（重制版）")
        if cjk_q in cjk_r or cjk_r in cjk_q:
            return True

    # --- Strategy 3: ASCII sub-match ---
    ascii_q = ascii_only(query_track)
    ascii_r = ascii_only(result_track)
    if ascii_q and ascii_r and len(ascii_q) >= 4:
        ascii_ratio = difflib.SequenceMatcher(None, ascii_q, ascii_r).ratio()
        if ascii_ratio >= 0.75:
            return True
        if ascii_q in ascii_r or ascii_r in ascii_q:
            return True

    # --- Strategy 4: Duration fingerprint fallback ---
    if target_duration > 0 and result_duration > 0:
        if abs(target_duration - result_duration) <= 3500:
            # Relaxed length constraint: allow if one title is contained in the other
            # or if length ratio is reasonable (handles bilingual vs short titles)
            len_ratio = min(len(c_query), len(c_res)) / max(len(c_query), len(c_res), 1)
            if len_ratio >= 0.4 or len(c_query) == len(c_res):
                return True

    return False

async def search_lyrics_netease(artist: str, track: str, duration_ms: int = 0) -> Optional[str]:
    """Fallback 1: Search Netease Cloud Music (163) for LRC lyrics."""
    # Build query variants to handle bilingual titles like 'A Love Letter 情书'
    def cjk_only(s):
        return re.sub(r'[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', '', s).strip()
    def ascii_only(s):
        return re.sub(r'[^a-zA-Z0-9 ]', '', s).strip()

    cjk_part   = cjk_only(track)
    ascii_part  = ascii_only(track)
    queries = [f"{artist} {track}", track]
    if cjk_part and cjk_part != track:
        queries.insert(1, f"{artist} {cjk_part}")
        queries.insert(2, cjk_part)
    if ascii_part and ascii_part.lower() != track.lower() and len(ascii_part) >= 3:
        queries.append(ascii_part)

    search_url = "https://music.163.com/api/search/get/web"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://music.163.com/"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for query in queries:
                params = {"s": query, "type": 1, "limit": 8}
                res = await client.get(search_url, params=params, headers=headers)
                data = res.json()
                
                songs = data.get("result", {}).get("songs", [])
                if not songs:
                    continue
                    
                for song in songs:
                    song_name = song.get("name", "")
                    song_dur = song.get("duration", 0)
                    
                    if not _is_valid_match(track, song_name, duration_ms, song_dur):
                        continue

                    song_id = song["id"]
                    lyric_url = "https://music.163.com/api/song/lyric"
                    l_params = {"id": song_id, "lv": 1, "tv": -1, "kv": 1}
                    l_res = await client.get(lyric_url, params=l_params, headers=headers)
                    l_data = l_res.json()
                    
                    lrc = l_data.get("lrc", {}).get("lyric", "")
                    if lrc:
                        logger.info("Netease lyrics found via query '%s' for '%s - %s'", query, artist, track)
                        return lrc
    except Exception as e:
        logger.warning("Netease fallback error for '%s - %s': %s", artist, track, e)
        
    return None

async def search_lyrics_kugou(artist: str, track: str, duration_ms: int = 0) -> Optional[str]:
    """
    Search KuGou for raw LRC text. Works well for tracks not on LRCLIB.
    """
    def cjk_only(s):
        return re.sub(r'[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', '', s).strip()
    def ascii_only(s):
        return re.sub(r'[^a-zA-Z0-9 ]', '', s).strip()

    cjk_part   = cjk_only(track)
    ascii_part  = ascii_only(track)
    queries = [f"{artist} {track}", track]
    if cjk_part and cjk_part != track:
        queries.insert(1, f"{artist} {cjk_part}")
        queries.insert(2, cjk_part)
    if ascii_part and ascii_part.lower() != track.lower() and len(ascii_part) >= 3:
        queries.append(ascii_part)

    search_url = "http://songsearch.kugou.com/song_search_v2"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for query in queries:
                params = {
                    "keyword": query,
                    "page": 1,
                    "pagesize": 5,
                    "platform": "WebFilter"
                }
                # 1. Search for the song
                res = await client.get(search_url, params=params, headers=headers)
                data = res.json()
                songs = data.get("data", {}).get("lists", [])
                if not songs:
                    continue
                for song in songs:
                    song_name = song.get("SongName", "")
                    song_dur = song.get("Duration", 0) * 1000
                    
                    if not _is_valid_match(track, song_name, duration_ms, song_dur):
                        continue
                        
                    filehash = song.get("FileHash")
                    artist_name = song.get("SingerName")
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
                        continue
                        
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
                        logger.info("KuGou lyrics found via query '%s' for '%s - %s'", query, artist, track)
                        return base64.b64decode(lrc_b64).decode('utf-8')
                    
    except Exception as e:
        logger.warning("KuGou fallback error for '%s - %s': %s", artist, track, e)
        
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

async def get_lyrics(artist: str, track: str, duration_ms: int = 0) -> LyricsResponse:
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
        
    # If LRCLIB fails to find the track entirely, OR if it finds an entry but it only has PLAIN lyrics,
    # we heavily prioritize SYNCED lyrics, so we STILL execute the fallbacks to see if Netease has synced!
    if not synced_raw:
        logger.info("Falling back to Netease Cloud Music for '%s' – '%s'", artist, track)
        netease_lrc = await search_lyrics_netease(artist, track, duration_ms)
        if netease_lrc:
            synced_raw = netease_lrc
            source = "netease"
        else:
            logger.info("Falling back to KuGou for '%s' – '%s'", artist, track)
            kugou_lrc = await search_lyrics_kugou(artist, track, duration_ms)
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

    # Only persist to disk if we actually found lyrics — don't cache "not found"
    if synced_lines or plain_raw:
        _set_cache(key, result)
    else:
        _hot_cache[key] = result  # session-only, retried next startup

    return result
