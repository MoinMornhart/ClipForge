@echo off
chcp 65001 >nul
setlocal
title ClipForge – Update
cd /d "%~dp0"

where git >nul 2>nul || (echo  [FEHLER] Git ist nicht installiert: winget install Git.Git & pause & exit /b 1)

echo  Hole neueste Version ...
git fetch --prune origin || (echo  [FEHLER] GitHub nicht erreichbar. Internetverbindung prüfen. & pause & exit /b 1)
git pull --ff-only || (
  echo  [FEHLER] Lokale Änderungen verhindern das Update.
  echo  Sichere sie oder verwirf sie mit:  git stash
  pause & exit /b 1
)

echo  Aktualisiere Pakete ...
"%LOCALAPPDATA%\ClipForge\venv\Scripts\python.exe" -m pip install -q -r requirements.txt
for /f %%v in ('git rev-parse --short HEAD') do echo  Update fertig – Version %%v
pause
