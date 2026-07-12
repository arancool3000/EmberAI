@echo off
setlocal EnableExtensions
set "ROOT=%~dp0.."
set "GUIDE=%~dp0Installation Guide.html"

if /I not "%~1"=="--skip-guide" (
  start "" "%GUIDE%"
  echo.
  echo The installation guide is open in your browser.
  choice /C YN /N /M "Continue installing Ember now? [Y/N] "
  if errorlevel 2 exit /b 0
)

pushd "%ROOT%"
echo.
echo ============================================
echo   Installing Ember for Windows
echo ============================================

if exist ".venv\Scripts\python.exe" goto dependencies

py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  echo Creating Ember's private Python environment...
  py -3.12 -m venv .venv
  goto dependencies
)

python -c "import sys; assert sys.version_info ^>= (3,10)" >nul 2>&1
if not errorlevel 1 (
  echo Creating Ember's private Python environment...
  python -m venv .venv
  goto dependencies
)

where winget >nul 2>&1
if errorlevel 1 goto no_python
echo Python is not installed. Installing Python 3.12 for this user...
winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto no_python

set "PY312=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PY312%" goto no_python
"%PY312%" -m venv .venv
if errorlevel 1 goto failed

:dependencies
echo Installing Ember's dependencies into its private environment...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Installation complete. Starting Ember...
start "Ember" /D "%ROOT%" "%ROOT%\.venv\Scripts\pythonw.exe" "%ROOT%\main.py"
popd
exit /b 0

:no_python
echo.
echo Ember could not install Python automatically.
echo Open https://www.python.org/downloads/windows/ and install Python 3.12.
echo Tick "Add python.exe to PATH", then run this installer again.
start "" "https://www.python.org/downloads/windows/"
goto failed

:failed
echo.
echo Installation did not finish. The window will stay open so you can read the error above.
pause
popd
exit /b 1
