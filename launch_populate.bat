@echo off
title DVDRewind - Catalog Population Engine
cd /d "C:\Users\deadman36g\.gemini\antigravity\scratch\dvdrewind"
echo ===================================================
echo   DVDRewind Catalog Population & Ingestion Engine
echo ===================================================
echo Starting live dashboard...
python populate_all.py
echo.
echo Process exited with code %ERRORLEVEL%.
pause
