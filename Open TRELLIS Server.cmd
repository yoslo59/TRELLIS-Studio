@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Server.ps1" %*
set "trellis_server_exit=%errorlevel%"
if not "%trellis_server_exit%"=="0" (
    echo TRELLIS Server could not start. See the message above for details.
    pause
)
exit /b %trellis_server_exit%
