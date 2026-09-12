@echo off
REM ===========================================================================
REM  PHOENIX DE LUMIERE  -  SW WALL  -  DELIVERY EXPORT
REM
REM  Double-click this file. Pick a number. It does the rest:
REM    checks the machine, renders the two projector plates, verifies them,
REM    and writes the notes that go with them.
REM
REM  Run "Proof" first (about two minutes) to see that everything works.
REM  Then run "Deliver" for the real thing.
REM
REM  If a second, aligned mask set has been built it also asks which one to
REM  use. Each goes to its own folder with its own file names, so both can be
REM  rendered one after the other. See _pipeline\docs\05_MASKS.md.
REM
REM  The window stays open at the end so you can read what happened.
REM ===========================================================================
title Phoenix de Lumiere - SW wall - delivery export
cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
  echo.
  echo   PowerShell was not found on this machine, which this export needs.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_pipeline\scripts\export_delivery.ps1" %*
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
  echo   Finished. Read the summary above - it names the two files to send.
) else (
  echo   It stopped with a problem ^(code %RC%^). The reason is printed above.
  echo   Nothing was sent anywhere; it is safe to fix it and run this again.
)
echo.
pause
exit /b %RC%
