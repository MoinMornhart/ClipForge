@echo off
chcp 65001 >nul
setlocal
title ClipForge – Installation
set "VENV=%LOCALAPPDATA%\ClipForge\venv"
cd /d "%~dp0"

echo.
echo  ClipForge wird eingerichtet ...
echo  Python-Umgebung: %VENV%
echo  (liegt bewusst außerhalb von iCloud, damit nichts synchronisiert wird)
echo.

where py >nul 2>nul && (set "PY=py -3.11") || (set "PY=python")
%PY% --version >nul 2>nul || (
  echo  [FEHLER] Python 3.11 wurde nicht gefunden.
  echo  Installiere es mit:  winget install Python.Python.3.11
  pause & exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
  echo  [1/4] Lege Python-Umgebung an ...
  %PY% -m venv "%VENV%" || (echo  [FEHLER] venv konnte nicht angelegt werden. & pause & exit /b 1)
)

echo  [2/4] Installiere Pakete (beim ersten Mal einige Minuten) ...
"%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt || (echo  [FEHLER] Paketinstallation fehlgeschlagen. Details oben. & pause & exit /b 1)

echo  [3/4] Installiere CUDA-Bibliotheken für die GPU-Transkription ...
"%VENV%\Scripts\python.exe" -m pip install -q nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" || echo  [HINWEIS] CUDA-Bibliotheken fehlen – Transkription läuft dann auf der CPU.

echo  [4/4] Prüfe FFmpeg ...
where ffmpeg >nul 2>nul || if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" (
  echo  FFmpeg fehlt, installiere über winget ...
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
)

echo.
"%VENV%\Scripts\python.exe" -m clipforge check
echo.
echo  Fertig. Starte ClipForge mit start.bat
pause
