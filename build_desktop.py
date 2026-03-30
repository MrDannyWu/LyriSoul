import os
import sys
import subprocess
import shutil
import time

def install_dependencies():
    print("🚀 [1/2] Installing Desktop UI dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "pywebview", "fastapi", "uvicorn", "spotipy", "pystray", "pillow"])
    print("✅ Python dependencies installed!")

def build_desktop_app():
    print("🚀 [2/2] Compiling LyriSoul Desktop Executable (This may take a minute)...")
    
    with open("run_app.py", "w", encoding="utf-8") as f:
        f.write('''
import uvicorn
from main import app
import threading
import webview
import time
import urllib.request
import sys
import os
import ctypes
import ctypes.wintypes

def start_server(port):
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")

def make_frameless_with_resize(window):
    time.sleep(0.5)
    hwnd = ctypes.windll.user32.FindWindowW(None, "LyriSoul")
    if not hwnd:
        return

    # ONLY apply DWM Cosmetics (Dark Mode and Rounded Corners)
    # NO WS_THICKFRAME Hacks! This prevents the black top bar natively.
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMWA_WINDOW_CORNER_PREFERENCE = 33
    is_true = ctypes.c_int(1)
    corner_pref = ctypes.c_int(2)
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(is_true), ctypes.sizeof(is_true))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner_pref), ctypes.sizeof(corner_pref))
    except Exception: pass


class NativeApi:
    def __init__(self):
        self._window = None
        self.lang = 'zh'
        self.tray_icon = None
        
    def set_language(self, lang):
        self.lang = lang
        # System tray menu is now strictly bound to Windows OS language natively.

    def set_window(self, w):
        self._window = w

    def minimize_window(self):
        if self._window:
            self._window.minimize()

    def close_window(self):
        if self._window:
            self._window.destroy()
            os._exit(0)


    def _get_hwnd(self):
        if hasattr(self, '_hwnd_cache') and self._hwnd_cache and ctypes.windll.user32.IsWindow(self._hwnd_cache):
            return self._hwnd_cache
            
        hwnd = getattr(self._window, 'native', None)
        if isinstance(hwnd, int) and ctypes.windll.user32.IsWindow(hwnd):
            self._hwnd_cache = hwnd
            return hwnd

        def callback(handle, _):
            nonlocal hwnd
            length = ctypes.windll.user32.GetWindowTextLengthW(handle)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(handle, buff, length + 1)
                if "LyriSoul" in buff.value:
                    hwnd = handle
                    return False
            return True
        
        ctypes.windll.user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback), 0)
        self._hwnd_cache = hwnd
        return hwnd

    def start_window_drag(self):
        hwnd = self._get_hwnd()
        if hwnd:
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0xA1, 2, 0)

    def resize_window(self, width, height):
        if self._window:
            self._window.resize(int(width), int(height))

    def open_external_auth(self):
        import webbrowser
        webbrowser.open("http://127.0.0.1:666/auth/login?desktop=1")


    def start_resize(self, edge):
        hwnd = self._get_hwnd()
        if hwnd:
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0xA1, int(edge), 0)


def setup_tray(api):
    try:
        import pystray
        from PIL import Image
        import threading
        import sys
        import os
        
        # Resolve PyInstaller extracted path vs dev script path
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        icon_path = os.path.join(base_dir, "frontend", "icon.ico")
        image = Image.open(icon_path)
        
        def on_show(icon, item):
            if api._window:
                api._window.restore()
                api._window.show()

        def on_quit(icon, item):
            icon.stop()
            api.close_window()
            
        # Hard-Poll Windows Kernel directly for OS Language (0x04 = Chinese)
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        is_zh = (lang_id & 0x00FF) == 0x04
        
        wake_text = '唤醒 LyriSoul窗体' if is_zh else 'Show LyriSoul Window'
        quit_text = '关闭 LyriSoul' if is_zh else 'Quit LyriSoul'
        
        menu = pystray.Menu(
            pystray.MenuItem(wake_text, on_show),
            pystray.MenuItem(quit_text, on_quit)
        )
        icon = pystray.Icon("LyriSoul", image, "LyriSoul", menu)
        api.tray_icon = icon

        # We must abandon the DarkMode Daemon: pystray's hidden #32768 shadow window class
        # inherently blocks DWM immersiveness in secondary Win32 threads without C++ Owner-Drawn rendering.
        
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception as e:
        print("Tray initialization failed:", e)

if __name__ == "__main__":
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = os.path.join(data_dir, "browser_profile")

    port = 666
    t = threading.Thread(target=start_server, args=(port,))
    t.daemon = True
    t.start()

    url = f"http://127.0.0.1:{port}/"
    while True:
        try:
            urllib.request.urlopen(url)
            break
        except Exception:
            time.sleep(0.1)

    api = NativeApi()
    window = webview.create_window(
        "LyriSoul",
        url,
        width=1280,
        height=800,
        frameless=True,
        transparent=True,
        js_api=api
    )
    api.set_window(window)
    
    # Initialize system tray exactly before pushing UI loop
    setup_tray(api)
    
    webview.start(private_mode=False, func=make_frameless_with_resize, args=[window])
'''.strip())

    try:
        subprocess.call(["taskkill", "/F", "/IM", "LyriSoul.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
    except Exception:
        pass

    subprocess.check_call([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "LyriSoul",
        "--icon", "frontend/icon.ico",
        "--add-data", "frontend;frontend",
        "--hidden-import", "spotipy",
        "--hidden-import", "fastapi",
        "--hidden-import", "webview",
        "--hidden-import", "pystray",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL",
        "run_app.py"
    ])
    
    if os.path.exists("run_app.py"):
        os.remove("run_app.py")
        
    print("\\n🎉 SUCCESS! Desktop App Compiled.")
    print("Your double-clickable Windows App is ready at: /dist/LyriSoul.exe")

if __name__ == "__main__":
    print("=======================================")
    print(" LyriSoul Desktop (PyWebView) Builder")
    print("=======================================")
    install_dependencies()
    build_desktop_app()
