@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Install TRELLIS Studio before adding Pixal3D.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -u pixal3d_setup.py %*
set "pixal3d_exit=%errorlevel%"
pause
exit /b %pixal3d_exit%
