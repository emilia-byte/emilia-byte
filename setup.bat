@echo off
cd /d "%~dp0"
title Facebook Page Publisher - First-time Setup

echo ==================================================
echo   Facebook Page Publisher - First-time Setup
echo ==================================================
echo.

:: ── Check Python ─────────────────────────────────────────────────────────────

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in your PATH.
    echo.
    echo  1. Go to https://www.python.org/downloads/
    echo  2. Download and run the installer
    echo  3. On the first screen, CHECK "Add Python to PATH"
    echo  4. Re-run this setup once Python is installed.
    echo.
    goto :error
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] %PYVER% found
echo.

:: ── Install packages ──────────────────────────────────────────────────────────

echo Installing required packages...
echo (This may take a minute on first run.)
echo.

python -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo ERROR: Could not upgrade pip.
    goto :error
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Package installation failed.
    echo Check the output above for details.
    goto :error
)

echo.
echo [OK] Packages installed.
echo.

:: ── Install Playwright browser ────────────────────────────────────────────────

echo Installing Chromium browser for Playwright...
echo (This downloads ~150 MB — may take a few minutes.)
echo.

python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo ERROR: Playwright browser installation failed.
    goto :error
)

echo.
echo [OK] Chromium installed.
echo.

:: ── Done ─────────────────────────────────────────────────────────────────────

echo ==================================================
echo   Setup complete!
echo ==================================================
echo.
echo   You can now double-click run.bat to start.
echo.
echo   First time running? You will be asked for your
echo   Multilogin email and password (saved automatically
echo   so you only need to enter them once).
echo.
goto :end

:error
echo ==================================================
echo   Setup did not complete successfully.
echo   Read the error above, fix it, then run setup
echo   again.
echo ==================================================
echo.

:end
pause
