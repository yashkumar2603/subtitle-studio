@echo off
setlocal
cd /d "%~dp0"
title Subtitle Studio

rem --- uv (Python + package manager). Installed once into your user folder. ---
where uv >nul 2>nul
if errorlevel 1 set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv, one time only...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>nul
if errorlevel 1 (
  echo Could not install uv. See https://docs.astral.sh/uv/ and run this file again.
  pause
  exit /b 1
)

rem --- NVIDIA GPU? Also install the CUDA libraries for fast transcription. ---
set "EXTRA="
where nvidia-smi >nul 2>nul
if not errorlevel 1 set "EXTRA=--extra gpu"

echo Preparing Subtitle Studio. The first run downloads about 1-2 GB, please wait...
uv sync %EXTRA% --quiet
if errorlevel 1 (
  echo Setup failed. Check your internet connection and try again.
  pause
  exit /b 1
)
uv run --no-sync python -m app %*
pause
