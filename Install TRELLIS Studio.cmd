@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Studio.ps1" %*
set "studio_exit=%errorlevel%"
if not "%studio_exit%"=="0" echo Installation stopped. Read the message above and INSTALLATION.md.
pause
exit /b %studio_exit%
