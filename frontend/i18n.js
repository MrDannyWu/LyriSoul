/**
 * i18n.js — Pure Frontend Internationalization for Lyrica
 * ─────────────────────────────────────────────────────────
 * Usage:
 *   window.i18n.t('key')           → get current translation
 *   window.i18n.setLang('en'|'zh') → switch language & update DOM
 *   window.i18n.toggle()           → flip between en/zh
 *
 * In HTML, mark translatable elements with: data-i18n="key"
 * Elements that contain only text use textContent replacement.
 * Elements with mixed content (SVG + text) should wrap
 * their text in a child <span data-i18n="key">.
 */

'use strict';

const _translations = {
  en: {
    /* ── Login ───────────────── */
    login_sub:        'Synchronize lyrics in real-time, feel the rhythm',
    btn_login_spotify:'Login with Spotify',
    login_hint:       'Requires a Spotify Premium or Free account',
    btn_open_config:  '⚙️ Config',

    /* ── Configuration form ──── */
    config_title:     'App Configuration',
    config_sub:       'Enter your Spotify Developer credentials to get started.',
    lbl_client_id:    'Client ID',
    lbl_client_secret:'Client Secret',
    lbl_redirect_uri: 'Redirect URI',
    btn_cancel:       'Cancel',
    btn_save_login:   'Save & Login',

    /* ── Player topbar / User menu ─ */
    btn_logout:       'Logout',
    menu_profile:     'Profile',
    menu_lang:        '中 / EN',
    menu_shortcuts:   'Keyboard Shortcuts',
    menu_about:       'About Lyrica',

    /* ── Lyrics panel ────────── */
    title_lyrics:     'Lyrics',
    idle_waiting:     'Waiting for music to play...',
    no_lyrics:        'No lyrics found for this track',
    lrc_synced:       'Synced',
    lrc_plain:        'Plain',

    /* ── Nothing-playing overlay */
    np_title:         'Nothing is playing right now',
    np_hint:          'Open Spotify and start playing',

    /* ── Player controls ──────── */
    btn_shuffle_title: 'Shuffle',
    btn_prev_title:    'Previous',
    btn_play_pause_title: 'Play/Pause',
    btn_next_title:    'Next',
    btn_repeat_title:  'Repeat',
    btn_open_config_title: 'Configure API',
    btn_lang_toggle_title: 'Switch Language',
    cfg_client_id_placeholder: 'e.g. 1a2b3c4d5e...',

    /* ── Share modal ─────────── */
    share_title:      'Share Lyrics Card',
    share_btn_dl:     'Download Card',
    btn_share_tooltip:'Share Lyrics Card',

    /* ── Dynamic JS strings ──── */
    msg_scroll_hint:  'Manual scroll \u2014 auto-scrolls back in 2s',
    msg_req_client_id:'Please fill in Client ID',
    msg_saving:       'Saving\u2026',
    msg_save_fail:    'Failed to save configuration.',
  },

  zh: {
    /* ── Login ───────────────── */
    login_sub:        '实时同步歌词，跟随音乐律动',
    btn_login_spotify:'使用 Spotify 登录',
    login_hint:       '需要 Spotify Premium / Free 账号',
    btn_open_config:  '⚙️ 配置',

    /* ── Configuration form ──── */
    config_title:     'App Configuration',
    config_sub:       '请填写 Spotify 开发者凭证以完成初始化配置。',
    lbl_client_id:    'Client ID',
    lbl_client_secret:'Client Secret',
    lbl_redirect_uri: 'Redirect URI',
    btn_cancel:       '取消',
    btn_save_login:   '保存并登录',

    /* ── Player topbar / User menu ─ */
    btn_logout:       '退出账号',
    menu_profile:     '用户资料',
    menu_lang:        '中 / EN',
    menu_shortcuts:   '键盘快捷键',
    menu_about:       '关于 Lyrica',

    /* ── Lyrics panel ────────── */
    title_lyrics:     '歌 词',
    idle_waiting:     '正在等待音乐播放…',
    no_lyrics:        '未找到该歌曲的歌词',
    lrc_synced:       '同步歌词',
    lrc_plain:        '文本歌词',

    /* ── Nothing-playing overlay */
    np_title:         '当前没有音乐播放',
    np_hint:          '打开 Spotify 开始播放吧',

    /* ── Player controls ──────── */
    btn_shuffle_title: '随机播放',
    btn_prev_title:    '上一首',
    btn_play_pause_title: '播放 / 暂停',
    btn_next_title:    '下一首',
    btn_repeat_title:  '循环模式',
    btn_open_config_title: '配置 API',
    btn_lang_toggle_title: '切换语言',
    cfg_client_id_placeholder: '例如: 1a2b3c4d5e...',

    /* ── Share modal ─────────── */
    share_title:      '分享歌词卡片',
    share_btn_dl:     '下载卡片',
    btn_share_tooltip:'分享歌词卡片',

    /* ── Dynamic JS strings ──── */
    msg_scroll_hint:  '手动浏览中，2 秒后自动回到当前歌词',
    msg_req_client_id:'请填写 Client ID',
    msg_saving:       '保存中…',
    msg_save_fail:    '无法保存配置，请稍后再试。',
  }
};

// ─── State ────────────────────────────────────────────────
let _lang = localStorage.getItem('lyrica_lang') ||
            (navigator.language.startsWith('zh') ? 'zh' : 'en');

// ─── Core helpers ─────────────────────────────────────────
function t(key) {
  const dict = _translations[_lang] || _translations.en;
  return Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : key;
}

// ─── DOM update ───────────────────────────────────────────
function applyTranslations() {
  // 1. Handle all elements with data-i18n=""
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const val = t(key);
    // Safely update: if element has only text nodes (no child elements), use textContent.
    const hasChildElements = [...el.childNodes].some(n => n.nodeType === Node.ELEMENT_NODE);
    if (!hasChildElements) {
      el.textContent = val;
    } else {
      // Has mixed content (e.g. SVG + text) — update only text nodes
      el.childNodes.forEach(n => {
        if (n.nodeType === Node.TEXT_NODE && n.nodeValue.trim().length > 0) {
          n.nodeValue = ' ' + val + ' ';
        }
      });
    }
  });

  // 2. Update placeholders and titles
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.getAttribute('data-i18n-title'));
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
  });

  // 3. Update the toggle button label: show the OTHER language as the label
  const toggleBtn = document.getElementById('lang-toggle-btn');
  if (toggleBtn) {
    toggleBtn.textContent = _lang === 'zh' ? 'EN' : '中';
    toggleBtn.title = t('btn_lang_toggle_title');
    toggleBtn.setAttribute('aria-label', toggleBtn.title);
  }

  // 4. Update <html lang=""> attribute for accessibility
  document.documentElement.lang = _lang === 'zh' ? 'zh-CN' : 'en';
}

// ─── Set language and persist ─────────────────────────────
function setLang(lang) {
  if (!_translations[lang]) return;
  _lang = lang;
  localStorage.setItem('lyrica_lang', lang);
  applyTranslations();
}

// ─── Toggle ───────────────────────────────────────────────
function toggle() {
  setLang(_lang === 'zh' ? 'en' : 'zh');
}

// ─── Public API ───────────────────────────────────────────
window.i18n = {
  t,
  setLang,
  toggle,
  getCurrent: () => _lang,
  applyTranslations, // expose so app.js can call after DOMContentLoaded
};
