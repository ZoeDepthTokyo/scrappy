from pathlib import Path
import os
import sys

APP_NAME = "Scrappy"

def data_dir() -> Path:
    override = os.getenv("SCRAPPY_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        # Windows provides the per-user application data location through its API.
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.shell32.SHGetFolderPathW(None, 0x1c, None, 0, buf):
            path = Path(buf.value) / APP_NAME
        else:
            path = Path.home() / "AppData" / "Local" / APP_NAME
    else:
        path = Path.home() / ".scrappy"
    return path

def db_path() -> Path:
    return data_dir() / "scrappy.db"

def reports_dir() -> Path:
    path = data_dir() / "reports"
    return path

def user_config_path() -> Path:
    return data_dir() / "config.json"

def bundled_path(name: str) -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return root / name
