@echo off
chcp 65001 >nul
setlocal
title ClipForge – Einrichtung
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
set "PIPOPT=--no-warn-script-location --disable-pip-version-check -q"

echo.
echo   ClipForge wird eingerichtet (einmalig).
echo   Es werden ca. 3 GB an Bibliotheken geladen. Je nach Leitung 5 bis 20 Minuten.
echo   Dieses Fenster schließt sich am Ende von selbst.
echo.

del /q "runtime\.ready" 2>nul

echo   [1/4] Paketverwaltung ...
"%PY%" -m pip --version >nul 2>nul || "%PY%" "runtime\get-pip.py" %PIPOPT% || goto fail

echo   [2/4] ClipForge-Bibliotheken (Spracherkennung, Video, KI, Oberfläche) ...
"%PY%" -m pip install %PIPOPT% -r requirements.txt || goto fail

echo   [3/4] Grafikkarten-Beschleunigung (CUDA) ...
"%PY%" -m pip install %PIPOPT% nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" || echo   Hinweis: ohne CUDA läuft die Transkription auf dem Prozessor.

echo   [4/4] FFmpeg ...
where ffmpeg >nul 2>nul && goto ffok
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" goto ffok
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent || echo   Hinweis: FFmpeg bitte von Hand installieren: winget install Gyan.FFmpeg
:ffok

echo ok> "runtime\.ready"
echo.
"%PY%" -m clipforge check
echo.
echo   Fertig.
timeout /t 6 >nul
exit /b 0

:fail
echo.
echo   [FEHLER] Die Einrichtung ist fehlgeschlagen. Prüfe die Internetverbindung.
echo   ClipForge versucht es beim nächsten Start erneut.
pause
exit /b 1
