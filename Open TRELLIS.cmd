@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Studio.ps1" %*
set "studio_exit=%errorlevel%"
if not "%studio_exit%"=="0" (
    echo TRELLIS Studio could not start. See the message above and "%~dp0logs".
    pause
)
exit /b %studio_exit%
