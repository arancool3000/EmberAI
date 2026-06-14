@echo off
REM Double-click this file to run Ember on Windows.
REM First run installs dependencies automatically; later runs just launch.
cd /d "%~dp0"

python -c "import PyQt6, google.genai" >nul 2>&1
if errorlevel 1 (
    echo First-time setup: installing Ember dependencies ^(a few minutes^)...
    python -m pip install --upgrade pip >nul 2>&1
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Dependency install failed. Install Python 3.10+ from https://www.python.org/downloads/
        echo Make sure you checked "Add Python to PATH" during install.
        pause
        exit /b 1
    )
)

python main.py
if errorlevel 1 pause
