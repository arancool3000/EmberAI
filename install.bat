@echo off
REM Ember for Windows - one-time install.
setlocal
cd /d "%~dp0"

echo ===========================================
echo   Ember - Windows install
echo ===========================================

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found. Install Python 3.10+ from https://www.python.org/downloads/windows/
    echo        During install, tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing dependencies (PyQt6, Gemini SDK, Anthropic SDK, UI Automation, voice)...
python -m pip install -r requirements.txt

echo.
echo ===========================================
echo   Install complete!
echo.
echo   Run:  run.bat   (or:  python main.py)
echo   Free Gemini key at:  https://aistudio.google.com/apikey
echo ===========================================
pause
