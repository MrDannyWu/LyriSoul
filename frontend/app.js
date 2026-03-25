/**
 * app.js — Lyrica Frontend
 * ─────────────────────────
 * Sync engine: requestAnimationFrame loop for buttery-smooth lyrics tracking.
 * Highlights the current lyric line, auto-scrolls it to the viewport centre,
 * and shows a per-line progress bar for the active line.
 */

'use strict';

/* ── Config ─────────────────────────────────────────────────────── */
const API_BASE = '';
const POLL_INTERVAL = 4000;   // ms between server polls for track/lyrics data

/* ── State ──────────────────────────────────────────────────────── */
let state = {
  isPlaying: false,
  trackId: null,
  progressMs: 0,
  durationMs: 0,
  lastFetchTime: 0,
  _fetchPerfMark: 0,    // ← initialized so estimateProgressMs() doesn't return NaN
  syncedLyrics: [],   // [{ time_ms, text }] sorted ascending
  plainLyrics: null,
  activeLineIdx: -1,
  pollTimer: null,
  rafId: null,
  isDragging: false,
};
// Debug: print key state every 8s so you can open DevTools Console to diagnose issues
setInterval(() => {
  console.debug('[Lyrica] state:', {
    isPlaying: state.isPlaying,
    progressMs: state.progressMs,
    estimatedMs: estimateProgressMs(),
    activeLineIdx: state.activeLineIdx,
    syncedLyricsCount: state.syncedLyrics.length,
    _fetchPerfMark: state._fetchPerfMark,
  });
}, 8000);

/* ── Toast Notifications ──────────────────────────────────────────── */
function showToast(message, type = 'error') {
  let existing = document.getElementById('lyrica-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'lyrica-toast';
  toast.className = `toast toast-${type}`;
  toast.innerText = message;
  document.body.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add('show'));

  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 400);
  }, 4000);
}

/* ── DOM refs ───────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);

const loginScreen = $('login-screen');
const playerScreen = $('player-screen');
const loginContent = $('login-content');
const configContent = $('config-content');
const cfgClientId = $('cfg-client-id');
const cfgRedirectUri = $('cfg-redirect-uri');
const btnOpenConfig = $('btn-open-config');
const btnCancelConfig = $('btn-cancel-config');
const btnSaveConfig = $('btn-save-config');
const albumArt = $('album-art');
const albumGlow = $('album-glow');
const bgAlbumArt = $('bg-album-art');
const trackNameEl = $('track-name');
const artistNameEl = $('artist-name');
const btnShuffle = $('btn-shuffle');
const btnPrev = $('btn-prev');
const btnPlayPause = $('btn-play-pause');
const iconPlay = $('icon-play');
const iconPause = $('icon-pause');
const btnNext = $('btn-next');
const btnRepeat = $('btn-repeat');
const progressBarFill = $('progress-bar');
const timeCurrent = $('time-current');
const timeTotal = $('time-total');
const lyricsList = $('lyrics-list');
const lyricsIdle = $('lyrics-idle');
const lyricsContainer = $('lyrics-container');
const nothingPlaying = $('nothing-playing');
const userNameEl = $('user-name');
const lyricsSource = $('lyrics-source');

// State flags for optimistic updates
let _isTogglingPlay = false;

/* ── Helpers ────────────────────────────────────────────────────── */
function formatTime(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
function lerp(a, b, t) { return a + (b - a) * t; }

/* ── Real-time progress estimate ────────────────────────────────── */
function estimateProgressMs() {
  if (!state.isPlaying || state.isDragging) return state.progressMs;
  return state.progressMs + (performance.now() - state._fetchPerfMark);
}

/* ── Mood / Background ──────────────────────────────────────────── */
const PALETTES = {
  happy: { a: '#ff6b35', b: '#ffd700', c: '#ff0080' },
  angry: { a: '#ff0040', b: '#cc0000', c: '#ff6600' },
  peaceful: { a: '#00d4ff', b: '#0080ff', c: '#00ff88' },
  melancholic: { a: '#1a1aff', b: '#6600cc', c: '#00ccaa' },
  default: { a: '#1a1aff', b: '#6600cc', c: '#00ccaa' },
};

function applyMood(f) {
  if (!f) return;
  const mood =
    f.valence >= 0.6 && f.energy >= 0.6 ? 'happy' :
      f.valence < 0.4 && f.energy >= 0.6 ? 'angry' :
        f.valence >= 0.6 && f.energy < 0.4 ? 'peaceful' : 'melancholic';

  const p = PALETTES[mood];
  const speed = clamp(lerp(28, 8, f.energy), 8, 30).toFixed(1);
  const root = document.documentElement;
  root.style.setProperty('--mood-a', p.a);
  root.style.setProperty('--mood-b', p.b);
  root.style.setProperty('--mood-c', p.c);
  root.style.setProperty('--blob-speed', `${speed}s`);
  albumGlow.style.background = p.a;
}



/* ── Lyrics rendering ───────────────────────────────────────────── */
function renderSynced(lines) {
  lyricsList.innerHTML = '';
  lyricsIdle.style.display = 'none';
  lines.forEach((line, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'lyric-line';
    wrap.dataset.idx = i;

    const text = document.createElement('span');
    text.className = 'lyric-text';
    text.textContent = line.text;

    wrap.appendChild(text);
    lyricsList.appendChild(wrap);
  });
}

function renderPlain(text) {
  lyricsList.innerHTML = '';
  lyricsIdle.style.display = 'none';
  const el = document.createElement('div');
  el.className = 'plain-lyrics fade-enter';
  el.textContent = text;
  lyricsList.appendChild(el);
}

function renderNoLyrics() {
  lyricsList.innerHTML = '';
  lyricsIdle.style.display = 'flex';
  lyricsIdle.innerHTML = `<div class="idle-icon">📝</div><p data-i18n="no_lyrics">${window.i18n ? window.i18n.t('no_lyrics') : 'Lyrics not found'}</p>`;
}

/* ── Scroll: centre active line in container ─────────────────────── */
let _scrollTarget = null;
let _lastUserScroll = 0;       // timestamp of last user scroll gesture (ms)
const SCROLL_PAUSE_MS = 2000;   // how long to pause auto-scroll after user scrolls

// Hint pill
let _hintEl = null;
function _ensureHint() {
  if (_hintEl) return;
  _hintEl = document.createElement('div');
  _hintEl.setAttribute('data-i18n', 'msg_scroll_hint');
  _hintEl.textContent = window.i18n ? window.i18n.t('msg_scroll_hint') : 'Manual scroll — auto-scroll resumes in 2s';
  _hintEl.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%)'
    + ';background:rgba(0,0,0,.65);color:#fff;font-size:.78rem;padding:6px 16px'
    + ';border-radius:100px;pointer-events:none;opacity:0;transition:opacity .3s'
    + ';z-index:9999;backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.12)';
  document.body.appendChild(_hintEl);
}
function _setHintVisible(v) { _ensureHint(); _hintEl.style.opacity = v ? '1' : '0'; }

// Detect user scroll gestures
if (lyricsContainer) {
  const onUserScroll = () => {
    _lastUserScroll = Date.now();
    _scrollTarget = null; // cancel any in-progress auto-scroll animation
    _setHintVisible(true);
  };
  lyricsContainer.addEventListener('wheel', onUserScroll, { passive: true });
  lyricsContainer.addEventListener('touchstart', onUserScroll, { passive: true });
  lyricsContainer.addEventListener('touchmove', onUserScroll, { passive: true });
}

// Check every 200ms whether we should hide the hint
setInterval(() => {
  if (_lastUserScroll && Date.now() - _lastUserScroll >= SCROLL_PAUSE_MS) {
    _setHintVisible(false);
    // If no auto-scroll pending yet, snap back to the active line now
    if (_scrollTarget === null) {
      const activeEl = lyricsList.querySelector('.lyric-line.active');
      if (activeEl) scrollToLine(activeEl);
    }
    _lastUserScroll = 0; // reset so we only do this once
  }
}, 200);

function _isUserScrolling() {
  return _lastUserScroll > 0 && Date.now() - _lastUserScroll < SCROLL_PAUSE_MS;
}

function scrollToLine(el) {
  if (!el || _isUserScrolling()) return; // don't interfere while user is browsing
  const containerH = lyricsContainer.clientHeight;
  const lineTop = el.offsetTop;
  const lineH = el.offsetHeight;
  _scrollTarget = lineTop - containerH / 2 + lineH / 2;
}

function smoothScrollTick() {
  if (_scrollTarget === null || _isUserScrolling()) return;
  const current = lyricsContainer.scrollTop;
  const dist = _scrollTarget - current;
  if (Math.abs(dist) < 0.5) {
    lyricsContainer.scrollTop = _scrollTarget;
    _scrollTarget = null;
    return;
  }
  lyricsContainer.scrollTop += dist * 0.15; // ease-out
}

/* ── Core rAF sync loop ─────────────────────────────────────────── */
function syncLoop() {
  // Progress bar & time (smooth, every frame)
  const progressMs = estimateProgressMs();
  if (state.durationMs > 0 && !state.isDragging) {
    const pct = clamp(progressMs / state.durationMs * 100, 0, 100);
    progressBarFill.style.width = pct + '%';
    timeCurrent.textContent = formatTime(progressMs);
  }

  // LRC sync
  if (state.syncedLyrics.length) {
    let idx = -1;
    for (let i = 0; i < state.syncedLyrics.length; i++) {
      if (state.syncedLyrics[i].time_ms <= progressMs) idx = i;
      else break;
    }

    if (idx >= 0 && idx !== state.activeLineIdx) {
      // Deactivate old
      const prev = lyricsList.querySelector('.lyric-line.active');
      if (prev) { prev.classList.remove('active'); prev.classList.add('past'); }

      // Mark from 0..idx-1 as past (handle seeking)
      lyricsList.querySelectorAll('.lyric-line').forEach((el, i) => {
        el.classList.remove('active', 'past', 'upcoming');
        if (i < idx) el.classList.add('past');
        else if (i > idx) el.classList.add('upcoming');
      });

      // Activate current
      const activeEl = lyricsList.querySelector(`[data-idx="${idx}"]`);
      if (activeEl) {
        activeEl.classList.add('active');
        scrollToLine(activeEl);
      }
      state.activeLineIdx = idx;
    }
  }

  // Smooth scroll tick
  smoothScrollTick();

  state.rafId = requestAnimationFrame(syncLoop);
}

/* ── Track update ───────────────────────────────────────────────── */
function updateTrack(data) {
  const { track, lyrics, audio_features } = data;
  const trackChanged = track.id !== state.trackId;

  // Always update server-side progress anchor
  const prevProgressMs = state.progressMs;
  state.progressMs = track.progress_ms;
  state.durationMs = track.duration_ms;
  state.lastFetchTime = Date.now();
  state._fetchPerfMark = performance.now();

  // Smart is_playing: trust the API, but if progress advanced, we know music is playing
  // This guards against Spotify's occasional reporting lag (returns is_playing: false while audibly playing)
  const progressAdvanced = !trackChanged && track.progress_ms > prevProgressMs;
  state.isPlaying = track.is_playing || progressAdvanced;

  timeTotal.textContent = formatTime(track.duration_ms);

  if (trackChanged) {
    state.trackId = track.id;
    state.activeLineIdx = -1;
    _scrollTarget = 0;

    // UI: track title & artist
    trackNameEl.textContent = track.name;
    trackNameEl.title = track.name;
    artistNameEl.textContent = track.artists.map(a => a.name).join(', ');

    // Album art cross-fade — route through local cache proxy
    if (track.album.cover_url) {
      const cachedUrl = `/api/cover/${track.id}?url=${encodeURIComponent(track.album.cover_url)}`;
      albumArt.style.opacity = '0';
      if (bgAlbumArt) bgAlbumArt.style.opacity = '0';
      const img = new Image();
      img.onload = () => {
        albumArt.src = cachedUrl;
        if (bgAlbumArt) bgAlbumArt.src = cachedUrl;
        requestAnimationFrame(() => {
          albumArt.style.transition = 'opacity .6s ease';
          albumArt.style.opacity = '1';
          if (bgAlbumArt) bgAlbumArt.style.opacity = '1';
        });
      };
      img.src = cachedUrl;
    }
  }

  // Lyrics: reload when track changes OR when lyrics are missing for the current track
  // (after a pause/resume gap the syncedLyrics array can be empty even though track ID is the same)
  const lyricsAreMissing = state.syncedLyrics.length === 0 && !state.plainLyrics;
  if (trackChanged || lyricsAreMissing) {
    state.syncedLyrics = lyrics.synced || [];
    state.plainLyrics = lyrics.plain || null;
    const srcKey = lyrics.has_synced ? 'lrc_synced' : 'lrc_plain';
    lyricsSource.setAttribute('data-i18n', srcKey);
    lyricsSource.textContent = window.i18n ? window.i18n.t(srcKey) : (lyrics.has_synced ? 'Synced' : 'Plain');

    if (lyrics.has_synced && lyrics.synced.length) {
      renderSynced(lyrics.synced);
      if (trackChanged) state.activeLineIdx = -1; // force re-sync from beginning
    } else if (lyrics.plain) {
      renderPlain(lyrics.plain);
    } else {
      renderNoLyrics();
    }
  }


  // Update Player Controls UI
  if (!_isTogglingPlay) {
    if (track.is_playing) {
      iconPlay.style.display = 'none';
      iconPause.style.display = 'block';
    } else {
      iconPlay.style.display = 'block';
      iconPause.style.display = 'none';
    }
  }

  if (track.shuffle_state) btnShuffle.classList.add('active');
  else btnShuffle.classList.remove('active');

  // Sync repeat state (off | context | track) including single-track indicator
  const rs = track.repeat_state || 'off';
  const num = document.getElementById('repeat-track-num');
  btnRepeat.dataset.state = rs;
  if (rs === 'off') {
    btnRepeat.classList.remove('active');
    if (num) num.style.display = 'none';
  } else if (rs === 'context') {
    btnRepeat.classList.add('active');
    if (num) num.style.display = 'none';
  } else { // track — show icon + SVG '1'
    btnRepeat.classList.add('active');
    if (num) num.style.display = 'inline';
  }
}

/* ── API ────────────────────────────────────────────────────────── */
async function fetchCurrentTrack() {
  try {
    const res = await fetch(`${API_BASE}/api/current-track`, { credentials: 'include' });
    if (res.status === 204) { showNothingPlaying(); return; }
    if (res.status === 401) { showLogin(); return; }
    if (!res.ok) { console.warn('API', res.status); return; }
    hideNothingPlaying();
    const data = await res.json();
    // Debug: log key fields so you can diagnose lyrics/scrolling issues in DevTools Console
    console.debug('[Lyrica] track poll:', {
      id: data.track?.id?.slice(0, 8),
      is_playing: data.track?.is_playing,
      progress_ms: data.track?.progress_ms,
      has_synced: data.lyrics?.has_synced,
      synced_count: data.lyrics?.synced?.length,
    });
    updateTrack(data);
  } catch (err) {
    console.error('Fetch error:', err);
  }
}

async function checkConfigStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/config/status`);
    return await res.json();
  } catch { return { is_configured: false }; }
}

async function saveConfig() {
  const payload = {
    client_id: cfgClientId?.value.trim() || '',
    redirect_uri: cfgRedirectUri?.value.trim() || 'http://127.0.0.1:666/callback',
  };

  if (!payload.client_id) {
    showToast(window.i18n ? window.i18n.t('msg_req_client_id') : 'Please enter your Spotify Developer Client ID', 'error');
    return;
  }

  // Basic validation for typical Spotify Client ID format (32 hex characters)
  if (!/^[a-fA-F0-9]{32}$/.test(payload.client_id)) {
    showToast('错误：无效的 Spotify Client ID！\n这通常是一串由 32 位数字和字母组成的特征码，请确保您没有多复制空格或标点符号。', 'error');
    return;
  }

  const oldTxt = btnSaveConfig.textContent;
  const oldI18n = btnSaveConfig.getAttribute('data-i18n');
  if (oldI18n) {
    btnSaveConfig.setAttribute('data-i18n', 'msg_saving');
  }
  btnSaveConfig.textContent = window.i18n ? window.i18n.t('msg_saving') : 'Saving...';

  try {
    const res = await fetch(`${API_BASE}/api/config/setup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.is_configured) {
      window.location.href = '/auth/login';
    }
  } catch (err) {
    console.error("Config save failed:", err);
    showToast(window.i18n ? window.i18n.t('msg_save_fail') : 'Failed to save configuration.', 'error');
    if (oldI18n) btnSaveConfig.setAttribute('data-i18n', oldI18n);
    else btnSaveConfig.removeAttribute('data-i18n');
    btnSaveConfig.textContent = oldTxt;
  }
}

async function checkAuthStatus() {
  try {
    const res = await fetch(`${API_BASE}/auth/status`, { credentials: 'include' });
    return await res.json();
  } catch { return { logged_in: false }; }
}

/* ── Player Controls API ────────────────────────────────────────── */
async function controlPlayer(action, params = {}) {
  try {
    await fetch(`${API_BASE}/api/player/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      credentials: 'include'
    });
    // Accelerate the next poll for immediate sync
    setTimeout(fetchCurrentTrack, 500);
  } catch (err) {
    console.error(`Player control failed (${action}):`, err);
  }
}

function togglePlayPause() {
  _isTogglingPlay = true;
  if (state.isPlaying) {
    // Optimistic Pause
    state.isPlaying = false;
    iconPause.style.display = 'none';
    iconPlay.style.display = 'block';
    controlPlayer('pause');
  } else {
    // Optimistic Play
    state.isPlaying = true;
    state._fetchPerfMark = performance.now(); // reset anchor
    iconPlay.style.display = 'none';
    iconPause.style.display = 'block';
    controlPlayer('play');
  }
  setTimeout(() => { _isTogglingPlay = false; }, 2000); // release programmatic lock
}

function toggleShuffle() {
  const willBeActive = !btnShuffle.classList.contains('active');
  if (willBeActive) btnShuffle.classList.add('active'); else btnShuffle.classList.remove('active');
  controlPlayer('shuffle', { state: willBeActive });
}

function toggleRepeat() {
  // Cycle: off → context (playlist) → track (single song) → off
  const cur = btnRepeat.dataset.state || 'off';
  const next = cur === 'off' ? 'context' : cur === 'context' ? 'track' : 'off';
  btnRepeat.dataset.state = next;
  const num = document.getElementById('repeat-track-num');
  if (next === 'off') {
    btnRepeat.classList.remove('active');
    if (num) num.style.display = 'none';
  } else if (next === 'context') {
    btnRepeat.classList.add('active');
    if (num) num.style.display = 'none';
  } else { // track — show icon + SVG '1'
    btnRepeat.classList.add('active');
    if (num) num.style.display = 'inline';
  }
  controlPlayer('repeat', { state: next });
}

/* ── Navigation ─────────────────────────────────────────────────── */
function showLogin() {
  stopAll();
  loginScreen.classList.add('active');
  playerScreen.classList.remove('active');
}

function showPlayer(user) {
  loginScreen.classList.remove('active');
  playerScreen.classList.add('active');
  if (user?.display_name) userNameEl.textContent = user.display_name;
  startAll();
}

function showNothingPlaying() { nothingPlaying.classList.remove('hidden'); }
function hideNothingPlaying() { nothingPlaying.classList.add('hidden'); }

/* ── Start / Stop ───────────────────────────────────────────────── */
function startAll() {
  stopAll();
  fetchCurrentTrack();
  state.pollTimer = setInterval(fetchCurrentTrack, POLL_INTERVAL);
  state.rafId = requestAnimationFrame(syncLoop);
}

function stopAll() {
  clearInterval(state.pollTimer);
  cancelAnimationFrame(state.rafId);
}

async function handleLogout(e) {
  if (e) e.preventDefault();
  stopAll();
  try {
    await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
  } catch (err) {
    console.error('Logout failed:', err);
  }
  // Clear the UI and revert to the login screen
  showLogin();
  // Ensure the config panel is hidden and login button is shown
  loginContent.classList.remove('hidden');
  configContent.classList.add('hidden');
}

/* ── Share Card Generator ────────────────────────────────────────── */
const shareModal = $('share-modal');
const shareCanvas = $('share-canvas');
const shareModalClose = $('share-modal-close');
const btnShareCard = $('btn-share-card');
const btnDownloadCard = $('btn-download-card');
const shareLyricPicker = $('share-lyric-picker');
let _shareCardIdx = 0; // currently selected lyric index for the card

function wrapText(ctx, text, x, y, maxWidth, lineHeight, maxLines = Infinity) {
  const hasSpaces = text.includes(' ');
  const tokens = hasSpaces ? text.split(' ') : [...text];
  // Collect all wrapped lines first
  const lines = [];
  let line = '';
  for (const tok of tokens) {
    const sep = hasSpaces ? (line ? ' ' : '') : '';
    const test = line + sep + tok;
    if (ctx.measureText(test).width > maxWidth && line) {
      lines.push(line); line = tok;
    } else { line = test; }
  }
  if (line) lines.push(line);
  // Draw up to maxLines, truncating last with '…' if needed
  const limit = Math.min(lines.length, maxLines);
  let cy = y;
  for (let i = 0; i < limit; i++) {
    let l = lines[i];
    if (i === limit - 1 && limit < lines.length) {
      // Need to truncate — chop chars until it fits with '…'
      while (l.length > 1 && ctx.measureText(l + '…').width > maxWidth) l = l.slice(0, -1);
      l += '…';
    }
    ctx.fillText(l, x, cy);
    cy += lineHeight;
  }
  return cy;
}

function buildLyricPicker(selectedIdx) {
  if (!shareLyricPicker) return;
  shareLyricPicker.innerHTML = '';
  state.syncedLyrics.forEach((l, i) => {
    if (!l.text.trim()) return; // skip blank lines
    const chip = document.createElement('button');
    chip.className = 'lyric-chip' + (i === selectedIdx ? ' selected' : '');
    chip.textContent = l.text;
    chip.title = l.text;
    chip.addEventListener('click', () => {
      _shareCardIdx = i;
      shareLyricPicker.querySelectorAll('.lyric-chip').forEach(c => c.classList.remove('selected'));
      chip.classList.add('selected');
      chip.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      generateShareCard(_shareCardIdx);
    });
    shareLyricPicker.appendChild(chip);
  });
  // Scroll the selected chip into view after DOM is populated
  const sel = shareLyricPicker.querySelector('.selected');
  if (sel) setTimeout(() => sel.scrollIntoView({ inline: 'center', block: 'nearest' }), 80);
}

async function generateShareCard(idx) {
  const W = 1080, H = 1080;
  const RADIUS = 64;
  const cv = shareCanvas;
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');

  const prevLine = idx > 0 ? state.syncedLyrics[idx - 1].text : '';
  const curLine = state.syncedLyrics[idx].text;
  const nextLine = idx < state.syncedLyrics.length - 1 ? state.syncedLyrics[idx + 1].text : '';

  /* ── Rounded clip ───────────────────────────────────────────── */
  ctx.beginPath();
  ctx.roundRect(0, 0, W, H, RADIUS);
  ctx.clip();

  /* ── 1. Background: blurred album art ──────────────────────── */
  const coverUrl = albumArt.src;
  const coverImg = await new Promise(resolve => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = coverUrl;
  });

  if (coverImg) {
    const tmp = document.createElement('canvas');
    tmp.width = W; tmp.height = H;
    const tc = tmp.getContext('2d');
    const sc = Math.max(W / coverImg.width, H / coverImg.height) * 2;
    const dw = coverImg.width * sc, dh = coverImg.height * sc;
    tc.filter = 'blur(120px) saturate(200%) brightness(0.55)';
    tc.drawImage(coverImg, (W - dw) / 2, (H - dh) / 2, dw, dh);
    tc.filter = 'none';
    ctx.drawImage(tmp, 0, 0);
  } else {
    ctx.fillStyle = '#080c14'; ctx.fillRect(0, 0, W, H);
  }

  /* ── 2. Dramatic diagonal gradient overlay ──────────────────── */
  const g1 = ctx.createLinearGradient(0, H, W, 0);
  g1.addColorStop(0, 'rgba(4,8,16,0.85)');
  g1.addColorStop(0.5, 'rgba(4,8,16,0.30)');
  g1.addColorStop(1, 'rgba(4,8,16,0.55)');
  ctx.fillStyle = g1; ctx.fillRect(0, 0, W, H);

  /* ── 3. Decorative giant quotation mark ─────────────────────── */
  ctx.save();
  ctx.font = '900 520px "Inter", serif';
  ctx.fillStyle = 'rgba(255,255,255,0.04)';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText('\u201C', -30, -80); // Unicode left double quotation
  ctx.restore();

  /* ── 4. Left green stripe ───────────────────────────────────── */
  const stripeW = 6;
  const stripeGrad = ctx.createLinearGradient(0, 120, 0, H - 160);
  stripeGrad.addColorStop(0, 'rgba(29,185,84,0)');
  stripeGrad.addColorStop(0.2, 'rgba(29,185,84,0.9)');
  stripeGrad.addColorStop(0.8, 'rgba(29,185,84,0.9)');
  stripeGrad.addColorStop(1, 'rgba(29,185,84,0)');
  ctx.fillStyle = stripeGrad;
  ctx.fillRect(52, 120, stripeW, H - 280);

  /* ── 5. Rotated artist name (left side) ─────────────────────── */
  const artist = artistNameEl.textContent || '';
  ctx.save();
  ctx.translate(34, H / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.font = '600 22px "Inter", sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.35)';
  ctx.textAlign = 'center';
  ctx.letterSpacing = '0.18em';
  ctx.fillText(artist.toUpperCase().slice(0, 28), 0, 0);
  ctx.letterSpacing = '0em';
  ctx.restore();

  /* ── 6. Dot grid decoration (top-right corner) ───────────────── */
  ctx.save();
  const dotCols = 7, dotRows = 5, dotGap = 28, dotR = 2.5;
  const gridX = W - 280, gridY = 80;
  for (let r = 0; r < dotRows; r++) {
    for (let c = 0; c < dotCols; c++) {
      ctx.beginPath();
      ctx.arc(gridX + c * dotGap, gridY + r * dotGap, dotR, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,255,255,0.18)';
      ctx.fill();
    }
  }
  ctx.restore();

  /* ── 7. Circular album art (top right) ──────────────────────── */
  if (coverImg) {
    const cx = W - 165, cy = 280, cr = 110;
    // Glow ring
    ctx.save();
    ctx.shadowColor = 'rgba(29,185,84,0.5)';
    ctx.shadowBlur = 32;
    ctx.beginPath();
    ctx.arc(cx, cy, cr + 4, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(29,185,84,0.6)';
    ctx.lineWidth = 2.5;
    ctx.stroke();
    ctx.restore();
    // Clip circle and draw art
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, cr, 0, Math.PI * 2);
    ctx.clip();
    const s3 = Math.max((cr * 2) / coverImg.width, (cr * 2) / coverImg.height);
    const dw3 = coverImg.width * s3, dh3 = coverImg.height * s3;
    ctx.drawImage(coverImg, cx - cr + (cr * 2 - dw3) / 2, cy - cr + (cr * 2 - dh3) / 2, dw3, dh3);
    ctx.restore();
  }

  /* ── 8. Previous lyric ─────────────────────────────────────── */
  const lyricX = 90, lyricMaxW = W - 340;
  let lyricStartY = 480; // default start when no prevLine
  if (prevLine) {
    ctx.font = '300 36px "Inter", sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.22)';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    const prevEndY = wrapText(ctx, prevLine, lyricX, 440, lyricMaxW, 54);
    lyricStartY = prevEndY + 48; // always at least 48px gap after prev line
  }

  /* ── 9. Current lyric — auto-shrink until text fits ─────────── */
  const barY = H - 120;
  const availH = (barY - 80) - lyricStartY; // space before bottom bar
  const MIN_FZ = 44, STEP = 8;
  let fz = curLine.length > 28 ? 66 : curLine.length > 18 ? 78 : 92;

  // Count wrapped lines without drawing
  function linesNeeded(size) {
    ctx.font = `800 ${size}px "Inter", sans-serif`;
    const hasSpaces = curLine.includes(' ');
    const tokens = hasSpaces ? curLine.split(' ') : [...curLine];
    let line = '', n = 1;
    for (const tok of tokens) {
      const sep = hasSpaces ? (line ? ' ' : '') : '';
      const test = line + sep + tok;
      if (ctx.measureText(test).width > lyricMaxW && line) { n++; line = tok; }
      else { line = test; }
    }
    return n;
  }

  // Shrink font until it fits or floor is hit
  while (fz > MIN_FZ && linesNeeded(fz) * (fz * 1.3) > availH) fz -= STEP;

  const lh = fz * 1.3;
  const maxLines = Math.max(1, Math.floor(availH / lh));

  ctx.font = `800 ${fz}px "Inter", sans-serif`;
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'alphabetic';
  ctx.shadowColor = 'rgba(29,185,84,0.55)';
  ctx.shadowBlur = 40;
  const endY = wrapText(ctx, curLine, lyricX, lyricStartY, lyricMaxW, lh, maxLines);
  ctx.shadowBlur = 0;

  // Green underline accent below current lyric
  ctx.beginPath();
  const ulLen = Math.min(ctx.measureText(curLine.slice(0, 14)).width, lyricMaxW * 0.7);
  ctx.moveTo(lyricX, endY - fz * 0.2);
  ctx.lineTo(lyricX + ulLen, endY - fz * 0.2);
  ctx.strokeStyle = '#1db954';
  ctx.lineWidth = 3.5;
  ctx.lineCap = 'round';
  ctx.stroke();

  /* ── 10. Next lyric ────────────────────────────────────────── */
  if (nextLine) {
    ctx.font = '300 36px "Inter", sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.22)';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    wrapText(ctx, nextLine, lyricX, endY + 28, lyricMaxW, 50);
  }

  /* ── 11. Bottom strip ──────────────────────────────────────── */
  const barH = 120; // barY already declared above as H - 120
  // frosted dark bar
  ctx.fillStyle = 'rgba(0,0,0,0.55)';
  ctx.fillRect(0, barY, W, barH);
  // thin top edge line
  ctx.beginPath();
  ctx.moveTo(0, barY); ctx.lineTo(W, barY);
  ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1; ctx.stroke();

  // small square album thumb
  const thS = 68, thX = 64, thY = barY + (barH - thS) / 2;
  if (coverImg) {
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(thX, thY, thS, thS, 8);
    ctx.clip();
    const s4 = Math.max(thS / coverImg.width, thS / coverImg.height);
    ctx.drawImage(coverImg, thX + (thS - coverImg.width * s4) / 2, thY + (thS - coverImg.height * s4) / 2, coverImg.width * s4, coverImg.height * s4);
    ctx.restore();
  }

  const tx2 = thX + thS + 18;
  const tn = (trackNameEl.textContent || '').slice(0, 26);
  const ar = (artistNameEl.textContent || '').slice(0, 32);
  ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  ctx.font = '700 30px "Inter", sans-serif'; ctx.fillStyle = '#fff';
  ctx.fillText(tn + (trackNameEl.textContent?.length > 26 ? '…' : ''), tx2, barY + 44);
  ctx.font = '400 22px "Inter", sans-serif'; ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.fillText(ar + (artistNameEl.textContent?.length > 32 ? '…' : ''), tx2, barY + 76);

  // Lyrica watermark + small icon
  ctx.textAlign = 'right'; ctx.font = '300 20px "Inter", sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.2)';
  ctx.fillText('Lyrica ♪', W - 56, barY + 60);
}

async function openShareCard() {
  if (!state.syncedLyrics.length) { alert('请先开始播放一首有同步歌词的歌曲'); return; }
  _shareCardIdx = Math.max(0, state.activeLineIdx);
  buildLyricPicker(_shareCardIdx);
  await generateShareCard(_shareCardIdx);
  shareModal.classList.remove('hidden');
}

function closeShareModal() { shareModal.classList.add('hidden'); }

function downloadCard() {
  const link = document.createElement('a');
  const name = (trackNameEl.textContent || 'lyrics').slice(0, 20).replace(/\s+/g, '-');
  link.download = `lyrica-${name}.png`;
  link.href = shareCanvas.toDataURL('image/png');
  link.click();
}


/* ── Init ───────────────────────────────────────────────────────── */
async function init() {
  const cfg = await checkConfigStatus();
  if (cfg.redirect_uri && cfgRedirectUri) cfgRedirectUri.value = cfg.redirect_uri;

  if (!cfg.is_configured) {
    showLogin();
    loginContent.classList.add('hidden');
    configContent.classList.remove('hidden');
    if (btnCancelConfig) btnCancelConfig.style.display = 'none'; // Can't cancel if not configured
  } else {
    const auth = await checkAuthStatus();
    if (auth.logged_in) showPlayer(auth);
    else {
      showLogin();
      // Ensure login panel is visible if configured but not logged in
      loginContent.classList.remove('hidden');
      configContent.classList.add('hidden');
    }
  }

  const logoutBtn = $('logout-btn');
  if (logoutBtn) logoutBtn.addEventListener('click', handleLogout);

  // Config Events
  if (btnOpenConfig) btnOpenConfig.addEventListener('click', () => {
    loginContent.classList.add('hidden');
    configContent.classList.remove('hidden');
    if (btnCancelConfig) btnCancelConfig.style.display = 'inline-flex';
  });
  if (btnCancelConfig) btnCancelConfig.addEventListener('click', () => {
    configContent.classList.add('hidden');
    loginContent.classList.remove('hidden');
  });
  if (btnSaveConfig) btnSaveConfig.addEventListener('click', saveConfig);

  /* ── Desktop PyWebView: Edge Resize + Window Controls ─────────── */
  window.addEventListener('pywebviewready', function () {
    const api = window.pywebview && window.pywebview.api;
    if (!api) return;

    // ── Edge Resize Detection ──
    const B = 6; // border thickness in px
    const cursors = {
      't': 'n-resize', 'b': 's-resize', 'l': 'w-resize', 'r': 'e-resize',
      'tl': 'nw-resize', 'tr': 'ne-resize', 'bl': 'sw-resize', 'br': 'se-resize'
    };
    const htValues = {
      't': 12, 'b': 15, 'l': 10, 'r': 11,
      'tl': 13, 'tr': 14, 'bl': 16, 'br': 17
    };

    function getEdge(e) {
      const w = window.innerWidth, h = window.innerHeight;
      const t = e.clientY < B, b = e.clientY > h - B;
      const l = e.clientX < B, r = e.clientX > w - B;
      if (t && l) return 'tl'; if (t && r) return 'tr';
      if (b && l) return 'bl'; if (b && r) return 'br';
      if (t) return 't'; if (b) return 'b';
      if (l) return 'l'; if (r) return 'r';
      return '';
    }

    document.addEventListener('mousemove', (e) => {
      const edge = getEdge(e);
      document.body.style.cursor = edge ? cursors[edge] : '';
    });

    document.addEventListener('mousedown', (e) => {
      if (e.buttons !== 1) return;
      const edge = getEdge(e);
      if (edge) {
        e.preventDefault();
        e.stopPropagation();
        api.start_resize(htValues[edge]);
      }
    }, true); // ← capture phase: runs BEFORE easy_drag

    // ── Window Control Buttons ──
    const btnMin = document.getElementById('win-min');
    const btnClose = document.getElementById('win-close');
    if (btnMin) {
      btnMin.addEventListener('click', (e) => {
        e.stopPropagation();
        api.minimize_window();
      });
    }
    if (btnClose) {
      btnClose.addEventListener('click', (e) => {
        e.stopPropagation();
        api.close_window();
      });
    }
  });

  // Intercept Login Button
  const loginBtn = $('login-btn');
  if (loginBtn) {
    loginBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      const cfg = await checkConfigStatus();
      if (!cfg.is_configured) {
        showToast(window.i18n ? window.i18n.t('msg_req_client_id') : 'Please configure your Spotify Client ID first!', 'error');
        loginContent.classList.add('hidden');
        configContent.classList.remove('hidden');
      } else {
        window.location.href = '/auth/login';
      }
    });
  }

  // Player Control Events
  if (btnPlayPause) btnPlayPause.addEventListener('click', togglePlayPause);
  if (btnNext) btnNext.addEventListener('click', () => controlPlayer('next'));
  if (btnPrev) btnPrev.addEventListener('click', () => controlPlayer('previous'));
  if (btnShuffle) btnShuffle.addEventListener('click', toggleShuffle);
  if (btnRepeat) btnRepeat.addEventListener('click', toggleRepeat);

  // Lyric interactive seeking
  if (lyricsList) {
    lyricsList.addEventListener('click', (e) => {
      const lineEl = e.target.closest('.lyric-line');
      if (!lineEl || !state.syncedLyrics || !state.syncedLyrics.length) return;

      const idx = parseInt(lineEl.dataset.idx, 10);
      if (isNaN(idx)) return;

      const targetMs = state.syncedLyrics[idx].time_ms;
      if (typeof targetMs === 'number') {
        controlPlayer('seek', { position_ms: targetMs });
        state.progressMs = targetMs;
        state._fetchPerfMark = performance.now();
      }
    });
  }

  // Progress Bar Scrubber
  const pbWrap = document.querySelector('.progress-bar-wrap');
  if (pbWrap) {
    const updateScrub = (e) => {
      if (!state.durationMs) return;
      const rect = pbWrap.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const pct = Math.max(0, Math.min(1, x / rect.width));

      const pb = document.getElementById('progress-bar');
      if (pb) {
        pb.style.transition = 'none'; // Snap instantly to mouse
        pb.style.width = (pct * 100) + '%';
      }

      const targetMs = Math.floor(pct * state.durationMs);
      const timeCurrent = document.getElementById('time-current');
      if (timeCurrent) timeCurrent.textContent = formatTime(targetMs);
      return targetMs;
    };

    pbWrap.addEventListener('mousedown', (e) => {
      state.isDragging = true;
      updateScrub(e);
    });
    document.addEventListener('mousemove', (e) => {
      if (state.isDragging) updateScrub(e);
    });
    document.addEventListener('mouseup', (e) => {
      if (state.isDragging) {
        state.isDragging = false;

        const pb = document.getElementById('progress-bar');
        if (pb) pb.style.transition = 'width 1s linear, background-color 0.2s ease'; // Restore CSS smoothing

        const targetMs = updateScrub(e);
        if (targetMs !== undefined) {
          controlPlayer('seek', { position_ms: targetMs });
          state.progressMs = targetMs;
          state._fetchPerfMark = performance.now();
        }
      }
    });
  }

  // Share card events
  if (btnShareCard) btnShareCard.addEventListener('click', openShareCard);
  if (shareModalClose) shareModalClose.addEventListener('click', closeShareModal);
  if (btnDownloadCard) btnDownloadCard.addEventListener('click', downloadCard);
  // Close on backdrop click
  const backdrop = shareModal?.querySelector('.share-modal-backdrop');
  if (backdrop) backdrop.addEventListener('click', closeShareModal);

  // Apply translations on first paint
  window.i18n?.applyTranslations();
}

/* ── User Avatar Menu ───────────────────────────────────────────── */
async function initUserMenu() {
  const wrap      = document.getElementById('user-menu-wrap');
  const btn       = document.getElementById('user-avatar-btn');
  const imgEl     = document.getElementById('user-avatar-img');
  const initials  = document.getElementById('user-avatar-initials');
  const tooltip   = document.getElementById('user-avatar-tooltip');
  const dropdown  = document.getElementById('user-dropdown');
  const ddImg     = document.getElementById('dd-avatar-img');
  const ddInit    = document.getElementById('dd-avatar-initials');
  const ddName    = document.getElementById('dd-user-name');
  const ddFollowers = document.getElementById('dd-user-followers');

  if (!wrap || !btn) return;

  // Load profile (cached in sessionStorage)
  let profile = null;
  try {
    const cached = sessionStorage.getItem('_lyrica_profile');
    if (cached) {
      profile = JSON.parse(cached);
    } else {
      const res = await fetch('/api/user/profile');
      if (res.ok) {
        profile = await res.json();
        sessionStorage.setItem('_lyrica_profile', JSON.stringify(profile));
      }
    }
  } catch (e) { /* non-critical */ }

  if (profile) {
    const name = profile.name || '?';
    const initial = name.charAt(0).toUpperCase();
    tooltip.textContent = name;

    // Topbar avatar
    if (profile.avatar_url) {
      imgEl.src = profile.avatar_url;
      imgEl.onload = () => {
        imgEl.classList.remove('hidden');
        initials.style.display = 'none';
      };
    } else {
      initials.textContent = initial;
    }

    // Dropdown header
    ddName.textContent = name;
    if (profile.followers !== undefined) {
      ddFollowers.textContent = `${profile.followers.toLocaleString()} 关注者`;
    }
    if (profile.avatar_url) {
      ddImg.src = profile.avatar_url;
      ddImg.onload = () => {
        ddImg.classList.remove('hidden');
        ddInit.style.display = 'none';
      };
    } else {
      ddInit.textContent = initial;
    }
  }

  // Toggle dropdown
  function openMenu() {
    wrap.classList.add('open');
    btn.setAttribute('aria-expanded', 'true');
  }
  function closeMenu() {
    wrap.classList.remove('open');
    btn.setAttribute('aria-expanded', 'false');
  }
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    wrap.classList.contains('open') ? closeMenu() : openMenu();
  });
  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
  });

  // Menu actions
  document.getElementById('dd-lang')?.addEventListener('click', () => {
    closeMenu();
    window.i18n?.toggle();
  });

  document.getElementById('dd-profile')?.addEventListener('click', () => {
    closeMenu();
    if (profile?.profile_url) window.open(profile.profile_url, '_blank');
  });


  document.getElementById('dd-about')?.addEventListener('click', () => {
    closeMenu();
    showToast('Lyrica v1.0 — Spotify Lyrics Player ✨', 3000);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  init();
  // Init avatar menu only on player screen (after first login it's visible)
  initUserMenu();
});

