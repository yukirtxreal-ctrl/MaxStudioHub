@echo off
rem Run Max Studio Hub as a native window directly from source (no browser).
rem Tip: the polished installed app is the "Max Studio Hub" icon on your Desktop.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0Start.ps1"
