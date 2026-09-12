@echo off
REM ===========================================================================
REM  PHOENIX DE LUMIERE  -  OFFLINE INSTALL
REM
REM  Double-click this. It installs, from the files in this folder and without
REM  using the internet at all:
REM
REM    - the python packages the render needs (moderngl, numpy)
REM    - a full ffmpeg, into the project's _pipeline\bin\
REM
REM  Nothing is installed system-wide and no administrator rights are needed.
REM  It finds the project folder by itself if it can, and asks if it cannot.
REM
REM  This file is a copy. The original lives in the project at
REM  _pipeline\scripts\offline_INSTALL.cmd and is placed here by
REM  make_offline_bundle.ps1.
REM
REM  The window stays open at the end so you can read what happened.
REM ===========================================================================
title Phoenix de Lumiere - offline install
cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
  echo.
  echo   PowerShell was not found on this machine, which this installer needs.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
  echo   Finished. The machine is ready - read the two commands printed above.
) else (
  echo   It stopped with a problem ^(code %RC%^). The reason is printed above.
  echo   Nothing was damaged; it is safe to fix it and run this again.
)
echo.
pause
exit /b %RC%
