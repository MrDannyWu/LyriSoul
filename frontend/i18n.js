/**
 * i18n.js - Pure Frontend Translations
 */

const translations = {
  en: {
    // Login Screen
    "login_sub": "Synchronize lyrics in real-time, follow the rhythm",
    "btn_login_spotify": "Login with Spotify",
    "login_hint": "Requires Spotify Premium / Free account",
    "btn_open_config": "⚙️ Config",
    
    // Config View
    "config_title": "App Configuration",
    "config_sub": "Please enter your Spotify Developer credentials to connect to the API.",
    "lbl_client_id": "Client ID",
    "lbl_client_secret": "Client Secret",
    "lbl_redirect_uri": "Redirect URI",
    "btn_cancel": "Cancel",
    "btn_save_login": "Save & Login",
    
    // Player Topbar
    "btn_logout": "Logout",
    
    // Main UI
    "title_lyrics": "Lyrics",
    "btn_share_tooltip": "Share Lyrics Card",
    "idle_waiting": "Waiting for music to play...",
    
    // Nothing Playing
    "np_title": "Nothing is playing right now",
    "np_hint": "Open Spotify and start playing",
    
    // Share Modal
    "share_title": "Share Lyrics Card",
    "share_btn_dl": "Download Card",
    
    // JS Dynamic Messages
    "msg_scroll_hint": "Manual browsing, auto-scroll resumes in 2s",
    "msg_req_client_id": "Please enter Client ID and Secret",
    "msg_saving": "Saving...",
    "msg_save_fail": "Failed to save configuration.",
  },
  
  zh: {
    // Login Screen
    "login_sub": "实时同步歌词，跟随音乐律动",
    "btn_login_spotify": "使用 Spotify 登录",
    "login_hint": "需要 Spotify Premium / Free 账号",
    "btn_open_config": "⚙️ 配置",
    
    // Config View
    "config_title": "App Configuration",
    "config_sub": "Please enter your Spotify Developer credentials to connect to the API.",
    "lbl_client_id": "Client ID",
    "lbl_client_secret": "Client Secret",
    "lbl_redirect_uri": "Redirect URI",
    "btn_cancel": "取消",
    "btn_save_login": "保存并登录",
    
    // Player Topbar
    "btn_logout": "退出",
    
    // Main UI
    "title_lyrics": "歌 词",
    "btn_share_tooltip": "分享歌词卡片",
    "idle_waiting": "正在等待音乐播放…",
    
    // Nothing Playing
    "np_title": "当前没有音乐播放",
    "np_hint": "打开 Spotify 开始播放吧",
    
    // Share Modal
    "share_title": "分享歌词卡片",
    "share_btn_dl": "下载卡片",
    
    // JS Dynamic Messages
    "msg_scroll_hint": "手动浏览中，2 秒后自动回到当前歌词",
    "msg_req_client_id": "请在此输入 Client ID 和 Client Secret",
    "msg_saving": "保存中...",
    "msg_save_fail": "无法保存配置",
  }
};

// State
let currentLang = localStorage.getItem('lyrica_lang') || 
                  (navigator.language.startsWith('zh') ? 'zh' : 'en');

// Helper to get translated string
function t(key) {
  return translations[currentLang][key] || key;
}

// Function to update the DOM
function updateDOMTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.childNodes.forEach(node => {
      // Find the text node directly if there are mixed elements (like SVG + text)
      if (node.nodeType === Node.TEXT_NODE && node.nodeValue.trim().length > 0) {
        node.nodeValue = " " + t(key) + " ";
      }
    });

    // Fallback for purely text elements without multiple mixed tags
    if (el.childNodes.length === 0 || (el.childNodes.length === 1 && el.childNodes[0].nodeType === Node.TEXT_NODE)) {
      el.textContent = t(key);
    }
  });
  
  // Custom manual mappings where `data-i18n` is tricky to use gracefully due to icons
  const loginBtnTextNode = document.querySelector('#login-btn')?.lastChild;
  if(loginBtnTextNode && loginBtnTextNode.nodeType === Node.TEXT_NODE) loginBtnTextNode.nodeValue = " " + t('btn_login_spotify');

  const configBtnTextNode = document.querySelector('#btn-open-config')?.lastChild;
  if(configBtnTextNode && configBtnTextNode.nodeType === Node.TEXT_NODE) configBtnTextNode.nodeValue = " " + t('btn_open_config');

  const dlBtnTextNode = document.querySelector('#btn-download-card')?.lastChild;
  if(dlBtnTextNode && dlBtnTextNode.nodeType === Node.TEXT_NODE) dlBtnTextNode.nodeValue = " " + t('share_btn_dl');
  
  // Tooltips
  const shareBtn = document.getElementById('btn-share-card');
  if (shareBtn) shareBtn.title = t('btn_share_tooltip');
}

// Function to set and save language
function setLanguage(lang) {
  if (!translations[lang]) return;
  currentLang = lang;
  localStorage.setItem('lyrica_lang', lang);
  updateDOMTranslations();
  
  // Update toggle button UI
  const toggleBtn = document.getElementById('lang-toggle-btn');
  if (toggleBtn) {
    toggleBtn.textContent = currentLang === 'zh' ? 'EN' : '中';
  }
}

// Export mapping to window
window.i18n = {
  t,
  setLanguage,
  getCurrent: () => currentLang,
  toggle: () => setLanguage(currentLang === 'zh' ? 'en' : 'zh')
};
