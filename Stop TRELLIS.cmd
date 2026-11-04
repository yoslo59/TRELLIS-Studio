@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Arreter-TRELLIS.ps1"
if errorlevel 1 pause
