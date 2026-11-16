@echo off
rem Double-click launcher for the Pochven filament router web UI.
cd /d "%~dp0"
python web.py %*
if errorlevel 1 pause
