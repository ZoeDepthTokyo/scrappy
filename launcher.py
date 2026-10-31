"""Desktop entry point for the local Scrappy server."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import webbrowser

import httpx
from werkzeug.serving import make_server

import ai_providers
import paths
from config_loader import load_config

VERSION = "0.5.0"


def health(port):
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5, trust_env=False)
        return response.status_code == 200 and response.json().get("app") == "scrappy"
    except (httpx.HTTPError, ValueError):
        return False


def main():
    if getattr(sys, "frozen", False) and sys.stdout is None and os.name == "nt":
        import ctypes
        import msvcrt
        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.restype = ctypes.c_void_p
        handle = kernel.GetStdHandle(-11)
        if not handle or handle == ctypes.c_void_p(-1).value:
            if kernel.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        else:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_TEXT)
            sys.stdout = os.fdopen(fd, "w", encoding="utf-8", buffering=1, closefd=False)
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--import-db")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.run:
        import run
        run.main(start_server=False)
        return
    port = int(os.getenv("SCRAPPY_PORT", "5055"))
    running = health(port)
    runtime_file = paths.data_dir() / "runtime.json"
    if not running and runtime_file.exists():
        try:
            recorded = int(json.loads(runtime_file.read_text(encoding="utf-8"))["port"])
            if health(recorded):
                port, running = recorded, True
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if args.doctor:
        provider = load_config().get("ai", {}).get("provider", "none")
        print(json.dumps({"version": VERSION, "data_dir": str(paths.data_dir()), "db_exists": paths.db_path().exists(),
                          "port": port, "server_running": running, "ai_provider": provider,
                          "ai_connected": ai_providers.connected(provider),
                          "firecrawl_configured": bool(ai_providers.credential("firecrawl"))}))
        return
    if args.import_db:
        if running or paths.db_path().exists():
            parser.error("Import requires a stopped app and an empty data directory")
        paths.data_dir().mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(Path(args.import_db).resolve().as_uri() + "?mode=ro", uri=True) as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                parser.error("Database integrity check failed")
            with sqlite3.connect(paths.db_path()) as target:
                source.backup(target)
        print(json.dumps({"ok": True, "imported": True}))
        return
    if not running:
        import feedback_server
        paths.data_dir().mkdir(parents=True, exist_ok=True)
        import db
        db.connect(str(paths.db_path())).close()
        while port <= 65535:
            if health(port):
                running = True
                break
            try:
                server = make_server("127.0.0.1", port, feedback_server.app)
                break
            except (OSError, SystemExit):
                port += 1
        else:
            raise RuntimeError("No available port")
        if not running:
            feedback_server._SERVER = server
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            url = f"http://127.0.0.1:{port}/favorites"
            runtime_file.write_text(json.dumps({"port": port, "pid": os.getpid(), "url": url}), encoding="utf-8")
            if sys.stdout:
                print(f"Scrappy running at {url}", flush=True)
            for _ in range(50):
                if health(port):
                    break
                # Quit can arrive before the initial health check completes.
                if not server_thread.is_alive():
                    break
                time.sleep(0.1)
            else:
                server.shutdown()
                server.server_close()
                raise RuntimeError("Server did not start")
    if not args.no_browser and not os.getenv("SCRAPPY_NO_BROWSER"):
        webbrowser.open(f"http://127.0.0.1:{port}/favorites")
    if not running:
        try:
            server_thread.join()
        except KeyboardInterrupt:
            server.shutdown()
        finally:
            server.server_close()
            try:
                runtime_file.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
