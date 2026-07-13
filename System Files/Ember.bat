@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  call "%~dp0..\Windows Install\Install Ember.bat" --skip-guide
  exit /b %errorlevel%
)

start "Ember" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
