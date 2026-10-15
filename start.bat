@echo off
chcp 65001 >nul
setlocal
set "VENV=%LOCALAPPDATA%\ClipForge\venv"
cd /d "%~dp0"

if not exist "%VENV%\Scripts\python.exe" (
  echo  ClipForge ist noch nicht eingerichtet – starte Installation ...
  call install.bat || exit /b 1
)

if not "%~1"=="" (
  "%VENV%\Scripts\python.exe" -m clipforge run "%~1"
  pause
  exit /b
)

if exist "clipforge\ui\app.py" (
  start "" "%VENV%\Scripts\pythonw.exe" -m clipforge
  exit /b
)

title ClipForge
echo.
set /p "SRC=  Twitch-VOD-Link oder Videodatei (auch per Drag & Drop hierher): "
"%VENV%\Scripts\python.exe" -m clipforge run "%SRC:"=%"
pause
