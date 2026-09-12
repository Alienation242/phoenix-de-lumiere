@echo off
REM ===========================================================================
REM  PHOENIX DE LUMIERE  -  SW WALL  -  THE LOOK
REM
REM  Double-click this. A page opens in your browser with sliders for colour,
REM  contrast, the noise plate, the oil and the dither. Move one and the wall
REM  re-renders - about three seconds for a real frame at quarter size.
REM
REM  It is the REAL renderer behind those sliders, so what you see is what the
REM  delivery makes.
REM
REM  Press Save and it writes _pipeline\look.json. render_shader.py loads that
REM  as its defaults, which means EXPORT.cmd uses your look with nothing else
REM  to remember. "Delete look.json" puts the built-in look back.
REM
REM  Close this window (or Ctrl+C in it) when you are done - it is the little
REM  web server the page talks to, and it only listens to this machine.
REM ===========================================================================
title Phoenix de Lumiere - the look
cd /d "%~dp0_pipeline\scripts"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   python was not found on PATH, which the renderer needs.
  echo   If this machine has no internet either, use the offline bundle:
  echo   see _pipeline\docs\02_PORTABLE_RENDER.md
  echo.
  pause
  exit /b 1
)

python tune_look.py %*
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
  echo.
  echo   It stopped with a problem ^(code %RC%^). The reason is printed above.
  echo.
  pause
)
exit /b %RC%
