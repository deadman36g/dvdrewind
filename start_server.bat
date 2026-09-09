@echo off
REM DVDRewind Web Server - Auto-start script
REM This runs the DVDRewind web server on http://127.0.0.1:8088
REM Registered via Windows Task Scheduler to start on user login

cd /d "C:\Users\deadman36g\.gemini\antigravity\scratch\dvdrewind"
python -m src.cli serve --host 127.0.0.1 --port 8088
