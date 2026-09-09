"""
DVDRewind Web Server - Persistent runner with auto-restart.
Run this as a background process or via Task Scheduler.
It will keep the aiohttp server alive and restart it if it crashes.
"""
import subprocess
import sys
import time
import os

PROJECT_ROOT = r"C:\Users\deadman36g\.gemini\antigravity\scratch\dvdrewind"
LOG_FILE = os.path.join(PROJECT_ROOT, "server.log")

def run_server():
    while True:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting DVDRewind server on http://127.0.0.1:8088 ...", flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log:
                log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Server starting...\n")
                proc = subprocess.Popen(
                    [sys.executable, "-m", "src.cli", "serve", "--host", "127.0.0.1", "--port", "8088"],
                    cwd=PROJECT_ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                proc.wait()
                log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Server exited with code {proc.returncode}\n")
        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error: {e}", flush=True)
        
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Server stopped. Restarting in 3 seconds...", flush=True)
        time.sleep(3)

if __name__ == "__main__":
    run_server()
