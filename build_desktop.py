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
import webbrowser

def start_server(port):
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")

def apply_window_tweaks(window):
    """Only apply DWM cosmetics — no window style changes, no subclassing."""
    time.sleep(0.5)
    hwnd = ctypes.windll.user32.FindWindowW(None, "LyriSoul")
    if not hwnd:
        return
    dwmapi = ctypes.windll.dwmapi
    # DWM dark mode
    val = ctypes.c_int(1)
    try:
        dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass
    # DWM rounded corners (Win11)
    corner = ctypes.c_int(2)
    try:
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    except Exception:
        pass

class NativeApi:
    def __init__(self):
        self._window = None
        self.lang = 'zh'
        self.tray_icon = None

    def set_language(self, lang):
        self.lang = lang

    def set_window(self, w):
        self._window = w

    def minimize_window(self):
        if self._window:
            self._window.minimize()

    def close_window(self):
        if self._window:
            self._window.destroy()
            os._exit(0)

    def resize_window(self, width, height):
        if self._window:
            self._window.resize(int(width), int(height))

    def get_window_size(self):
        if self._window:
            return {'width': self._window.width, 'height': self._window.height}
        return {'width': 1280, 'height': 800}

    def open_external_auth(self):
        """Open the system browser to the auth login endpoint."""
        webbrowser.open("http://127.0.0.1:666/auth/login")

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
        easy_drag=True,
        resizable=True,
        js_api=api
    )
    api.set_window(window)
    
    # Initialize system tray exactly before pushing UI loop
    setup_tray(api)
    
    webview.start(private_mode=False, func=apply_window_tweaks, args=[window])
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
