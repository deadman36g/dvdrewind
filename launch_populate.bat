@echo off
setlocal
title DVDRewind - NAS Catalog Monitor
echo ===================================================
echo   DVDRewind NAS Catalog Monitor
echo ===================================================
if not defined DVDREWIND_NAS set DVDREWIND_NAS=nas
echo Connecting to the live DVDRewind archive on %DVDREWIND_NAS%...
echo Press Ctrl+C to close the monitor. The NAS sync keeps running.
echo.
ssh -t %DVDREWIND_NAS% "docker exec -it dvdrewind python populate_all.py --watch"
echo.
echo Monitor closed with code %ERRORLEVEL%.
pause
