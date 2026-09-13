@echo off
setlocal
title DVDRewind - NAS Catalog Monitor
echo ===================================================
echo   DVDRewind NAS Catalog Monitor
echo ===================================================
echo Connecting to the live DVDRewind archive on 192.168.50.39...
echo Press Ctrl+C to close the monitor. The NAS sync keeps running.
echo.
ssh -t deadman36g@192.168.50.39 "docker exec -it dvdrewind python populate_all.py --watch"
echo.
echo Monitor closed with code %ERRORLEVEL%.
pause
